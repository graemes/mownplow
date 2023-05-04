#!/usr/bin/env python3
"""
BladeBit wrapper.

A simple wrapper for BladeBit to suspend plotting if there is insufficient free space 
in the (single) destination.

Author: Graeme Seaton <graemes@graemes.com>
SPDX-License-Identifier: GPL-3.0-or-later

Feel free to buy me a drink (only if you want to :)): 
xch1vlnelz9ef43z3xa4x6a3zzfm7cezwvmq332p97xlflmxxcgzdrpsqamyee
"""
import asyncio
import os
import shutil

import psutil
import yaml


class ExecutableBladeBitDoesNotExistError(Exception):
    pass


class DestDoesNotExistError(Exception):
    pass


class MissingKeyError(Exception):
    pass


class Config:
    def __init__(self, config_file: str) -> None:
        with open(config_file, "r") as file:
            config = yaml.safe_load(file)

        # BladeBit command
        self.cmd = config["Cmd"]
        if not os.path.isfile(self.cmd) and not os.access(self.cmd, os.X_OK):
            raise ExecutableBladeBitDoesNotExistError(
                f"An executable BladeBit does not exist at '{self.cmd}'."
            )

        # Destination
        self.dest = config["Dest"]
        if not os.path.exists(self.dest):
            raise DestDoesNotExistError(
                f"The destination '{self.dest}' does not exist."
            )

        # Plot parameters
        # Required
        self.farmer_key = config["Plot"]["FarmerKey"]
        if not self.farmer_key:
            raise MissingKeyError("FarmerKey has not been specified.")
        self.pool_contract = config["Plot"]["PoolContract"]
        if not self.pool_contract:
            raise MissingKeyError("PoolContract has not been specified.")

        # Optional
        self.compress_level = int(config["Plot"].get("CompressLevel", 1))
        self.num_plots = config["Plot"].get("NumberPlots", 0)

        self.threads = config["Plot"].get("Threads", None)
        self.device = config["Plot"].get("Device", None)

        # Free space required
        plot_sizes = {
            0: 101.3,
            1: 87.54,
            2: 86.03,
            3: 84.46,
            4: 82.86,
            5: 81.26,
            6: 79.65,
            7: 78.05,
            9: 75.2,
        }
        # Default to uncompressed plot size
        self.min_free_space = int(
            (plot_sizes.get(self.compress_level, 101.3) + 1) * (1024**3)
        )


async def run_and_monitor_bladebit(config: Config):
    run_cmd = [
        config.cmd,
        "-f",
        f"{config.farmer_key}",
        "-c",
        f"{config.pool_contract}",
        "--compress",
        f"{config.compress_level}",
        "-n",
        f"{config.num_plots}",
    ]
    if config.threads:
        run_cmd.append("-t")
        run_cmd.append(f"{config.threads}")
    run_cmd.append("cudaplot")
    if config.device:
        run_cmd.append("-d")
        run_cmd.append(f"{config.device}")
    run_cmd.append(f"{config.dest}")

    proc = await asyncio.create_subprocess_exec(
        *run_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    is_paused = False
    while True:
        output = await proc.stdout.readline()
        if not output:
            break

        print(output.decode().strip())
        if "Generating plot" in output.decode():
            while True:
                # Check for free disk space
                disk_usage = shutil.disk_usage(config.dest)
                free_space = disk_usage.free

                if free_space < config.min_free_space and not is_paused:
                    print("Pausing BladeBit - low disk space - free/min: "
                        + f"{free_space}/{config.min_free_space}"
                        + " bytes"
                    )
                    await suspend_process(proc.pid)
                    is_paused = True
                elif free_space > config.min_free_space and is_paused:
                    print("Resuming BladeBit - sufficient disk space - free/min: "
                        + f"{free_space}/{config.min_free_space}"
                        + " bytes"
                    )
                    await resume_process(proc.pid)
                    is_paused = False
                    break
                elif not is_paused:
                    break

                await asyncio.sleep(5)

    await proc.wait()


async def suspend_process(pid):
    process = psutil.Process(pid)
    process.suspend()


async def resume_process(pid):
    process = psutil.Process(pid)
    process.resume()


async def main():
    config = Config("bb_config.yaml")
    await run_and_monitor_bladebit(config)


# Run the main function in the event loop
loop = asyncio.get_event_loop()
loop.run_until_complete(main())
