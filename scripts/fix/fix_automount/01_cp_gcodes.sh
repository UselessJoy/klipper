#!/bin/sh
if [ -d "$HOME/printer_data/mmcblk0p1/" ]; then
  cp -r /home/orangepi/printer_data/gcodes/* /home/orangepi/printer_data/mmcblk0p1/gcodes/
fi
exit 0