import logging
import locales
LED_EFFECTS = {
            "error": ["ERROR"],
            "print_error": ["ERROR"],
            "set_led": ["DEFAULT"],
            "extruder_heating": ["EXTRUDER"],
            "bed_heating": ["HEATER_BED"],
            "stop_heating": ["DEFAULT"],
            "extruder_bed_heating": ["EXTRUDER_LEFT", "BED_RIGHT"],
            "enabled": ["DEFAULT"],
            "printing": ["DEFAULT"],
            "interrupt": ["WARNING"],
            "paused": ["PAUSED"],
            "cancelled": ["DEFAULT"],
            "complete": ["COMPLETE"],
        }

class LedControl:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.print_stats = self.heaters = None
        self.gcode = self.printer.lookup_object('gcode')
        self.reactor = self.printer.get_reactor()
        self.timer = None
        self.printing_timer = None
        self.rgb = [0,0,0]
        self.last_eventtime = None
        self.luft_temp = 3.
        self.now_effect = ""
        self.is_printing = False
        self.paused = False
        self.enabled = True
        self.extruder = None
        self.heater_bed = None
        self.set_led_on_printing = False
        self.last_ex_target = self.last_hb_target = 0
        self.gcode.register_command("DISABLE_LED_EFFECTS", self.cmd_DISABLE_LED_EFFECTS)
        self.gcode.register_command("ENABLE_LED_EFFECTS", self.cmd_ENABLE_LED_EFFECTS)
        self.printer.register_event_handler("klippy:ready",
                                            self._handle_control)
        self.printer.register_event_handler("klippy:shutdown", self._handle_shutdown)
    
    def cmd_DISABLE_LED_EFFECTS(self, gcmd):
        self._handle_disabled()

    def cmd_ENABLE_LED_EFFECTS(self, gcmd):
        self._handle_enabled()
    
    def _handle_control(self):
        self.extruder = self.printer.lookup_object('extruder')
        self.heater_bed = self.printer.lookup_object('heater_bed')
        self.led_effect = self.printer.lookup_object('led_effect')
        self.print_stats = self.printer.lookup_object('print_stats')
        self.heaters = self.printer.lookup_object('heaters')
        self.printer.register_event_handler("gcode:command_error", self._handle_error)
        self.printer.register_event_handler("led:set_led", self._handle_led)
        self.printer.register_event_handler("extruder:heating", self._handle_extruder_heating)
        self.printer.register_event_handler("heater_bed:heating", self._handle_bed_heating)
        self.printer.register_event_handler("heaters:stop_heating", self._handle_stop_heating)
        self.printer.register_event_handler("led_control:disabled", self._handle_disabled)
        self.printer.register_event_handler("led_control:enabled", self._handle_enabled)
        
        self.printer.register_event_handler("print_stats:printing", self._handle_printing)
        self.printer.register_event_handler("print_stats:interrupt", self._handle_interrupt)
        self.printer.register_event_handler("print_stats:paused", self._handle_paused)
        self.printer.register_event_handler("print_stats:cancelled", self._handle_finish_cancelled)
        self.printer.register_event_handler("print_stats:complete", self._handle_finish_complete)
        self.printer.register_event_handler("print_stats:error", self._handle_finish_error)
    
    def set_start_print_effect(self):
        self.is_printing = True
        if not self.printing_timer:
            self.printing_timer = self.reactor.register_timer(
                    self.watch_for_printing, self.reactor.NOW)
            
    def watch_for_printing(self, eventtime):
        if self.enabled:
            if self.is_printing:
                
                last_ex_target, ex_target = self.extruder.get_heater().get_temp(eventtime)
                last_hb_target, hb_target = self.heater_bed.get_heater().get_temp(eventtime)
                is_heater_bed_cold = last_hb_target + self.luft_temp <= hb_target
                is_extruder_busy_cold = last_ex_target + self.luft_temp <= ex_target
                is_heaters_cold = is_heater_bed_cold or is_extruder_busy_cold

                # Эффекты печати включаются только если закончились временные эффекты
                if not self.timer:
                    if self.paused:
                        if self.now_effect != "paused":
                            self.run_if_enabled("paused")
                    elif not(self.heaters.get_waiting() or is_heaters_cold):
                        if self.set_led_on_printing:
                            if self.now_effect != "set_led":
                                self.run_if_enabled("set_led")
                        elif self.now_effect != "printing":
                            self.run_if_enabled("printing")
                    else:
                        if self.now_effect != "extruder_bed_heating":
                            self.run_if_enabled("extruder_bed_heating")
        else:
            if self.printing_timer:
                self.reset_printing_timer()
                return self.reactor.NEVER
        return eventtime + 1
    
    def reset_printing_timer(self):
        if self.printing_timer:
            self.set_led_on_printing = False
            self.reactor.unregister_timer(self.printing_timer)
            self.printing_timer = None
            
    def create_ten_seconds_timer(self):
        self.last_eventtime = None
        if self.timer is None:
            self.timer = self.reactor.register_timer(
                self._on_tick_timer, self.reactor.NOW) 
    def _on_tick_timer(self, eventtime):
        if not self.last_eventtime:
            self.last_eventtime = eventtime 
        pass_time = abs(eventtime - self.last_eventtime)
        if pass_time > 10:
            self.reset_timer()
            return self.reactor.NEVER
        return eventtime + 1
    def reset_timer(self):
        if self.timer:
            self.reactor.unregister_timer(self.timer)
            self.timer = None
            self.last_eventtime = None
            if not self.is_printing:
              self.now_effect = "enabled"
              self.led_effect.run_effect(LED_EFFECTS['enabled'])
    
    def _handle_shutdown(self):
        self.enabled = False
     
    def _handle_enabled(self):
        self.enabled = True
        if self.is_printing:
            if not self.printing_timer:
                self.set_start_print_effect()
        self.run_if_enabled(self.now_effect if self.now_effect in ["extruder_heating", "bed_heating","extruder_bed_heating","paused", "set_led"] else "enabled")
        
    
    def _handle_disabled(self):
        self.enabled = False
        if self.timer:
            self.reset_timer()
        if self.printing_timer:
            self.reset_printing_timer()
        self.now_effect = "disabled"
        self.led_effect.stop_all_effects()
    
    def _handle_error(self):
        self.run_if_enabled("error")
            
    def _handle_led(self, red, green, blue):
        self.rgb = [red, green, blue]
        self.run_if_enabled("set_led")
    
    def _handle_stop_heating(self):
        self.run_if_enabled("stop_heating")
      
    def _handle_extruder_heating(self, heater_target_temp):
        self.last_ex_target = heater_target_temp
        self.run_if_enabled("extruder_heating")
        
    def _handle_bed_heating(self, heater_target_temp):
        self.last_hb_target = heater_target_temp
        self.run_if_enabled("bed_heating")
    
    def _handle_interrupt(self):
        self.run_if_enabled("interrupt")
    
    def _handle_paused(self):
        self.run_if_enabled("paused")
    
    def _handle_finish_error(self):
        self.run_if_enabled("print_error")
    
    def _handle_finish_cancelled(self):
        self.run_if_enabled("cancelled")
    
    def _handle_finish_complete(self):
        self.run_if_enabled("complete")
    
    def _handle_printing(self):
        self.run_if_enabled("printing")
    
    def run_if_enabled(self, event):
        if self.enabled and self.now_effect != event:
            if self.is_printing:
                if event in ["error", "print_error", "interrupt", "complete", "cancelled"]:
                    if event in ["print_error", "cancelled", "complete"]:
                        self.is_printing = False
                        self.paused = False
                        if self.printing_timer:
                            self.reset_printing_timer()   
                    if event != "cancelled":
                        self.create_ten_seconds_timer()
                    self.led_effect.run_effect(LED_EFFECTS[event])
                    self.now_effect = event
                elif event in ["paused", "printing", "extruder_bed_heating", "set_led"]:
                    if event == "set_led":
                        if not self.paused:
                            self.set_led_on_printing = True
                            self.led_effect.run_effect(LED_EFFECTS[event], self.rgb[0], self.rgb[1], self.rgb[2])
                    else:
                        self.paused = True if event == "paused" else False
                        self.led_effect.run_effect(LED_EFFECTS[event])
                    self.now_effect = event
            else:
                if event in ["error", "print_error", "interrupt", "complete"]:
                    self.create_ten_seconds_timer()
                elif self.timer:
                    self.reset_timer()
                    
                if event == "set_led":
                    self.led_effect.run_effect(LED_EFFECTS[event], self.rgb[0], self.rgb[1], self.rgb[2])
                    self.now_effect = event
                    return
                elif event in ["extruder_heating", "bed_heating"]:
                    if self.last_ex_target > 0 and self.last_hb_target > 0:
                        event = "extruder_bed_heating"
                    elif self.last_ex_target > 0:
                        event = "extruder_heating"
                    elif self.last_hb_target > 0:
                        event = "bed_heating"
                    else:
                        event = "enabled"
                self.now_effect = event
                self.led_effect.run_effect(LED_EFFECTS[self.now_effect])
    
    def get_status(self, eventtime):
        return {
            'led_status': self.now_effect,
            'enabled': self.enabled
        }

def load_config(config):
    return LedControl(config)