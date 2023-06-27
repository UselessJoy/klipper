import logging
import locales

class SafetyPrinting:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.safety = config.getboolean('safety')
        self.open = False
        
        self.door_endstop = self.printer.load_object(config, 'gcode_button endstop_door')
        self.cap_endstop = self.printer.load_object(config, 'gcode_button endstop_cap')
        self.virtual_sdcard_object = self.printer.lookup_object('virtual_sdcard')
        self.gcode = self.printer.lookup_object('gcode')
        webhooks = self.printer.lookup_object('webhooks')
        webhooks.register_endpoint("safety_printing/set_safety_printing",
                                   self._handle_set_safety_printing)
        self.reactor = self.printer.get_reactor()
        self.timer = self.reactor.register_timer(self.endstop_status, self.reactor.NOW)

    def endstop_status(self, eventtime):
        door_status = self.door_endstop.get_status(eventtime)['state']
        cap_status = self.cap_endstop.get_status(eventtime)['state']
        pause_resume_object = self.printer.lookup_object('pause_resume')
        if door_status == 'RELEASED' or cap_status == 'RELEASED':
            self.open = True
        else:
            self.open = False
        if self.safety:
            if self.open:
                if self.virtual_sdcard_object.is_active():#not pause_resume_object.is_paused:
                    self.gcode.run_script("PAUSE")
            elif pause_resume_object.is_paused:#self.virtual_sdcard_object.print_stats.get_status(eventtime)['state'] == 'paused':
                self.gcode.run_script("RESUME")
                    
        return eventtime + 1
    
    def _handle_set_safety_printing(self, web_request):
        self.safety = web_request.get_boolean('safety')
        cfgname = self.printer.get_start_args()['config_file']
        with open(cfgname, 'r+') as file:
            lines = file.readlines()
            i = 0
            for line in enumerate(lines):
                if line[1].lstrip().startswith('safety'):
                    lines[i] = f' safety = {self.safety}\n'
                i+=1
            end_lines = lines
        with open(cfgname, 'w') as file:
            file.writelines(end_lines)
    
    
    def get_status(self, eventtime=None):
        return {'safety': self.safety,
                'open': self.open}
    
def load_config(config):
    return SafetyPrinting(config)
