#!/bin/sh
detect_password() {
  if command -v apt &> /dev/null; then
    echo "orangepi"
  elif command -v dnf &> /dev/null; then
    echo "user"
  else
    echo "unknown"
  fi
}

if [ -d "$HOME/udev-media-automount" ]; then
  rm -rf "$HOME/udev-media-automount"
fi
cd "$HOME"
git clone https://github.com/UselessJoy/udev-media-automount
cd "$HOME/udev-media-automount"
pwd=$(detect_password)
echo "$pwd" | sudo --stdin make install
echo "$pwd" | sudo --stdin udevadm control --reload-rules
echo "$pwd" | sudo --stdin udevadm trigger
exit 1