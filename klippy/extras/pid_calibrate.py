# Calibration of heater PID settings
#
# Copyright (C) 2016-2018  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import math, logging
from . import heaters
import locales

TUNE_PID_DELTA = 5.0

class PIDCalibrate:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.stop = False
        self.is_calibrating = False
        self.gcode = self.printer.lookup_object('gcode')       
        self.gcode.register_command('CALIBRATE_HEATER_PID', self.cmd_CALIBRATE_HEATER_PID,
                               desc=self.cmd_CALIBRATE_HEATER_PID_help)
        self.printer.register_event_handler('klippy:shutdown', self.set_no_calibrate_status)
        webhooks = self.printer.lookup_object('webhooks')
        webhooks.register_endpoint("pid_calibrate/stop_pid_calibrate", self._handle_stop_pid_calibrate)
    
    cmd_CALIBRATE_HEATER_PID_help = _("Calibration pid by an array of temperatures") # no locale
    def cmd_CALIBRATE_HEATER_PID(self, gcmd):
        self.stop = False
        heater_name = gcmd.get('HEATER')
        pheaters = self.printer.lookup_object('heaters')
        try:
            heater = pheaters.lookup_heater(heater_name)
        except self.printer.config_error as e:
            raise self.gcode.error(str(e))
        temperatures = gcmd.get_list_str('TEMPERATURES')
        pid_config = {}
        pid_dev_dict = {}
        self.is_calibrating = True
        for temp in temperatures:
            if self.stop:
                break
            gcmd.respond_info(f"{_('Heating %s to') % _(heater_name)} {temp}")
            Kp, Ki, Kd = self.pid_calibrate(pheaters, heater, float(temp))
            pid_dev_dict[float(temp)] = [Kp, Ki, Kd]
            pid_config[f"pid_{temp}"] = f"{Kp:.3f}, {Ki:.3f}, {Kd:.3f}"
        if not self.stop:
          saving_section = {heater_name: pid_config}
          self.printer.lookup_object('configfile').update_config(saving_section, save_immediatly = True)
          self.printer.lookup_object('messages').send_message("success", _("End PID calibrate, new data saved"))
        else:
          self.printer.lookup_object('messages').send_message("suggestion", _("pid_calibrate interrupted"))
        self.is_calibrating = False
        if hasattr(heater.control, "update_pid_mass") and not self.stop:
          self.stop = False
          heater.control.update_pid_mass(pid_dev_dict)

    def set_no_calibrate_status(self):
        self.stop = True
        self.is_calibrating = False
        
    def _handle_stop_pid_calibrate(self, web_request):
         self.set_no_calibrate_status()
         self.printer.lookup_object('heaters').turn_off_all_heaters()

    def pid_calibrate(self, pheaters, heater, target, write_file = 0):
        self.printer.lookup_object('toolhead').get_last_move_time()
        calibrate = ControlAutoTune(self, heater, target)
        old_control = heater.set_control(calibrate)
        try:
              pheaters.set_temperature(heater, target, True)
        except self.printer.command_error as e:
            heater.set_control(old_control)
            raise
        heater.set_control(old_control)
        if write_file:
            calibrate.write_file('/tmp/heattest.txt')
        if calibrate.check_busy(0., 0., 0.):
            return 0,0,0
        # Log and report results
        return calibrate.calc_final_pid()
    
    def get_status(self, eventtime):
        return {'is_calibrating': self.is_calibrating}

class ControlAutoTune:
    def __init__(self, pid_calibrate, heater, target):
        self.pid_calibrate = pid_calibrate
        self.heater = heater
        self.heater_max_power = heater.get_max_power()
        self.calibrate_temp = target
        # Heating control
        self.heating = False
        self.peak = 0.
        self.peak_time = 0.
        # Peak recording
        self.peaks = []
        # Sample recording
        self.last_pwm = 0.
        self.pwm_samples = []
        self.temp_samples = []
    # Heater control
    def set_pwm(self, read_time, value):
        if value != self.last_pwm:
            self.pwm_samples.append(
                (read_time + self.heater.get_pwm_delay(), value))
            self.last_pwm = value
        self.heater.set_pwm(read_time, value)
    def temperature_update(self, read_time, temp, target_temp):
        self.temp_samples.append((read_time, temp))
        # Check if the temperature has crossed the target and
        # enable/disable the heater if so.
        if self.heating and temp >= target_temp:
            self.heating = False
            self.check_peaks()
            if not self.pid_calibrate.stop:
              self.heater.alter_target(self.calibrate_temp - TUNE_PID_DELTA)
            else:
              self.heating = False
        elif not self.heating and temp <= target_temp:
            self.heating = True
            self.check_peaks()
            if not self.pid_calibrate.stop:
              self.heater.alter_target(self.calibrate_temp)
            else:
              self.heating = False
        # Check if this temperature is a peak and record it if so
        if self.heating:
            self.set_pwm(read_time, self.heater_max_power)
            if temp < self.peak:
                self.peak = temp
                self.peak_time = read_time
        else:
            self.set_pwm(read_time, 0.)
            if temp > self.peak:
                self.peak = temp
                self.peak_time = read_time
    def check_busy(self, eventtime, smoothed_temp, target_temp):
        if self.heating or len(self.peaks) < 12:
            return True
        return False
    # Analysis
    def check_peaks(self):
        self.peaks.append((self.peak, self.peak_time))
        if self.heating:
            self.peak = 9999999.
        else:
            self.peak = -9999999.
        if len(self.peaks) < 4:
            return
        self.calc_pid(len(self.peaks)-1)
    def calc_pid(self, pos):
        temp_diff = self.peaks[pos][0] - self.peaks[pos-1][0]
        time_diff = self.peaks[pos][1] - self.peaks[pos-2][1]
        # Use Astrom-Hagglund method to estimate Ku and Tu
        amplitude = .5 * abs(temp_diff)
        Ku = 4. * self.heater_max_power / (math.pi * amplitude)
        Tu = time_diff
        # Use Ziegler-Nichols method to generate PID parameters
        Ti = 0.5 * Tu
        Td = 0.125 * Tu
        Kp = 0.6 * Ku * heaters.PID_PARAM_BASE
        Ki = Kp / Ti
        Kd = Kp * Td
        logging.info("Autotune: raw=%f/%f Ku=%f Tu=%f  Kp=%f Ki=%f Kd=%f",
                     temp_diff, self.heater_max_power, Ku, Tu, Kp, Ki, Kd)
        return Kp, Ki, Kd
    def calc_final_pid(self):
        cycle_times = [(self.peaks[pos][1] - self.peaks[pos-2][1], pos)
                       for pos in range(4, len(self.peaks))]
        midpoint_pos = sorted(cycle_times)[len(cycle_times)//2][1]
        return self.calc_pid(midpoint_pos)
    # Offline analysis helper
    def write_file(self, filename):
        pwm = ["pwm: %.3f %.3f" % (time, value)
               for time, value in self.pwm_samples]
        out = ["%.3f %.3f" % (time, temp) for time, temp in self.temp_samples]
        f = open(filename, "w")
        f.write('\n'.join(pwm + out))
        f.close()
    
    def get_control(self):
        return {'auto_tune': {'calibrate_temp': self.calibrate_temp}}

def load_config(config):
    return PIDCalibrate(config)



# self.gcode.register_command('PID_CALIBRATE', self.cmd_PID_CALIBRATE,
        #                        desc=self.cmd_PID_CALIBRATE_help) 


# cmd_PID_CALIBRATE_help = _("Run PID calibration test")
# def cmd_PID_CALIBRATE(self, gcmd):
#     heater_name = gcmd.get('HEATER')
#     target = gcmd.get_float('TARGET')
#     write_file = gcmd.get_int('WRITE_FILE', 0)
#     Kp, Ki, Kd = self.pid_calibrate(heater_name, target, write_file)
#     logging.info(f"Autotune: final: Kp={Kp} Ki={Ki} Kd={Kd}")
#     gcmd.respond_info(
#         _("PID parameters: pid_Kp=%.3f pid_Ki=%.3f pid_Kd=%.3f\n"
#         "The SAVE_CONFIG command will update the printer config file\n"
#         "with these parameters and restart the printer.") % (Kp, Ki, Kd))
    
#     # Store results for SAVE_CONFIG
#     pid_target = {f"pid_{target}": f"{Kp:.3f}, {Ki:.3f}, {Kd:.3f}"}
#     saving_section = {heater_name: pid_target}
#     self.printer.lookup_object('configfile').update_config(saving_section, save_immediatly = False)