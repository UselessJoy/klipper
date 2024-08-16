# Virtual sdcard support (print files directly from a host g-code file)
#
# Copyright (C) 2018  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os, sys, logging, io
import pathlib
import re
import locales
import subprocess
VALID_GCODE_EXTS = ['gcode', 'g', 'gco']

class VirtualSD:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.printer.register_event_handler("klippy:shutdown",
                                            self.handle_shutdown)
        # sdcard state
        path = config.get('path')
        if os.path.isdir(os.path.join(path, 'mmcblk0p1')):
          sd = os.path.join(path, 'mmcblk0p1/gcodes')
        else:
          sd = os.path.join(path, 'gcodes')
        self.sdcard_dirname = os.path.normpath(os.path.expanduser(sd))
        self.media_dirname = "/media"
        self.rebuild_choise = config.get('rebuild')
        stepper_z = config.getsection('stepper_z')
        self.max_z = stepper_z.getint('position_max')
        self.current_file = None
        self.interrupted_file = None
        self.show_interrupt = False
        self.watch_bed_mesh = config.getboolean('watch_bed_mesh', False)
        self.autoload_bed_mesh = config.getboolean('autoload_bed_mesh', False)
        self.last_coord = [0.0, 0.0, 0.0, 0.0]
        self.file_position = self.file_size = 0
        # Print Stat Tracking
        self.print_stats = self.printer.load_object(config, 'print_stats')
        # Work timer
        self.reactor = self.printer.get_reactor()
        self.must_pause_work = self.cmd_from_sd = False
        self.next_file_position = 0
        self.work_timer = None
        # Error handling
        gcode_macro = self.printer.load_object(config, 'gcode_macro')
        self.on_error_gcode = gcode_macro.load_template(
            config, 'on_error_gcode', '')
        # Register commands
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode_move = self.printer.load_object(config, 'gcode_move')
        for cmd in ['M20', 'M21', 'M23', 'M24', 'M25', 'M26', 'M27']:
            self.gcode.register_command(cmd, getattr(self, 'cmd_' + cmd))
        for cmd in ['M28', 'M29', 'M30']:
            self.gcode.register_command(cmd, self.cmd_error)
        self.gcode.register_command(
            "SDCARD_RESET_FILE", self.cmd_SDCARD_RESET_FILE,
            desc=self.cmd_SDCARD_RESET_FILE_help)
        self.gcode.register_command(
            "SDCARD_PRINT_FILE", self.cmd_SDCARD_PRINT_FILE,
            desc=self.cmd_SDCARD_PRINT_FILE_help)
        ####      NEW      ####
        self.gcode.register_command(
            "SDCARD_SAVE_FILE", self.cmd_SDCARD_SAVE_FILE)

        self.gcode.register_command(
            "SDCARD_RUN_FILE", self.cmd_SDCARD_RUN_FILE)

        self.gcode.register_command(
            "SDCARD_REMOVE_FILE", self.cmd_SDCARD_REMOVE_FILE)
        
        self.gcode.register_command(
            "SDCARD_PASS_FILE", self.cmd_SDCARD_PASS_FILE)

        self.printer.register_event_handler("klippy:ready",
                                            self.was_shutdown_at_printing)
    
        webhooks = self.printer.lookup_object('webhooks')
        webhooks.register_endpoint("virtual_sdcard/set_watch_bed_mesh",
                                   self._handle_set_watch_bed_mesh)
        webhooks.register_endpoint("virtual_sdcard/set_autoload_bed_mesh",
                                   self._handle_autoload_bed_mesh)
        ####    END NEW    ####

    def _handle_set_watch_bed_mesh(self, web_request):
      self.watch_bed_mesh = web_request.get_boolean('watch_bed_mesh')
      configfile = self.printer.lookup_object('configfile')
      safety_section = {"virtual_sdcard": {"watch_bed_mesh": self.watch_bed_mesh}}
      configfile.update_config(setting_sections=safety_section, save_immediatly=True)
        
    def _handle_autoload_bed_mesh(self, web_request):
      self.autoload_bed_mesh = web_request.get_boolean('autoload_bed_mesh')
      configfile = self.printer.lookup_object('configfile')
      safety_section = {"virtual_sdcard": {"autoload_bed_mesh": self.autoload_bed_mesh}}
      configfile.update_config(setting_sections=safety_section, save_immediatly=True)
    
    def handle_shutdown(self):
        if self.work_timer is not None:
            self.must_pause_work = True
            try:
                readpos = max(self.file_position - 1024, 0)
                readcount = self.file_position - readpos
                self.current_file.seek(readpos)
                data = self.current_file.read(readcount + 128)
                self.save_printing_parameters()
            except:
                logging.exception("virtual_sdcard shutdown read")
                return
            logging.info("Virtual sdcard (%d): %s\nUpcoming (%d): %s",
                         readpos, repr(data[:readcount]),
                         self.file_position, repr(data[readcount:]))
    def stats(self, eventtime):
        if self.work_timer is None:
            return False, ""
        return True, "sd_pos=%d" % (self.file_position,)
    def get_file_list(self, check_subdirs=False):
        if check_subdirs:
            flist = []
            for root, dirs, files in os.walk(
                    self.sdcard_dirname, followlinks=True):
                for name in files:
                    ext = name[name.rfind('.')+1:]
                    if ext not in VALID_GCODE_EXTS:
                        continue
                    full_path = os.path.join(root, name)
                    r_path = full_path[len(self.sdcard_dirname) + 1:]
                    size = os.path.getsize(full_path)
                    flist.append((r_path, size))
            return sorted(flist, key=lambda f: f[0].lower())
        else:
            dname = self.sdcard_dirname
            try:
                filenames = os.listdir(self.sdcard_dirname)
                return [(fname, os.path.getsize(os.path.join(dname, fname)))
                        for fname in sorted(filenames, key=str.lower)
                        if not fname.startswith('.')
                        and os.path.isfile((os.path.join(dname, fname)))]
            except:
                logging.exception("virtual_sdcard get_file_list")
                raise self.gcode.error(_("Unable to get file list"))
    def get_status(self, eventtime):
        return {
            'gcode_path': self.sdcard_dirname,
            'file_path': self.file_path(),
            'progress': self.progress(),
            'is_active': self.is_active(),
            'file_position': self.file_position,
            'file_size': self.file_size,
            'interrupted_file': self.interrupted_file,
            'rebuild': str(self.rebuild_choise),
            'has_interrupted_file': self.has_interrupted_file(),
            'show_interrupt': self.show_interrupt,
            'watch_bed_mesh': self.watch_bed_mesh,
            'autoload_bed_mesh': self.autoload_bed_mesh
        }
    def file_path(self):
        if self.current_file:
            return self.current_file.name
        return None
    def progress(self):
        if self.file_size:
            return float(self.file_position) / self.file_size
        else:
            return 0.
    def is_active(self):
        return self.work_timer is not None
    def do_pause(self):
        if self.work_timer is not None:
            self.must_pause_work = True
            while self.work_timer is not None and not self.cmd_from_sd:
                self.reactor.pause(self.reactor.monotonic() + .001)
    def do_resume(self):
        self.try_check_open()
        self.printer.lookup_object('homing').run_G28_if_unhomed()
        # probe_object = self.printer.lookup_object('probe')
        # if probe_object.is_magnet_probe_on(self.printer.lookup_object('toolhead')):
        #   probe_object.run_gcode_return_magnet()
        # probe_object.return_z()
        messages = self.printer.lookup_object('messages')
        if self.autoload_bed_mesh and not self.printer.lookup_object('bed_mesh').pmgr.get_current_profile():
            start_heater_bed_temp = self.find_start_heater_bed_temp()
            cur_profile = self.printer.lookup_object('bed_mesh').load_best_mesh(start_heater_bed_temp)
            if cur_profile:
              if re.match(r"^profile_\d+$", cur_profile):
                  cur_profile = _("profile_%s") % cur_profile.partition('_')[2]
            messages.send_message("warning", _("No mesh loaded")) if not cur_profile else messages.send_message("success", _("Automatic loaded bed mesh %s") % cur_profile)
        elif self.watch_bed_mesh:
            cur_profile = self.printer.lookup_object('bed_mesh').pmgr.get_current_profile()
            messages.send_message("warning", _("No mesh loaded")) if not cur_profile else messages.send_message("success", _("Loaded mesh profile: %s") % cur_profile)
        if self.work_timer is not None:
            raise self.gcode.error(_("SD busy"))
        self.must_pause_work = False
        self.printer.send_event("virtual_sdcard:printing")
        led_control = self.printer.lookup_object("led_control")
        led_control.set_start_print_effect()
        self.work_timer = self.reactor.register_timer(
            self.work_handler, self.reactor.NOW)
    def do_cancel(self):
        if self.current_file is not None:
            self.do_pause()
            self.current_file.close()
            ####      NEW      ####
            self._remove_file()
            ####    END NEW    ####
            self.current_file = None
            self.print_stats.note_cancel()
        self.file_position = self.file_size = 0.
        self.run_gcode_on_cancel()
        
    def run_gcode_on_cancel(self):
      pos = self.printer.lookup_object('toolhead').get_position()
      # self.gcode.run_script_from_command(f"G1 Z{pos[2]+5 if pos[2]+5 <= self.max_z else self.max_z}")
      self.gcode.run_script_from_command(f"G28 Y")
      self.gcode.run_script_from_command(f"G28 X")
            
    # G-Code commands
    def cmd_error(self, gcmd):
        raise gcmd.error(_("SD write not supported"))
    def _reset_file(self):
        if self.current_file is not None:
            self.do_pause()
            self.current_file.close()
            self.current_file = None
            ####      NEW      ####
            self._remove_file()
            ####    END NEW    ####
        self.file_position = self.file_size = 0.
        self.print_stats.reset()
        self.printer.send_event("virtual_sdcard:reset_file")
    cmd_SDCARD_RESET_FILE_help = _("Clears a loaded SD File. Stops the print if necessary")
    ####      NEW      ####
    def was_shutdown_at_printing(self):
        if self.has_interrupted_file():
            if self.rebuild_choise == 'confirm':
                self.show_interrupt = True
                self.print_stats.note_interrupt()
                logging.info("Waiting confirm for continue print")
            elif self.rebuild_choise == 'autoconfirm':
                self.cmd_SDCARD_RUN_FILE()
                logging.info("Virtual sdcard continue print")
            else:
                logging.info("Nothing to do")
                return
    ####    END NEW    ####
    def cmd_SDCARD_RESET_FILE(self, gcmd):
        if self.cmd_from_sd:
            raise gcmd.error(
                _("SDCARD_RESET_FILE cannot be run from the sdcard"))
        self._reset_file()
    cmd_SDCARD_PRINT_FILE_help = _("Loads a SD file and starts the print. May include files in subdirectories.")
    def cmd_SDCARD_PRINT_FILE(self, gcmd):
        self.try_check_open()
        if self.work_timer is not None:
            raise gcmd.error(_("SD busy"))
        self._reset_file()
        filename = gcmd.get("FILENAME")
        if filename[0] == '/':
            filename = filename[1:]
        self._load_file(gcmd, filename, file_position=0, check_subdirs=True)
        self.do_resume()
    ####      NEW      ####
    def cmd_SDCARD_SAVE_FILE(self, gcmd):
        if self.work_timer is None:
            #raise gcmd.error(_("SD busy"))
            self.save_printing_parameters()

    def cmd_SDCARD_PASS_FILE(self, gcmd):
        self.show_interrupt = False
        self.print_stats.reset()

    def try_check_open(self):
        try:
          safety_printing_object = self.printer.lookup_object('safety_printing')
          if safety_printing_object.safety_enabled:
              safety_printing_object.raise_error_if_open()
        except:
            pass 
          
    def cmd_SDCARD_RUN_FILE(self, gcmd):
        self.show_interrupt = False
        self.try_check_open()
        gcmd.respond_raw(_("Restart file"))
        self.load_saved_parameters()
        self._load_file(gcmd, self.current_file, file_position=self.file_position, check_subdirs=True)
        self.work_timer = self.reactor.register_timer(
            self.rebuild_begin_print, self.reactor.NOW)
         
    def cmd_SDCARD_REMOVE_FILE(self, gcmd):
        self.printer.lookup_object('gcode_move').reset_e()
        self.show_interrupt = False
        self._remove_file()
        gcmd.respond_raw(_("Remove interrupted file"))
        self.print_stats.reset()

    def _remove_file(self):
        if self.has_interrupted_file():
            try:
                os.system(f"rm \"{self.sdcard_dirname}/{self.interrupted_file}\"")
            except:
                logging.info("Cannot delete file")
            self.current_file = None
            self.file_position = 0
            self.last_coord = [0.0, 0.0, 0.0, 0.0]
            self.interrupted_file = None
    ####    END NEW    ####
    def cmd_M20(self, gcmd):
        # List SD card
        files = self.get_file_list()
        gcmd.respond_raw(_("Begin file list"))
        for fname, fsize in files:
            gcmd.respond_raw("%s %d" % (fname, fsize))
        gcmd.respond_raw(_("End file list"))
    def cmd_M21(self, gcmd):
        # Initialize SD card
        gcmd.respond_raw(_("SD card ok"))
    def cmd_M23(self, gcmd):
        # Select SD file
        if self.work_timer is not None:
            raise gcmd.error(_("SD busy"))
        self._reset_file()
        filename = gcmd.get_raw_command_parameters().strip()
        if filename.startswith('/'):
            filename = filename[1:]
        self._load_file(gcmd, filename)

    #filename is path from flash
    #if flash name is FLASH, then filename will be FLASH/gcode_name
    def find_media_file(self, filename):
        media_file = os.path.join(self.media_dirname, filename)
        if os.path.isfile(media_file):
            return media_file
        raise Exception()
        
    def _load_file(self, gcmd, filename: str, file_position=0, check_subdirs=False):
        files = self.get_file_list(check_subdirs)
        flist = [f[0] for f in files]
        #files_by_lower = { fname.lower(): fname for fname, fsize in files }
        fname: str = filename
        try:
            if fname not in flist:
                media_fname = self.find_media_file(fname)
                name = fname.split('/').pop()
                parent_files = self.get_file_list()
                parent_flist = [f[0] for f in parent_files]
                tmp_r = re.compile('_tmp(?:[0-9]*)')
                i = 0
                result_name = None
                while not result_name:
                    if name in parent_flist:
                        name = name.split('.')
                        if i == 0:
                            name[0] = name[0] + ('_tmp')
                        else:
                            name[0] = tmp_r.sub('', name[0]).rstrip()
                            name[0] = name[0] + f'_tmp{i}'
                        name = name[0] + '.' + name[1]
                        i = i + 1
                    else:
                        result_name = name
                fname = os.path.join(self.sdcard_dirname, result_name)
                subprocess.check_output(f"cp \"{media_fname}\" \"{fname}\"", universal_newlines=True, shell=True, stderr=subprocess.STDOUT)  
            else:  
                fname = os.path.join(self.sdcard_dirname, fname)
            f = io.open(fname, 'r', newline='')
            f.seek(0, os.SEEK_END)
            fsize = f.tell()
            f.seek(0)
        except:
            logging.exception("virtual_sdcard file open")
            raise gcmd.error(_("Unable to open file"))
        gcmd.respond_raw(_("File opened:%s Size:%d") % (filename, fsize))
        gcmd.respond_raw(_("File selected"))
        self.current_file = f
        self.file_position = file_position
        self.file_size = fsize
        self.print_stats.set_current_file(filename)
    def cmd_M24(self, gcmd):
        # Start/resume SD print
        self.do_resume()
    def cmd_M25(self, gcmd):
        # Pause SD print
        self.do_pause()
    def cmd_M26(self, gcmd):
        # Set SD position
        if self.work_timer is not None:
            raise gcmd.error(_("SD busy"))
        pos = gcmd.get_int('S', minval=0)
        self.file_position = pos
    def cmd_M27(self, gcmd):
        # Report SD print status
        if self.current_file is None:
            gcmd.respond_raw(_("Not SD printing."))
            return
        gcmd.respond_raw(_("SD printing byte %d/%d")
                         % (self.file_position, self.file_size))
    def get_file_position(self):
        return self.next_file_position
    def set_file_position(self, pos):
        self.next_file_position = pos
    def is_cmd_from_sd(self):
        return self.cmd_from_sd
    # Background work timer
    def work_handler(self, eventtime):
        logging.info("Starting SD card print (position %d)", self.file_position)
        self.reactor.unregister_timer(self.work_timer)
        try:
            self.current_file.seek(self.file_position)
        except:
            logging.exception("virtual_sdcard seek")
            self.work_timer = None
            return self.reactor.NEVER
        self.print_stats.note_start()
        gcode_mutex = self.gcode.get_mutex()
        partial_input = ""
        lines = []
        error_message = None
        while not self.must_pause_work:
            if not lines:
                # Read more data
                try:
                    data = self.current_file.read(8192)
                except:
                    logging.exception("virtual_sdcard read")
                    break
                if not data:
                    # End of file
                    self.current_file.close()
                    self.current_file = None
                    logging.info("Finished SD card print")
                    self.gcode.respond_raw(_("Done printing file"))
                    self.run_gcode_on_cancel()
                    break
                lines = data.split('\n')
                lines[0] = partial_input + lines[0]
                partial_input = lines.pop()
                lines.reverse()
                self.reactor.pause(self.reactor.NOW)
                continue
            # Pause if any other request is pending in the gcode class
            if gcode_mutex.test():
                self.reactor.pause(self.reactor.monotonic() + 0.100)
                continue
            # Dispatch command
            self.cmd_from_sd = True
            line = lines.pop()
            if sys.version_info.major >= 3:
                next_file_position = self.file_position + len(line.encode()) + 1
            else:
                next_file_position = self.file_position + len(line) + 1
            self.next_file_position = next_file_position
            try:
                self.gcode.run_script(line)
            except self.gcode.error as e:
                error_message = str(e)
                try:
                    self.gcode.run_script(self.on_error_gcode.render())
                except:
                    logging.exception("virtual_sdcard on_error")
                break
            except:
                logging.exception("virtual_sdcard dispatch")
                break
            self.cmd_from_sd = False
            self.file_position = self.next_file_position
            # Do we need to skip around?
            if self.next_file_position != next_file_position:
                try:
                    self.current_file.seek(self.file_position)
                except:
                    logging.exception("virtual_sdcard seek")
                    self.work_timer = None
                    return self.reactor.NEVER
                lines = []
                partial_input = ""
        logging.info("Exiting SD card print (position %d)", self.file_position)
        self.work_timer = None
        self.cmd_from_sd = False
        if error_message is not None:
            self.print_stats.note_error(error_message)
        elif self.current_file is not None:
            self.print_stats.note_pause()
        else:
            self.printer.send_event("virtual_sdcard:complete")
            self.print_stats.note_complete()
        return self.reactor.NEVER

####      NEW      ####
    def save_printing_parameters(self):
        if self.file_path():
            file = self.current_file.name + '.interrupted'
            os.system(f'touch "{file}"')
            interrupted_file = io.open(f"{file}", 'r+')
            filename = self.file_path().rsplit('/', 1)[-1] + '\n'
            position = str(self.file_position) + '\n'
            last_pos = self.gcode_move.get_status()['position']
            last_e  = self.gcode_move.last_param_e
            lines = [filename, position, f"{last_pos.z}\n", f"{last_pos.x}\n", f"{last_pos.y}\n", f"{last_e}\n"]
            interrupted_file.writelines(lines)
            interrupted_file.close()
            return

    def load_saved_parameters(self):
        if os.path.isdir(self.sdcard_dirname):
            filelist = os.listdir(self.sdcard_dirname)
            if len(filelist) != 0:
                for file in filelist:
                    if file.endswith('.interrupted'):
                        self.interrupted_file = file
                        break
        file = io.open(self.sdcard_dirname + '/' + self.interrupted_file, "r")
        lines = file.readlines()
        self.current_file = lines[0].rstrip()
        self.file_position = int(lines[1].rstrip())
        self.last_coord = [float(lines[2]), float(lines[3]), float(lines[4]), float(lines[5])]
        self.gcode.respond_info(str(self.current_file))
        
        file.close()
    

    def find_start_heater_bed_temp(self): 
        lines = []
        partial_input = ""
        file = io.open(self.file_path(), "r", newline='')
        file_position = 0
        file.seek(file_position)
        data = file.read(4092)
        while data:
            if not lines:
                # Read more data
                try:
                    data = file.read(8192)
                except:
                    return 0
                if not data:
                    # End of file
                    file.close()
                    return 0
                lines = data.split('\n')
                lines[0] = partial_input + lines[0]
                partial_input = lines.pop()
                lines.reverse()
                self.reactor.pause(self.reactor.NOW)
                continue
            line: str = lines.pop()
            if line.startswith('M190') or line.startswith('M109'):
                return int(line.split(" ")[1][1:])
            if line.find('CURRENT_LAYER=1') != -1: # типо после данной команды дальше температуру искать нет смысла
                return 0
            next_file_position = file_position + len(line.encode()) + 1       
            file_position = next_file_position
            file.seek(file_position)
        return 0
            
    def rebuild_begin_print(self, eventtime):
        self.reactor.unregister_timer(self.work_timer)
        self.print_stats.note_start()
        gcode_mutex = self.gcode.get_mutex()
        partial_input = ""
        lines = []
        file = io.open(self.file_path(), "r", newline='')
        file_position = 0
        file.seek(file_position)
        data = file.read(4096)
        lines = data.split('\n')
        lines[0] = partial_input + lines[0]
        partial_input = lines.pop()
        lines.reverse()
        line = lines.pop()
        #self.reactor.pause(self.reactor.NOW)
        # self.gcode.run_script(
        #                         "M104 S150\n"
        #                         "M140 S50\n"
        #                         "M109 S150\n"
        #                         "M190 S50\n")
        while not line.startswith('G28') or not data:
            if not lines:
                # Read more data
                try:
                    data = self.current_file.read(8192)
                    lines = data.split('\n')
                    lines[0] = partial_input + lines[0]
                    partial_input = lines.pop()
                    lines.reverse()
                except:
                    logging.exception("virtual_sdcard read")
                    break
            # if gcode_mutex.test():
            #    # self.reactor.pause(self.reactor.monotonic() + 0.100)
            #     continue
            self.cmd_from_sd = True
            self.gcode.run_script(line)
            next_file_position = file_position + len(line.encode()) + 1       
            file_position = next_file_position
            file.seek(file_position)
            self.cmd_from_sd = False
            line = lines.pop()
        toolhead = self.printer.lookup_object('toolhead')
        kin_status = toolhead.get_kinematics().get_status(self.reactor.monotonic())
        if "z" not in kin_status['homed_axes']:
          self.gcode.run_script(f"SET_KINEMATIC_POSITION Z={self.last_coord[0]}\n")
        lead_z = 7 if self.max_z - self.last_coord[0] > 7 else self.max_z
        self.gcode.run_script(
          "G92 E0\n"
          "G1 F2100 E-1\n"
          "G91\n"
          "G0 Z%f\n"
          "G90\n"
          "G28 X Y\n"
          "G0 X%f Y%f Z%f F6000\n"
          "G92 E%f\n"
          % (lead_z, 
            self.last_coord[1], self.last_coord[2], self.last_coord[0], self.last_coord[3])
        )
        
        self.work_timer = None
        try:
            self.work_timer = self.reactor.register_timer(
                self.work_handler, self.reactor.NOW)
        except:
            logging.exception("begin gcode not ended")
            raise self.gcode.error("begin gcode not ended")
        return self.reactor.NEVER

    def has_interrupted_file(self):
        if os.path.isdir(self.sdcard_dirname):
            filelist = os.listdir(self.sdcard_dirname)
            if len(filelist) != 0:
                for file in filelist:
                    if file.endswith('.interrupted'):
                        self.interrupted_file = file
                        return True
        return False

####    END NEW    ####

def load_config(config):
    return VirtualSD(config)
