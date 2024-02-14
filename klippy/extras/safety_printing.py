import logging
import locales
from configfile import ConfigWrapper, PrinterConfig

class SafetyPrinting:
    
    ALL_RELEASED        = 0x00
    ONLY_DOOR_PRESSED   = 0x01
    ONLY_HOOD_PRESSED   = 0x02
    ALL_PRESSED         = 0x03
    
    def __init__(self, config: ConfigWrapper):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object("gcode")
        self.safety_enabled = config.getboolean("safety_enabled")
        self.luft_timeout = config.getfloat("luft_timeout")
        self.luft_overload = False
        self.reactor = self.printer.get_reactor()
        self.endstops_state = self.ALL_RELEASED
        self.luft_timer = None
        self.last_eventtime = None
        
        buttons = self.printer.load_object(config, "buttons")
        doors_pin = config.get("doors_pin")
        hood_pin = config.get("hood_pin")
        buttons.register_buttons([doors_pin, hood_pin], self.endstops_callback)

        webhooks = self.printer.lookup_object("webhooks")
        webhooks.register_endpoint("safety_printing/set_safety_printing",
                                   self._handle_set_safety_printing)
        webhooks.register_endpoint("safety_printing/set_luft_timeout",
                                   self._handle_set_luft_timeout)
    
    def endstops_callback(self, eventtime, state):
        self.endstops_state = state
        virtual_sdcard_object = self.printer.lookup_object("virtual_sdcard")
        pause_resume_object = self.printer.lookup_object('pause_resume')
        if self.safety_enabled:
            if virtual_sdcard_object.print_stats.state in ["paused", "printing"]:
                if state == self.ALL_PRESSED:
                    self.reset_luft_timer()
                    if pause_resume_object.is_paused and not pause_resume_object.manual_pause:
                        self.gcode.run_script_from_command("RESUME")
                elif not pause_resume_object.manual_pause:
                    if not self.luft_timer:
                        self.last_eventtime = eventtime
                        self.luft_timer = self.reactor.register_timer(self.is_luft_timer, self.reactor.NOW)  

    def is_luft_timer(self, eventtime):
        if abs(eventtime - self.last_eventtime) > self.luft_timeout:
            pause_resume_object = self.printer.lookup_object('pause_resume')
            if self.endstops_state != self.ALL_PRESSED and not pause_resume_object.is_paused and not pause_resume_object.manual_pause:
                self.luft_overload = True
                self.gcode.run_script_from_command("PAUSE")
            self.reset_luft_timer()
            return self.reactor.NEVER
        return eventtime + 1

    def reset_luft_timer(self):
        if self.luft_timer:
            self.reactor.unregister_timer(self.luft_timer)
            self.luft_timer = None
            self.luft_overload = False
            self.last_eventtime = None
    
    def raise_error_if_open(self):
        if self.endstops_state == self.ALL_PRESSED:
            return     
        if self.endstops_state == self.ALL_RELEASED:
            raise self.gcode.error(_("Printing is paused. Must close doors and hood"))
        elif self.endstops_state == self.ONLY_HOOD_PRESSED:
            raise self.gcode.error(_("Printing is paused. Must close doors"))
        else:
            raise self.gcode.error(_("Printing is paused. Must close hood"))
        
    def _handle_set_safety_printing(self, web_request):
        self.safety_enabled: bool = web_request.get_boolean('safety_enabled')
        configfile: PrinterConfig = self.printer.lookup_object('configfile')
        safety_section = {"safety_printing": {"safety_enabled": self.safety_enabled}}
        configfile.update_config(setting_sections=safety_section, save_immediatly=True)
        
    def _handle_set_luft_timeout(self, web_request):
        self.luft_timeout: float = web_request.get_float('luft_timeout')
        configfile: PrinterConfig = self.printer.lookup_object('configfile')
        safety_section = {"safety_printing": {"luft_timeout": self.luft_timeout}}
        configfile.update_config(setting_sections=safety_section, save_immediatly=True)
    
    def get_status(self, eventtime):
        return {
                'safety_enabled': self.safety_enabled,
                'is_hood_open': bool(self.endstops_state & self.ONLY_HOOD_PRESSED),
                'is_doors_open': bool(self.endstops_state & self.ONLY_DOOR_PRESSED),
                'luft_timeout': self.luft_timeout,
                'luft_overload': self.luft_overload
                }
    
def load_config(config):
    return SafetyPrinting(config)
