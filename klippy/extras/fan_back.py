from . import fan
import locales
class PrinterFanBack:
    cmd_SET_FAN_SPEED_help = _("Sets the speed of a fan")
    def __init__(self, config):
        self.printer = config.get_printer()
        self.fan = fan.Fan(config, default_shutdown_speed=0.)
        self.fan_name = config.get_name().split()[-1]
        self.max_temp = 0
        gcode = self.printer.lookup_object("gcode")
        gcode.register_mux_command("SET_FAN_SPEED", "FAN",
                                   self.fan_name,
                                   self.cmd_SET_FAN_SPEED,
                                   desc=self.cmd_SET_FAN_SPEED_help)
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.printer.register_event_handler("temperature_host:sample_temperature", self._on_temp)
        self.printer.register_event_handler("temperature_mcu:sample_temperature", self._on_temp)
    

    def _on_temp(self, temp):
      if temp <= self.max_temp:
          return
      self.max_temp = temp
      if temp >= 55 and self.fan.last_fan_value < 100:
        self.fan.set_speed_from_command(80 + (temp - 40))
      else:
        self.fan.set_speed_from_command(80)
        
    def _handle_ready(self):
        self.fan.set_speed_from_command(80)
        
    def get_status(self, eventtime):
        return self.fan.get_status(eventtime)
    def cmd_SET_FAN_SPEED(self, gcmd):
        speed = gcmd.get_float('SPEED', 0.)
        self.fan.set_speed_from_command(speed)

def load_config_prefix(config):
    return PrinterFanBack(config)