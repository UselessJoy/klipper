import logging
from . import fan
import locales
import re
class PrinterFanBack:
    temp_re = re.compile('^temp_\d+$')
    cmd_SET_FAN_SPEED_help = _("Sets the speed of a fan")
    def __init__(self, config):
        self.printer = config.get_printer()
        self.fan = fan.Fan(config, default_shutdown_speed=0.)
        self.fan_name = config.get_name().split()[-1]
        self.linear = config.getboolean('linear', True)
        self.last_host_temp = self.last_mcu_temp = 0
        if not self.linear:
          self.config_temps = {}
          buff = {}
          for option in config.getoptions():
              if self.temp_re.match(option):
                  ct = float(option.partition('_')[2])
                  cs = config.getfloat(option)
                  if cs > 1.:
                     logging.warning(f"In option {option} the set speed ({cs}) is greater than 1. On setting the speed, this value will be interpreted as 1")
                  buff[ct] = cs
          if not buff:
             raise self.printer.config_error(
                _("Must set temps and speeds"))
          self.config_temps = dict(sorted(buff.items()))
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.printer.register_event_handler("temperature_host:sample_temperature", self._on_host_temp)
        self.printer.register_event_handler("temperature_mcu:sample_temperature", self._on_mcu_temp)
    

    def _on_host_temp(self, temp):
      self.last_host_temp = temp
      if temp <= self.last_mcu_temp:
         return
      self.set_speed(temp)

    def _on_mcu_temp(self, temp):
      self.last_mcu_temp = temp
      if temp <= self.last_host_temp:
         return
      self.set_speed(temp)

    def set_speed(self, temp):
      if temp >= 70:
        if self.fan.last_fan_value != 1.:
          self.fan.set_speed_from_command(1., False)
        return
      if self.linear:
        if temp > 65:
          setting_speed = (80 + (temp - 65)) / 100
        else:
          setting_speed = .7
        if self.fan.last_fan_value != setting_speed:
          self.fan.set_speed_from_command(setting_speed, False)
      else:
        for config_temp in self.config_temps:
          if temp <= config_temp:
              if self.fan.last_fan_value != self.config_temps[config_temp]:
                self.fan.set_speed_from_command(self.config_temps[config_temp], False)
              return
        if self.fan.last_fan_value != next(reversed(self.config_temps.values())):
          self.fan.set_speed_from_command(next(reversed(self.config_temps.values())), False)
      
        
    def _handle_ready(self):
        self.fan.set_speed_from_command(.7, False)
    
    def get_status(self, eventtime):
       return {'last_fan_value': self.fan.last_fan_value}

def load_config_prefix(config):
    return PrinterFanBack(config)