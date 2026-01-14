import locales
from configfile import ConfigWrapper, PrinterConfig

ALL_OPEN    = 0x00
DOOR_OPEN   = 0x01
HOOD_OPEN   = 0x02
ALL_PRESSED = 0x03

class SafetyPrinting:  
    def __init__(self, config: ConfigWrapper):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object("gcode")
        self.safety_enabled = config.getboolean("safety_enabled")
        self.show_respond = config.getboolean("show_respond", False)
        self.luft_timeout = config.getfloat("luft_timeout")
        self.luft_overload = False
        self.reactor = self.printer.get_reactor()
        self.last_state  = None
        self.first_run = True
        self.endstops_state = ALL_OPEN
        self.past_state = None
        self.luft_timer = None
        self.last_eventtime = self.last_eventtime = 0
        self.send_pause = self.send_resume = False
        self.messages = None
        self.pause_timer = self.resume_timer = None
        self.pause_command_running = False
        self.pause_command_eventtime = 0
        self.resume_command_running = False
        self.resume_command_eventtime = 0
        self.last_touch_timer = None
        self.vsd = self.pause_resume = None
        buttons = self.printer.load_object(config, "buttons")
        doors_pin = config.get("doors_pin")
        hood_pin = config.get("hood_pin")
        buttons.register_buttons([doors_pin, hood_pin], self._timewall)
        webhooks = self.printer.lookup_object("webhooks")
        webhooks.register_endpoint("safety_printing/set_safety_printing",
                                   self._handle_set_safety_printing)
        webhooks.register_endpoint("safety_printing/set_luft_timeout",
                                   self._handle_set_luft_timeout)
        # self.printer.register_event_handler("print_stats:printing", self._handle_printing)
        # self.printer.register_event_handler("print_stats:paused", self._handle_paused)

        # self.printer.register_event_handler("print_stats:interrupt", self._handle_clear_pause_resume)
        # self.printer.register_event_handler("print_stats:cancelled", self._handle_clear_pause_resume)
        # self.printer.register_event_handler("print_stats:complete", self._handle_clear_pause_resume)
        # self.printer.register_event_handler("print_stats:error", self._handle_clear_pause_resume)
        self.printer.register_event_handler("klippy:ready", self._on_ready)

    def reset_last_touch_timer(self):
        if self.last_touch_timer:
          self.reactor.unregister_timer(self.last_touch_timer)
          self.last_touch_timer = None

    def reset_pause_timer(self):
      if self.pause_timer:
          self.reactor.unregister_timer(self.pause_timer)
          self.pause_timer = None

    def reset_resume_timer(self):
      if self.resume_timer:
          self.reactor.unregister_timer(self.resume_timer)
          self.resume_timer = None

    def get_endstops_state(self):
        return self.endstops_state

    def _on_ready(self):
        self.messages = self.printer.lookup_object("messages")
        self.vsd = self.printer.lookup_object("virtual_sdcard")
        self.print_stats = self.printer.lookup_object("print_stats")
        self.pause_resume = self.printer.lookup_object('pause_resume')

    # Наивное решение проблемы с быстрым переключением (внутренняя реализация добавляет асинхронные коллбэки через put_nowait в модуле reactor)
    def _timewall(self, eventtime, state):
        if self.first_run:
            self.endstops_state = state
            self.first_run = False
        self.reset_last_touch_timer()
        self.last_state = state
        self.last_eventtime = eventtime
        self.last_touch_timer = self.reactor.register_timer(self.is_last_touch, self.reactor.NOW)

    def is_last_touch(self, eventtime):
        # is last touch => true
        if eventtime - self.last_eventtime > self.luft_timeout:
            # if self.resume_command_running or self.pause_command_running:
            #     return eventtime + 1
            if self.last_state != self.endstops_state:
              self.on_state_change(self.last_state)
            self.reset_last_touch_timer()
            return self.reactor.NEVER
        return eventtime + .1

    def on_state_change(self, state):
        # self.reset_luft_timer()# Сброс таймера
        self.printer.send_event("safety_printing:endstops_state", state)
        self.endstops_state = state
        # Не обрабатываем, если безопасная печать отключена или принтер не в состоянии печати или если принтер поставлен на паузу вручную
        if not (self.safety_enabled and self.print_stats.state in ["paused", "printing"]) or self.pause_resume.manual_pause:
            return
        if state != ALL_PRESSED:
            if self.pause_timer:
                self.reactor.unregister_timer(self.pause_timer)
                self.pause_timer = None
            self.pause_timer = self.reactor.register_timer(self.await_pause, self.reactor.NOW)
            # self.do_pause()
        else:
            if self.resume_timer:
                self.reactor.unregister_timer(self.resume_timer)
                self.resume_timer = None
            self.resume_timer = self.reactor.register_timer(self.await_resume, self.reactor.NOW)
            # self.do_resume()

    def await_pause(self, eventtime):
        if self.pause_resume.is_paused:
            return eventtime + .1
        self.gcode.run_script("PAUSE")
        return self.reactor.NEVER

    def await_resume(self, eventtime):
        if not self.pause_resume.is_paused:
            return eventtime + .1
        self.gcode.run_script("RESUME")
        return self.reactor.NEVER

    # def do_pause(self):
    #     # Он не делает нормальную паузу, потому что считает, что пока идет ретракт, он находится в паузе 
    #     if not self.pause_resume.is_paused:
    #       self.gcode.run_script_from_command("PAUSE")
    #       self.pause_command_running = True
    #       self.pause_command_eventtime = self.reactor.monotonic()
    #       self.pause_timer = self.reactor.register_timer(self.pause_running, self.reactor.NOW)

    # def pause_running(self, eventtime):
    #     if eventtime - self.pause_command_eventtime > 10:
    #         self.pause_command_running = False
    #         self.reset_pause_timer()
    #         return self.reactor.NEVER
    #     return eventtime + 1

    # def do_resume(self):
    #     if self.pause_resume.is_paused:
    #       self.gcode.run_script_from_command("RESUME")
    #       self.resume_command_running = True
    #       self.resume_command_eventtime = self.reactor.monotonic()
    #       self.resume_timer = self.reactor.register_timer(self.resume_running, self.reactor.NOW)

    # def resume_running(self, eventtime):
    #     if eventtime - self.resume_command_eventtime > 10:
    #         self.resume_command_running = False
    #         self.reset_resume_timer()
    #         return self.reactor.NEVER
    #     return eventtime + 1

    # Дерьмово, что и сообщение тоже показывает
    def is_open(self):
        if self.safety_enabled:
          if self.endstops_state == ALL_PRESSED:
              return False
          if self.endstops_state == DOOR_OPEN:
              self.messages.send_message("error", _("Printing is paused. Must close hood"))
          elif self.endstops_state == HOOD_OPEN:
              self.messages.send_message("error", _("Printing is paused. Must close doors"))
          else:
              self.messages.send_message("error", _("Printing is paused. Must close doors and hood"))
          return True
        return False

    def respond_status(self):
        if self.endstops_state == ALL_PRESSED:
            self.gcode.respond_info(_("All closed"))
        elif self.endstops_state == DOOR_OPEN:
            self.gcode.respond_info(_("Hood open"))
        elif self.endstops_state == HOOD_OPEN:
            self.gcode.respond_info(_("Doors are open"))
        else:
            self.gcode.respond_info(_("Doors and hood are open"))

    def _handle_set_safety_printing(self, web_request):
        self.safety_enabled: bool = web_request.get_boolean('safety_enabled')
        configfile: PrinterConfig = self.printer.lookup_object('configfile')
        safety_section = {"safety_printing": {"safety_enabled": self.safety_enabled}}
        configfile.update_config(setting_sections=safety_section, save_immediatly=True)
 
    def _handle_set_luft_timeout(self, web_request):
        self.luft_timeout: float = web_request.get_float('luft_timeout')
        configfile: PrinterConfig = self.printer.lookup_object('configfile')
        safety_section = {"safety_printing": {"luft_timeout": self.luft_timeout}}
        configfile.update_config(setting_sections=safety_section, save_immediatly=True)

    def get_status(self, eventtime):
        return {
                'safety_enabled': self.safety_enabled,
                'is_doors_open': bool(not(self.endstops_state & DOOR_OPEN) & DOOR_OPEN),
                'is_hood_open': bool(not(self.endstops_state & HOOD_OPEN) & HOOD_OPEN),
                'luft_timeout': self.luft_timeout,
                'luft_overload': self.luft_overload,
                'show_respond': self.show_respond
                }

def load_config(config):
    return SafetyPrinting(config)