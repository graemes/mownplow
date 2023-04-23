# 🚜 The Mow'n'Plow MK2

## Overview

An efficient Chia Plot mower and mover.  It's purpose is minimise the amount of time that drives are unavailable for harvesting while replotting (or even normal plotting).

Uses `inotify` to watch for new Chia plots and then fires off `rsync` to move them to their final destination.

Based on '🚜 The Plow' (https://github.com/lmacken/plow) it also:

**MK1**
* Prioritises moving of plots to fill the disks in order of definition
* (Optionally) removes plots older than a given timestamp
* Collects all of the available mounted plot drives so you don't have specify them manually (though you can if you want to)

**MK2**

This is a fairly major restructure to the codebase which adds:
* a separate configuration file
* the option to remove all matching plots on the destination drive at the beginning of plowing
* the ability to remove a destination drive from harvesting until it is 'full'

Rather than spilling too much ink here (who reads doco anyway?) there are comments in the `config.yaml` which should point you in the right direction.

I built this tool for my own use (and the lol's :innocent:) but if you find it useful and feel the urge to buy me a drink use: xch1vlnelz9ef43z3xa4x6a3zzfm7cezwvmq332p97xlflmxxcgzdrpsqamyee

## Usage Notes

This tool is an 'opinionated' method for mowing and plowing and only works with a single harvester at a time.  It has been developed and tested on Linux so YMMV might vary on Windows.

### Directory Structure

This script reflects the way that I organise the plots on my harvester. Each plot drive is mounted under `\data\chia\plots` like so:

```
├── c0b0
│   ├── plot-k32-2023-01-10-05-52-f0037e35e748d775aa5d7743c1280343e9c4779bd6eca195e2281bff9c13111c.plot
│   ├── plot-k32-2023-01-10-06-05-11dd9e96363612db2a643fe32592f9003f1908cb3c9d91fa6b327c9d4e2773a0.plot
│   ...
├── c0b1
│   ├── plot-k32-2023-01-10-05-52-f0037e35e748d775aa5d7743c1280343e9c4779bd6eca195e2281bff9c13111c.plot
│   ├── plot-k32-2023-01-10-06-05-11dd9e96363612db2a643fe32592f9003f1908cb3c9d91fa6b327c9d4e2773a0.plot
│   ...
├── c1b0
│   ├── plot-k32-2023-01-10-05-52-f0037e35e748d775aa5d7743c1280343e9c4779bd6eca195e2281bff9c13111c.plot
│   ├── plot-k32-2023-01-10-06-05-11dd9e96363612db2a643fe32592f9003f1908cb3c9d91fa6b327c9d4e2773a0.plot
│   ...
│...
  
```

### Rsync

```rsync``` is run as a service on the harvester.  This by-passes the encryption of the file during transfer (there is very little value to be obtained by sniffing the traffic and I *mostly* trust the servers on my network).

#### Configuration files

```
# /etc/rsyncd.conf

# Configuration file for rsync daemon mode
# See rsyncd.conf man page for more options.

# configuration example:
uid = chia
gid = chia
port = 12000

# plots is the rsync 'module' which becomes the site root
[plots]
path = /data/chia/plots
comment = Chia plots
read only = false
```

```
# /etc/systemd/system/rsync.service

[Unit]
Description=fast remote file copy program daemon
ConditionPathExists=/etc/rsyncd.conf
After=network.target
Documentation=man:rsync(1) man:rsyncd.conf(5)

[Service]
ExecStart=/usr/bin/nocache /usr/bin/ionice -c 3 /usr/bin/rsync --daemon --no-detach
RestartSec=1

# Citing README.md:
#
#   [...] Using ssh is recommended for its security features.
#
#   Alternatively, rsync can run in `daemon' mode, listening on a socket.
#   This is generally used for public file distribution, [...]
#
# So let's assume some extra security is more than welcome here. We do full
# system protection (which makes /usr, /boot, & /etc read-only) and hide
# devices. To override these defaults, it's best to do so in the drop-in
# directory, often done via `systemctl edit rsync.service`. The file needs
# just the bare minimum of the right [heading] and override values.
# See systemd.unit(5) and search for "drop-in" for full details.

ProtectSystem=full
#ProtectHome=on|off|read-only
PrivateDevices=on
NoNewPrivileges=on

[Install]
WantedBy=multi-user.target
```

### Disk Scheduler

#### Plotting
Writing plot files while harvesting can have a large impact on the latency on harvester lookups (which can increase the number of stale plots being submitted to the pool).
`ionice` (specified in the rsync *ExecStart* command above) only works if the disk scheduler in use is either CFQ (obsolete) or [BFQ](https://algo.ing.unimo.it/people/paolo/disk_sched/description.php).   Unfortunately, Debian Bullseye and Ubuntu 22.04 default to using the `mq-deadline` scheduler so ionice is ineffective.  

To check the current schedules in use run:
```
grep "" /sys/block/*/queue/scheduler
```

If BFQ is not in use for your hard disks then to get ionice to work the following configuration files will need to be added/changed:

```
# /etc/modules-load.d/bfq.conf
# Make sure the bfq module is loaded at boot

bfq
```

```
# /etc/udev/rules.d/60-ioschedulers.rules 

# set scheduler for rotating disks
ACTION=="add|change", KERNEL=="sd[a-z]*", ATTR{queue/rotational}=="1", ATTR{queue/scheduler}="bfq"
```

After changing these files a reboot will be required.

#### Normal Plotting

The `mq-scheduler` has lower latency during normal operation of the harvester so after plotting is complete reverse the changes above and reboot again.

### Nocache

The `rsync` service also uses the [`nocache`](https://github.com/Feh/nocache) utility which ensures that rsync writes bypass the filesystem cache.  To install:
```
apt-get install nocache
```

### SSH

The script assumes that there is an entry in the `authorized_keys` file for the DEST_USER specified.  If necessary, see [Understanding SSH authorized_keys file with Examples](https://www.howtouselinux.com/post/ssh-authorized_keys-file) for instructions.
