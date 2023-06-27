import os, logging, pathlib
import locales

class AudioMessages:
    def __init__(self, config):
        self.printer = config.get_printer()
        klipperpath = pathlib.Path(__file__).parent.parent.resolve()
        self.audio_path = os.path.join(klipperpath, "audio_messages")
        self.audio_files = [file for file in os.listdir(self.audio_path) if os.path.isfile(os.path.join(self.audio_path, file))]
        self.audio_files.sort()
        self.audio_collection = {"begin_print" : self.audio_files[0], 
                                 "end_print" : self.audio_files[1],
                                 "error" : self.audio_files[2],
                                 "hello" : self.audio_files[3],
                                 "poweroff" : self.audio_files[4]}
        self.print_stats = self.printer.load_object(config, 'print_stats')
        self.reactor = self.printer.get_reactor()
        self.timer = None
        self.state = ""
        self.state_mass = ["interrupt","paused","cancelled","complete","error"]
        self.printer.register_event_handler("klippy:error", self._handle_error)
        self.printer.register_event_handler("klippy:shutdown", self._handle_shutdown)
        self.printer.register_event_handler("klippy:firmware_restart", self._handle_restart)
        self.printer.register_event_handler("klippy:ready",
                                            self._handle_ready)
        
        
    def _handle_ready(self):
        os.system("aplay %s" % (os.path.join(self.audio_path, self.audio_collection["hello"])))
        self.printer.register_event_handler("gcode:command_error", self._handle_error)
        self.timer = self.reactor.register_timer(self._audio_control, self.reactor.NOW)
    
    def _handle_error(self):
        logging.info("error %s" % (self.audio_collection["error"]))
        os.system("aplay %s" % (os.path.join(self.audio_path, self.audio_collection["error"])))
    def _handle_shutdown(self):
        logging.info("shutdown %s" % (self.audio_collection["poweroff"]))
        os.system("aplay %s" % (os.path.join(self.audio_path, self.audio_collection["poweroff"])))
    def _handle_restart(self):
        logging.info("restart %s" % (self.audio_collection["poweroff"]))
        os.system("aplay %s" % (os.path.join(self.audio_path, self.audio_collection["poweroff"])))
        
    def _audio_control(self, eventtime):
        now_print_state = self.print_stats.get_status(eventtime)['state']
        if now_print_state != self.state:
            if now_print_state == "printing":
                os.system("aplay %s" % (os.path.join(self.audio_path, self.audio_collection["begin_print"])))
            elif now_print_state == "complete":
                os.system("aplay %s" % (os.path.join(self.audio_path, self.audio_collection["end_print"])))
            elif now_print_state == "error":
                os.system("aplay %s" % (os.path.join(self.audio_path, self.audio_collection["error"])))
        self.state = now_print_state
        return eventtime + .1
    
def load_config(config):
    return AudioMessages(config)
