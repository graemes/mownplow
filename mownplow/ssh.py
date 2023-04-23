import asyncio
import logging

import asyncssh


class SSHClient:
    def __init__(self, hostname: str, username: str, private_key_path: str):
        self.hostname = hostname
        self.username = username
        self.private_key_path = private_key_path
        self.connection = None

    async def connect(self):
        self.connection = await asyncssh.connect(
            self.hostname, username=self.username, client_keys=[self.private_key_path]
        )
        logging.debug(f"SSH connected to {self.username}@{self.hostname}")

    async def run_command(self, command: str):
        if self.connection is None:
            await self.connect()

        logging.debug(f"SSH running: {command}")
        result = await self.connection.run(command)

        return result

    async def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
