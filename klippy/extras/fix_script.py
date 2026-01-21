import errno
import os
import pathlib
import subprocess
import logging
import socket
import fcntl
import time
from configfile import PrinterConfig
import locales

class FixScript:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.fixed = config.getboolean('fixed', False)
        self.require_internet = config.getboolean('require_internet', False)
        self.last_done = config.getint('last_done', 0)
        self.script_dir = config.get_name().split()[-1]
        klipperpath = pathlib.Path(__file__).parent.parent.parent.resolve()
        self.scriptpath = os.path.join(klipperpath, f"scripts/fix/{self.script_dir}")
        self.reactor = self.printer.get_reactor()
        self.format_dir = []
        self.stdout_timer = None
        self.stdout_fd = None
        self.process = None
        self.message_callback = None
        self.on_done_callback = None
        self.buffer = ""
        self.raw_buffer = b''
        self.line_buffer = ""
        self.start_time = None
        self.timeout = 600

    def set_nonblocking(self, fd):
        """Устанавливаем неблокирующий режим для файлового дескриптора"""
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    def stdout_checker(self, eventtime):
        try:
            # Проверяем завершение процесса
            return_code = self.process.poll()
            if return_code is not None:
                self._cleanup_and_finish(return_code)
                return self.reactor.NEVER
            
            # Проверяем таймаут
            if self.start_time and time.time() - self.start_time > self.timeout:
                logging.error(f"Таймаут скрипта {self.script_dir}")
                self.process.terminate()
                self._cleanup_and_finish(3)
                return self.reactor.NEVER
            
            # Читаем данные как байты
            data = os.read(self.stdout_fd, 4096)
            if data:
                # Конкатенируем байты с байтами
                self.raw_buffer += data
                # Пытаемся декодировать накопленные байты
                try:
                    # Декодируем все накопленные байты
                    data_str = self.raw_buffer.decode('utf-8', errors='replace')
                    self.raw_buffer = b''  # Очищаем буфер байтов после успешного декодирования
                    
                    # Добавляем декодированную строку в буфер строк
                    self.line_buffer += data_str
                    self.message_callback(self.line_buffer)
                except UnicodeDecodeError:
                    pass
            return eventtime + 0.01  # Продолжаем проверять
            
        except (OSError, IOError) as e:
            # Проверяем, это "нет данных" или реальная ошибка
            if hasattr(e, 'errno') and e.errno not in [errno.EAGAIN, errno.EWOULDBLOCK]:
                # Реальная ошибка ввода-вывода
                logging.error(f"Ошибка чтения вывода: {e}")
                self._cleanup_and_finish(3)
                return self.reactor.NEVER
            
            # Просто нет данных для чтения - проверяем завершение процесса
            return_code = self.process.poll()
            if return_code is not None:
                self._cleanup_and_finish(return_code)
                return self.reactor.NEVER
            
            return eventtime + 0.01

    def _cleanup_and_finish(self, return_code):
        """Корректное завершение скрипта"""
        # Отменяем таймер
        if self.stdout_timer:
            self.reactor.unregister_timer(self.stdout_timer)
            self.stdout_timer = None
        
        # Обрабатываем оставшиеся недекодированные байты
        if self.raw_buffer:
            try:
                # Пытаемся декодировать остатки
                remaining_str = self.raw_buffer.decode('utf-8', errors='replace')
                self.line_buffer += remaining_str
                self.message_callback(self.line_buffer)
            except UnicodeDecodeError:
                # Если не получается декодировать, заменяем на сообщение об ошибке
                self.line_buffer += "[некорректные бинарные данные]"
        self.on_done_script(return_code)

    def run_fix(self, on_message, on_done):
        self.message_callback = on_message
        self.on_done_callback = on_done
        
        if self.fixed:
            return
        
        script_list = os.listdir(self.scriptpath)
        self.format_dir = []
        
        for f in script_list:
            can_to_int = True
            try:
                int(f[:2])
            except:
                can_to_int = False
            if f.endswith('.sh') and can_to_int:
                self.format_dir.append(f)
        
        self.format_dir = sorted(self.format_dir)
        
        if not len(self.format_dir):
            logging.info(f"[{self.script_dir}] - no scripts found")
            return
        
        if self.last_done >= len(self.format_dir):
            logging.info(f"[{self.script_dir}] - all scripts done")
            return
        
        self.start_process()

    def start_process(self):
        if self.last_done >= len(self.format_dir):
            logging.info(f"Can't start process, self.last_done >= len(self.format_dir)")
            return
        
        script_name = self.format_dir[self.last_done]
        script_path = os.path.join(self.scriptpath, script_name)
        
        try:
            self.process = subprocess.Popen(
                ['bash', script_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,  # Используем binary mode
                bufsize=0,   # Без буферизации
                env={**os.environ, 'PYTHONUNBUFFERED': '1'},
                cwd=self.scriptpath
            )
            
            # Получаем файловый дескриптор stdout
            self.stdout_fd = self.process.stdout.fileno()
            self.set_nonblocking(self.stdout_fd)
            
            # Регистрируем таймер для чтения stdout
            self.stdout_timer = self.reactor.register_timer(
                self.stdout_checker, 
                self.reactor.NOW
            )
            
            logging.info(f"[{self.script_dir}] Started script {script_name}")
            
        except Exception as e:
            logging.error(f"[{self.script_dir}] Failed to start script: {e}")
            self.on_done_callback(3)

    def save_result(self):
        configfile: PrinterConfig = self.printer.lookup_object('configfile')
        fix_script_section = {f"fix_script {self.script_dir}": {"last_done": self.last_done, "fixed": self.fixed}}
        configfile.update_config(setting_sections=fix_script_section)

    def on_done_script(self, status):
        if status in [0, 1]:
            self.last_done += 1
            self.fixed = self.last_done >= len(self.format_dir)
        self.save_result()
        self.process = None
        self.stdout_fd = None
        self.buffer = ""
        if self.fixed or status in [2, 3]:
            self.on_done_callback(status)
        else:
            self.start_process()   

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