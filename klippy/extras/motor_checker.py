# Support for disabling the printer on an idle timeout
#
# Copyright (C) 2018  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
import locales
DEFAULT_IDLE_GCODE = """
TURN_OFF_HEATERS
M84
"""

PIN_MIN_TIME = 0.100
READY_TIMEOUT = .500

class MotorChecker:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.toolhead = self.timeout_timer = None
        self.printer.register_event_handler("klippy:ready", self.handle_ready)
        self.idle_timeout = config.getfloat('timeout', 900., above=0.)
        gcode_macro = self.printer.load_object(config, 'gcode_macro')
        self.idle_gcode = gcode_macro.load_template(config, 'gcode',
                                                    DEFAULT_IDLE_GCODE)
        self.finish_time = self.last_eventtime = 0
        self.last_print_start_systime = 0.

    def handle_ready(self):
        self.toolhead = self.printer.lookup_object('toolhead')
        self.timeout_timer = self.reactor.register_timer(self.timeout_handler, self.reactor.NOW)
        self.printer.register_event_handler("print_stats:cancelled", self._handle_finish)
        self.printer.register_event_handler("print_stats:complete", self._handle_finish)
        self.printer.register_event_handler("print_stats:error", self._handle_finish)
    
    def _handle_finish(self):
        self.finish_time = self.reactor.monotonic()

    def timeout_handler(self, eventtime):
        if self.printer.is_shutdown():
            return self.reactor.NEVER
        # Выравнивание времени евента от конца печати
        if self.finish_time:
          finish = self.finish_time
          self.finish_time = 0
          return eventtime + (finish - self.last_eventtime)
        self.last_eventtime = eventtime
        if self.gcode.get_mutex().test():
            # Gcode class busy
            return eventtime + self.idle_timeout
        if self.printer.lookup_object('pause_resume').is_paused or self.printer.lookup_object('virtual_sdcard').is_active():
            return eventtime + self.idle_timeout
        try:
          script = self.idle_gcode.render()
          self.gcode.run_script(script)
          return eventtime + self.idle_timeout
        except:
            logging.exception("idle timeout gcode execution")
            return eventtime + 1.
        

def load_config(config):
    return MotorChecker(config)
