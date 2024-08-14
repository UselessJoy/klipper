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
        self.last_host_temp = self.last_mcu_temp = self.last_speed = 0
        self.linear = config.getboolean('linear', True)
        self.config_temps = self.config_speed = []
        if not self.linear:
          for option in config.getoptions():
              if self.temp_re.match(option):
                  ct = float(option.partition('_')[2])
                  cs = config.getfloat(option)
                  self.config_temps.append(ct)
                  if cs > 1.:
                     logging.warning(f"In option {option} the set speed ({cs}) is greater than 1. On setting the speed, this value will be interpreted as 1")
                  self.config_speed.append(cs)
          if not self.config_temps:
             raise self.printer.config_error(
                _("Must set temps and speeds"))
        
        gcode = self.printer.lookup_object("gcode")
        gcode.register_mux_command("SET_FAN_SPEED", "FAN",
                                   self.fan_name,
                                   self.cmd_SET_FAN_SPEED,
                                   desc=self.cmd_SET_FAN_SPEED_help)
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.printer.register_event_handler("temperature_host:sample_temperature", self._on_host_temp)
        #self.printer.register_event_handler("temperature_mcu:sample_temperature", self._on_mcu_temp)
    

    def _on_host_temp(self, temp):
      self.set_speed(temp)

    def _on_mcu_temp(self, temp):
      self.set_speed(temp)

    def set_speed(self, temp):
      if temp >= 60:
         self.fan.set_speed_from_command(1)
         return
      if self.linear:
        if temp >= 55:
          setting_speed = (80 + (temp - 40)) / 100
        else:
          setting_speed = .8
        self.fan.set_speed_from_command(setting_speed)
      else:
        for i, config_temp in enumerate(self.config_temps):
          if temp < config_temp:
              self.fan.set_speed_from_command(self.config_speed[i - 1] if i != 0 else self.config_speed[0])
              return
        self.fan.set_speed_from_command(self.config_speed[-1])
      
        
    def _handle_ready(self):
        self.fan.set_speed_from_command(.8)

    def cmd_SET_FAN_SPEED(self, gcmd):
        speed = gcmd.get_float('SPEED', 0.)
        self.fan.set_speed_from_command(speed)
    
    def get_status(self, eventtime):
       return {'last_fan_value': self.fan.last_fan_value}

def load_config_prefix(config):
    return PrinterFanBack(config)