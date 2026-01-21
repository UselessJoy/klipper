import logging
import socket
from .fix_script import FixScript
import locales

class Fixing():
    def __init__(self, config):
        self.printer = config.get_printer()
        self.require_reboot = config.getboolean('require_reboot', False)
        self.require_internet = config.getboolean('require_internet', False)
        self.is_updating = config.getboolean('is_updating', False)
        self.is_all_updated = config.getboolean('is_all_updated', False)
        self.scripts = None
        
        self.pivot_i = 0
        self.open_msg = _("Current update has system fixes. Install now?")
        self.printer.register_event_handler("klippy:ready",
                                            self._handle_ready)
        self.webhooks = self.printer.lookup_object('webhooks')
        self.webhooks.register_endpoint("fixing/repeat_update",
                            self._start_update)

    def _handle_ready(self):
        self.scripts: list[(str, FixScript)] = self.printer.lookup_objects(module = 'fix_script')
        self.is_all_updated = all(script.fixed for name, script in self.scripts)
        if self.is_all_updated:
            return
        if any(script.require_internet for _, script in self.scripts) and not self.has_internet():
            self.on_done(2)
            return

    def _start_update(self, web_request = None):
        self.require_internet = False
        self.require_reboot = False
        self.is_all_updated = all(script.fixed for name, script in self.scripts)
        if self.is_all_updated:
            web_request.send({'updating': False})
            return
        if any(script.require_internet for _, script in self.scripts) and not self.has_internet():
            self.on_done(2)
            web_request.send({'updating': False})
            return
        for i, (name, script) in enumerate(self.scripts):
            if not script.fixed:
                script.run_fix(self.on_message, self.on_done)
                self.pivot_i = i
                self.is_updating = True
                web_request.send({'updating': True})
                return
        web_request.send({'updating': False})

    def on_message(self, msg):
        self.open_msg += '\n' + msg

    def on_done(self, status):
        self.is_all_updated = all(script.fixed for name, script in self.scripts)
        self.require_internet = bool(status & 2)
        self.require_reboot = bool(status & 1)
        self.is_updating = not (status or self.is_all_updated)
        if status:
            if self.require_reboot:
                self.open_msg += '\n'+_("Installed updates requiring reboot. Please, reboot the system")
            elif self.require_internet:
                self.open_msg += '\n'+_("System updates requiring the internet. Please, connect to the internet")
            else:
                self.open_msg += '\n'+_("The update failed. Please check the logs")
            return
        if self.is_all_updated:
            self.open_msg += '\n'+_("Fix updates was installed")
            if self.require_reboot:
                self.open_msg += '\n' + _("Installed updates requiring reboot. Please, reboot the system")
        else:
            self.pivot_i += 1
            if self.pivot_i <= len(self.scripts) - 1:
                self.scripts[self.pivot_i][1].run_fix(self.on_message, self.on_done)

    def has_internet(self):
        try:
            host = socket.gethostbyname("one.one.one.one")
            s = socket.create_connection((host, 80), 3)
            s.close()
            return True
        except Exception as e:
            logging.exception(f"Exception on internet_access: {e}")
        return False
    
    def get_status(self, eventtime=None):
        return {
            'all_updated': self.is_all_updated,
            'dialog_message': self.open_msg,
            'require_internet': self.require_internet,
            'require_reboot': self.require_reboot,
            'updating': self.is_updating
        }

def load_config(config):
    return Fixing(config)