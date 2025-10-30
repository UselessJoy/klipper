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
pwd=$(detect_password)

detect_package_manager() {
  if command -v apt &> /dev/null; then
    echo "apt"
  elif command -v dnf &> /dev/null; then
    echo "dnf"
  else
    echo "unknown"
  fi
}
PKG_MANAGER=$(detect_package_manager)

cd $HOME

if [[ "$PKG_MANAGER" == "apt" ]]; then
	echo "$pwd" | sudo --stdin dpkg -i cassistant_6.5-1_arm64.deb
else
	echo "$pwd" | sudo --stdin rpm -i cassistant-6.5-1.aarch64.rpm
fi
echo "$pwd" | sudo --stdin stop assistant.service
echo "$pwd" | sudo --stdin disable assistant.service
exit 0
