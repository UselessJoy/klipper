import logging
from . import fan
import locales
import re
class PrinterFanBack:
    temp_re = re.compile('^temp_\d+$')

    def __init__(self, config):
        self.printer = config.get_printer()
        self.fan = fan.Fan(config, default_shutdown_speed=0.)
        self.reactor = self.printer.get_reactor()
        section_name = config.get_name()
        self.fan_name = section_name.split()[-1]
        self.is_manual = False
        self.timer = None
        self.last_eventtime = None
        self.is_greater_temp = False
        self.greater_eventtime = 0
        try:
            self.printer.lookup_object('fans').add_fan(section_name, self)
        except:
            self.printer.load_object(config, 'fans').add_fan(section_name, self)
        self.linear = config.getboolean('linear', True)
        self.last_HOST_temp = self.last_MCU_temp = 0
        if not self.linear:
          self.config_temps = {}
          buff = {}
          for option in config.getoptions():
              if self.temp_re.match(option):
                  ct = float(option.partition('_')[2])
                  cs = config.getfloat(option)
                  if cs > 1.:
                     logging.warning(f"In option {option} installed speed ({cs}) greater than 1, this value will be interpreted as 1")
                  buff[ct] = cs
          if not buff:
             raise self.printer.config_error(
                _("Must set temps and speeds"))
          self.config_temps = dict(sorted(buff.items()))
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.printer.register_event_handler("temperature_host:sample_temperature", self._on_host_temp)
        self.printer.register_event_handler("temperature_mcu:sample_temperature", self._on_mcu_temp)
    def get_manual(self):
       return self.is_manual
    def set_manual(self, is_manual):
       self.is_manual = is_manual
    def _on_host_temp(self, temp):
      self.last_HOST_temp = temp
      # Изменяем скорость по наибольшей температуре
      if temp <= self.last_MCU_temp:
         return
      self.set_speed(temp)

    def _on_mcu_temp(self, temp):
      self.last_MCU_temp = temp
      # Изменяем скорость по наибольшей температуре
      if temp <= self.last_HOST_temp:
         return
      self.set_speed(temp)

    def create_recover_speed_timer(self):
      if self.is_manual:
        if self.timer:
          self.reactor.unregister_timer(self.timer)
          self.timer = None
        self.last_eventtime = self.reactor.monotonic()
        self.timer = self.reactor.register_timer(self.open_timer, self.reactor.NOW)

    def set_speed(self, temp):
      if self.is_manual:
         return
      if temp >= 70:
        if self.fan.last_fan_value != 1.:
          self.fan.set_speed_from_command(1., False)
        return
      if self.linear:
        setting_speed = .7
        if temp > 65:
          if not self.is_greater_temp:
             self.is_greater_temp = True
             self.greater_eventtime = self.reactor.monotonic()
          elif self.reactor.monotonic() - self.greater_eventtime >= 5: 
            setting_speed = .8
        else:
          self.is_greater_temp = False
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
    
    def open_timer(self, eventtime):
        if abs(eventtime - self.last_eventtime) > 10:
          self.reset_open_timer()
          return self.reactor.NEVER
        return eventtime + 1

    def reset_open_timer(self, web_request=None):
        self.fan.set_speed_from_command(.3, False)
        if self.timer:
            self.reactor.unregister_timer(self.timer)
            self.timer = None
        
    def _handle_ready(self):
        self.fan.set_speed_from_command(.7, False)
    
    def get_status(self, eventtime):
       return {'is_manual': self.is_manual, 'last_fan_value': self.fan.last_fan_value}

def load_config_prefix(config):
    return PrinterFanBack(config)