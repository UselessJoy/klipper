#!/bin/bash

stopProcess()
{
	systemctl stop $1
	systemctl disable $1
	systemctl mask $1
}

startProcess()
{
	systemctl unmask $1
	systemctl enable $1
	systemctl start $1
}

loadInterfaceDefault()
{
	cat /etc/network/interfaces.default > /etc/network/interfaces
}

loadInterfaceAP()
{
	cat /etc/network/interfaces.new > /etc/network/interfaces
}

isAccessPoint()
{
	if systemctl is-active NetworkManager.service == "active" || systemctl is-active networkd-dispathcer.service == "active"
	then
		return 1
	else
		return 0
	fi
}

if isAccessPoint
then
	echo "Stop Wifi in AP mode"
	stopProcess "hostapd.service"
	stopProcess "dnsmasq.service"
	loadInterfaceDefault
	startProcess "NetworkManager.service"
	startProcess "networkd-dispatcher.service"
	echo "Start Wifi in default mode, system will reboot"
else
	echo "Stop Wifi in default mode"
	stopProcess "NetworkManager.service"
	stopProcess "networkd-dispathcer.service"
	loadInterfaceAP
	startProcess "hostapd.service"
	startProcess "dnsmasq.service"
	echo "Start Wifi in AP mode, system will reboot"
fi
