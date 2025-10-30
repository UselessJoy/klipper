#!/bin/bash

detect_password() {
    if command -v apt &> /dev/null; then
        echo "orangepi"
    elif command -v dnf &> /dev/null; then
        echo "user"
    else
        echo "unknown"
    fi
}

detect_package_manager() {
    if command -v apt &> /dev/null; then
        echo "apt"
    elif command -v dnf &> /dev/null; then
        echo "dnf"
    else
        echo "unknown"
    fi
}

pwd=$(detect_password)
PKG_MANAGER=$(detect_package_manager)

if [[ "$PKG_MANAGER" == "apt" ]]; then
    echo "$pwd" | sudo -S dpkg -i "$HOME/klipper/scripts/fix/fix_assistant/cassistant_6.5-1_arm64.deb"
elif [[ "$PKG_MANAGER" == "dnf" ]]; then
    echo "$pwd" | sudo -S rpm -i "$HOME/klipper/scripts/fix/fix_assistant/cassistant-6.5-1.aarch64.rpm"
else
    echo "Unsupported package manager: $PKG_MANAGER"
    exit 1
fi

echo "$pwd" | sudo -S systemctl stop assistant.service
echo "$pwd" | sudo -S systemctl disable assistant.service

exit 0