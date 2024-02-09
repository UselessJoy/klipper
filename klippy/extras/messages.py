import logging
import locales
class Messages:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.messages = {'warning': 
                            {'on_wait_temperature': _("Heating with the wait parameter, gcodes will be processed after warming up")},
                         'success': 
                            {},
                         'suggestion': 
                            {},
                         'error':
                             {},
                        }
        self.message_type = ""
        self.current_message = ""
        self.last_eventtime = None
        self.reactor = self.printer.get_reactor()
        self.open_timer = None
        webhooks = self.printer.lookup_object('webhooks')
        webhooks.register_endpoint("messages/open_message",
                                   self._handle_open_message)
    
    def _handle_open_message(self, web_request):
        self.last_eventtime = self.reactor.monotonic()
        self.message_type = web_request.get_str('message_type')
        m = web_request.get_str('message')
        self.current_message = self.messages[self.message_type][m]

    def get_status(self, eventtime):
        return {
            'last_message_eventtime': self.last_eventtime if self.last_eventtime else .0,
            'message': self.current_message,
            'message_type': self.message_type
        }
    
def load_config(config):
    return Messages(config)