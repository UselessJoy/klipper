import logging
import locales

class SafetyPrinting:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.safety_enabled = config.getboolean('safety_enabled')
        self.is_doors_open = True
        self.is_hood_open = True
        self.doors_endstop = self.printer.load_object(config, 'gcode_button endstop_doors')
        self.hood_endstop = self.printer.load_object(config, 'gcode_button endstop_hood')
        self.virtual_sdcard_object = self.printer.lookup_object('virtual_sdcard')
        self.gcode = self.printer.lookup_object('gcode')
        webhooks = self.printer.lookup_object('webhooks')
        webhooks.register_endpoint("safety_printing/set_safety_printing",
                                   self._handle_set_safety_printing)
        self.reactor = self.printer.get_reactor()
        self.timer = self.reactor.register_timer(self.endstop_status, self.reactor.NOW)
        self.luft_timer = None
        self.last_evettime = None
        self.not_luft_open = False
        self.is_safety_pause = False

    def endstop_status(self, eventtime):
        self.is_doors_open = True if self.doors_endstop.get_status(eventtime)['state'] == 'RELEASED' else False
        self.is_hood_open = True if self.hood_endstop.get_status(eventtime)['state'] == 'RELEASED' else False
        pause_resume_object = self.printer.lookup_object('pause_resume')
        if self.safety_enabled:
            if self.is_doors_open or self.is_hood_open:
                if self.virtual_sdcard_object.is_active():#not pause_resume_object.is_paused:
                    if not self.luft_timer:
                        self.luft_timer = self.reactor.register_timer(self.is_luft_timer, self.reactor.NOW)
                    if self.not_luft_open:
                        self.not_luft_open = False
                        self.is_safety_pause = True
                        self.gcode.run_script("PAUSE")
            elif pause_resume_object.is_paused and self.is_safety_pause:#self.virtual_sdcard_object.print_stats.get_status(eventtime)['state'] == 'paused':
                self.is_safety_pause = False
                self.gcode.run_script("RESUME")
                    
        return eventtime + 1
    
    def is_luft_timer(self, eventtime):
        if not self.last_eventtime:
            self.last_eventtime = eventtime 
        pass_time = abs(eventtime - self.last_eventtime)
        if pass_time > 3:
            self.reset_timer()
            if self.is_doors_open or self.is_hood_open:
                self.not_luft_open = True
            return self.reactor.NEVER
        return eventtime + 1

    def reset_timer(self):
        if self.luft_timer:
            self.reactor.unregister_timer(self.luft_timer)
            self.luft_timer = None
            self.last_eventtime = None
    
    
    def _handle_set_safety_printing(self, web_request):
        self.safety_enabled = web_request.get_boolean('safety_enabled')
        cfgname = self.printer.get_start_args()['config_file']
        with open(cfgname, 'r+') as file:
            lines = file.readlines()
            i = 0
            for line in enumerate(lines):
                if line[1].lstrip().startswith('safety_enabled'):
                    lines[i] = f' safety_enabled = {self.safety_enabled}\n'
                i+=1
            end_lines = lines
        with open(cfgname, 'w') as file:
            file.writelines(end_lines)
    
    def raise_error_if_open(self):
        if not self.is_doors_open and not self.is_hood_open:
            return
        
        if self.is_doors_open and self.is_hood_open:
            raise self.gcode.error(_("Printing is paused. Must close doors and hood"))
        elif self.is_doors_open:
            raise self.gcode.error(_("Printing is paused. Must close doors"))
        else:
            raise self.gcode.error(_("Printing is paused. Must close hood"))
    
    
    def get_status(self, eventtime=None):
        return {
                'safety_enabled': self.safety_enabled,
                'is_hood_open': self.is_hood_open,
                'is_doors_open': self.is_doors_open
                }
    
def load_config(config):
    return SafetyPrinting(config)
