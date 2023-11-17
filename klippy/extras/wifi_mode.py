import os, logging
import subprocess
#import locales
class WifiMode:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.wifiMode = 'Default' 
        self.reactor = self.printer.get_reactor()
        self.timer = None
        self.printer.register_event_handler("klippy:ready",
                                            self._handle_ready)
        webhooks = self.printer.lookup_object('webhooks')
        webhooks.register_endpoint("wifi_mode/set_wifi_mode",
                                   self._handle_set_wifi_mode)


    def create_AP_connection(self):
        #for future, if don-t want to change AP_connection regardless of id connection
        return 1
    
    def change_AP_connection(self):
        #if want to control connection from klipper (maybe moonraker will be better)
        return 1
    
    def find_AP_connection(self):
        #for future, for details look to create_AP_connection
        return 1
    
    
    
    def watch_wifi_mode(self, eventtime):
        data = None
        try:
            data = subprocess.check_output("nmcli device show wlan0 | grep Gelios", universal_newlines=True, shell=True)
        except:
            pass
        if data:
            self.wifiMode = 'AP'
        else:
            self.wifiMode = 'Default'
        return eventtime + 0.1
    
    def _handle_ready(self):
        self.timer = self.reactor.register_timer(self.watch_wifi_mode, self.reactor.NOW)

    
    def _handle_set_wifi_mode(self, web_request):
        wifi_mode = web_request.get_str('wifi_mode')
        if wifi_mode == 'AP':
            os.system("nmcli connection up Gelios")
        else:
            os.system("nmcli connection down Gelios")   

    def get_status(self, eventtime):
        return {
            'wifiMode': self.wifiMode
        }
    
def load_config(config):
    return WifiMode(config)