import asyncio
import logging
from pathlib import Path

from mownplow.config import Config
from mownplow.ssh import SSHClient


class DestMan:
    # Manage plow destination properties

    def __init__(self, config: Config, dest_dir: str, ssh_conn: SSHClient):
        self.dest_dir = dest_dir
        self.ssh_conn = ssh_conn

        self.dest = (
            config.dest_protocol
            + "://"
            + config.dest_host
            + ":"
            + str(config.dest_port)
            + config.dest_root
            + "/"
            + self.dest_dir
        )
        self.dest_root = config.dest_root
        self.replot_before = config.replot_before
        self.virtual_dest = f"{self.dest_root}/{self.dest_dir}"

        self.dest_mount_path = None
        self.delete_candidate_scr = None
        self.free_space_scr = None
        self.sync_dest_mount_scr = None

    async def init_scripts(self) -> str:
        # Get the physical mount point for this destination
        dest_mount_scr = (
            "mount | grep "
            + self.dest_root
            + " | grep -w "
            + self.dest_dir
            + " | awk '{print $3}'"
        )
        result = await self.ssh_conn.run_command(dest_mount_scr)
        if result.returncode == 0:
            self.dest_mount_path = result.stdout.strip()
            logging.debug(f"Destination path: {self.dest_mount_path}")
            self.delete_candidate_scr = (
                "find "
                + self.dest_mount_path
                + " -type f ! -newermt '"
                + self.replot_before
                + "' | sort | head -n1"
            )
            self.free_space_scr = (
                "df " + self.dest_mount_path + " | awk 'NR==2{print $4}'"
            )
            self.sync_dest_mount_scr = "sync -f " + self.dest_mount_path
        else:
            logging.info(f"⁉️  {dest_mount_scr!r} exited with {result.returncode}")
            return False
        return True

    async def get_dest_free_space(self) -> int:
        dest_free = 0
        result = await self.ssh_conn.run_command(self.free_space_scr)
        if result.returncode == 0:
            dest_free = int(result.stdout)
        else:
            logging.info(f"⁉️  {self.free_space_scr!r} exited with {result.returncode}")
        return dest_free

    async def sync_dest_mount_path(self) -> int:
        logging.debug(f"⁉️  Syncing {self.dest_mount_path}")
        return await self.ssh_conn.run_command(self.sync_dest_mount_scr)

    async def remove_next_replot(
        self,
        plot_size_KB: int,
    ) -> bool:
        rem_file = await self._get_delete_candidate()
        if rem_file:
            if await self._remove_remote_plot(rem_file):
                # Sync to flush deletion
                await self.sync_dest_mount_path()
            else:
                return False
        return True

    async def remove_all_replots(self) -> bool:
        rem_file = await self._get_delete_candidate()
        while rem_file:
            logging.debug(f"⁉️  Removing {rem_file}")
            if not await self._remove_remote_plot(rem_file):
                return False
            rem_file = await self._get_delete_candidate()
            # Give other processes room to breathe
            await asyncio.sleep(0)
        # Sync to flush all deletions
        await self.sync_dest_mount_path()
        return True

    async def _get_delete_candidate(self) -> str:
        remote_result = await self.ssh_conn.run_command(self.delete_candidate_scr)
        if remote_result.returncode != 0:
            logging.error(
                f"⁉️  {self.delete_candidate_scr!r} exited with {remote_result.returncode}"  # noqa: E501
            )
            return None
        rem_file = remote_result.stdout.strip()
        return rem_file

    async def _remove_remote_plot(self, rem_file: str) -> bool:
        logging.info(f"␡ Removing {rem_file}")
        remove_file_scr = f"rm {rem_file}"
        remote_result = await self.ssh_conn.run_command(remove_file_scr)
        if remote_result.returncode != 0:
            logging.error(
                f"⁉️  {remove_file_scr!r} exited with {remote_result.returncode}"
            )
            return False
        return True
