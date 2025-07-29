#!/bin/sh
if [ -d "$HOME/printer_data/mmcblk0p1/" ]; then
  cp -r "$HOME/printer_data/gcodes/*" "$HOME/printer_data/mmcblk0p1/gcodes/"
  rm -rf "$HOME/printer_data/gcodes/*"
fi
exit 0