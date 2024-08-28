import logging
import locales
from configfile import ConfigWrapper, PrinterConfig

ALL_RELEASED        = 0x00
ONLY_DOORS_PRESSED   = 0x01
ONLY_HOOD_PRESSED   = 0x02
ALL_PRESSED         = 0x03

class SafetyPrinting:  
    def __init__(self, config: ConfigWrapper):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object("gcode")
        self.safety_enabled = config.getboolean("safety_enabled")
        self.show_respond = config.getboolean("show_respond")
        self.luft_timeout = config.getfloat("luft_timeout")
        self.luft_overload = False
        self.reactor = self.printer.get_reactor()
        self.endstops_state = ALL_RELEASED
        self.past_state = None
        self.luft_timer = None
        self.last_eventtime = 0
        self.send_pause = self.send_resume = False
        self.messages = self.printer.lookup_object("messages")
        buttons = self.printer.load_object(config, "buttons")
        doors_pin = config.get("doors_pin")
        hood_pin = config.get("hood_pin")
        buttons.register_buttons([doors_pin, hood_pin], self.endstops_callback)
        webhooks = self.printer.lookup_object("webhooks")
        webhooks.register_endpoint("safety_printing/set_safety_printing",
                                   self._handle_set_safety_printing)
        webhooks.register_endpoint("safety_printing/set_luft_timeout",
                                   self._handle_set_luft_timeout)
        self.printer.register_event_handler("print_stats:printing", self._handle_printing)
        self.printer.register_event_handler("print_stats:paused", self._handle_paused)
    
    def _handle_printing(self):
        self.send_resume = False
        self.luft_overload = False
        
    def _handle_paused(self):
        self.send_pause = False   

    def endstops_callback(self, eventtime, state):
        self.endstops_state = state
        self.printer.send_event("safety_printing:endstops_state", state)
        virtual_sdcard_object = self.printer.lookup_object("virtual_sdcard")
        pause_resume_object = self.printer.lookup_object('pause_resume')
        sd_state = virtual_sdcard_object.print_stats.state
        if self.safety_enabled:
            if sd_state in ["paused", "printing"]:
                if self.endstops_state == ALL_PRESSED:
                    self.reset_luft_timer()
                    if pause_resume_object.is_paused and not pause_resume_object.manual_pause and not self.send_resume and not virtual_sdcard_object.is_active():
                        self.send_resume = True
                        self.gcode.run_script("RESUME")
                elif not pause_resume_object.manual_pause:
                    if not self.luft_timer:
                        self.last_eventtime = eventtime
                        self.luft_timer = self.reactor.register_timer(self.is_luft_timer, self.reactor.NOW)
            if self.show_respond:
                self.respond_status()

    def is_luft_timer(self, eventtime):
        if abs(eventtime - self.last_eventtime) > self.luft_timeout:
            pause_resume_object = self.printer.lookup_object('pause_resume')
            if self.endstops_state != ALL_PRESSED and not pause_resume_object.is_paused and not pause_resume_object.manual_pause:
                self.luft_overload = True
                if not self.send_pause:
                    self.send_pause = True
                    self.gcode.run_script("PAUSE")
                self.messages.send_message("warning", _("Printing is paused. Printing will be continued after closing doors and hood"))
            self.reset_luft_timer()
            return self.reactor.NEVER
        return eventtime + 1

    def reset_luft_timer(self):
        if self.luft_timer:
            self.reactor.unregister_timer(self.luft_timer)
            self.luft_timer = None
            self.last_eventtime = 0
    
    def raise_error_if_open(self):
        if self.endstops_state == ALL_PRESSED:
            return     
        if self.endstops_state == ONLY_DOORS_PRESSED:
            raise self.gcode.error(_("Printing is paused. Must close hood")) 
        elif self.endstops_state == ONLY_HOOD_PRESSED:
            raise self.gcode.error(_("Printing is paused. Must close doors"))
        else:
            raise self.gcode.error(_("Printing is paused. Must close doors and hood"))
    
    def respond_status(self):
        if self.endstops_state == ALL_PRESSED:
            self.gcode.respond_info(_("All closed"))
        elif self.endstops_state == ONLY_DOORS_PRESSED:
            self.gcode.respond_info(_("Hood open"))
        elif self.endstops_state == ONLY_HOOD_PRESSED:
            self.gcode.respond_info(_("Doors are open"))
        else:
            self.gcode.respond_info(_("Doors and hood are open"))
    
    def get_endstops_state(self):
        return self.endstops_state
    
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
                'is_hood_open': bool(self.endstops_state & ONLY_HOOD_PRESSED),
                'is_doors_open': bool(self.endstops_state & ONLY_DOORS_PRESSED),
                'luft_timeout': self.luft_timeout,
                'luft_overload': self.luft_overload,
                'show_respond': self.show_respond
                }
    
def load_config(config):
    return SafetyPrinting(config)
