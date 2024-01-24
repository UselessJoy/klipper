#from . import bus

#BACKGROUND_PRIORITY_CLOCK = 0x7fffffff00000000
import logging
import threading
import locales
class LedControl:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.print_stats = self.printer.load_object(config, 'print_stats')
        self.reactor = self.printer.get_reactor()
        self.timer = None
        self.start_print_timer = None
        self.print_state = None
        self.rgb = None
        self.last_eventtime = None
        self.is_interrupt = False
        self.events = []
        self.now_event = ""
        self.previous_event = ""
        self.is_printing = False
        self.print_state = None
        self.target_bed = None
        self.temp_bed = None
        self.target_ex = None
        self.temp_ex = None
        self.send_event = False
        self.is_set_printing = False
        self.extruder = None#self.printer.lookup_object('toolhead')
        self.heater_bed = None#self.printer.lookup_object('heater_bed')
        self.state_dictionary = {
         "interrupt":"STOP_LED_EFFECTS\nSET_LED_EFFECT EFFECT=WARNING_DEFAULT\n",
         "paused": "STOP_LED_EFFECTS\nSET_LED_EFFECT EFFECT=NEOPIXEL_DEFAULT RED=0.95 GREEN=0.5 BLUE=0.0\n",
         "cancelled": "STOP_LED_EFFECTS\nSET_LED_EFFECT EFFECT=NEOPIXEL_DEFAULT\n",
         "complete":"STOP_LED_EFFECTS\nSET_LED_EFFECT EFFECT=COMPLETE_DEFAULT\n",
         "error":"STOP_LED_EFFECTS\nSET_LED_EFFECT EFFECT=ERROR_DEFAULT\n"
        }
        self.gcode.register_command("DISABLE_LED_EFFECTS", self.cmd_DISABLE_LED_EFFECTS)
        self.gcode.register_command("ENABLE_LED_EFFECTS", self.cmd_ENABLE_LED_EFFECTS)
        self.printer.register_event_handler("klippy:ready",
                                            self._handle_control)

    def cmd_DISABLE_LED_EFFECTS(self, gcmd):
        self.printer.send_event("led_control:disabled")

    def cmd_ENABLE_LED_EFFECTS(self, gcmd):
        self.printer.send_event("led_control:enabled")
    
    def _handle_control(self):
        self.extruder = self.printer.lookup_object('extruder')
        self.heater_bed = self.printer.lookup_object('heater_bed')
        self.printer.register_event_handler("gcode:command_error", self._handle_error)
        self.printer.register_event_handler("led:set_led", self._handle_led)
        self.printer.register_event_handler("extruder:heating", self._handle_extruder_heating)
        self.printer.register_event_handler("bed:heating", self._handle_bed_heating)
        #self.printer.register_event_handler("heaters:stop_heating", self._handle_enabled)
        self.printer.register_event_handler("led_control:disabled", self._handle_disabled)
        self.printer.register_event_handler("led_control:enabled", self._handle_enabled)
        self.timer = self.reactor.register_timer(self._main_control, self.reactor.NOW)

    
    def set_start_print_effect(self):
        if self.timer != None:
            self.start_print_timer = self.reactor.register_timer(
                self.set_ledEffect_bed_extruder_heating_start, self.reactor.NOW)
    
    def set_ledEffect_bed_extruder_heating_start(self, eventtime):
        self.is_printing = True
        self.now_event = "printing"
        self.gcode.run_script("STOP_LED_EFFECTS\n"
                                        "SET_LED_EFFECT EFFECT=EXTRUDER_LEFT\n"
                                        "SET_LED_EFFECT EFFECT=BED_RIGHT\n")
        return self.reactor.NEVER 
    
    
    def get_status(self, eventtime):
        return {
            'led_status': self.now_event
        }
    
    
    
    
           
    def _handle_error(self):
        self._set_event("error")
        
    def _handle_led(self, red, green, blue):
        self.rgb = [red, green, blue]
        self._set_event("set_led")
        
    def _handle_extruder_heating(self):
        self._set_event("heating")
        
    def _handle_bed_heating(self):
        self._set_event("heating")
        
    def _handle_disabled(self):
        self._set_event("disabled")
        
    def _handle_enabled(self):
        self._set_event("enabled")
        if self.timer is None: 
            self.timer = self.reactor.register_timer(self._main_control, self.reactor.NOW)
    
    def _set_event(self, event):
        if self.now_event != 'disabled' or event == 'enabled':
            self.now_event = event
            buff = self.get_event()
            if buff != self.previous_event:
                self.previous_event = buff
            self.add_event(event)
            self.send_event = True
    
    def add_event(self, event):
        if len(self.events) >= 1:
            return
        else:
            self.events.append(event)
    def get_event(self):
        if len(self.events) >= 1:
            return self.events.pop()
        else:
            return self.previous_event
        
    def _main_control(self, eventtime):
        if self.now_event == "disabled":
            self.set_ledEffect_disabled(eventtime)
            if self.timer is not None:
                self.reactor.unregister_timer(self.timer)
                self.timer = None
            return self.reactor.NEVER
        now_print_state = self.print_stats.get_status(eventtime)['state']
        self.target_bed = self.heater_bed.get_status(eventtime)['target']
        self.temp_bed = self.heater_bed.get_status(eventtime)['temperature']
        self.target_ex = self.extruder.get_status(eventtime)['target']
        self.temp_ex = self.extruder.get_status(eventtime)['temperature']
        if self.last_eventtime != None:
            self._control_eventtime(eventtime)
        if (self.send_event or self.print_state != now_print_state):
            self.send_event = False
            self.print_state = now_print_state
            # logging.info("IM IN CONTROL_SWITCH")
            self._control_switch(eventtime)
        return eventtime + 0.1
    
    #print_state = "standby", "cancelled", "error", "complete", "interrupt", "printing"
    def _control_switch(self, eventtime):
        if self.is_printing:
            self._printing_control(eventtime)
        else:
            self._standby_control(eventtime)

    def _control_eventtime(self, eventtime):
        pass_time = abs(eventtime - self.last_eventtime)
        # logging.info("pass time " + str(pass_time))
        # logging.info("eventtime " + str(eventtime))
        # logging.info("last eventtime " + str(self.last_eventtime))
        # logging.info("is printing " + str(self.is_printing))
        # logging.info("now event " + str(self.now_event))
        # logging.info("print event " + str(self.print_state))
        if self.now_event == "error" and pass_time > 10:
            if self.rgb != None:
                self.set_ledEffect_set_led(eventtime)
            else:
                self.set_ledEffect_default(eventtime)
            self.now_event = "plug"
            return
        if not self.is_printing:
            if self.now_event == "set_led" or self.now_event == "enabled":
                if self.target_bed != 0 and self.target_ex != 0 and pass_time > 10:
                    self.set_ledEffect_bed_extruder_heating(eventtime)
                elif self.target_ex != 0 and pass_time > 10:
                        self.set_ledEffect_extruder_heating(eventtime)
                elif self.target_bed != 0 and pass_time > 10:
                        self.set_ledEffect_heater_bed_heating(eventtime)
        else:
            if self.print_state == "printing":
                if self.now_event == "set_led" and pass_time > 10:
                        self.set_ledEffect_printing(eventtime)
                        self.now_event = "plug"
                if self.target_bed != 0 and self.target_ex != 0:
                    if not self.extruder.get_heater().check_busy(eventtime) and not self.heater_bed.get_heater().check_busy(eventtime):
                        if self.now_event != "printing":
                            self.set_ledEffect_printing(eventtime)
                            self.now_event = "printing"
                else:
                    if self.now_event != "heating":
                        self.set_ledEffect_bed_extruder_heating(eventtime)
            else:
                if self.print_state != "paused" and (self.print_state in self.state_dictionary):
                    if pass_time > 10:
                        if self.rgb != None:
                            self.set_ledEffect_set_led(eventtime)
                        else:
                            self.set_ledEffect_default(eventtime)
                        self.now_event = "plug"
                        self.is_printing = False
                    
                                  
    def _standby_control(self, eventtime):
        # logging.info("IM in standby mode")
        if self.now_event == "error":
            self.set_ledEffect_error(eventtime)
        elif self.print_state == "interrupt":
            self.set_ledEffect_state(eventtime)
        elif self.now_event == "set_led" or self.now_event == "enabled":
            if self.rgb != None:
                self.set_ledEffect_set_led(eventtime)
            else:
                self.set_ledEffect_default(eventtime)
        elif self.now_event == "heating":
            if self.target_bed != 0 and self.target_ex != 0:
                self.set_ledEffect_bed_extruder_heating(eventtime)
            elif self.target_bed != 0:
                self.set_ledEffect_heater_bed_heating(eventtime)
            elif self.target_ex != 0:
                self.set_ledEffect_extruder_heating(eventtime)
            else:
                if self.rgb != None:
                    self.set_ledEffect_set_led(eventtime)
                else:
                    self.set_ledEffect_default(eventtime)
        else:
            self.set_ledEffect_default(eventtime)
    
    def _printing_control(self, eventtime):
        # logging.info("IM in printing mode")
        if self.print_state == "printing":
            if self.extruder.get_heater().check_busy(eventtime) or self.heater_bed.get_heater().check_busy(eventtime):
                self.set_ledEffect_bed_extruder_heating(eventtime)
            else:
                self.set_ledEffect_printing(eventtime)
        elif self.now_event == "set_led":
            self.set_ledEffect_set_led(eventtime)
        elif self.print_state in self.state_dictionary:
            self.set_ledEffect_state(eventtime)
      
    def set_ledEffect_default(self, eventtime):
        self.last_eventtime = eventtime
        self.gcode.run_script("STOP_LED_EFFECTS\n"
                                        "SET_LED_EFFECT EFFECT=NEOPIXEL_DEFAULT\n")  
    
    def set_ledEffect_disabled(self, eventtime):
        self.last_eventtime = eventtime
        self.gcode.run_script("STOP_LED_EFFECTS\n")
    
    def set_ledEffect_printing(self, eventtime):
        self.last_eventtime = eventtime
        self.gcode.run_script("STOP_LED_EFFECTS\n"
                                        "SET_LED_EFFECT EFFECT=NEOPIXEL_DEFAULT\n")     
    
    def set_ledEffect_error(self, eventtime):
        self.last_eventtime = eventtime
        self.gcode.run_script("STOP_LED_EFFECTS\n"
                                        "SET_LED_EFFECT EFFECT=ERROR_DEFAULT\n")
                                        #"SET_PIN PIN=BEEPER_pin VALUE=0.5 CYCLE_TIME=0.0024696\n"
                                        #"G4 P500\n"
                                        #"SET_PIN PIN=BEEPER_pin VALUE=0\n")
   
    def set_ledEffect_set_led(self, eventtime):
        self.last_eventtime = eventtime
        self.gcode.run_script("STOP_LED_EFFECTS\n"
                                        f"SET_LED_EFFECT EFFECT=NEOPIXEL_DEFAULT RED={self.rgb[0]} GREEN={self.rgb[1]} BLUE={self.rgb[2]}\n")
    
    def set_ledEffect_extruder_heating(self, eventtime):
        self.last_eventtime = eventtime
        self.gcode.run_script("STOP_LED_EFFECTS\n"
                                        "SET_LED_EFFECT EFFECT=EXTRUDER_DEFAULT\n")
    
    def set_ledEffect_heater_bed_heating(self, eventtime):
        self.last_eventtime = eventtime
        self.gcode.run_script("STOP_LED_EFFECTS\n"
                                        "SET_LED_EFFECT EFFECT=HEATER_BED_DEFAULT\n")
    
    def set_ledEffect_bed_extruder_heating(self, eventtime):
        self.last_eventtime = eventtime
        self.gcode.run_script("STOP_LED_EFFECTS\n"
                                        "SET_LED_EFFECT EFFECT=EXTRUDER_LEFT\n"
                                        # "SET_LED_EFFECT EFFECT=LED_CENTER\n"
                                        "SET_LED_EFFECT EFFECT=BED_RIGHT\n") 
    
    def set_ledEffect_state(self, eventtime):
        self.last_eventtime = eventtime
        self.gcode.run_script(self.state_dictionary[self.print_state])

def load_config(config):
    return LedControl(config)