import os, logging
import subprocess
import NetworkManager
from dbus.mainloop.glib import DBusGMainLoop
import locales

class WifiMode:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.wifiMode = 'Default' 
        DBusGMainLoop(set_as_default=True)
        self.hotspot = self.find_hotspot_connection()
        self.wifiMode = 'AP' if self.is_hotspot() else 'Default'
        webhooks = self.printer.lookup_object('webhooks')
        webhooks.register_endpoint("wifi_mode/set_wifi_mode",
                                   self._handle_set_wifi_mode)
        webhooks.register_endpoint("wifi_mode/set_hotspot",
                                   self._handle_set_hotspot)
        self.wifi_dev = NetworkManager.NetworkManager.GetDeviceByIpIface('wlan0')
        self.wifi_dev.OnStateChanged(self.on_state_changed)

    
    def on_state_changed(self, nm, interface, signal, old_state, new_state, reason):
        if new_state == NetworkManager.NM_DEVICE_STATE_ACTIVATED:
            self.wifiMode = 'AP' if self.is_hotspot() else 'Default'
    
    def is_hotspot(self):
        try:
            if self.wifi_dev.SpecificDevice().ActiveAccessPoint.Mode == NetworkManager.NM_802_11_MODE_AP:
                return True
        except:
            pass
        return False
    
    def find_hotspot_connection(self) -> str:
        for con in NetworkManager.Settings.ListConnections():
            settings = con.GetSettings()
            if '802-11-wireless' in settings:
                if settings['802-11-wireless']['mode'] == 'ap':
                    logging.info(f"found hotspot connection {settings['connection']['id']}")
                    return settings['connection']['id']
        return ""
    
    def _handle_set_wifi_mode(self, web_request):
        self.wifiMode = web_request.get_str('wifi_mode')
        logging.info(f"changing to {self.wifiMode}")
        self.hotspot = self.find_hotspot_connection()
        if self.wifiMode == 'AP':
            try:
                proc = subprocess.run([ "nmcli", "connection", "up", self.hotspot], 
                                        check=True, capture_output=True, text=True)
            except subprocess.CalledProcessError as exc:
                logging.error(exc.stderr)
                raise exc.stderr
        elif self.wifiMode == 'Default':
            try:
                proc = subprocess.run([ "nmcli", "connection", "down", self.hotspot], 
                                        check=True, capture_output=True, text=True)
            except subprocess.CalledProcessError as exc:
                logging.error(exc.stderr)
                raise exc.stderr
            
    def _handle_set_hotspot(self, web_request):
        self.hotspot = web_request.get_str('hotspot')

    def get_status(self, eventtime):
        return {
            'wifiMode': self.wifiMode,
            'hotspot': self.hotspot
        }
    
def load_config(config):
    return WifiMode(config)