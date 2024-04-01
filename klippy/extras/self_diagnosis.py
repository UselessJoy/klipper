import os, logging, pathlib
import locales

class AudioMessages:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        #self.gcode.register_command("SELF_DIAGNOSIS", self.self_diagnosis)
        
    def self_diagnosis(self, gcmd):
        self.check_motors()
        self.check_fans()
        self.check_retract()
        self.check_endstops()
        self.check_heaters()
        
    def check_motors(self):
        self.gcode.run_script_from_command(  "STEPPER_BUZZ STEPPER=stepper_z\n"
                                "STEPPER_BUZZ STEPPER=stepper_x\n"
                                "STEPPER_BUZZ STEPPER=stepper_y\n")

    def check_fans(self):
        self.gcode.run_script_from_command(  "M106 S60\n"
                                "M106 S0\n")
        
    def check_retract(self):
        self.gcode.run_script_from_command(  "SET_RETRACTION RETRACT_LENGTH=5 RETRACT_SPEED=10\n ")
        
    def check_endstops(self):
        self.gcode.run_script_from_command(  "SET_RETRACTION RETRACT_LENGTH=5 RETRACT_SPEED=10\n ")
    
    def check_heaters(self):
        self.gcode.run_script_from_command(  "SET_HEATER_TEMPERATURE HEATER=extruder TARGET=220\n"
                                "SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET=65\n")
    
def load_config(config):
    return AudioMessages(config)