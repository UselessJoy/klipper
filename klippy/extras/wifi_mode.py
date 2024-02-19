import os, logging
import subprocess
import NetworkManager
import dbus
from dbus.mainloop.glib import DBusGMainLoop
import locales

### Перенести WifiManager из KlipperScreen на Klipper

class WifiMode:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.wifiMode = 'Default' 
        DBusGMainLoop(set_as_default=True)
        self.access_point = self.find_hotspot_connection()
        self.wifiMode = 'AP' if self.is_hotspot() else 'Default'
        webhooks = self.printer.lookup_object('webhooks')
        webhooks.register_endpoint("wifi_mode/set_wifi_mode",
                                   self._handle_set_wifi_mode)
        self.wifi_dev = NetworkManager.NetworkManager.GetDeviceByIpIface('wlan0')
        self.wifi_dev.OnStateChanged(self.on_state_changed)

    
    def on_state_changed(self, nm, interface, signal, old_state, new_state, reason):
        if new_state == NetworkManager.NM_DEVICE_STATE_ACTIVATED:
            self.wifiMode = 'AP' if self.is_hotspot() else 'Default'
    
    def is_hotspot(self):
        try:
            if self.wifi_dev.SpecificDevice().ActiveAccessPoint.Ssid == self.access_point:
                return True
        except:
            pass
        return False
                     
    # def create_AP_connection(self):
    #     #for future, if don-t want to change AP_connection regardless of id connection
    #     return 1
    
    # def change_AP_connection(self):
    #     #if want to control connection from klipper (maybe moonraker will be better)
    #     return 1
    
    def find_hotspot_connection(self) -> str:
        for con in NetworkManager.Settings.ListConnections():
            settings = con.GetSettings()
            if '802-11-wireless' in settings:
                logging.info(settings['802-11-wireless'])
                if settings['802-11-wireless']['mode'] == 'ap':
                    logging.info(f"found hotspot connection {settings['802-11-wireless']['ssid']}")
                    return settings['802-11-wireless']['ssid']
        return ""
    
    def _handle_set_wifi_mode(self, web_request):
        self.wifiMode = web_request.get_str('wifi_mode')
        logging.info(f"changing to {self.wifiMode}")
        if self.wifiMode == 'AP':
            os.system(f"nmcli connection up {self.access_point}")
        elif self.wifiMode == 'Default':
            os.system(f"nmcli connection down {self.access_point}")   

    def get_status(self, eventtime):
        return {
            'wifiMode': self.wifiMode,
            'hotspot': self.access_point
        }
    
def load_config(config):
    return WifiMode(config)