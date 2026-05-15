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

nice -n 19 ionice -c 3 bash -c "
    case $OS in
      'debian')
        echo '$SUDO_PASS' | sudo -S apt clean > /dev/null 2>&1
        echo '$SUDO_PASS' | sudo -S apt autoclean > /dev/null 2>&1
        echo '$SUDO_PASS' | sudo -S apt autoremove -y > /dev/null 2>&1
        ;;
      'redos')
        echo '$SUDO_PASS' | sudo -S dnf clean all > /dev/null 2>&1
        echo '$SUDO_PASS' | sudo -S dnf autoremove -y > /dev/null 2>&1
        ;;
    esac

    echo '$SUDO_PASS' | sudo -S journalctl --vacuum-time=1h > /dev/null 2>&1
    echo '$SUDO_PASS' | sudo -S journalctl --vacuum-size=100M > /dev/null 2>&1

    echo '$SUDO_PASS' | sudo -S rm -rf /var/tmp/* 2>/dev/null
    rm -rf ~/.cache/* 2>/dev/null
" &

disown

exit 0