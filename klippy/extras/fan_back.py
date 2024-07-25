from . import fan
import locales
class PrinterFanBack:
    cmd_SET_FAN_SPEED_help = _("Sets the speed of a fan")
    def __init__(self, config):
        self.printer = config.get_printer()
        self.fan = fan.Fan(config, default_shutdown_speed=0.)
        self.fan_name = config.get_name().split()[-1]
        self.last_host_temp = self.last_mcu_temp = self.last_speed = 0
        gcode = self.printer.lookup_object("gcode")
        gcode.register_mux_command("SET_FAN_SPEED", "FAN",
                                   self.fan_name,
                                   self.cmd_SET_FAN_SPEED,
                                   desc=self.cmd_SET_FAN_SPEED_help)
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
      if temp >= 55:
        self.last_speed = 80 + (temp - 40)
      else:
        self.last_speed = 80
      self.fan.set_speed_from_command(self.last_speed)
        
    def _handle_ready(self):
        self.fan.set_speed_from_command(80)
        
    def get_status(self, eventtime):
        return self.fan.get_status(eventtime)
    def cmd_SET_FAN_SPEED(self, gcmd):
        speed = gcmd.get_float('SPEED', 0.)
        self.fan.set_speed_from_command(speed)
    
    def get_status(self, eventtime):
       return {'setting_speed': self.last_speed,
               'last_fan_value': self.fan.last_fan_value}

def load_config_prefix(config):
    return PrinterFanBack(config)