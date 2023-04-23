import random

import yaml


class Config:
    def __init__(self, config_file: str) -> None:
        with open(config_file, "r") as file:
            config = yaml.safe_load(file)

        # Sources
        self.sources = config["Sources"]

        # Destination
        self.dest_host = config["Dest"]["Host"]
        self.dest_username = config["Dest"]["Username"]
        self.dest_protocol = config["Dest"]["Protocol"]
        self.dest_port = config["Dest"]["Port"]
        self.dest_root = config["Dest"]["Root"]
        self.dest_dirs = config["Dest"].get("Dirs")

        # Options:
        self.replot = config["PlowOptions"].get("Replot", False)
        self.replot_before = config["PlowOptions"].get("ReplotBefore")
        self.remove_all_replots = config["PlowOptions"].get("RemoveAllAtStart", False)
        self.farm_during_plow = config["PlowOptions"].get("FarmDuring", False)
        self.plow_shuffle = config["PlowOptions"].get("Shuffle", False)

        # Rsync
        self.rsync_cmd = config["Rsync"].get("Cmd", "rsync")
        self.rsync_flags = config["Rsync"].get(
            "Flags",
            "--remove-source-files --preallocate --whole-file --skip-compress=plot --sync"
        )

        # SSH
        self.ssh_private_key_path = config["SSH"].get(
            "Private_Key_Path", "/home/chia/.ssh/id_ed25519"
        )
        self.ssh_port = config["SSH"].get("Port", 22)

        # Harvester
        self.harvester_host = config["Harvester"]["Host"]
        self.harvester_port = config["Harvester"]["Port"]
        self.harvester_cacert_path = config["Harvester"]["CACertPath"]
        self.harvester_cert_path = config["Harvester"]["CertPath"]
        self.harvester_key_path = config["Harvester"]["KeyPath"]

        # Logging
        self.logging = config["Logging"].get("Level","INFO")

        # Shuffle destinations if requested
        if self.plow_shuffle:
            random.shuffle(self.dest_dirs)

    def update_dest_dirs(self, dest_dirs=None):
        if dest_dirs is not None:
            if self.plow_shuffle:
                random.shuffle(dest_dirs)
            self.dest_dirs = dest_dirs
