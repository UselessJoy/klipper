#!/bin/sh
set -e

sudo_cmd() {
    echo "$SUDO_PASS" | sudo -S --prompt="" -- "$@" > /dev/null 2>&1
}

if command -v apt &> /dev/null; then
  OS="debian"
  SUDO_PASS="orangepi"
elif command -v dnf &> /dev/null; then
  OS="redos"
  SUDO_PASS="user"
else
  echo "Unknown OS"
  exit 1
fi

case $OS in
  "debian")
    sudo_cmd apt clean
    sudo_cmd apt autoremove -y
    sudo_cmd apt autoclean
    ;;
  "redos")
    sudo_cmd dnf clean all
    sudo_cmd dnf autoremove -y
    ;;
esac

sudo_cmd journalctl --vacuum-time=1h
sudo_cmd journalctl --vacuum-size=100M
sudo_cmd rm -rf ~/.cache/*
sudo_cmd rm -rf /var/tmp/*