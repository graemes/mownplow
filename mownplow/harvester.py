import asyncio
import json
import logging
import ssl
import tempfile

import aiohttp
import asyncssh


class HarvesterCert:
    def __init__(
        self,
        ssh_hostname: str,
        ssh_username: str,
        ssh_key_path: str,
        cacert_path: str,
        cert_path: str,
        key_path: str,
    ):
        self.ssh_hostname = ssh_hostname
        self.ssh_username = ssh_username
        self.ssh_key_path = ssh_key_path
        self.cacert_path = cacert_path
        self.cert_path = cert_path
        self.key_path = key_path

        self.cacert_file = None
        self.cert_file = None
        self.key_file = None

    async def retrieve_cert_and_key(self):
        try:
            async with asyncssh.connect(
                self.ssh_hostname,
                username=self.ssh_username,
                client_keys=[self.ssh_key_path],
            ) as conn:
                result = await asyncio.gather(
                    conn.run(f"cat {self.cacert_path}", encoding=None),
                    conn.run(f"cat {self.cert_path}", encoding=None),
                    conn.run(f"cat {self.key_path}", encoding=None),
                )
                self.cacert = result[0].stdout
                self.cert = result[1].stdout
                self.key = result[2].stdout

                self._create_temp_ssl_files()
        except (OSError, asyncssh.Error) as e:
            raise Exception(f"Failed to retrieve cert and key files: {e}") from e

    def _create_temp_ssl_files(self):
        # Create temporary files for use SSL/TLS context creation
        cacert_data = self.cacert
        cert_data = self.cert
        key_data = self.key

        try:
            with tempfile.NamedTemporaryFile(
                delete=False
            ) as cacert_file, tempfile.NamedTemporaryFile(
                delete=False
            ) as cert_file, tempfile.NamedTemporaryFile(
                delete=False
            ) as key_file:
                cacert_file.write(cacert_data)
                cert_file.write(cert_data)
                key_file.write(key_data)

            self.cacert_file = cacert_file.name
            self.cert_file = cert_file.name
            self.key_file = key_file.name
        except (OSError, asyncssh.Error) as e:
            raise Exception(f"Failed to create cert and key files: {e}") from e


class HarvesterRequest:
    def __init__(self, host: str, port, harvester_cert: HarvesterCert):
        self.host = host
        self.port = port

        self.cacert_file = harvester_cert.cacert_file
        self.cert_file = harvester_cert.cert_file
        self.key_file = harvester_cert.key_file

        self.ssl_context = None

    def _set_context(self):
        ssl_context = ssl._create_unverified_context(
            purpose=ssl.Purpose.CLIENT_AUTH, cafile=self.cacert_file
        )
        ssl_context.check_hostname = False
        ssl_context.load_cert_chain(certfile=self.cert_file, keyfile=self.key_file)
        ssl_context.verify_mode = ssl.CERT_REQUIRED

        self.ssl_context = ssl_context

    async def _post_json(self, url: str, data: dict) -> dict:
        # Set context for each request (avoid timeouts)
        self._set_context()
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, data=json.dumps(data), headers=headers, ssl=self.ssl_context
            ) as response:
                response_data = await response.json()
        return response_data

    async def get_plot_directories(self) -> str:
        url = f"https://{self.host}:{self.port}/get_plot_directories"
        response = await self._post_json(url, data={})
        formatted_response = json.dumps(response, indent=2)
        logging.debug(f"Plot directories: {formatted_response}")
        return response

    async def add_plot_directory(self, directory: str) -> str:
        request_data = {"dirname": f"{directory}"}
        url = f"https://{self.host}:{self.port}/add_plot_directory"
        response = await self._post_json(url, data=request_data)
        formatted_response = json.dumps(response, indent=2)
        logging.debug(f"Add plot directory response: {formatted_response}")
        return response

    async def remove_plot_directory(self, directory: str) -> str:
        request_data = {"dirname": f"{directory}"}
        url = f"https://{self.host}:{self.port}/remove_plot_directory"
        response = await self._post_json(url, data=request_data)
        formatted_response = json.dumps(response, indent=2)
        logging.debug(f"Remove plot directory response: {formatted_response}")
        return response

    async def get_routes(self) -> str:
        url = f"https://{self.host}:{self.port}/get_routes"
        response = await self._post_json(url, data={})
        formatted_response = json.dumps(response, indent=2)
        logging.debug(f"Routes: {formatted_response}")
        return response
