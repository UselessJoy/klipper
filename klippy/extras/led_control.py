#from . import bus

#BACKGROUND_PRIORITY_CLOCK = 0x7fffffff00000000
import logging
import threading
class LedControl:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.print_stats = self.printer.load_object(config, 'print_stats')
        self.reactor = self.printer.get_reactor()
        self.timer = None
        self.print_state = None
        self.heating = False
        self.led = False
        self.rgb = None
        self.error = False
        self.state_dictionary = {
         "standby":"STOP_LED_EFFECTS\nSET_LED_EFFECT EFFECT=NEOPIXEL_DEFAULT\n",
         "interrupt":"STOP_LED_EFFECTS\nSET_LED_EFFECT EFFECT=WARNING_DEFAULT\n",
         "cancelled": "STOP_LED_EFFECTS\nSET_LED_EFFECT EFFECT=WARNING_DEFAULT\n",
         "complete":"STOP_LED_EFFECTS\nSET_LED_EFFECT EFFECT=COMPLETE_DEFAULT\n",
         "printing":"STOP_LED_EFFECTS\nSET_LED_EFFECT EFFECT=PRINTING_DEFAULT\n",
         "error":"STOP_LED_EFFECTS\nSET_LED_EFFECT EFFECT=NEOPIXEL_DEFAULT RED=0.95 GREEN=0.0 BLUE=0.0\n",
        }
        self.extruder = None#self.printer.lookup_object('toolhead')
        self.heater_bed = None#self.printer.lookup_object('heater_bed')
        self.printer.register_event_handler("klippy:ready",
                                            self._handle_control)
        self.last_eventtime = None
        self.events = []
        self.now_event = ""
        self.previous_event = ""

    def _handle_control(self):
        self.extruder = self.printer.lookup_object('extruder')
        self.heater_bed = self.printer.lookup_object('heater_bed')
        self.printer.register_event_handler("gcode:command_error", self._handle_error)
        self.printer.register_event_handler("led:set_led", self._handle_led)
        self.printer.register_event_handler("extruder:heating", self._extruder_heating)
        self.printer.register_event_handler("bed:heating", self._bed_heating)
        self.timer = self.reactor.register_timer(
            self.control, self.reactor.NOW)

    def _handle_default(self):
        self.now_event = "default"
        buff = self.get_event()
        if buff != self.now_event:
            self.previous_event = buff
        self.add_event("default")
        self.timer = self.reactor.register_timer(
            self.set_ledEffect_default, self.reactor.NOW)
        
    def set_ledEffect_default(self, eventtime):
        self.last_eventtime = eventtime
        self.gcode.run_script("STOP_LED_EFFECTS\n"
                                        "SET_LED_EFFECT EFFECT=NEOPIXEL_DEFAULT\n")
        return self.reactor.NEVER 

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
        
    def control(self, eventtime):
        #klippy_state = "ready", "startup", "shutdown", "error"
        #print_state = "standby", "cancelled", "error", "complete", "interrupt", "printing"
       # logging.info(str(self.events) + str(self.now_event) + str(self.previous_event))
        klippy_state, print_state = self.get_data(eventtime)
        if self.now_event == "error":
            if self.last_eventtime != None and eventtime - self.last_eventtime > 10:
                self._handle_default()
        elif self.now_event == "set_led" and self.previous_event == "heating":
            if self.last_eventtime != None and eventtime - self.last_eventtime > 10:
                if self.heater_bed.get_status(eventtime)['target'] != 0:
                    self._bed_heating(eventtime)
                elif self.extruder.get_status(eventtime)['target'] != 0:
                    self._extruder_heating(eventtime)
                else:
                   self._handle_default()
        elif self.now_event == "heating":
            if self.heater_bed.get_status(eventtime)['target'] != 0:
                    self._bed_heating(eventtime)
            elif self.extruder.get_status(eventtime)['target'] != 0:
                    self._extruder_heating(eventtime)
            else:
                self._handle_default()
        else:
            self.check_state(print_state, eventtime)
        return eventtime + 1.

    def check_state(self, print_state, eventtime):
        if print_state in self.state_dictionary and self.now_event != print_state:
            self.now_event = print_state
            self.gcode.run_script(self.state_dictionary[print_state])
    
    #def not_previous_state(self, state):
    #    if state == self.print_state:
    #        return False
    #    else:
    #        self.print_state = state
    #        return True
         
    def get_data(self, eventtime):
        print_state = self.print_stats.get_status(eventtime)['state']
        klippy_state = self.printer.get_state_message()
        return klippy_state, print_state
    


    def handle_shutdown(self):
         logging.info("LedControl in shutdown state!")

    def _handle_error(self):
        self.now_event = "error"
        buff = self.get_event()
        if buff != self.now_event:
            self.previous_event = buff
        self.add_event("error")
        self.timer = self.reactor.register_timer(
            self.set_ledEffect_error, self.reactor.NOW)

    def _handle_led(self, red, green, blue):
         self.now_event = "set_led"
         buff = self.get_event()
         if buff != self.now_event:
            self.previous_event = buff
         self.add_event("set_led")
         self.rbg = [red, green, blue]
         self.timer = self.reactor.register_timer(
            self.set_ledEffect_set_led, self.reactor.NOW)
    
    def _extruder_heating(self, eventtime=0):
        self.now_event = "heating"
        buff = self.get_event()
        if buff != self.now_event:
            self.previous_event = buff
        self.add_event("heating")
        if self.heater_bed.get_status(eventtime)['target'] != 0:
            self.timer = self.reactor.register_timer(
                self.set_ledEffect_bed_extruder_heating, self.reactor.NOW)
        else:
            self.timer = self.reactor.register_timer(
                self.set_ledEffect_extruder_heating, self.reactor.NOW)

    def _bed_heating(self, eventtime=0):
        self.now_event = "heating"
        buff = self.get_event()
        if buff != self.now_event:
            self.previous_event = buff
        self.add_event("heating")
        if self.extruder.get_status(eventtime)['target'] != 0:
            self.timer = self.reactor.register_timer(
                self.set_ledEffect_bed_extruder_heating, self.reactor.NOW)
        else:
            self.timer = self.reactor.register_timer(
                self.set_ledEffect_heater_bed_heating, self.reactor.NOW)


    def set_ledEffect_error(self, eventtime):
        self.last_eventtime = eventtime
        self.gcode.run_script("STOP_LED_EFFECTS\n"
                                        "SET_LED_EFFECT EFFECT=ERROR_DEFAULT\n")
        return self.reactor.NEVER
    
    def set_ledEffect_set_led(self, eventtime):
        self.last_eventtime = eventtime
        self.gcode.run_script("STOP_LED_EFFECTS\n"
                                        f"SET_LED_EFFECT EFFECT=NEOPIXEL_DEFAULT RED={self.rbg[0]} GREEN={self.rbg[1]} BLUE={self.rbg[2]}\n")
        return self.reactor.NEVER
    
    def set_ledEffect_extruder_heating(self, eventtime):
        self.last_eventtime = eventtime
        self.gcode.run_script("STOP_LED_EFFECTS\n"
                                        "SET_LED_EFFECT EFFECT=EXTRUDER_DEFAULT\n")
        return self.reactor.NEVER
    
    def set_ledEffect_heater_bed_heating(self, eventtime):
        self.last_eventtime = eventtime
        self.gcode.run_script("STOP_LED_EFFECTS\n"
                                        "SET_LED_EFFECT EFFECT=HEATER_BED_DEFAULT\n")
        return self.reactor.NEVER
    
    def set_ledEffect_bed_extruder_heating(self, eventtime):
        self.last_eventtime = eventtime
        self.gcode.run_script("STOP_LED_EFFECTS\n"
                                        "SET_LED_EFFECT EFFECT=EXTRUDER_LEFT\n"
                                        "SET_LED_EFFECT EFFECT=LED_CENTER\n"
                                        "SET_LED_EFFECT EFFECT=BED_RIGHT\n")
        return self.reactor.NEVER
    

def load_config(config):
    return LedControl(config)