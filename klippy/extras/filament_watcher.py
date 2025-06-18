# Support fans that are controlled by gcode
#
# Copyright (C) 2016-2020  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from .safety_printing import ALL_PRESSED
import locales
class FilamentWatcher:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.show_message = False
        self.last_fan_speed = .0
        self.vsd = None
        self.printer.register_event_handler("klippy:ready",
                                            self._on_ready)
        

    def _on_ready(self):
        self.vsd = self.printer.lookup_object('virtual_sdcard')
        self.printer.register_event_handler("print_stats:printing", self._handle_printing)
        self.printer.register_event_handler("print_stats:cancelled", self._handle_stop_printing)
        self.printer.register_event_handler("print_stats:complete", self._handle_stop_printing)
        self.printer.register_event_handler("print_stats:error", self._handle_stop_printing)

        self.printer.register_event_handler("safety_printing:endstops_state", self.on_endstops_callback)
        
    
    def _handle_printing(self):
        if self.is_PLA_printing() and not (self.last_fan_speed or self.is_something_open()):
          self.show_message = True

    def _handle_stop_printing(self):
        self.show_message = False
        
    def is_something_open(self):
        return self.printer.lookup_object('safety_printing').get_endstops_state() != ALL_PRESSED

    def on_endstops_callback(self, state):
        if self.is_PLA_printing():
          if state != ALL_PRESSED:
              self.show_message = False
          else:
              self.show_message = True
        else:
            self.show_message = False
    
    def is_PLA_printing(self):
        if self.vsd:
          return self.vsd.is_active() and self.vsd.get_filament_type() == 'PLA'
        return None

    def get_status(self, eventtime):
        ft = None
        if self.vsd:
            ft = self.vsd.get_filament_type()
        return {
            'filament_type': ft,
            'show_message': self.show_message
        }

def load_config(config):
    return FilamentWatcher(config)
