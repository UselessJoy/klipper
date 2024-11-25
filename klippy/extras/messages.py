import logging
import locales
class Messages:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.is_open = False
        self.timer = None
        self.webhooks_messages = {   'warning': 
                            {   
                                'on_wait_temperature': _("Heating with the wait parameter, gcodes will be processed after warming up"),
                                'on_open_door_and_hood': _("Printing is paused. Printing will be continued after closing doors and hood"),
                                'wait_for_pause': _("Printer already go to pause"),
                                'wait_for_resume': _("Printer already wait go to resume"),
                                'has_next_screw': _("Cannot set new calibrating screw: already wait move to next screw %s")
                            },
                            'success': 
                            {'saved_default_color': _("New default color was successfully saved"),
                             'successfull_save_bed_mesh': _("Bed mesh was successfull saved!"),
                             },
                            'suggestion': 
                            {
                                
                            },
                            'error':
                            {
                                "calibration_not_perfomed": _("Cannot set new calibrating screw: calibration is not performed"),
                                "screw_not_defined": _("Cannot set new calibrating screw: screw is not defined"),
                                "screw_is_base_screw": _("Cannot set new calibrating screw: screw %s is base screw"),
                                "screw_not_in_screws": _("Cannot set new calibrating screw: screw %s not in defined screws")
                            },
                        }
        self.message_type = ""
        self.current_message = ""
        self.last_eventtime = None
        self.reactor = self.printer.get_reactor()
        webhooks = self.printer.lookup_object('webhooks')
        webhooks.register_endpoint("messages/open_message",
                                   self._handle_open_message)
        webhooks.register_endpoint("messages/close_message",
                                   self.reset_open_timer)
    
    def _handle_open_message(self, web_request):
        message_type = web_request.get_str('message_type')
        message = web_request.get_str('message')
        self.send_message(message_type, message, True)
        
    def send_message(self, message_type, message, from_webhooks=False, respond=True):
        self.reset_open_timer()
        self.message_type = message_type
        self.last_eventtime = self.reactor.monotonic()
        if from_webhooks:
            self.current_message = self.webhooks_messages[self.message_type][message]
        else:
            self.current_message = message
        self.timer = self.reactor.register_timer(self.open_timer, self.reactor.NOW)
        self.is_open = True
        if respond:
          self.gcode.respond_msg(self.current_message, f"({self.message_type})", True)
        
    def open_timer(self, eventtime):
        if abs(eventtime - self.last_eventtime) > 10:
            self.reset_open_timer()
            return self.reactor.NEVER
        return eventtime + 1

    def reset_open_timer(self, web_request=None):
        self.is_open = False
        self.message_type = ""
        self.current_message = ""
        if self.timer:
            self.reactor.unregister_timer(self.timer)
            self.timer = None

    def get_status(self, eventtime):
        return {
            'last_message_eventtime': self.last_eventtime if self.last_eventtime else .0,
            'message': self.current_message,
            'message_type': self.message_type,
            'is_open': self.is_open
        }
    
def load_config(config):
    return Messages(config)