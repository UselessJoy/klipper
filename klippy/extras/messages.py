import logging
import locales
class Messages:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.is_open = False
        self.messages = {   'warning': 
                            {   
                                'on_wait_temperature': _("Heating with the wait parameter, gcodes will be processed after warming up"),
                                'on_open_door_or_hood': _("Printing is paused. Printing will be continued after closing doors and hood"), ## locale
                                'wait_for_pause': _("Printer already go to pause"),
                                'wait_for_resume': _("Printer already wait go to resume")
                            },
                            'success': 
                            {'saved_default_color': _("New default color was successfully saved!")
                             },
                            'suggestion': 
                            {},
                            'error':
                            {},
                        }
        self.message_type = ""
        self.current_message = ""
        self.last_eventtime = None
        self.reactor = self.printer.get_reactor()
        webhooks = self.printer.lookup_object('webhooks')
        webhooks.register_endpoint("messages/open_message",
                                   self._handle_open_message)
    
    def _handle_open_message(self, web_request):
        message_type = web_request.get_str('message_type')
        message = web_request.get_str('message')
        self.send_message(message_type, message)
        
    def send_message(self, message_type, message):
        self.message_type = message_type
        self.last_eventtime = self.reactor.monotonic()
        self.current_message = self.messages[self.message_type][message]
        self.reset_open_timer()
        self.timer = self.reactor.register_timer(self.open_timer, self.reactor.NOW)
        self.is_open = True    
        
    def open_timer(self, eventtime):
        if abs(eventtime - self.last_eventtime) > 10:
            self.reset_open_timer()
            return self.reactor.NEVER
        return eventtime + 1

    def reset_open_timer(self):
        self.is_open = False
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