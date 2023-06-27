import gettext, pathlib, os, optparse, configparser, logging, sys
import logging
klipperpath = pathlib.Path(__file__).parent.resolve()
lang_path = os.path.join(klipperpath, "locales")
gettext.translation('Klipper', localedir=lang_path, languages=["en"], fallback=True).install()

def set_locale():
    config_file = sys.argv[1]
    config = configparser.ConfigParser()
    config.read(config_file)
    lang_list = [d for d in os.listdir(lang_path) if not os.path.isfile(os.path.join(lang_path, d))]
    lang_list.sort()
    langs = {}
    for lng in lang_list:
        langs[lng] = gettext.translation('Klipper', localedir=lang_path, languages=[lng], fallback=True)
    try:
        lang = config.get("locale", "lang")
        if lang in lang_list:
            langs[lang].install()
    except:
        return