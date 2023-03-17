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


loadHosts() {
	sudo echo 20.20.1.1   printer.gelios.test >> /etc/hosts
}

loadResolvConf() {
	sudo echo nameserver 127.0.0.1 >> /etc/resolv.conf
	sudo echo nameserver 20.20.1.1 >> /etc/resolv.conf
	sudo echo nameserver 8.8.8.8 >> /etc/resolv.conf
}

if isAccessPoint
then
	echo "Stop Wifi in AP mode"
	stopProcess "hostapd.service"
	stopProcess "dnsmasq.service"
	sudo ifdown wlan0
	sudo ifdown eth0
	loadInterfaceDefault
	sudo ifup wlan0
	sudo ifup eth0
	startProcess "NetworkManager.service"
	startProcess "networkd-dispatcher.service"
	echo "Now wifi in Default mode!"
else
	echo "Stop Wifi in default mode"
	stopProcess "NetworkManager.service"
	stopProcess "networkd-dispathcer.service"
	sudo ifdown wlan0
	sudo ifdown eth0
	loadInterfaceAP
	loadResolvConf
	loadHosts
	sudo ifup wlan0
	sudo ifup eth0
	startProcess "hostapd.service"
	startProcess "dnsmasq.service"
	echo "Now wifi in AP mode!"
fi
