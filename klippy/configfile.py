# Code for reading and writing the Klipper config file
#
# Copyright (C) 2016-2021  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations
import sys, os, glob, re, time, logging, configparser, io
#from klippy import Printer
import locales
locales.set_locale()
error = configparser.Error
SECTIONS_DEPRECATED = ['motor_checker', 'wifi_mode']
SECTIONS_FULL_REWRITABLE = []
SECTIONS_SKIPPED = ['include']
SECTIONS_SKIPPED = []#['include']
#section -> deprecated option -> new option with def value
SECTIONS_CHANGED = {
    'printer': {
        'remove_option': ['max_accel_to_decel'],
        'add_option': {'minimum_cruise_ratio': 0.5}
    },
    'tmc2209 stepper_x': {
        'remove_option': ['hold_current'],
        'add_option': {'run_current': 1}
    },
    'tmc2209 stepper_y': {
      'remove_option': ['hold_current'],
      'add_option': {'run_current': 1}
    },
    'tmc2209 stepper_z': {
      'remove_option': ['hold_current'],
      'add_option': {'run_current': 1}
    },
    'tmc2209 extruder': {
      'remove_option': ['hold_current'],
      'add_option': {'run_current': 1}
    },
    'fan_back back': {
        'add_option': {
            'mode': 'config',
            'temp_30': 0.45,
            'temp_40': 0.55,
            'temp_50': 0.65,
            'temp_65': 0.75,
        }
    }
}

class sentinel:
    pass

class ConfigParser(configparser.RawConfigParser):
    def write(self, filename):
        if self._defaults:
            filename.write("[%s]\n" % DEFAULTSECT)
            for (key, value) in self._defaults.items():
                filename.write("%s : %s\n" % (key, str(value).replace('\n', '\n\t')))
            filename.write('\n')
        for section in self._sections:
            filename.write("[%s]\n" % section)
            for (key, value) in self._sections[section].items():
                if key != "__name__":
                    sval = str(value).replace('\n', '\n\t').replace('\t(tab)', '#').replace('@', '#')
                    filename.write("%s: %s\n" %
                             (key, sval))
            filename.write('\n')


class ConfigWrapper:
    error = configparser.Error
    def __init__(self, printer, fileconfig : ConfigParser, access_tracking, section):
        self.printer = printer
        self.fileconfig = fileconfig
        self.access_tracking = access_tracking
        self.section = section

    def get_printer(self):
        return self.printer

    def get_name(self):
        return self.section

    def _get_wrapper(self, parser, option, default, minval=None, maxval=None,
                     above=None, below=None, note_valid=True):
        if not self.fileconfig.has_option(self.section, option):
            if default is not sentinel:
                if note_valid and default is not None:
                    acc_id = (self.section.lower(), option.lower())
                    self.access_tracking[acc_id] = default
                return default
            raise error(_("Option '%s' in section '%s' must be specified")
                        % (option, self.section))
        try:
            v = parser(self.section, option)
        except self.error as e:
            raise
        except:
            raise error(_("Unable to parse option '%s' in section '%s'")
                        % (option, self.section))
        if note_valid:
            self.access_tracking[(self.section.lower(), option.lower())] = v
        if minval is not None and v < minval:
            raise error(_("Option '%s' in section '%s' must have minimum of %s")
                        % (option, self.section, minval))
        if maxval is not None and v > maxval:
            raise error(_("Option '%s' in section '%s' must have maximum of %s")
                        % (option, self.section, maxval))
        if above is not None and v <= above:
            raise error(_("Option '%s' in section '%s' must be above %s")
                        % (option, self.section, above))
        if below is not None and v >= below:
            raise self.error(_("Option '%s' in section '%s' must be below %s")
                             % (option, self.section, below))
        return v

    def get(self, option, default=sentinel, note_valid=True):
        return self._get_wrapper(self.fileconfig.get, option, default,
                                 note_valid=note_valid)

    def getint(self, option, default=sentinel, minval=None, maxval=None,
               note_valid=True):
        return self._get_wrapper(self.fileconfig.getint, option, default,
                                 minval, maxval, note_valid=note_valid)

    def getfloat(self, option, default=sentinel, minval=None, maxval=None,
                 above=None, below=None, note_valid=True):
        return self._get_wrapper(self.fileconfig.getfloat, option, default,
                                 minval, maxval, above, below,
                                 note_valid=note_valid)

    def getboolean(self, option, default=sentinel, note_valid=True):
        return self._get_wrapper(self.fileconfig.getboolean, option, default,
                                 note_valid=note_valid)

    def getchoice(self, option, choices, default=sentinel, note_valid=True):
        if type(choices) == type([]):
            choices = {i: i for i in choices}
        if choices and type(list(choices.keys())[0]) == int:
            c = self.getint(option, default, note_valid=note_valid)
        else:
            c = self.get(option, default, note_valid=note_valid)
        if c not in choices:
            raise error(_("Choice '%s' for option '%s' in section '%s'"
                        " is not a valid choice") % (c, option, self.section))
        return choices[c]

    def getlists(self, option, default=sentinel, seps=(',',), count=None,
                 parser=str, note_valid=True):
        def lparser(value, pos):
            if pos:
                # Nested list
                parts = [p.strip() for p in value.split(seps[pos])]
                return tuple([lparser(p, pos - 1) for p in parts if p])
            res = [parser(p.strip()) for p in value.split(seps[pos])]
            if count is not None and len(res) != count:
                raise error(_("Option '%s' in section '%s' must have %d elements")
                            % (option, self.section, count))
            return tuple(res)
        def fcparser(section, option):
            return lparser(self.fileconfig.get(section, option), len(seps) - 1)
        return self._get_wrapper(fcparser, option, default,
                                 note_valid=note_valid)

    def getlist(self, option, default=sentinel, sep=',', count=None,
                note_valid=True):
        return self.getlists(option, default, seps=(sep,), count=count,
                             parser=str, note_valid=note_valid)

    def getintlist(self, option, default=sentinel, sep=',', count=None,
                   note_valid=True):
        return self.getlists(option, default, seps=(sep,), count=count,
                             parser=int, note_valid=note_valid)

    def getfloatlist(self, option, default=sentinel, sep=',', count=None,
                     note_valid=True):
        return self.getlists(option, default, seps=(sep,), count=count,
                             parser=float, note_valid=note_valid)

    def getsection(self, section) -> ConfigWrapper:
        return ConfigWrapper(self.printer, self.fileconfig,
                             self.access_tracking, section)

    def getoptions(self) -> list[str]:
        return self.fileconfig.options(self.section)

    def has_section(self, section: str) -> bool:
        return self.fileconfig.has_section(section)

    def has_option(self, section: str, option) -> bool:
        return self.fileconfig.has_option(section, option)

    def get_prefix_sections(self, prefix) -> list[ConfigWrapper]:
        return [self.getsection(s) for s in self.fileconfig.sections()
                if s.startswith(prefix)]

    def get_prefix_options(self, prefix):
        return [o for o in self.fileconfig.options(self.section)
                if o.startswith(prefix)]

    def deprecate(self, option, value=None):
        if not self.fileconfig.has_option(self.section, option):
            return
        if value is None:
            msg = (_("Option '%s' in section '%s' is deprecated.")
                   % (option, self.section))
        else:
            msg = (_("Value '%s' in option '%s' in section '%s' is deprecated.")
                   % (value, option, self.section))
        pconfig = self.printer.lookup_object("configfile")
        pconfig.deprecate(self.section, option, value, msg)

AUTOSAVE_HEADER = """
#*# <---------------------- SAVE_CONFIG ---------------------->
#*# DO NOT EDIT THIS BLOCK OR BELOW. The contents are auto-generated.
#*#
"""

class PrinterConfig:
    # Поиск новой строки
    line_r = re.compile('\n')
    # Поиск комментариев в опции
    comment_value_option_r = re.compile('@')
    # Поиск знака комментария отдельно
    comment_symbol_r = re.compile('[#;]')
    # Поиск части строки после символа '#', включая сам символ
    comment_r = re.compile('[#;].*$')
    # Поиск части строки, соответствующей значению поля (после любого знака, не входящего в список)
    value_r = re.compile('[^A-Za-z0-9_].*$')
    def __init__(self, printer):
        self.printer = printer
        self.autosave = None
        self.deprecated = {}
        self.status_raw_config = {}
        self.pendingSaveItems = {}
        self.status_remove_sections = []
        self.status_settings = {}
        self.status_warnings = []
        self.haveUnsavedChanges = False
        gcode = self.printer.lookup_object('gcode')
        webhooks = printer.lookup_object('webhooks')
        webhooks.register_endpoint("configfile/load_backup_config",
                             self._load_backup_config)
        webhooks.register_endpoint("configfile/check_backup",
                             self._find_backup)
        webhooks.register_endpoint("configfile/return_base_config",
                             self._load_base_config_with_options)
        gcode.register_command("SAVE_CONFIG", self.cmd_SAVE_CONFIG,
                               desc=self.cmd_SAVE_CONFIG_help)
        gcode.register_command("BACKUP_CONFIG", self.cmd_BACKUP_CONFIG,
                               desc=self.cmd_BACKUP_CONFIG_help)

    def get_printer(self):
        return self.printer

    def _read_config_file(self, filename) -> str:
        try:
            f = open(filename, 'r')
            data = f.read()
            f.close()
        except:
            msg = _("Unable to open config file %s") % (filename,)
            logging.exception(msg)
            raise error(msg)
        return data.replace("\r\n", '\n')

    def _find_autosave_data(self, data: str) -> tuple[str, str]:
        regular_data = data
        autosave_data = ""
        pos = data.find(AUTOSAVE_HEADER)
        if pos >= 0:
            regular_data = data[:pos]
            autosave_data = data[pos + len(AUTOSAVE_HEADER):].strip()
        # Check for errors and strip line prefixes
        if "\n#*# " in regular_data:
            logging.warning("Can't read autosave from config file"
                         " - autosave state corrupted")
            return data, ""
        out = [""]
        for line in autosave_data.split('\n'):
            if ((not line.startswith("#*#")
                 or (len(line) >= 4 and not line.startswith("#*# ")))
                and autosave_data):
                logging.warning("Can't read autosave from config file"
                             " - modifications after header")
                return data, ""
            out.append(line[4:])
        out.append("")
        return regular_data, '\n'.join(out)

    def _strip_duplicates(self, data: str, config: ConfigWrapper) -> str:
        # Comment out fields in 'data' that are defined in 'config'
        lines = data.split('\n')
        section = None
        is_dup_field = False
        for lineno, line in enumerate(lines):
            pruned_line = self.comment_r.sub('', line).rstrip()
            if not pruned_line:
                continue
            if pruned_line[0].isspace():
                if is_dup_field:
                    lines[lineno] = "#" + lines[lineno]
                continue
            is_dup_field = False
            if pruned_line[0] == "[":
                section = pruned_line[1:-1].strip()
                continue
            field = self.value_r.sub('', pruned_line)
            if config.fileconfig.has_option(section, field):
                is_dup_field = True
                lines[lineno] = "#" + lines[lineno]    
        return '\n'.join(lines)

    def _parse_config_buffer(self, buffer, filename, fileconfig):
        if not buffer:
            return
        data = '\n'.join(buffer)
        del buffer[:]
        sbuffer = io.StringIO(data)
        if sys.version_info.major >= 3:
            fileconfig.read_file(sbuffer, filename)
        else:
            fileconfig.readfp(sbuffer, filename)

    def _resolve_include(self, source_filename, include_spec, fileconfig,
                         visited):
        dirname = os.path.dirname(source_filename)
        include_spec = include_spec.strip()
        include_glob = os.path.join(dirname, include_spec)
        include_filenames = glob.glob(include_glob)
        if not include_filenames and not glob.has_magic(include_glob):
            # Empty set is OK if wildcard but not for direct file reference
            raise error(_("Include file '%s' does not exist") % (include_glob,))
        include_filenames.sort()
        for include_filename in include_filenames:
            include_data = self._read_config_file(include_filename)
            self._parse_config(include_data, include_filename, fileconfig,
                               visited)
        return include_filenames

    def _parse_config(self, data, filename, fileconfig, visited, parse_includes=True):
        path = os.path.abspath(filename)
        if path in visited:
            raise error(_("Recursive include of config file '%s'") % (filename))
        visited.add(path)
        lines = data.split('\n')
        # Buffer lines between includes and parse as a unit so that overrides
        # in includes apply linearly as they do within a single file
        buffer = []
        for line in lines:
            # Strip trailing comment
            pos = line.find("#")
            if pos >= 0:
                line = line[:pos]
            # Process include or buffer line
            mo = configparser.RawConfigParser.SECTCRE.match(line)
            header = mo and mo.group('header')
            if header and header.startswith('include ') and parse_includes:
                self._parse_config_buffer(buffer, filename, fileconfig)
                include_spec = header[8:].strip()
                self._resolve_include(filename, include_spec, fileconfig,
                                      visited)
            else:
                buffer.append(line)
        self._parse_config_buffer(buffer, filename, fileconfig)
        visited.remove(path)
 
    def _build_config_wrapper(self, data: str, filename: str, parse_includes=True) -> ConfigWrapper:
        if sys.version_info.major >= 3:
            fileconfig = ConfigParser(
                strict=False, inline_comment_prefixes=(';', '#'))
        else:
            fileconfig = ConfigParser()
        self._parse_config(data, filename, fileconfig, set(), parse_includes)
        return ConfigWrapper(self.printer, fileconfig, {}, 'printer')

    def _build_config_string(self, config: ConfigWrapper) -> str:
        sfile = io.StringIO()
        config.fileconfig.write(sfile)
        return sfile.getvalue().strip()

    def read_config(self, filename: str, parse_includes=True) -> ConfigWrapper:
        return self._build_config_wrapper(self._read_config_file(filename),
                                          filename, parse_includes)

    def create_base_config_wrapper(self, file):
        klipperpath = os.path.dirname(__file__)
        filepath = os.path.join(klipperpath, file)
        data = self._read_config_file(filepath)
        data_option_comments, remain_comments = self.comments_to_option_value(data)
        configWrapper = self._build_config_wrapper(data_option_comments, filepath, parse_includes=False)
        return configWrapper, remain_comments

    def compare_base_config(self, config: ConfigWrapper):
        base_config, __ = self.create_base_config_wrapper("printer_base.cfg")
        missed_sections = {}
        deprecated_sections = []
        for section in base_config.fileconfig.sections():
            if (config.has_section(section) and section.split(' ')[0] not in SECTIONS_FULL_REWRITABLE) \
                or section.split(' ')[0] in SECTIONS_SKIPPED:
                continue
            logging.info(f"new missing section is {section}")
            missed_sections[section] = {}
            for option in base_config.fileconfig.options(section):
                missed_sections[section][option] = base_config.fileconfig.get(section, option)
        for section in SECTIONS_DEPRECATED:
            if config.has_section(section):
                deprecated_sections.append(section)
        logging.info(f"missed_sections: {missed_sections}")
        logging.info(f"deprecated_sections: {deprecated_sections}")
        if missed_sections or deprecated_sections:
          self.update_config(setting_sections=missed_sections, removing_sections=deprecated_sections, save_immediatly=True, need_restart=True)

    def compare_pause_resume_config(self):
        base_pause_resume_config, __ = self.create_base_config_wrapper("pause_resume_base.cfg")
        base_confg_path:str = self.printer.get_start_args()['config_file']
        config_dir = os.path.dirname(base_confg_path)
        pause_resume_path =  config_dir + '/pause_resume.cfg'
        data = self._read_config_file(pause_resume_path)
        pause_resume_config: ConfigWrapper = self._build_config_wrapper(data, pause_resume_path, False)
        current_resume = pause_resume_config.fileconfig.get('gcode_macro RESUME', 'gcode')
        base_resume = base_pause_resume_config.fileconfig.get('gcode_macro RESUME', 'gcode')
        if current_resume != base_resume:
            logging.info("updating RESUME macro")
            self.update_config({'gcode_macro RESUME': {'gcode': base_resume}}, save_immediatly=True, need_restart=True, cfgname=pause_resume_path)

    def read_main_config(self, parse_includes=True, compare=True) -> ConfigWrapper:
    def read_current_config(self, parse_includes=True)-> ConfigWrapper:
        filename = self.printer.get_start_args()['config_file']
        data = self._read_config_file(filename)
        regular_data, autosave_data = self._find_autosave_data(data)
        regular_config = self._build_config_wrapper(regular_data, filename, parse_includes)
        autosave_data = self._strip_duplicates(autosave_data, regular_config)
        self.autosave = self._build_config_wrapper(autosave_data, filename, parse_includes)
        cfg = self._build_config_wrapper(regular_data + autosave_data, filename, parse_includes)
        return cfg

    def read_main_config(self) -> ConfigWrapper:
        cfg = self.read_current_config()
        compare = cfg.getsection('printer').getboolean('autoload_missing_sections', True)
        if compare:
          self.compare_base_config(cfg) 
          cfg_to_compare = self.read_current_config(False)
          self.compare_base_config(cfg_to_compare) 
          self.compare_pause_resume_config()
          if self.has_deprecated_options(cfg):
          if self.has_deprecated_options(cfg_to_compare):
            logging.info("saving config")
            self.save_config(True, True, with_options=True)
        return cfg

    def has_deprecated_options(self, cfg: ConfigWrapper):
      for section in SECTIONS_CHANGED:
        if 'remove_option' in SECTIONS_CHANGED[section]:
          for remove_option in SECTIONS_CHANGED[section]['remove_option']:
              if cfg.has_option(section, remove_option):
                  logging.info("has removing sections")
                  return True
        if 'add_option' in SECTIONS_CHANGED[section]:
          for add_option in SECTIONS_CHANGED[section]['add_option']:
              if cfg.has_option(section, add_option):
                  if cfg.getsection(section).get(add_option) != str(SECTIONS_CHANGED[section]['add_option'][add_option]):
                      logging.info("has changed options")
                      return True
              else:
                  return True
      logging.info("nothing to save")
      return False

    def check_unused_options(self, config: ConfigWrapper):
        fileconfig = config.fileconfig
        objects = dict(self.printer.lookup_objects())
        # Determine all the fields that have been accessed
        access_tracking = dict(config.access_tracking)
        for section in self.autosave.fileconfig.sections():
            for option in self.autosave.fileconfig.options(section):
                access_tracking[(section.lower(), option.lower())] = 1
        # Validate that there are no undefined parameters in the config file
        valid_sections = { s: 1 for s, o in access_tracking }
        for section_name in fileconfig.sections():
            section = section_name.lower()
            if section not in valid_sections and section not in objects:
                raise error(_("Section '%s' is not a valid config section")
                            % (section,))
            for option in fileconfig.options(section_name):
                option = option.lower()
                if (section, option) not in access_tracking:
                    raise error(_("Option '%s' is not valid in section '%s'")
                                % (option, section))
        # Setup get_status()
        self._build_status(config)

    def log_config(self, config: ConfigWrapper):
        lines = ["===== Config file =====",
                 self._build_config_string(config),
                 "======================="]
        self.printer.set_rollover_info("config", '\n'.join(lines))
    # Status reporting
    def deprecate(self, section, option, value=None, msg=None):
        self.deprecated[(section, option, value)] = msg
    def _build_status(self, config: ConfigWrapper):
        self.status_raw_config.clear()
        for section in config.get_prefix_sections(''):
            self.status_raw_config[section.get_name()] = section_status = {}
            for option in section.get_prefix_options(''):
                section_status[option] = section.get(option, note_valid=False)
        self.status_settings = {}
        for (section, option), value in config.access_tracking.items():
            self.status_settings.setdefault(section, {})[option] = value
        self.status_warnings = []
        for (section, option, value), msg in self.deprecated.items():
            if value is None:
                res = {'type': 'deprecated_option'}
            else:
                res = {'type': 'deprecated_value', 'value': value}
            res['message'] = msg
            res['section'] = section
            res['option'] = option
            self.status_warnings.append(res)
            
    def get_status(self, eventtime):
        return {'config': self.status_raw_config,
                'settings': self.status_settings,
                'warnings': self.status_warnings,
                'save_config_pending': self.haveUnsavedChanges,
                'save_config_pending_items': self.pendingSaveItems,
        }
    
    def update_config(self, setting_sections: dict = {}, removing_sections: list = [], 
                      save_immediatly = True, need_restart = False, need_backup = False, cfgname = "") -> None:
        """
        Метод добавляет в словарь измененных/добавляемых секций и список удаляемых секций словарь
        setting_sections и список removing_sections соответственно и создает новый конфигурационный файл в зависимости от параметра
        save_immediatly. Параметры need_restart и need_backup указывают на необходимость перезагрузки после сохранения и 
        создания бэкапа при сохранении соответственно. 
        """
        for section in setting_sections:
            if not setting_sections[section]:
                self.set(section, save_immediatly=save_immediatly)
            else:
              for option in setting_sections[section]:
                  self.set(section, option, setting_sections[section][option], save_immediatly)
        
        for section in removing_sections:
            self.remove_section(section)
        if save_immediatly:
            self.save_config(need_restart, need_backup, cfgname)

    def set(self, section: str, option = None, value = None, save_immediatly = False) -> None:
        """
        Метод устанавливает для указанной опции option в секции section значение value, если указанная секция существует. 
        Иначе секция добавляется в словарь измененных/добавляемых секций в конфигурации. Если секция находилась в словаре 
        удаляемых секций, то из него она удаляется.
        """
        if section in self.status_remove_sections:
            self.status_remove_sections.remove(section)
        pending = dict(self.pendingSaveItems)
        if not section in pending or pending[section] is None:
            pending[section] = {}
        else:
            pending[section] = dict(pending[section])
        if option:
          try:
              svalue = str(value)
          except Exception as e:
              logging.error(f"Can't convert to string format: {e}. Value is {value}")
              svalue = ""
          pending[section][option] = svalue
        self.pendingSaveItems = pending
        if not save_immediatly:
            self.haveUnsavedChanges = True
        #logging.info("save_config: set [%s] %s = %s", section, option, svalue)
        
    def remove_section(self, section: str, save_immediatly = False):
        """
        Метод добавляет секцию section в список удаляемых секций, если секция существует в файле конфигурации. 
        Если секция находилась в словаре измененных/новых секций, то из него она удаляется вместе со всеми опциями
        """
        pending_save = dict(self.pendingSaveItems)
        if section in self.pendingSaveItems:
            del pending_save[section]
            self.pendingSaveItems = pending_save
            return
        config = self.read_current_config(parse_includes=False)
        if (not section in self.status_remove_sections and section in config.fileconfig.sections()):
            self.status_remove_sections.append(section)
        if not save_immediatly:
            self.haveUnsavedChanges = True

    def comments_to_option_value(self, data: str) -> tuple(str, dict[str, list]):
        """
        Метод изменяет символы комментария (#;) внутри опций на символ '@', чтобы они обрабатывались как часть значения у опции,
        и возвращает кортеж с изменнными данными и словарем неизмененных комментариев. Неизменными комментариями являются комметарии перед
        первой секцией и перед первой опцией в очередной встреченной секции.  
        Словарь изначально имеет ключ 'before_sections' с пустым множеством в значении. В общем виде 
        возвращаемый словарь выглядит следующим образом: {'before_sections': ["#before_sections_comment_1", "#before_sections_comment_1"],
        'section_1_with_comments_before_option': ["#before_option_comment_1", "#before_option_comment_2"], ...}
        """
        dataLines = data.split('\n')
        start_sections_index = 0
        comments = {'before_sections': []}
        # Индекс первого вхождения в секцию
        for line in dataLines:
            if line.startswith('['):
                break
            comments['before_sections'].append(line)
            start_sections_index +=1
        in_option = False
        section = ''
        for i in range(start_sections_index, len(dataLines)):
            # Исключение комментария в строке
            filtered_line = self.comment_r.sub('', dataLines[i]).rstrip()
            # Если это секция
            if filtered_line.startswith('['):
                section = filtered_line[1:-1].strip()
                in_option = False
                continue
            # Если это опция
            if self.value_r.sub('', filtered_line.strip()) != '':
                in_option = True
            # Если была найдена первая опция
            if in_option:
                # Символ комментария заменить на @
                dataLines[i] = self.comment_symbol_r.sub('@', dataLines[i])
                # Если в строке был только комментарий и он не табулирован
                if not filtered_line and dataLines[i].startswith('@'):
                    # Добавить табуляцию, чтобы ConfigParser правильно прочитал значение 
                    dataLines[i] = '\t' + dataLines[i]
            elif section != '' and self.comment_r.search(dataLines[i]):
                if section in comments:
                    comments[section].append(dataLines[i])
                else:
                    comments[section] = [dataLines[i]]  
        return '\n'.join(dataLines), comments
    
    # Новый сейв конфиг. Может сохранять данные либо с бэкапом, либо без, аналогично с перезагрузкой
    # Сейв конфиг делает полный апдейт (изменяет существующие параметры, удаляет их и записывает новые)
    def save_config(self, need_restart: bool, need_backup: bool, cfgname: str = "", with_options=False) -> None:
        """
        Метод записывает в конфигурационный файл новые параметры конфигурации, устанавливаемые в зависимости от списка удаляемых секций и словаря измененных/добавленных секций. 
        Параметры need_restart и need_backup указывают на необходимость перезагрузки после сохранения и создания бэкапа при сохранении соответственно. 
        """
        gcode = self.printer.lookup_object('gcode')
        if not self.is_data_changed() and not with_options:
          msg = _("No data changed")
          logging.exception(msg)
          raise gcode.error(msg)
        #Read in and validate current config file
        if not cfgname:
          cfgname = self.printer.get_start_args()['config_file']
        try:
          configWrapper, remain_comments = self.old_config_wrapper(cfgname)
        except error as e:
            msg = _("Unable to parse existing config on SAVE_CONFIG")
            logging.exception(msg)
            raise gcode.error(msg)
        newConfigWrapper = self.new_config_wrapper(configWrapper, with_options)
        if need_backup:
            self.backup_config()
        try:
            self.write(cfgname, newConfigWrapper, remain_comments)
        except:
            msg = _("Unable to write config file during SAVE_CONFIG")
            logging.exception(msg)
            raise gcode.error(msg)
        # Request a restart
        if need_restart:
            gcode.request_restart('restart')
    
    def is_data_changed(self):
        return len(self.pendingSaveItems.items()) != 0 or self.status_remove_sections

    def old_config_wrapper(self, cfgname):
        data = self._read_config_file(cfgname)
        data_option_comments, remain_comments = self.comments_to_option_value(data)
        configWrapper = self._build_config_wrapper(data_option_comments, cfgname, parse_includes=False)
        return configWrapper, remain_comments

    def new_config_wrapper(self, old_config: ConfigWrapper, with_options=False) -> ConfigWrapper:
        newConfigWrapper = old_config
        newConfigParser = newConfigWrapper.fileconfig

        #Lookup for deleting sections
        for section in self.status_remove_sections:
            if section in newConfigParser.sections():
                newConfigParser.remove_section(section)
   
        #Lookup for overwriting and new sections                   
        for section in self.pendingSaveItems:
            if not newConfigParser.has_section(section):
                newConfigParser.add_section(section)
            for option in self.pendingSaveItems[section]:
                value = self.pendingSaveItems[section][option]
                newConfigParser.set(section, option, value)
        if with_options:
          for section in SECTIONS_CHANGED:
            if 'remove_option' in SECTIONS_CHANGED[section]:
              for remove_option in SECTIONS_CHANGED[section]['remove_option']:
                  if newConfigParser.has_option(section, remove_option):
                      newConfigParser.remove_option(section, remove_option)
            if 'add_option' in SECTIONS_CHANGED[section]:
              if section == 'fan_back back':
                  if newConfigParser.has_option(section, 'mode'):
                      continue
              for add_option in SECTIONS_CHANGED[section]['add_option']:
                  newConfigParser.set(section, add_option, SECTIONS_CHANGED[section]['add_option'][add_option])

        newConfigWrapper.fileconfig = newConfigParser
        return newConfigWrapper

    def write(self, filename: str, configWrapper: ConfigWrapper, comments: dict[str, list]):
        configParser = configWrapper.fileconfig
        with open(filename, 'w') as configfile:
            # Запись комментарией перед первой секцией 
            configfile.write(str('\n'.join(comments['before_sections']) + '\n'))
            for section in configParser.sections():
                # Запись секции
                configfile.write("[%s]\n" % section)
                if section in comments:
                    # Запись комментариев перед первой опцией в секции
                    configfile.write(str('\n'.join(comments[section]) + '\n'))
                for key, value in configParser.items(section):
                    # Преобразование комментариев в исходный вид
                    sval = self.comment_value_option_r.sub('#', 
                                                           self.line_r.sub('\n\t', str(value)))
                    configfile.write("%s: %s\n" % (key, sval))
                # Перед очередной секций добавить пустую строку
                configfile.write('\n')
        # Обнулить измененные значения
        self.pendingSaveItems = {}
        self.haveUnsavedChanges = False
    
    cmd_SAVE_CONFIG_help = _("Overwrite config file and restart")
    def cmd_SAVE_CONFIG(self, gcmd):
        need_restart, need_backup = (gcmd.get_boolean('NEED_RESTART', True), gcmd.get_boolean('NEED_BACKUP', True))
        self.save_config(need_restart, need_backup)

    cmd_BACKUP_CONFIG_help = _("Create backup config")
    def cmd_BACKUP_CONFIG(self, gcmd):
        self.backup_config()

    def backup_config(self):
        cfgname = self.printer.get_start_args()['config_file']
        backup_name = cfgname[:-4] + time.strftime("-%Y%m%d_%H%M%S") + ".backup"
        os.system(f"cp {cfgname} {backup_name}")
        # Create new config file with temporary name and swap with main config
        logging.info("Backup config in '%s')", backup_name)
        backup_files = [filename for filename in os.listdir(os.path.dirname(cfgname)) if os.path.isfile(os.path.dirname(cfgname) + '/' + filename) and filename.endswith('.backup') and filename.startswith('printer')]
        if len(backup_files) > 5:
            backup_files.sort(reverse=True)
        while len(backup_files) > 5:
            os.remove(os.path.dirname(cfgname) + '/' + backup_files.pop())

    def _find_backup(self, web_request = None):
        cfgname = self.printer.get_start_args()['config_file']
        config_dir = os.path.dirname(cfgname)
        latest_backup = ""
        for filename in os.listdir(config_dir):
            if os.path.isfile(config_dir + '/' + filename) and filename.endswith('.backup') and filename.startswith('printer'):
                if not latest_backup:
                    latest_backup = filename
                else:
                    if os.path.getmtime(config_dir + '/' + latest_backup) < os.path.getmtime(config_dir + '/' + filename):
                        latest_backup = filename
        if web_request:
            web_request.send({'backup': latest_backup})
        return latest_backup

    def _load_backup_config(self, web_request):
        cfgname = self.printer.get_start_args()['config_file']
        config_dir = os.path.dirname(cfgname)
        latest_backup = self._find_backup()
        try:
            if latest_backup:
                os.remove(cfgname)
                os.system(f"cp {config_dir}/{latest_backup} {cfgname}")
            else:
                klipperpath = os.path.dirname(__file__)
                base_config_path = os.path.join(klipperpath, "printer_base.cfg")
                os.remove(cfgname)
                os.system(f"cp {base_config_path} {cfgname}")
            self.printer.lookup_object('gcode').request_restart('firmware_restart')
        except Exception as e:
            logging.error(e)
            messages = self.printer.lookup_object('messages')
            messages.send_message("error", _("Backup file not found"))
    
    def _load_base_config_with_options(self, web_request):
        cfg = self.read_current_config(False)
        base_cfg, comments = self.create_base_config_wrapper("printer_base.cfg")
        save_bed_mesh = web_request.getboolean('save_bed_mesh', False)
        save_pid = web_request.getboolean('save_pid', False)
        save_fix = web_request.getboolean('save_fix', False)
        if save_bed_mesh:
          for section in cfg.get_prefix_sections('bed_mesh'):
              base_cfg.fileconfig.add_section(section)
              for option in section.get_prefix_options(''):
                  base_cfg.fileconfig.set(section, option, section.get(option))
        if save_pid:
            extruder_section = cfg.getsection('extruder')
            for option in extruder_section.get_prefix_options('pid_'):
                base_cfg.fileconfig.set('extruder', option, extruder_section.get(option))
            heater_bed_section = cfg.getsection('heater_bed')
            for option in heater_bed_section.get_prefix_options('pid_'):
                base_cfg.fileconfig.set('heater_bed', option, heater_bed_section.get(option))
        if save_fix:
            fix_sections = cfg.get_prefix_sections('fix_script')
            for section in fix_sections:
                for option in section.get_prefix_options(''):
                    base_cfg.fileconfig.set(section, option, section.get(option))
        cfgname = self.printer.get_start_args()['config_file']
        try:
            self.write(cfgname, base_cfg, comments)
        except Exception as e:
            logging.error(e)
            messages = self.printer.lookup_object('messages')
            messages.send_message("error", _("Unable to write config file during SAVE_CONFIG"))