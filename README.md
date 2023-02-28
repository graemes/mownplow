# 🚜 The Mow'n'Plow

An efficient Chia Plot mower and mover.

Uses `inotify` to watch for new Chia plots and then fires off `rsync` to move
them to their final destination.

Based on '🚜 The Plow' (https://github.com/lmacken/plow) it also:
* Prioritises moving of plots to fill the disks in order of definition
* (Optionally) removes plots older than a given date
* Collects all of the available mounted plot drives so you don't have speficfy them manually (though you can if you want to)
