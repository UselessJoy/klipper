import logging

from .fan_generic import PrinterFanGeneric
from .fan_back import PrinterFanBack
import locales

# is_manual перевести на объект fan с соответствующими методами
class Fans:
    def __init__(self, config):
      self.fans = {}
      self.printer = config.get_printer()
      gcode = self.printer.lookup_object("gcode")
      self.unique_names = {'hotend': 0}
      gcode.register_command("SET_FAN_SPEED",
                                  self.cmd_SET_FAN_SPEED,
                                  desc=self.cmd_SET_FAN_SPEED_help)
      gcode.register_command("DEBUG_SET_MANUAL",
                                  self.cmd_DEBUG_SET_MANUAL,
                                  desc=self.cmd_SET_MANUAL_help)
      # gcode.register_command("_SET_TEST_FAN_SPEED",
      #                             self.cmd_SET_TEST_FAN_SPEED,
      #                             desc=self.cmd_SET_MANUAL_help)
    # Обязательно записывать полное имя, как в конфиге, чтобы
    # не происходила перезапись вентилятора с таким же именем,
    # но другого типа
    def add_fan(self, section_name, fan):
      self.fans[section_name] = fan

    def get_fans(self):
       return self.fans

    def found_name(self, name):
      found_name = False
      for fullname in self.fans.keys():
          if name == fullname or fullname.split()[-1] == name:
              name = fullname
              found_name = True
              return fullname
      if not found_name:
          self.printer.lookup_object('messages').send_message('error', _("Fan %s not found") % name)
      return None

    cmd_SET_FAN_SPEED_help = _("Set fan speed")
    def cmd_SET_FAN_SPEED(self, gcmd):
      name = gcmd.get('FAN')
      speed = gcmd.get_float('SPEED', 0.)
      self.set_fan_speed(name, speed)

    def set_fan_speed(self, name, speed):
      fullname = self.found_name(name)
      if not fullname:
        return
      self.fans[fullname].fan.set_speed_from_command(speed)
      if isinstance(self.fans[fullname], PrinterFanGeneric):
        self.printer.send_event("fan_generic:set_fan_speed", speed)
      elif isinstance(self.fans[fullname], PrinterFanBack):
        if speed == 0:
          self.fans[fullname].create_recover_speed_timer()

    cmd_SET_MANUAL_help = _("Set manual control for fan. Use it for diagnostic purposes")
    def cmd_DEBUG_SET_MANUAL(self, gcmd):
      name = gcmd.get('FAN')
      is_manual = gcmd.get_boolean('MANUAL')
      self.set_manual(name, is_manual)

    def set_manual(self, name, manual):
      fullname = self.found_name(name)
      if not fullname:
        return
      if hasattr(self.fans[fullname], 'set_manual'):
          self.fans[fullname].set_manual(manual)
      else:
          logging.info(f"Fan {fullname} manually controlled")

    def get_is_manual(self, name):
      # Проверка ручного управления только для тех, кто может менять свое состояние, для остальных None
      # криво, поскольку для изначально "ручных" вентиляторов будет возвращаться None
      fullname = self.found_name(name)
      if not fullname:
        return
      if hasattr(self.fans[fullname], 'get_manual'):
          return self.fans[fullname].get_manual()

def load_config(config):
    return Fans(config)