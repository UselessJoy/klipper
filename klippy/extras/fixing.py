import logging
import os
import pathlib
import subprocess
from .fix_script import FixScript
import locales

class Fixing():
    def __init__(self, config):
        self.printer = config.get_printer()
        self.open_dialog = self.require_reboot = self.require_internet = self.has_uninstalled_updates = False
        self.scripts = None
        self.open_msg = _("Current update has system fixes. Please, wait for install") #no locale
        self.printer.register_event_handler("klippy:ready",
                                            self._handle_ready)
        webhooks = self.printer.lookup_object('webhooks')
        webhooks.register_endpoint("fixing/repeat_update",
                            self._repeat_update)
        webhooks.register_endpoint("fixing/close_dialog",
                            self._close_dialog)

    def _handle_ready(self):
        self.scripts: list[FixScript] = self.printer.lookup_objects(module = 'fix_script')
        self.update()

    def update(self):
        if all(script.fixed for name, script in self.scripts):
          return
        else:
           self.open_dialog = True
        for name, script in self.scripts:
            status = script.run_fix()
            if status == 2:
                self.open_msg = _("System updates requiring the internet. Please, connect to the internet") #no locale
                self.require_internet = True
                self.require_reboot = False
                break
            elif status:
              self.open_msg = _("Installed updates requiring reboot. Please, reboot the system") #no locale
              self.require_reboot = True
              self.require_internet = False
              break
        if all(script.fixed for name, script in self.scripts):
          self.open_msg = _("Fix updates was installed") #no locale
          if self.require_reboot:
             self.open_msg += '\n' + _("Installed updates requiring reboot. Please, reboot the system") #no locale
          self.has_uninstalled_updates = False
        else:
           self.has_uninstalled_updates = True

    def _repeat_update(self, web_request):
       self.update()

    def _close_dialog(self, web_request):
       self.open_dialog = False

    def get_status(self, eventtime=None):
        return {
            'has_uninstalled_updates': self.has_uninstalled_updates,
            'open_dialog': self.open_dialog,
            'dialog_message': self.open_msg,
            'require_internet': self.require_internet,
            'can_reboot': self.require_reboot
        }

def load_config(config):
    return Fixing(config)