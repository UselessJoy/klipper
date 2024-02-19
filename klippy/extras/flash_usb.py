import os, pathlib
import locales

### Переделать логику работы с USB-накопителем
class FlashUsb:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.printer.register_event_handler('klippy:ready', self._handle_ready)
        self.flashpath = '/media'
        self.flashname = None
        self.timer = self.printer.get_reactor()
        self.gcodelist = None
        configpath = pathlib.Path(self.printer.get_start_args()['config_file']).parent.parent.resolve()
        self.gcodepath = os.path.join(configpath, "gcodes")
        #self.gcodepath = '/home/orangepi/printer_data/gcodes'
    
    def _handle_ready(self):
        self.timer.register_timer(self._check_flash_status, self.timer.monotonic()+ 0.1)

    def _load_gcode_files(self, filepath, flashpath):
        if os.path.isdir(filepath):
            filelist = os.listdir(filepath)
            if len(filelist) != 0:
                for file in filelist:
                    if os.path.isdir(str(filepath) + '/' + str(file)) and self._path_have_gcodes(str(filepath) + '/' + str(file)):
                        os.system('mkdir ' + str(self.gcodepath) + '/' + str(flashpath) + '/' + str(file))
                        self._load_gcode_files(str(filepath) + '/' + str(file), str(flashpath) + '/' + str(file))
                    else:
                        if (file.endswith('.gcode')) and (not self._file_already_exist(file, flashpath)):
                            os.system('cp ' + str(filepath) + '/' + str(file) + " " + str(self.gcodepath) + '/' + str(flashpath))

    def _path_have_gcodes(self, path):
        if os.path.isdir(path):
            pathlist = os.listdir(path)
            if len(pathlist) != 0:
                for file in pathlist:
                    if (file.endswith('.gcode')):
                        return True
        return False
    def _file_already_exist(self, file, flashpath):
        if os.path.isdir(str(self.gcodepath) + str(flashpath)):
            self.gcodelist = os.listdir(str(self.gcodepath) + '/' + str(flashpath))
            if len(self.gcodelist) != 0:
                for gcodefile in self.gcodelist:
                    if file == gcodefile:
                        return True
        return False
    
    def _flash_directory_already_exist(self, dir):
        self.gcodelist = os.listdir(self.gcodepath)
        if len(self.gcodelist) != 0:
            for gcodefile in self.gcodelist:
                if os.path.isdir(str(self.gcodepath) + '/' + str(gcodefile)):
                    if dir == gcodefile:
                        return True
        return False

    def _has_flash(self):
        filelist = os.listdir(self.flashpath)
        if len(filelist) != 0:
            self.flashname = filelist[0]
            return True
        else:
            return False

    def _create_flash_directory(self):
        os.system('mkdir ' + str(self.gcodepath) + '/' + str(self.flashname))

    def _delete_unused_files(self):
        os.system('rm -rf ' + str(self.gcodepath) + '/' + str(self.flashname))
        self.flashname = None

    def _check_flash_status(self, eventtime):
        if self._has_flash():
            if not self._flash_directory_already_exist(self.flashname):
                self._create_flash_directory()
                self._load_gcode_files(self.flashpath + '/' + self.flashname, self.flashname)
        else:
            if self.flashname != None:
                if self._flash_directory_already_exist(self.flashname):
                    self._delete_unused_files()
        return eventtime + 1.

def load_config(config):
    return FlashUsb(config)