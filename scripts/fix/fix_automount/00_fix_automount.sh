#!/bin/sh
if [ -d "$HOME/udev-media-automount" ]; then
  rm -rf $HOME/udev-media-automount
fi
cd $HOME
git clone https://github.com/UselessJoy/udev-media-automount
cd $HOME/udev-media-automount
echo orangepi | sudo --stdin make install
echo orangepi | sudo --stdin udevadm control --reload-rules
echo orangepi | sudo --stdin udevadm trigger
exit 1