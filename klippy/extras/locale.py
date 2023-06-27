import gettext, pathlib, os
import logging


class Locale:
    def __init__(self, config):
        self.printer = printer = config.get_printer()
        webhooks = self.printer.lookup_object('webhooks')
        
        self.currnetLang = config.get('lang', None)
        klipperpath = pathlib.Path(__file__).parent.parent.resolve()
        self.lang_path = os.path.join(klipperpath, "locales")
        self.lang_list = [d for d in os.listdir(self.lang_path) if not os.path.isfile(os.path.join(self.lang_path, d))]
        self.lang_list.sort()
        self.langs = {}
        for lng in self.lang_list:
                self.langs[lng] = gettext.translation('Klipper', localedir=self.lang_path, languages=[lng], fallback=True)
        logging.info(str(klipperpath))
        logging.info(str(self.lang_list))
        printer.register_event_handler("klippy:connect", self._handle_ready)
        webhooks.register_endpoint("locale/set_lang",
                                   self._handle_set_lang)
        
        self.lang_dict = []
        for lng in self.lang_list:
            self.lang_dict.append({"name": lng, "code": lng})
              
    def _handle_ready(self):
        if self.currnetLang is None or self.currnetLang not in self.lang_list:
            gettext.translation('Klipper', localedir=self.lang_path, languages=["en"], fallback=True).install()
        else:
            gettext.translation('Klipper', localedir=self.lang_path, languages=[self.currnetLang], fallback=True).install()
        
    def _handle_set_lang(self, web_request):
        lang = web_request.get_str('lang')
        logging.info(f"Get lang {lang}")
        if lang is None:
            lang = "en"
        if lang is not None and lang not in self.lang_list:
            # try to match a parent
            for language in self.lang_list:
                if lang.startswith(language):
                    lang = language
        if lang not in self.lang_list:
            logging.error(f"lang: {lang} not found")
            logging.info(f"Available lang list {self.lang_list}")
            lang = "en"
        logging.info(f"Using lang {lang}")
        self.currnetLang = lang
        try:
            self.langs[lang].install()
            logging.info(f"Install lang {lang}")
            self.rewrite_locale(lang)
        except:
            logging.error("Cannot set a new lang")
            return

    def rewrite_locale(self, lang):
        cfgname = self.printer.get_start_args()['config_file']
        with open(cfgname, 'r+') as file:
            lines = file.readlines()
            i = 0
            for line in enumerate(lines):
                if line[1].lstrip().startswith('lang'):
                    lines[i] = f' lang = {lang}\n'
                    logging.info(f"Lang changed to {lang}")
                    break
                i+=1
            end_lines = lines
        with open(cfgname, 'w') as file:
            file.writelines(end_lines)
            
    def get_status(self, eventtime):
        return {
            'langs': self.lang_dict,
            'currentLang': self.currnetLang
        }

def load_config(config):
    return Locale(config)