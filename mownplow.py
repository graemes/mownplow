#!/usr/bin/env python3
"""
Mow'n'Plow MK2.

An efficient Chia plot mower and mover.

Author: Graeme Seaton <graemes@graemes.com>
SPDX-License-Identifier: GPL-3.0-or-later

Based on 'The Plow' by Luke Macken <phorex@protonmail.com> @ https://github.com/lmacken/plow

Feel free to buy me a drink (only if you want to :)): 
xch1vlnelz9ef43z3xa4x6a3zzfm7cezwvmq332p97xlflmxxcgzdrpsqamyee
"""
import asyncio
import logging
import queue
from datetime import datetime
from pathlib import Path

import aiohttp
import aionotify
import asyncssh

from mownplow.config import Config
from mownplow.harvester import HarvesterCert, HarvesterRequest
from mownplow.destman import DestMan
from mownplow.scheduler import PlowScheduler
from mownplow.ssh import SSHClient

#####
# System variables - don't change unless you know what you are doing.
#####
# Short & long sleep durations upon various error conditions
SLEEP_FOR = 60 * 3
SLEEP_FOR_LONG = 60 * 20

#####
# Utility functions / classes
#####


async def get_dest_dirs(config: Config) -> list:
    dest_dirs = config.dest_dirs

    if config.dest_dirs is None or not config.dest_dirs:
        dest_dirs = []
        ssh_conn = SSHClient(
            config.dest_host, config.dest_username, config.ssh_private_key_path
        )
        await ssh_conn.connect()

        logging.debug(f"Destination root: {config.dest_root}")
        dest_mounts_scr = (
            "mount | grep " + config.dest_root + " | awk '{ print $3 }' | sort"
        )

        remote_result = await ssh_conn.run_command(dest_mounts_scr)
        if remote_result.returncode != 0:
            logging.error(
                f"⁉️  {dest_mounts_scr!r} exited with {remote_result.returncode}"
            )
            return dest_dirs

        dest_candidates = remote_result.stdout.splitlines()
        logging.info(f"Found destination mounts:\n {dest_candidates}")

        for dest_dir in dest_candidates:
            logging.debug(f"Evaluating {dest_dir}")
            dest_dirs.append(Path(dest_dir).name)

        dest_dirs.sort()
        config.update_dest_dirs(dest_dirs)
        await ssh_conn.close()

    return dest_dirs


#####
# This is where the magic happens
#####
async def plotfinder(paths: list, plot_queue: queue, loop):
    for path in paths:
        for plot in Path(path).glob("**/*.plot"):
            await plot_queue.put(plot)
    await plotwatcher(paths, plot_queue, loop)


async def plotwatcher(paths: list, plot_queue: queue, loop):
    watcher = aionotify.Watcher()
    for path in paths:
        if not Path(path).exists():
            logging.info(f"! Path does not exist: {path}")
            continue
        watcher.watch(
            alias=path,
            path=path,
            flags=aionotify.Flags.MOVED_TO,
        )
        logging.info(f"Watching {path}")
    await watcher.setup(loop)
    while True:
        event = await watcher.get_event()
        logging.info(event)
        if event.name.endswith(".plot"):
            plot_path = Path(event.alias) / event.name
            await plot_queue.put(plot_path)
            await asyncio.sleep(0)


async def plow(
    config: Config, dest_dir, plot_queue, dest_schedule, harvester_cert, loop
):
    # Plow initialisation
    ssh_conn = SSHClient(
        config.dest_host, config.dest_username, config.ssh_private_key_path
    )
    await ssh_conn.connect()

    destman = DestMan(config, dest_dir, ssh_conn)
    if not await destman.init_scripts():
        logging.info(f"Unable to initialise scripts for {dest_dir}")
        return

    harvester_req = None
    if not config.farm_during_plow:
        harvester_req = HarvesterRequest(
            config.harvester_host, config.harvester_port, harvester_cert
        )

    currently_farming_dest = not config.farm_during_plow
    incremental_remove = config.replot

    # Work loop
    logging.info(f"🧑‍🌾 plowing to {destman.dest}")
    while True:
        try:
            logging.debug(f"{destman.dest} waiting for plot")
            plot = await plot_queue.get()

            current_priority = dest_schedule.get_current_priority()
            logging.debug(f"Current dest priority: {current_priority}")
            if current_priority == dest_dir:
                plot_size = plot.stat().st_size
                plot_size_KB = int((plot_size) / (1024))

                # Remove from farm only when actually starting
                # to plow to this destination
                if currently_farming_dest:
                    logging.info(f"Removing {destman.virtual_dest} from farming")
                    await harvester_req.remove_plot_directory(destman.virtual_dest)
                    currently_farming_dest = False

                if incremental_remove and config.remove_all_replots:
                    # await asyncio.sleep(0)
                    logging.info(f"Removing all matching replots on {destman.dest}")
                    remove_success = await destman.remove_all_replots()
                    if not remove_success:
                        await plot_queue.put(plot)
                        break
                    incremental_remove = False
                    await asyncio.sleep(5)

                if incremental_remove:
                    remove_success = await destman.remove_next_replot(plot_size_KB)
                    if not remove_success:
                        await plot_queue.put(plot)
                        break
                
                # Only release current priority once deletion complete
                # Workaround for double clearance issue
                dest_schedule.remove_current_priority()
                await asyncio.sleep(0)

                # Treat all destinations as remote (even if local)
                dest_free = await destman.get_dest_free_space()
                if not dest_free:
                    await plot_queue.put(plot)
                    break
                await asyncio.sleep(0)

                if dest_free > plot_size_KB:
                    logging.info(
                        f"✅ Destination {destman.dest} has {int(dest_free/(1024*1024))}GiB free"
                    )
                else:
                    logging.info(f"❎ Destination {destman.dest} is full")
                    await plot_queue.put(plot)
                    # Just quit the worker entirely for this destination.
                    break

                logging.info(
                    f"🚜 {plot} ➡️  {destman.dest} - {int(plot_size_KB/(1024*1024))}GiB"
                )
                rsync_cmd = (
                    f"{config.rsync_cmd} {config.rsync_flags} {plot} {destman.dest}"
                )

                # Now rsync the real plot
                proc = await asyncio.create_subprocess_shell(
                    rsync_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                start = datetime.now()
                stdout, stderr = await proc.communicate()
                finish = datetime.now()

                if proc.returncode == 0:
                    logging.info(f"🏁 {rsync_cmd} ({finish - start})")
                    logging.debug(f"Adding {dest_dir} back to schedule")
                    dest_schedule.add_dest_to_q(dest_dir)
                    await asyncio.sleep(1)
                elif proc.returncode == 10:  # Error in socket I/O
                    # Retry later.
                    logging.warning(
                        f"⁉️ {rsync_cmd!r} exited with {proc.returncode} (error in socket I/O)"
                    )
                    await plot_queue.put(plot)
                    await asyncio.sleep(SLEEP_FOR_LONG)
                elif proc.returncode in (11, 23):  # Error in file I/O
                    # Most likely a full drive.
                    logging.error(
                        f"⁉️ {rsync_cmd!r} exited with {proc.returncode} (error in file I/O)"
                    )
                    dest_schedule.rem_dest_from_priorities(dest_dir)
                    await plot_queue.put(plot)
                    logging.error(f"{destman.dest} plow exiting")
                    break
                else:
                    logging.info(f"⁉️ {rsync_cmd!r} exited with {proc.returncode}")
                    await asyncio.sleep(SLEEP_FOR)
                    dest_schedule.rem_dest_from_priorities(dest_dir)
                    await plot_queue.put(plot)
                    logging.error(f"{destman.dest} plow exiting")
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
                await asyncio.sleep(5)

        except Exception as e:
            logging.error(f"! {e}")

    # Sync destination path before completion
    await destman.sync_dest_mount_path()
    await asyncio.sleep(5)
    
    if not currently_farming_dest:
        logging.info(f"Adding {destman.virtual_dest} back to farm")
        await harvester_req.add_plot_directory(destman.virtual_dest)
        currently_farming_dest = True

    await ssh_conn.close()
    await asyncio.sleep(5)


async def main(config, loop):
    plot_queue = asyncio.Queue()
    dest_schedule = PlowScheduler()
    plow_tasks = []

    harvester_cert = None
    if not config.farm_during_plow:
        harvester_cert = HarvesterCert(
            config.dest_host,
            config.dest_username,
            config.ssh_private_key_path,
            config.harvester_cacert_path,
            config.harvester_cert_path,
            config.harvester_key_path,
        )
        try:
            await harvester_cert.retrieve_cert_and_key()
        except (OSError, asyncssh.Error) as e:
            raise Exception(f"Failed to retrieve cert and key files: {e}") from e

    logging.info("🌱 Mow'n'Plow running...")

    create_dests = asyncio.create_task(get_dest_dirs(config))
    dest_dirs = await create_dests
    logging.debug(f"Destinations: {dest_dirs}")

    # Watch for new plots
    plow_tasks.append(asyncio.create_task(plotfinder(config.sources, plot_queue, loop)))

    # Fire up a worker for each destination
    priority = 1
    for dest_dir in dest_dirs:
        dest_schedule.add_dest_priority(dest_dir, priority)
        plow_tasks.append(
            asyncio.create_task(
                plow(config, dest_dir, plot_queue, dest_schedule, harvester_cert, loop)
            )
        )
        await asyncio.sleep(0.5)
        priority = priority + 1

    # Once all of the destinations are complete (probably full) then
    # plotfinder is the last task running
    while len(plow_tasks) > 1:
        done, plow_tasks = await asyncio.wait(
            plow_tasks, return_when=asyncio.FIRST_COMPLETED
        )

    plow_tasks.pop().cancel()
    await asyncio.sleep(0.5)

    logging.info("🌱 Plow destinations complete...")


if __name__ == "__main__":
    
    config = Config("config.yaml")

    logging.basicConfig(
        format="%(asctime)s %(levelname)-2s %(message)s",
        level=config.logging,
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )

    logging.getLogger("asyncssh").setLevel(logging.WARNING)

    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main(config, loop))
    except KeyboardInterrupt:
        pass
