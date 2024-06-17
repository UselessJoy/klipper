# Support for executing gcode when a hardware button is pressed or released.
#
# Copyright (C) 2019 Alec Plumb <alec@etherwalker.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
import os
import locales
class PowerButton:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.last_state = 0
        self.reactor = self.printer.get_reactor()
        self.luft_timer = 0
        self.last_eventtime = 0
        self.timeout = config.get("timeout", 3)
        buttons = self.printer.load_object(config, "buttons")
        power_pin = config.get("pin")
        buttons.register_buttons([power_pin], self.power_pin_callback)
        self.is_invert = power_pin.startswith('!')
        
    
    def power_pin_callback(self, eventtime, state):
        state = not state if self.is_invert else state
        self.last_state = state
        self.last_eventtime = eventtime
        if self.last_state:
          if not self.luft_timer:
            self.luft_timer = self.reactor.register_timer(self.is_luft_timer, self.reactor.NOW)
        else:
          self.reset_luft_timer()

  
    def is_luft_timer(self, eventtime):
      if self.last_state:
        if abs(eventtime - self.last_eventtime) > self.timeout:
          logging.info(f"timer is {eventtime} - {self.last_eventtime}")
          os.system("systemctl poweroff")
      else:
        self.reset_luft_timer()
        return self.reactor.NEVER
      return eventtime + 1

    def reset_luft_timer(self):
      if self.luft_timer:
          self.reactor.unregister_timer(self.luft_timer)
          self.luft_timer = None
          self.last_eventtime = None

    def get_status(self, eventtime):
      return {'state': self.last_state}

def load_config(config):
    return PowerButton(config)
