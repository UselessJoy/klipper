# Support fans that are controlled by gcode
#
# Copyright (C) 2016-2020  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from . import fan
import locales
class PrinterFanGeneric:
    cmd_SET_FAN_SPEED_help = _("Set fan speed")
    def __init__(self, config):
        self.printer = config.get_printer()
        self.fan = fan.Fan(config, default_shutdown_speed=0.)
        section_name = config.get_name()
        self.fan_name = section_name.split()[-1]
        try:
            self.printer.lookup_object('fans').add_fan(section_name, self)
        except:
            self.printer.load_object(config, 'fans').add_fan(section_name, self)
        # gcode = self.printer.lookup_object("gcode")
        # gcode.register_mux_command("SET_FAN_SPEED", "FAN",
        #                            self.fan_name,
        #                            self.cmd_SET_FAN_SPEED,
        #                            desc=self.cmd_SET_FAN_SPEED_help)

    def get_status(self, eventtime):
        return self.fan.get_status(eventtime)
    # def cmd_SET_FAN_SPEED(self, gcmd):
    #     speed = gcmd.get_float('SPEED', 0.)
    #     self.fan.set_speed_from_command(speed)
    #     self.printer.send_event("fan_generic:set_fan_speed", speed)

def load_config_prefix(config):
    return PrinterFanGeneric(config)
