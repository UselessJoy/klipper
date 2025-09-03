import logging
from math import exp
from . import fan
import locales
import re
class PrinterFanBack:
    def __init__(self, config):
        if config.getboolean('linear', None) != None:
          config.deprecate('linear')
        self.printer = config.get_printer()
        self.fan = fan.Fan(config, default_shutdown_speed=0.)
        self.reactor = self.printer.get_reactor()
        section_name = config.get_name()
        self.fan_name = section_name.split()[-1]
        self.is_manual = False
        self.timer = None
        self.last_eventtime = None
        self.greater_eventtime = 0
        try:
            self.printer.lookup_object('fans').add_fan(section_name, self)
        except:
            self.printer.load_object(config, 'fans').add_fan(section_name, self)
        strategies = {
           'base': LazySpeedStrategy,
           'sigmoid': SigmoidSpeedStrategy,
           'config': ConfiguredSpeedStrategy,
        }
        strategy = config.get('mode', 'base')
        if strategy not in strategies.keys():
           raise config.error(_(f"fan_back mode must be one of %s") % (', '.join(strategies.keys())))
        self.strategy = strategies[strategy](config, self.fan)
        # self.strategy = SigmoidSpeedStrategy(config, self.fan) if config.getboolean('linear', True) else ConfiguredSpeedStrategy(config, self.fan)
        self.last_HOST_temp = self.last_MCU_temp = 0
        self.printer.register_event_handler("klippy:ready", self._handle_ready)

    def _handle_ready(self):
        self.fan.set_speed_from_command(.7, False)
        self.printer.register_event_handler("temperature_host:sample_temperature", self._on_host_temp)
        self.printer.register_event_handler("temperature_mcu:sample_temperature", self._on_mcu_temp)

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

    def get_manual(self):
       return self.is_manual

    def set_manual(self, is_manual):
       self.is_manual = is_manual

    def create_recover_speed_timer(self):
      if self.is_manual:
        if self.timer:
          self.reactor.unregister_timer(self.timer)
          self.timer = None
        self.last_eventtime = self.reactor.monotonic()
        self.timer = self.reactor.register_timer(self.open_timer, self.reactor.NOW)

    def open_timer(self, eventtime):
        if abs(eventtime - self.last_eventtime) > 5:
          self.reset_open_timer()
          return self.reactor.NEVER
        return eventtime + 1

    def reset_open_timer(self, web_request=None):
        self.fan.set_speed_from_command(.7, False)
        if self.timer:
            self.reactor.unregister_timer(self.timer)
            self.timer = None

    def set_speed(self, temp):
      if self.is_manual:
         return
      if temp >= self.strategy.get_limit_temp():
         self.strategy.on_limit_temp()
         return
      self.strategy.set_speed(temp)

    def get_status(self, eventtime):
       return {'is_manual': self.is_manual, 'last_fan_value': self.fan.last_fan_value}

def load_config_prefix(config):
    return PrinterFanBack(config)

class BaseSpeedStrategy:
    def __init__(self, config, fan):
        self.fan = fan
        self.limit_temp = config.getfloat('limit_temp', 70)
    
    def get_limit_temp(self):
       return self.limit_temp

    def on_limit_temp(self):
        if self.fan.last_fan_value != 1.0:
            self.fan.set_speed_from_command(1.0, False)

    def set_speed(self, temp):
        raise NotImplementedError

class LazySpeedStrategy(BaseSpeedStrategy):
    def __init__(self, config, fan):
        super().__init__(config, fan)
        self.idle_speed: float = config.getfloat('idle_speed', 0.5, minval=0.3, maxval=1.)
        self.load_speed: float = config.getfloat('load_speed', 0.8, minval=self.idle_speed, maxval=1.)
        self.vsd = None
        self.printer = config.get_printer()
        self.printer.register_event_handler("klippy:ready", self._on_ready)

    def _on_ready(self):
        self.vsd = self.printer.lookup_object("virtual_sdcard")

    def set_speed(self, temp):
        speed = self.idle_speed
        if self.vsd.is_active():
           speed = self.load_speed
        if speed != self.fan.last_fan_value:
           self.fan.set_speed_from_command(speed, False)
       
class SigmoidSpeedStrategy(BaseSpeedStrategy):
    def __init__(self, config, fan):
        super().__init__(config, fan)
        self.min_speed: float = 100 * config.getfloat('min_speed', 0.3, minval=0.3, maxval=1.)

    def set_speed(self, temp):
        speed = self.min_speed + (100 - self.min_speed) / (1 + exp(-(temp - 50) / 30.))
        # if speed >= 0.58 and speed <= 0.6:
        #    speed = 0.6
        self.fan.set_speed_from_command(int(speed) / 100.0, False)

class ConfiguredSpeedStrategy(BaseSpeedStrategy):
    temp_re = re.compile('^temp_\d+$')
    def __init__(self, config, fan):
        super().__init__(config, fan)
        self.reactor = config.get_printer().get_reactor()
        self.config_speeds = {}
        buff = {}
        for option in config.getoptions():
            if self.temp_re.match(option):
                conf_temp = float(option.partition('_')[2])
                conf_speed = config.getfloat(option, None, minval=0.3)
                if conf_speed > 1.:
                    logging.warning(f"In option {option} installed speed ({conf_speed}) greater than 1, this value will be interpreted as 1")
                buff[conf_temp] = conf_speed
        if not buff:
            raise config.get_printer().config_error(
              _("Must set temps and speeds"))
        self.config_speeds = dict(sorted(buff.items()))
        self.current_range = None
        self.range_exit_time = None

    def on_limit_temp(self):
      self.range_exit_time = None
      if self.fan.last_fan_value != 1.0:
        self.fan.set_speed_from_command(1.0, False)
        self.current_range = self.limit_temp

    def set_speed(self, temp):
      current_range = self._find_range_for_temp(temp)
      speed = self.config_speeds[current_range]
      if self.current_range is None:
          self._set_speed(speed)
          self.current_range = current_range
          return
      if current_range == self.current_range:
          self.range_exit_time = None
          return
      current_time = self.reactor.monotonic()
      if self.range_exit_time is None:
          self.range_exit_time = current_time
          return
      if current_time - self.range_exit_time >= 5:
          self._set_speed(speed)
          self.current_range = current_range
          self.range_exit_time = None
    
    def _find_range_for_temp(self, temp):
        for config_temp in self.config_speeds.keys():
            if temp <= config_temp:
                return config_temp
        return max(self.config_speeds.keys())

    def _set_speed(self, speed):
        if self.fan.last_fan_value != speed:
            self.fan.set_speed_from_command(speed, False)