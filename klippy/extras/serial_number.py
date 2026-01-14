from configfile import PrinterConfig

class SerialNumber():
    def __init__(self, config):
        self.printer = config.get_printer()
        self.serial_number = config.get("serial_number", None)
        self.scripts = None
        webhooks = self.printer.lookup_object('webhooks')
        webhooks.register_endpoint("serial/get_serial",
                            self.get_serial)
        webhooks.register_endpoint("serial/set_serial",
                            self.set_serial)

    def get_serial(self, web_request):
        web_request.send({'serial_number': self.serial_number})
    
    def set_serial(self, web_request):
        try:
          sn = web_request.get("serial_number", None)
          if not sn:
              return
        except:
            return
        configfile: PrinterConfig = self.printer.lookup_object('configfile')
        serial_section = {"serial_number": {"serial_number": sn}}
        configfile.update_config(setting_sections=serial_section, save_immediatly=True)
        self.serial_number = sn

    def get_status(self, eventtime):
        return { 'serial_number': self.serial_number }

def load_config(config):
    return SerialNumber(config)