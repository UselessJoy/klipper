import os, logging

class WifiMode:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.printer.register_event_handler("klippy:ready",
                                            self._handle_ready)
        self.wifiMode = ''
        self.timer = self.printer.get_reactor()
        # Register commands
        self.gcode.register_command(
            "CHANGE_WIFI_MODE", self.cmd_CHANGE_WIFI_MODE)
        self.gcode.register_command(
            "GET_WIFI_MODE", self.cmd_GET_WIFI_MODE)


    def _handle_ready(self):
        self.timer.register_timer(self.set_wifiMode, self.timer.monotonic()+ 0.1)

    def cmd_CHANGE_WIFI_MODE(self, gcmd):
        self.run_bash_script("changemode.sh")
        
    def cmd_GET_WIFI_MODE(self, gcmd):
        if self.get_wifiMode():
            gcmd.respond_info(self.wifiMode)
        else:
            gcmd.respond_info(self.wifiMode)
        return self.get_wifiMode()


    def get_wifiMode(self):
        return self.wifiMode
    
    def set_wifiMode(self, eventtime):
        network_activity = self.get_service_activity("hostapd")
        if int(network_activity) == 0:
            self.wifiMode = 'AP'
        else:
            self.wifiMode = 'Default'
        return eventtime + 1.

    def get_service_activity(self, service):
        status = os.system("systemctl is-active %s.service" % service)
        return status
    
    def run_bash_script(self, service):
        os.system("./$HOME/klipper/scripts/%s" % service)

    def get_status(self, eventtime):
        return {
            'wifiMode': self.get_wifiMode()
        }
    
def load_config(config):
    return WifiMode(config)