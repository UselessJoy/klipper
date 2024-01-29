#from . import bus

#BACKGROUND_PRIORITY_CLOCK = 0x7fffffff00000000
import logging
import locales
class LedControl:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.print_stats = self.printer.lookup_object('print_stats')
        self.reactor = self.printer.get_reactor()
        self.timer = None
        self.printing_timer = None
        self.print_state = None
        self.rgb = [0,0,0]
        self.last_eventtime = None
        # self.clock = None
        # self.clock_eventtime = 0
        self.events = []
        self.now_event = ""
        self.now_effect = ""
        self.previous_event = ""
        self.is_printing = False
        self.paused = False
        self.enabled = True
        self.extruder = None
        self.heater_bed = None
        
        self.hb_temp = self.ex_temp = 0
        self.effects = {
            "error": "STOP_LED_EFFECTS\nSET_LED_EFFECT EFFECT=ERROR\n",
            "print_error": "STOP_LED_EFFECTS\nSET_LED_EFFECT EFFECT=ERROR\n",
            "set_led": "STOP_LED_EFFECTS\nSET_LED_EFFECT EFFECT=DEFAULT RED=%.3f GREEN=%.3f BLUE=%.3f\n",
            "extruder_heating": "STOP_LED_EFFECTS\nSET_LED_EFFECT EFFECT=EXTRUDER\n",
            "bed_heating": "STOP_LED_EFFECTS\nSET_LED_EFFECT EFFECT=HEATER_BED\n",
            "stop_heating": "STOP_LED_EFFECTS\nSET_LED_EFFECT EFFECT=DEFAULT\n",
            "extruder_bed_heating": "STOP_LED_EFFECTS\nSET_LED_EFFECT EFFECT=EXTRUDER_LEFT\nSET_LED_EFFECT EFFECT=BED_RIGHT\n",
            "disabled": "STOP_LED_EFFECTS\n",
            "enabled": "STOP_LED_EFFECTS\nSET_LED_EFFECT EFFECT=DEFAULT\n",
            "printing": "STOP_LED_EFFECTS\nSET_LED_EFFECT EFFECT=DEFAULT\n",
            "interrupt": "STOP_LED_EFFECTS\nSET_LED_EFFECT EFFECT=WARNING\n",
            "paused": "STOP_LED_EFFECTS\nSET_LED_EFFECT EFFECT=PAUSED\n",
            "cancelled": "STOP_LED_EFFECTS\nSET_LED_EFFECT EFFECT=DEFAULT\n",
            "complete": "STOP_LED_EFFECTS\nSET_LED_EFFECT EFFECT=COMPLETE\n",
        }
        self.gcode.register_command("DISABLE_LED_EFFECTS", self.cmd_DISABLE_LED_EFFECTS)
        self.gcode.register_command("ENABLE_LED_EFFECTS", self.cmd_ENABLE_LED_EFFECTS)
        self.printer.register_event_handler("klippy:ready",
                                            self._handle_control)
    #     self.clock = self.reactor.register_timer(
    #             self._on_clock, self.reactor.NOW) 

    # def _on_clock(self, eventtime):
    #     self.clock_eventtime = eventtime
    #     return eventtime + .1
    
    def cmd_DISABLE_LED_EFFECTS(self, gcmd):
        self._handle_disabled()
        #self.gcode.run_script_from_command(self.effects["disabled"])

    def cmd_ENABLE_LED_EFFECTS(self, gcmd):
        self._handle_enabled()
        #self.gcode.run_script_from_command(self.effects["enabled"])
    
    def _handle_control(self):
        self.extruder = self.printer.lookup_object('extruder')
        self.heater_bed = self.printer.lookup_object('heater_bed')
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
        # self.gcode.run_script_from_command(self.effects["extruder_bed_heating"])
        # self.now_effect = "extruder_bed_heating"
        # self.now_event = "extruder_bed_heating"
        # self.run_if_enabled("extruder_bed_heating")
        if not self.printing_timer:
            self.printing_timer = self.reactor.register_timer(
                    self.watch_for_printing, self.reactor.NOW)
    def watch_for_printing(self, eventtime):
        if self.enabled:
            if self.is_printing:
                logging.info("watch_for_printing")
                ex_temp, ex_target = self.extruder.get_heater().get_temp(eventtime)
                hb_temp, hb_target = self.heater_bed.get_heater().get_temp(eventtime)
                logging.info(f"ex_temp {self.extruder.get_heater().get_temp(eventtime)} hb_temp {self.heater_bed.get_heater().get_temp(eventtime)}")
                hb_warm = hb_temp >= hb_target if hb_target != 0 else False
                ex_warm = ex_temp >= ex_target if ex_target != 0 else False
                heater_bed_busy = self.heater_bed.get_heater().check_busy(eventtime) and not hb_warm
                extruder_busy = self.extruder.get_heater().check_busy(eventtime) and not ex_warm
                heaters_busy = heater_bed_busy or extruder_busy
                logging.info(f"default busy heater_bed {self.heater_bed.get_heater().check_busy(eventtime)} after all busy {heater_bed_busy}")
                logging.info(f"default busy extruder {self.heater_bed.get_heater().check_busy(eventtime)} after all busy {extruder_busy}")
                logging.info(f"paused {self.paused} effect {self.now_effect}")
                # Эффекты печати включаются только если закончились временные эффекты
                if not self.timer:
                    if self.paused:
                        if self.now_effect != "paused":
                            logging.info("paused run")
                            self.run_if_enabled("paused")
                    elif not heaters_busy and ex_target != 0 and hb_target != 0:
                        if self.now_effect != "printing":
                            logging.info("printing run")
                            self.run_if_enabled("printing")
                            return eventtime + 1
                    else:
                        if self.now_effect != "extruder_bed_heating":
                            logging.info("extruder_bed_heating run")
                            self.run_if_enabled("extruder_bed_heating")
                            return eventtime + 1
        else:
            if self.printing_timer:
                self.reset_printing_timer()
                return self.reactor.NEVER
        return eventtime + 1
    def reset_printing_timer(self):
        logging.info(f"reset_printing_timer")
        if self.printing_timer:
            self.reactor.unregister_timer(self.printing_timer)
            self.printing_timer = None
            
    def create_ten_seconds_timer(self):
        self.last_eventtime = None
        if self.timer is None:
            logging.info(f"if not timer")
            self.timer = self.reactor.register_timer(
                self._on_tick_timer, self.reactor.NOW) 
    def _on_tick_timer(self, eventtime):
        if not self.last_eventtime:
            self.last_eventtime = eventtime 
        logging.info(f"_on_tick_timer {eventtime} {self.last_eventtime}")
        pass_time = abs(eventtime - self.last_eventtime)
        if pass_time > 10:
            self.reset_timer()
            return self.reactor.NEVER
        return eventtime + 1
    def reset_timer(self):
        logging.info(f"reset timer from reset_timer")
        if self.timer:
            self.reactor.unregister_timer(self.timer)
            self.timer = None
            self.last_eventtime = None
            if not self.is_printing:
                    self.gcode.run_script_from_command(self.effects["enabled"])
                    self.now_effect = "enabled"
            # else:
            #     self.watch_for_printing(self.reactor.monotonic())
    
    
    
    def _handle_enabled(self):
        self.enabled = True
        if self.is_printing:
            if not self.printing_timer:
                self.set_start_print_effect()
        self.run_if_enabled(self.now_effect if self.now_effect in ["extruder_heating", "bed_heating","extruder_bed_heating","paused"] else "enabled")
        
    
    def _handle_disabled(self):
        self.enabled = False
        self.gcode.run_script_from_command(self.effects["disabled"])
        if self.timer:
            self.reset_timer()
        if self.printing_timer:
            self.reset_printing_timer()
        self.now_effect = "disabled"
    
    def _handle_error(self):
        self.run_if_enabled("error")
        
    def _handle_led(self, red, green, blue):
        self.rgb = [red, green, blue]
        self.run_if_enabled("set_led")
    
    def _handle_stop_heating(self):
        self.run_if_enabled("stop_heating")
      
    def _handle_extruder_heating(self, ex_temp):
        self.ex_temp = ex_temp
        self.run_if_enabled("extruder_heating")
        
    def _handle_bed_heating(self, hb_temp):
        self.hb_temp = hb_temp
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
        if self.enabled:
            self.now_event = event 
            logging.info(f"now event {self.now_event}")
            if self.is_printing:
                if event in ["error", "print_error", "interrupt", "complete", "cancelled"]:
                    if event in ["print_error", "cancelled", "complete"]:
                        self.is_printing = False
                        self.paused = False
                        if self.printing_timer:
                            logging.info(f"reset_printing_timer")
                            self.reset_printing_timer()   
                    logging.info(f"create_ten_seconds_timer in printing")
                    if event != "cancelled":
                        self.create_ten_seconds_timer()
                    self.gcode.run_script_from_command(self.effects[event])
                    self.now_effect = event
                elif event in ["paused", "printing", "extruder_bed_heating"]:
                    self.paused = True if event == "paused" else False
                    self.gcode.run_script_from_command(self.effects[event])
                    self.now_effect = event
            else:
                if event in ["error", "print_error", "interrupt", "complete"]:
                    logging.info(f"create_ten_seconds_timer in prostoi")
                    self.create_ten_seconds_timer()
                elif self.timer:
                    logging.info(f"reset_timer from main function")
                    self.reset_timer()
                    
                if event == "set_led":
                    logging.info(f"set_led rgb = {self.rgb[0], self.rgb[1], self.rgb[2]}")
                    self.gcode.run_script_from_command((self.effects[event] % (self.rgb[0], self.rgb[1], self.rgb[2])))
                    self.now_effect = event
                    return
                elif event in ["extruder_heating", "bed_heating"]:
                    if self.ex_temp != 0 and self.hb_temp != 0:
                        self.now_event = "extruder_bed_heating"
                    elif self.ex_temp != 0 or self.hb_temp != 0:
                        pass
                    else:
                        self.now_event = "enabled"
                self.gcode.run_script_from_command(self.effects[self.now_event])
                self.now_effect = self.now_event
                    
    
    
    
    
    
    
    
    # def _set_event(self, event):
    #     self.now_event = event
    #     buff = self.get_event()
    #     if buff != self.previous_event:
    #         self.previous_event = buff
    #     self.add_event(event)
    #     self.send_event = True
    
    # def add_event(self, event):
    #     if len(self.events) >= 1:
    #         return
    #     else:
    #         self.events.append(event)
    # def get_event(self):
    #     if len(self.events) >= 1:
    #         return self.events.pop()
    #     else:
    #         return self.previous_event
    
    
    def get_status(self, eventtime):
        return {
            'led_status': self.now_event,
            'enabled': self.enabled
        }
    
    
    
    
    
    
    
    
    # #If clicked on window in KlipperScreen or clicked in Fluidd, unrealized
    # def on_user_activity(self):
    #     self.reset_timer()
    # def _main_control(self, eventtime):
    #     if not self.enabled:
    #         if self.timer is not None:
    #             self.set_ledEffect_disabled(eventtime)
    #             self.reactor.unregister_timer(self.timer)
    #             self.timer = None
    #         return self.reactor.NEVER
    #     else:
    #         now_print_state = self.print_stats.get_status(eventtime)['state']
    #         self.target_bed = self.heater_bed.get_status(eventtime)['target']
    #         self.temp_bed = self.heater_bed.get_status(eventtime)['temperature']
    #         self.target_ex = self.extruder.get_status(eventtime)['target']
    #         self.temp_ex = self.extruder.get_status(eventtime)['temperature']
    #         if self.last_eventtime != None:
    #             self._control_eventtime(eventtime)
    #         if (self.send_event or self.print_state != now_print_state):
    #             self.send_event = False
    #             self.print_state = now_print_state
    #             # logging.info("IM IN CONTROL_SWITCH")
    #             self._control_switch(eventtime)
    #         return eventtime + 0.1
    
    # #print_state = "standby", "cancelled", "error", "complete", "interrupt", "printing"
    # def _control_switch(self, eventtime):
    #     if self.is_printing:
    #         self._printing_control(eventtime)
    #     else:
    #         self._standby_control(eventtime)

    # def _control_eventtime(self, eventtime):
    #     pass_time = abs(eventtime - self.last_eventtime)
    #     # logging.info("pass time " + str(pass_time))
    #     # logging.info("eventtime " + str(eventtime))
    #     # logging.info("last eventtime " + str(self.last_eventtime))
    #     # logging.info("is printing " + str(self.is_printing))
    #     # logging.info("now event " + str(self.now_event))
    #     # logging.info("print event " + str(self.print_state))
    #     if self.now_event == "error" and pass_time > 10:
    #         if self.rgb != None:
    #             self.set_ledEffect_set_led(eventtime)
    #         else:
    #             self.set_ledEffect_default(eventtime)
    #         self.now_event = "plug"
    #         return
    #     if not self.is_printing:
    #         if self.now_event == "set_led" or self.enabled:
    #             if self.target_bed != 0 and self.target_ex != 0 and pass_time > 10:
    #                 self.set_ledEffect_bed_extruder_heating(eventtime)
    #             elif self.target_ex != 0 and pass_time > 10:
    #                     self.set_ledEffect_extruder_heating(eventtime)
    #             elif self.target_bed != 0 and pass_time > 10:
    #                     self.set_ledEffect_heater_bed_heating(eventtime)
    #     else:
    #         if self.print_state == "printing":
    #             if self.now_event == "set_led" and pass_time > 10:
    #                     self.set_ledEffect_printing(eventtime)
    #                     self.now_event = "plug"
    #             if self.target_bed != 0 and self.target_ex != 0:
    #                 if not self.extruder.get_heater().check_busy(eventtime) and not self.heater_bed.get_heater().check_busy(eventtime):
    #                     if self.now_event != "printing":
    #                         self.set_ledEffect_printing(eventtime)
    #                         self.now_event = "printing"
    #             else:
    #                 if self.now_event != "heating":
    #                     self.set_ledEffect_bed_extruder_heating(eventtime)
    #         else:
    #             if self.print_state != "paused" and (self.print_state in self.state_dictionary):
    #                 if pass_time > 10:
    #                     if self.rgb != None:
    #                         self.set_ledEffect_set_led(eventtime)
    #                     else:
    #                         self.set_ledEffect_default(eventtime)
    #                     self.now_event = "plug"
    #                     self.is_printing = False
                    
                                  
    # def _standby_control(self, eventtime):
    #     # logging.info("IM in standby mode")
    #     if self.now_event == "error":
    #         self.set_ledEffect_error(eventtime)
    #     elif self.print_state == "interrupt":
    #         self.set_ledEffect_state(eventtime)
    #     elif self.now_event == "set_led" or self.enabled:
    #         if self.rgb != None:
    #             self.set_ledEffect_set_led(eventtime)
    #         else:
    #             self.set_ledEffect_default(eventtime)
    #     elif self.now_event == "heating":
    #         if self.target_bed != 0 and self.target_ex != 0:
    #             self.set_ledEffect_bed_extruder_heating(eventtime)
    #         elif self.target_bed != 0:
    #             self.set_ledEffect_heater_bed_heating(eventtime)
    #         elif self.target_ex != 0:
    #             self.set_ledEffect_extruder_heating(eventtime)
    #         else:
    #             if self.rgb != None:
    #                 self.set_ledEffect_set_led(eventtime)
    #             else:
    #                 self.set_ledEffect_default(eventtime)
    #     else:
    #         self.set_ledEffect_default(eventtime)
    
    # def _printing_control(self, eventtime):
    #     # logging.info("IM in printing mode")
    #     if self.print_state == "printing":
    #         if self.extruder.get_heater().check_busy(eventtime) or self.heater_bed.get_heater().check_busy(eventtime):
    #             self.set_ledEffect_bed_extruder_heating(eventtime)
    #         else:
    #             self.set_ledEffect_printing(eventtime)
    #     elif self.now_event == "set_led":
    #         self.set_ledEffect_set_led(eventtime)
    #     elif self.print_state in self.state_dictionary:
    #         self.set_ledEffect_state(eventtime)
    

def load_config(config):
    return LedControl(config)