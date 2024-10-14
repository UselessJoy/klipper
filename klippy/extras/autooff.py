
import locales

class Autooff:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.off_temp = config.getint('off_extruder_temp', 40)
        self.reactor = self.printer.get_reactor()
        self.timer = None
        self.need_autooff = False
        self.autooff_enable = config.getboolean('autooff', False)
        self.printer.register_event_handler("virtual_sdcard:complete", self.create_timer)
        webhooks = self.printer.lookup_object('webhooks')
        webhooks.register_endpoint("autooff/set_auto_off",
                                   self._handle_set_enable_autooff)
        webhooks.register_endpoint("autooff/off_autooff",
                                   self._handle_stop_current_autooff)
        
    def create_timer(self):
        if self.autooff_enable:
            self.extruder = self.printer.lookup_object('extruder')
            pheaters = self.printer.lookup_object('heaters')
            pheaters.turn_off_all_heaters()
            self.need_autooff = True
            self.timer = self.reactor.register_timer(self.check_temp, self.reactor.NOW)
    
    def check_temp(self, eventtime):
        if self.need_autooff:
            temp_ex = self.extruder.get_status(eventtime)['temperature']
            if temp_ex < self.off_temp:
                webhooks = self.printer.lookup_object('webhooks')
                webhooks.call_remote_method("shutdown_machine")
        else:
            self.reactor.unregister_timer(self.timer)
            return self.reactor.NEVER
        return eventtime + 1.
    
    def get_status(self, eventtime):
        return {
            'autoOff': self.need_autooff,
            'autoOff_enable': self.autooff_enable
        }
    
    def _handle_set_enable_autooff(self, web_request):
        self.autooff_enable = web_request.get_boolean('autoOff_enable')
        configfile = self.printer.lookup_object('configfile')
        safety_section = {"autooff": {"autooff": self.autooff_enable}}
        configfile.update_config(setting_sections=safety_section, save_immediatly=True)
    
    def _handle_stop_current_autooff(self, web_request):
        self.need_autooff = False
    
    
def load_config(config):
    return Autooff(config)