#!/usr/bin/env python3
"""
The Mow'n'Plow.

An efficient Chia plot mower and mover.

Author: Graeme Seaton <graemes@graemes.com>
SPDX-License-Identifier: GPL-3.0-or-later

Based on 'The Plow' by Luke Macken <phorex@protonmail.com> @ https://github.com/lmacken/plow
"""
import asyncio
import glob
import logging
import queue
import random
import shutil
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import aionotify
import asyncssh

# Local plot sources
# For wildcards:
#   SOURCES = glob.glob('/mnt/*')
SOURCES = ["/data/bladebit"]

# Rsync destinations
# Examples: ["/mnt/HDD1", "192.168.1.10::hdd1"]
DEST_USER = "chia"
DEST_PROTOCOL = "rsync://"
DEST_HOST = "chia01"
DEST_PORT = ":12000"
DEST_ROOT = "/plots"
#DEST_DIRS = ["c0b0", "c0b3"]
DEST_DIRS = []

# Shuffle plot destinations. Useful when using many plotters to decrease the odds
# of them copying to the same drive simultaneously.
SHUFFLE = False 

# Rsync bandwidth limiting
BWLIMIT = None

# Optionally set the I/O scheduling class and priority
IONICE = None  # "-c3" for "idle"

# Only send 1 plot at a time, regardless of source/dest. 
ONE_AT_A_TIME = False

# Each plot source can have a lock, so we don't send more than one file from
# that origin at any given time.
ONE_PER_DRIVE = False

# Short & long sleep durations upon various error conditions
SLEEP_FOR = 60 * 3
SLEEP_FOR_LONG = 60 * 20

if SHUFFLE:
    random.shuffle(DEST_DIRS)

RSYNC_CMD = "rsync"

if BWLIMIT:
    RSYNC_FLAGS = f"--remove-source-files --preallocate --whole-file --skip-compress=plot --bwlimit={BWLIMIT}"
else:
    RSYNC_FLAGS = "--remove-source-files --preallocate --whole-file --skip-compress=plot"

if IONICE:
    RSYNC_CMD = f"ionice {IONICE} {RSYNC_CMD}"

SSH_CMD = "ssh"

REPLOT = False
REPLOT_BEFORE = "2023-02-26 00:00"

LOCK = asyncio.Lock()  # Global ONE_AT_A_TIME lock
SRC_LOCKS = defaultdict(asyncio.Lock)  # ONE_PER_DRIVE locks


class PQueue(queue.PriorityQueue):
    def peek(self):
        try:
            with self.mutex:
                return self.queue[0]
        except IndexError:
            raise queue.Empty


class PlowScheduler:
    # Manage plow priorities

    def __init__(self):
        self.dest_queue = PQueue()
        self.dest_priorities = {}

    def add_dest_priority(self,dest,priority):
        # logging.info(f"Adding Dest: {dest} - Priority: {priority} to schedule")
        self.dest_priorities[dest] = priority
        self.dest_queue.put((priority,dest))

    def get_current_priority(self):
        try:
            priority,dest = self.dest_queue.peek()
            return dest
        except:
            return None

    def remove_current_priority(self):
        priority,dest = self.dest_queue.get()
        return dest

    def add_dest_to_q(self, dest):
        self.dest_queue.put((self.dest_priorities[dest], dest))

    def rem_dest_from_priorities(self, dest):
        self.dest_priorities.pop(dest, None)


async def run_ssh_command(host, username, command):
    async with asyncssh.connect(host, username=username) as conn:
        return await conn.run(command)

async def create_dest_dirs():

    if not DEST_DIRS:
        logging.debug(f"Destination root: {DEST_ROOT}")
        dest_mounts_scr = "df | grep " + DEST_ROOT + " | awk '{ print $6 }' | sort"

        remote_result = await run_ssh_command(DEST_HOST, DEST_USER, dest_mounts_scr )
        if remote_result.returncode != 0:
            logging.error(f"⁉️  {dest_mounts_scr!r} exited with {remote_result.returncode}")
            return DEST_DIRS

        dest_candidates = remote_result.stdout.splitlines()
        logging.debug(f"Destination mounts:\n {dest_candidates}")
        
        for dest_dir in dest_candidates:
            logging.debug(f"Evaluating {dest_dir}")
            DEST_DIRS.append(Path(dest_dir).name)   

    if SHUFFLE:
        random.shuffle(DEST_DIRS)
    
    return DEST_DIRS

async def plotfinder(paths, plot_queue, loop):
    for path in paths:
        for plot in Path(path).glob("**/*.plot"):
            await plot_queue.put(plot)
    await plotwatcher(paths, plot_queue, loop)

async def plotwatcher(paths, plot_queue, loop):
    watcher = aionotify.Watcher()
    for path in paths:
        if not Path(path).exists():
            logging.info(f'! Path does not exist: {path}')
            continue
        watcher.watch(
            alias=path,
            path=path,
            flags=aionotify.Flags.MOVED_TO,
        )
        logging.info(f"watching {path}")
    await watcher.setup(loop)
    while True:
        event = await watcher.get_event()
        logging.info(event)
        if event.name.endswith(".plot"):
            plot_path = Path(event.alias) / event.name
            await plot_queue.put(plot_path)


async def plow(dest, dest_host, plot_queue, dest_schedule, loop):
    logging.info(f"🧑‍🌾 plowing to {dest}")

    # Get the physical mount point for this destination
    dest_mount_scr = "df | grep -w " + Path(dest).name + " | awk '{ print $6 }'"
    dest_mount_path = ""

    while True:
        try:
            remote_result = await run_ssh_command(dest_host, DEST_USER, dest_mount_scr )
            if remote_result.returncode != 0:
                logging.info(f"⁉️  {dest_mount_scr!r} exited with {remote_result.returncode}")
                return
            break
        except asyncssh.ConnectionLost:
            await asyncio.sleep(1)
            logging.debug(f"WTF? Connection lost for {dest}")

    dest_mount_path = remote_result.stdout.strip()
    logging.info(f"Destination path: {dest_mount_path}")

    # Shared scripts/commands for this destination
    delete_candidate_scr = "find " + dest_mount_path + " -type f ! -newermt '" + REPLOT_BEFORE + "' | head -n1"
    free_space_scr = "df " + dest_mount_path + " | tail -1 | awk '{ print $4 }'"

    while True:
        try:
            plot = await plot_queue.get()
            logging.debug(f"Evaluating {dest}")

            current_priority = dest_schedule.get_current_priority()
            logging.debug(f"Current dest priority: {current_priority}")
            if current_priority == dest:
                dest_schedule.remove_current_priority()
                plot_size = plot.stat().st_size
                plot_size_KB = int(plot_size/(1024))

                if REPLOT:
                    remote_result = await run_ssh_command(dest_host, DEST_USER, delete_candidate_scr )
                    if remote_result.returncode != 0:
                        logging.error(f"⁉️  {delete_candidate_scr!r} exited with {remote_result.returncode}")
                        await plot_queue.put(plot)
                        break
                    await asyncio.sleep(1)

                    rem_file = remote_result.stdout.strip()
                    if rem_file:
                        logging.info(f"␡ Removing {rem_file}")
                        remove_file_scr = "rm " + rem_file

                        remote_result = await run_ssh_command(dest_host, DEST_USER, remove_file_scr )
                        if remote_result.returncode != 0:
                            logging.error(f"⁉️  {remove_file_scr!r} exited with {remote_result.returncode}")
                            await plot_queue.put(plot)
                            break
                        await asyncio.sleep(1)

                        # Wait for the freed space to show up
                        dest_free = 0
                        while dest_free < plot_size_KB:
                            remote_result = await run_ssh_command(dest_host, DEST_USER, free_space_scr )
                            if remote_result.returncode != 0:
                                logging.error(f"⁉️  {free_space_scr!r} exited with {remote_result.returncode}")
                                await plot_queue.put(plot)
                                break
                            await asyncio.sleep(1)

                            dest_free = int(remote_result.stdout)
                            await asyncio.sleep(5)
                        
                        # Sleep for a little bit longer so the disk can finish updating the free space 
                        await asyncio.sleep(5)

                # Treat all destinations as remote (even if local)
                remote_result = await run_ssh_command(dest_host, DEST_USER, free_space_scr )
                if remote_result.returncode != 0:
                    logging.info(f"⁉️  {free_space_scr!r} exited with {remote_result.returncode}")
                    await plot_queue.put(plot)
                    break
                await asyncio.sleep(1)

                dest_free = int(remote_result.stdout)
                if dest_free > plot_size_KB:
                    logging.info(f"✅ Destination {dest} has {int(dest_free/(1024*1024))}GiB free")
                else:
                    logging.info(f"❎ Destination {dest} is full")
                    await plot_queue.put(plot)
                    # Just quit the worker entirely for this destination.
                    break
                    
                # One at a time, system-wide lock
                if ONE_AT_A_TIME:
                    await LOCK.acquire()

                # Only send one plot from each SSD at a time
                if ONE_PER_DRIVE:
                    await SRC_LOCKS[plot.parent].acquire()

                try:
                    logging.info(f"🚜 {plot} ➡️  {dest} - {int(plot_size_KB/(1024*1024))}GiB")
                    rsync_cmd = f"{RSYNC_CMD} {RSYNC_FLAGS} {plot} {dest}"

                    # Now rsync the real plot
                    proc = await asyncio.create_subprocess_shell(
                        rsync_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                    )
                    start = datetime.now()
                    stdout, stderr = await proc.communicate()
                    finish = datetime.now()
                finally:
                    if ONE_PER_DRIVE:
                        SRC_LOCKS[plot.parent].release()
                    if ONE_AT_A_TIME:
                        LOCK.release()

                if proc.returncode == 0:
                    logging.info(f"🏁 {rsync_cmd} ({finish - start})")
                    # logging.info(f"Adding {dest} back to schedule")
                    dest_schedule.add_dest_to_q(dest)
                    await asyncio.sleep(1)
                elif proc.returncode == 10:  # Error in socket I/O
                    # Retry later.
                    logging.warning(f"⁉️ {rsync_cmd!r} exited with {proc.returncode} (error in socket I/O)")
                    await plot_queue.put(plot)
                    await asyncio.sleep(SLEEP_FOR_LONG)
                elif proc.returncode in (11, 23):  # Error in file I/O
                    # Most likely a full drive.
                    logging.error(f"⁉️ {rsync_cmd!r} exited with {proc.returncode} (error in file I/O)")
                    dest_schedule.rem_dest_from_priorities(dest)
                    await plot_queue.put(plot)
                    logging.error(f"{dest} plow exiting")
                    break
                else:
                    logging.info(f"⁉️ {rsync_cmd!r} exited with {proc.returncode}")
                    await asyncio.sleep(SLEEP_FOR)
                    dest_schedule.rem_dest_from_priorities(dest)
                    await plot_queue.put(plot)
                    logging.error(f"{dest} plow exiting")
                    break
                if stdout:
                    output = stdout.decode().strip()
                    if output:
                        logging.info(f"{stdout.decode()}")
                if stderr:
                    logging.warning(f"⁉️ {stderr.decode()}")
            else:
                # logging.info(f"Skipping {dest} for now")
                await plot_queue.put(plot)
                await asyncio.sleep(1)

        except Exception as e:
            logging.error(f"! {e}")


async def main(paths, loop):
    plot_queue = asyncio.Queue()
    dest_schedule = PlowScheduler()
    plow_tasks = []

    logging.info("🌱 Mow'n'Plow running...")

    if not DEST_DIRS:
        create_dests = asyncio.create_task(create_dest_dirs())
        created_dests = await create_dests
        logging.debug(f"Destinations: {created_dests}")

    # Watch for new plots
    plow_tasks.append(asyncio.create_task(plotfinder(paths, plot_queue, loop)))

    # Fire up a worker for each plow and create priority management handling variables
    priority = 0
    for dest_dir in DEST_DIRS:
        priority = priority + 1
        dest = f"{DEST_PROTOCOL}{DEST_HOST}{DEST_PORT}{DEST_ROOT}/{dest_dir}"
        dest_schedule.add_dest_priority(dest,priority)
        plow_tasks.append(asyncio.create_task(plow(dest, DEST_HOST, plot_queue, dest_schedule, loop)))

    # Once all of the destinations are complete (probably full) then
    # plotfinder is the last task running
    while (len(plow_tasks) > 1):
        done,plow_tasks = await asyncio.wait(
            plow_tasks,
            return_when=asyncio.FIRST_COMPLETED
        )
    
    plow_tasks.pop().cancel()
    await asyncio.sleep(0.5)

    logging.info('🌱 Plow destinations complete...')


if __name__ == "__main__":

    logging.basicConfig(
      format='%(asctime)s %(levelname)-2s %(message)s',
      level=logging.INFO,
      datefmt='%Y-%m-%d %H:%M:%S',
      force=True)

    logging.getLogger('asyncssh').setLevel(logging.WARNING)

    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main(SOURCES, loop))
    except KeyboardInterrupt:
        pass
