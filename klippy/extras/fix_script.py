import os
import pathlib
import subprocess
import logging
import socket
from configfile import PrinterConfig
import locales

class FixScript:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.fixed = config.getboolean('fixed', False)
        self.require_internet = config.getboolean('require_internet', False)
        self.last_done = config.getint('last_done', -1)
        self.script_dir = config.get_name().split()[-1]
        klipperpath = pathlib.Path(__file__).parent.parent.parent.resolve()
        self.scriptpath = os.path.join(klipperpath, f"scripts/fix/{self.script_dir}")

    # Проблема: в скриптах для проверки перезагрузки используется exit, что является полным калом
    # Нужно переделать на статусы с ошибками - для перезагрузки использовать что-то другое
    def run_fix(self):
        if not self.fixed:
          sorted_dir = sorted(os.listdir(self.scriptpath))
          f_nums = [int(f[:2]) for f in sorted_dir if f.endswith('.sh')]
          max_num = f_nums[-1]
          for script in sorted_dir:
              if int(script[:2]) > self.last_done:  # Предполагается, что в директории скрипта будут только скрипты
                                                    # формата NN_name.sh, где NN - порядковый номер выполнения скрипта 
                  if self.require_internet and not self.has_internet():
                      return 2
                  logging.info(f"run {script}")
                  must_reboot = subprocess.call([self.scriptpath + '/' + script])
                  self.last_done = int(script[:2])
                  
                  if self.last_done >= max_num:
                      self.fixed = True
                  configfile: PrinterConfig = self.printer.lookup_object('configfile')
                  fix_script_section = {f"fix_script {self.script_dir}": {"last_done": self.last_done, "fixed": self.fixed}}
                  configfile.update_config(setting_sections=fix_script_section)
                  if must_reboot:
                      return 1
        return 0

    def has_internet(self):
        try:
          host = socket.gethostbyname("one.one.one.one")
          s = socket.create_connection((host, 80), 3)
          s.close()
          return True
        except Exception as e:
          logging.exception(f"Exception on internet_access: {e}")
        return False
def load_config_prefix(config):
    return FixScript(config)
