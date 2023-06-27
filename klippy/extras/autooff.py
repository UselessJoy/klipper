
import locales

class Autooff:
    def __init__(self, config):
        self.config = config
        self.printer = config.get_printer()
        self.off_temp = self.config.getint('off_extruder_temp', 40)
        self.reactor = self.printer.get_reactor()
        self.print_stats = self.printer.load_object(config, 'print_stats')
        self.timer = None
        self.need_autooff = False
        self.autooff_enable = self.config.getboolean('autooff', False)
        self.printer.register_event_handler("virtual_sdcard:complete", self.create_timer)
        self.gcode = self.printer.lookup_object('gcode')
        webhooks = self.printer.lookup_object('webhooks')
        webhooks.register_endpoint("autooff/set_auto_off",
                                   self._handle_set_auto_off)
        webhooks.register_endpoint("autooff/off_autooff",
                                   self._handle_off_auto_off)
        
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
        return eventtime + 1
    
    def get_status(self, eventtime):
        return {
            'autoOff': self.need_autooff,
            'timeAutoOff': eventtime,
            'autoOff_enable': self.autooff_enable
        }
    
    def _handle_set_auto_off(self, web_request):
        self.autooff_enable = web_request.get_boolean('autoOff_enable')
        cfgname = self.printer.get_start_args()['config_file']
        with open(cfgname, 'r+') as file:
            lines = file.readlines()
            i = 0
            for line in enumerate(lines):
                if line[1].lstrip().startswith('autooff'):
                    lines[i] = f' autooff = {self.autooff_enable}\n'
                i+=1
            end_lines = lines
        with open(cfgname, 'w') as file:
            file.writelines(end_lines)
        return self.autooff_enable
    
    def _handle_off_auto_off(self, web_request):
        self.need_autooff = False
    
    
def load_config(config):
    return Autooff(config)