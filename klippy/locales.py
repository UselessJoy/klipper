import gettext, pathlib, os


locale = 'en'
klipperpath = pathlib.Path(__file__).parent.resolve().parent
lang_path = os.path.join(klipperpath, "klippy", "locales")
lang_list = []
langs = {}
el = gettext.translation('Klipper', localedir=lang_path, languages=[locale], fallback=True)
el.install()


def set_locale():
    locale = 'ru'
    el = gettext.translation('Klipper', localedir=lang_path, languages=[locale], fallback=False)
    el.install()
      #  def create_translations(self):
       #     self.lang_list = [d for d in os.listdir(self.lang_path) if not os.path.isfile(os.path.join(self.lang_path, d))]
       ##     self.lang_list.sort()
        #    for lng in self.lang_list:
        #        self.langs[lng] = gettext.translation('KlipperScreen', localedir=self.lang_path, languages=[lng], fallback=True)
        #    lang = self.config.get('locale', 'en')
        #    self.install_language(lang)

   # def install_language(self, lang):
  #      if lang is None or lang == "system_lang":
  #          for language in self.lang_list:
  #              if locale.getdefaultlocale()[0].startswith(language):
  #                  logging.debug("Using system lang")
        #             lang = language
        # if lang is not None and lang not in self.lang_list:
        #     # try to match a parent
        #     for language in self.lang_list:
        #         if lang.startswith(language):
        #             lang = language
        #             self.set("main", "language", lang)
        # if lang not in self.lang_list:
        #     logging.error(f"lang: {lang} not found")
        #     logging.info(f"Available lang list {self.lang_list}")
        #     lang = "en"
        # logging.info(f"Using lang {lang}")
        # self.lang = self.langs[lang]
        # self.lang.install(names=['gettext', 'ngettext'])


        #  def set_lang(cfgname):
        #         with open(cfgname, 'r') as file:
        #                 lines = file.readlines()
        #                 i = 0
        #                 lang_found = False
        #                 for line in enumerate(lines):
        #                     if line[1].lstrip().startswith('[language]'):
        #                         while not lines[i].lstrip().startswith('locale'):
        #                             i+=1
        #                         locale = lines[i][8:]
        #                         el = gettext.translation('')
        #                         i = i - line[0]
        #                         lang_found = True
        #                     if lang_found:
        #                         break
        #                     i+=1