# Support for reading acceleration data from an adxl345 chip
#
# Copyright (C) 2020-2021  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import collections, functools, logging, math, time
import multiprocessing, os
from . import bus, background_process, shaper_calibrate, bulk_sensor

from . import manual_probe, probe
import locales
# ADXL345 registers
REG_DEVID = 0x00
REG_BW_RATE = 0x2C
REG_POWER_CTL = 0x2D
REG_DATA_FORMAT = 0x31
REG_FIFO_CTL = 0x38
REG_MOD_READ = 0x80
REG_MOD_MULTI = 0x40
REG_THRESH_TAP = 0x1D
REG_DUR = 0x21
REG_INT_MAP = 0x2F
REG_TAP_AXES = 0x2A
REG_OFSX = 0x1E
REG_OFSY = 0x1F
REG_OFSZ = 0x20
REG_INT_ENABLE = 0x2E
REG_INT_SOURCE = 0x30

QUERY_RATES = {
    25: 0x8, 50: 0x9, 100: 0xa, 200: 0xb, 400: 0xc,
    800: 0xd, 1600: 0xe, 3200: 0xf,
}

ADXL345_DEV_ID = 0xe5
SET_FIFO_CTL = 0x90

FREEFALL_ACCEL = 9.80665 * 1000.
SCALE = 0.0039 * FREEFALL_ACCEL # 3.9mg/LSB * Earth gravity in mm/s**2
SCALE_XY = 0.003774 * FREEFALL_ACCEL # 1 / 265 (at 3.3V) mg/LSB
SCALE_Z  = 0.003906 * FREEFALL_ACCEL # 1 / 256 (at 3.3V) mg/LSB

CALIBRATION_NOISE_THRESHOLD = 1e4
ACCELERATION_OUTLIER_THRESHOLD = FREEFALL_ACCEL * 5.

DUR_SCALE = 0.000625 # 0.625 msec / LSB
TAP_SCALE = 0.0625 * FREEFALL_ACCEL # 62.5mg/LSB * Earth gravity in mm/s**2
OFS_SCALE = 0.0156 * FREEFALL_ACCEL # 15.6mg/LSB * Earth gravity in mm/s**2

PROBE_CALIBRATION_TIME = 1.
ADXL345_REST_TIME = .01

Accel_Measurement = collections.namedtuple(
    'Accel_Measurement', ('time', 'accel_x', 'accel_y', 'accel_z'))
        
        
class BedOffsetHelper:
    def __init__(self, config):
        self.printer = config.get_printer()
        # Register BED_OFFSET_CALIBRATE command
        zconfig = config.getsection('stepper_z')
        self.z_position_endstop = zconfig.getfloat('position_endstop', None,
                                                   note_valid=False)
        if self.z_position_endstop is None:
            return
        self.bed_probe_point = None
        if config.get('bed_probe_point', None) is not None:
            try:
                self.bed_probe_point = [
                        float(coord.strip()) for coord in
                        config.get('bed_probe_point').split(',', 1)]
            except:
                raise config.error(
                        "Unable to parse bed_probe_point '%s'" % (
                            config.get('bed_probe_point')))
            self.horizontal_move_z = config.getfloat(
                    'horizontal_move_z', 5.)
            self.horizontal_move_speed = config.getfloat(
                    'horizontal_move_speed', 50., above=0.)
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command(
            'BED_OFFSET_CALIBRATE', self.cmd_BED_OFFSET_CALIBRATE,
            desc=self.cmd_BED_OFFSET_CALIBRATE_help)
    def bed_offset_finalize(self, pos, gcmd):
        if pos is None:
            return
        z_pos = self.z_position_endstop - pos[2]
        gcmd.respond_info(
            "stepper_z: position_endstop: %.3f\n"
            "The SAVE_CONFIG command will update the printer config file\n"
            "with the above and restart the printer." % (z_pos,))
        configfile = self.printer.lookup_object('configfile')
        configfile.set('stepper_z', 'position_endstop', "%.3f" % (z_pos,))
    cmd_BED_OFFSET_CALIBRATE_help = "Calibrate a bed offset using ADXL345 probe"
    def cmd_BED_OFFSET_CALIBRATE(self, gcmd):
        manual_probe.verify_no_manual_probe(self.printer)
        probe = self.printer.lookup_object('probe')
        lift_speed = probe.get_lift_speed(gcmd)
        toolhead = self.printer.lookup_object('toolhead')
        oldpos = toolhead.get_position()
        if self.bed_probe_point is not None:
            toolhead.manual_move([None, None, self.horizontal_move_z],
                                 lift_speed)
            toolhead.manual_move(self.bed_probe_point + [None],
                                 self.horizontal_move_speed)
        curpos = probe.run_probe(gcmd)
        offset_pos = [0., 0., curpos[2] - probe.get_offsets()[2]]
        if self.bed_probe_point is not None:
            curpos[2] = self.horizontal_move_z
        else:
            curpos[2] = oldpos[2]
        toolhead.manual_move(curpos, lift_speed)
        self.bed_offset_finalize(offset_pos, gcmd)


class ADXL345EndstopWrapper:
    def __init__(self, config, adxl345, axes_map):
        self.printer = config.get_printer()
        self.config = config
       # self.printer.register_event_handler("klippy:connect", self.calibrate)
        self.adxl345 = adxl345
        self.axes_map = axes_map
        self.ofs_regs = (REG_OFSX, REG_OFSY, REG_OFSZ)
        int_pin = config.get('int_pin').strip()
        self.inverted = False
        if int_pin.startswith('!'):
            self.inverted = True
            int_pin = int_pin[1:].strip()
        if int_pin != 'int1' and int_pin != 'int2':
            raise config.error('int_pin must specify one of int1 or int2 pins')
        self.int_map = 0x40 if int_pin == 'int2' else 0x0
        probe_pin = config.get('probe_pin')
        self.position_endstop = config.getfloat('z_offset')
        self.tap_thresh = config.getfloat('tap_thresh', 5000,
                                          minval=TAP_SCALE, maxval=100000.)
        self.tap_dur = config.getfloat('tap_dur', 0.01,
                                       above=DUR_SCALE, maxval=0.1)
        self.next_cmd_time = self.action_end_time = 0.
        # Create an "endstop" object to handle the sensor pin
        ppins = self.printer.lookup_object('pins')
        pin_params = ppins.lookup_pin(probe_pin, can_invert=True,
                                      can_pullup=True)
        mcu = pin_params['chip']
        mcu.register_config_callback(self._build_config)
        self.mcu_endstop = mcu.setup_pin('endstop', pin_params)
        # Wrappers
        self.get_mcu = self.mcu_endstop.get_mcu
        self.add_stepper = self.mcu_endstop.add_stepper
        self.get_steppers = self.mcu_endstop.get_steppers
        self.home_start = self.mcu_endstop.home_start
        self.home_wait = self.mcu_endstop.home_wait
        self.query_endstop = self.mcu_endstop.query_endstop
        # Register commands
        gcode = self.printer.lookup_object('gcode')
        # gcode.register_mux_command(
        #         "ACCEL_PROBE_CALIBRATE", "CHIP", None,
        #         self.cmd_SET_ADXL_PROBE,
        #         desc=self.cmd_ACCEL_PROBE_CALIBRATE_help)
        # gcode.register_mux_command(
        #         "SET_ACCEL_PROBE", "CHIP", None, self.cmd_SET_ACCEL_PROBE,
        #         desc=self.cmd_SET_ACCEL_PROBE_help)
        # Register bed offset calibration helper
        BedOffsetHelper(config)
    def _build_config(self):
        kin = self.printer.lookup_object('toolhead').get_kinematics()
        for stepper in kin.get_steppers():
            if stepper.is_active_axis('z'):
                self.add_stepper(stepper)
    def calibrate(self, gcmd=None, retries=3):
        adxl345 = self.adxl345
        # if not adxl345.is_initialized():
             # ADXL345 that works as a probe must be initialized from the start
        #     adxl345.initialize()
        adxl345.set_reg(REG_POWER_CTL, 0x00)
        if self.inverted:
            adxl345.set_reg(REG_DATA_FORMAT, 0x2B)
        adxl345.set_reg(REG_INT_MAP, self.int_map)
        adxl345.set_reg(REG_TAP_AXES, 0x7)
        adxl345.set_reg(REG_THRESH_TAP, int(self.tap_thresh / TAP_SCALE))
        adxl345.set_reg(REG_DUR, int(self.tap_dur / DUR_SCALE))
        # Offset freefall accleration on the true Z axis
        for reg in self.ofs_regs:
            adxl345.set_reg(reg, 0x00)
        res = adxl345.start_internal_client()
        reactor = self.printer.get_reactor()
        reactor.register_callback(lambda ev: self._offset_axes(gcmd, retries, res),
                                  reactor.monotonic() + PROBE_CALIBRATION_TIME)
    def _offset_axes(self, gcmd, retries, res):
        logging.info(str(res))
        msg_func = gcmd.respond_info if gcmd is not None else logging.info
        res.finish_measurements()
        samples = res.get_samples()
        x_ofs = sum([s.accel_x for s in samples]) / len(samples)
        y_ofs = sum([s.accel_y for s in samples]) / len(samples)
        z_ofs = sum([s.accel_z for s in samples]) / len(samples)
        meas_freefall_accel = math.sqrt(x_ofs**2 + y_ofs**2 + z_ofs**2)
        if abs(meas_freefall_accel - FREEFALL_ACCEL) > FREEFALL_ACCEL * 0.5:
            err_msg = ("Calibration error: ADXL345 incorrectly measures "
                       "freefall accleration: %.0f (measured) vs %.0f "
                       "(expected)" % (meas_freefall_accel, FREEFALL_ACCEL))
            if retries > 0:
                msg_func(err_msg + ", retrying (%d)" % (retries-1,))
                self.calibrate(gcmd, retries-1)
            else:
                msg_func(err_msg + ", aborting self-calibration")
            return
        x_m = max([abs(s.accel_x - x_ofs) for s in samples])
        y_m = max([abs(s.accel_y - y_ofs) for s in samples])
        z_m = max([abs(s.accel_z - z_ofs) for s in samples])
        accel_noise = max(x_m, y_m, z_m)
        if accel_noise > self.tap_thresh:
            err_msg = ("Calibration error: ADXL345 noise level too high for "
                       "the configured tap_thresh: %.0f (tap_thresh) vs "
                       "%.0f (noise)" % (self.tap_thresh, accel_noise))
            if retries > 0:
                msg_func(err_msg + ", retrying (%d)" % (retries-1,))
                self.calibrate(gcmd, retries-1)
            else:
                msg_func(err_msg + ", aborting self-calibration")
            return
        for ofs, axis in zip((x_ofs, y_ofs, z_ofs), (0, 1, 2)):
            ofs_reg = self.ofs_regs[self.axes_map[axis][0]]
            ofs_val = 0xFF & int(round(
                -ofs / self.axes_map[axis][1] * (SCALE / OFS_SCALE)))
            self.adxl345.set_reg(ofs_reg, ofs_val)
        msg_func("Successfully calibrated ADXL345")
        self.calibrated = True
    def multi_probe_begin(self):
        pass
    def multi_probe_end(self):
        pass
    def _try_clear_tap(self):
        adxl345 = self.adxl345
        tries = 8
        while tries > 0:
            val = adxl345.read_reg(REG_INT_SOURCE)
            if not (val & 0x40):
                return True
            tries -= 1
        return False
    def probe_prepare(self, hmove):
        # if not self.calibrated:
        #     raise self.printer.command_error(
        #             "ADXL345 probe failed calibration, "
        #             "retry with ACCEL_PROBE_CALIBRATE command")
        adxl345 = self.adxl345
        #self.adxl345.set_reg(REG_POWER_CTL, 0x00)
        # if self.inverted:
        #     adxl345.set_reg(REG_DATA_FORMAT, 0x2B)
        # adxl345.set_reg(REG_INT_MAP, self.int_map)
        # adxl345.set_reg(REG_TAP_AXES, 0x7)
        # adxl345.set_reg(REG_THRESH_TAP, int(self.tap_thresh / TAP_SCALE))
        # adxl345.set_reg(REG_DUR, int(self.tap_dur / DUR_SCALE))
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.flush_step_generation()
        print_time = toolhead.get_last_move_time()
        clock = self.adxl345.get_mcu().print_time_to_clock(print_time +
                                                           ADXL345_REST_TIME)
       # if not adxl345.is_initialized():
       #     adxl345.initialize()
        adxl345.set_reg(REG_INT_ENABLE, 0x00, minclock=clock)
        adxl345.read_reg(REG_INT_SOURCE)
        adxl345.set_reg(REG_INT_ENABLE, 0x40, minclock=clock)
        # if not adxl345.is_measuring():
        #     adxl345.set_reg(REG_POWER_CTL, 0x08, minclock=clock)
        if not self._try_clear_tap():
            raise self.printer.command_error(
                    "ADXL345 tap triggered before move, too sensitive?")
    def probe_finish(self, hmove):
        adxl345 = self.adxl345
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.dwell(ADXL345_REST_TIME)
        print_time = toolhead.get_last_move_time()
        clock = adxl345.get_mcu().print_time_to_clock(print_time)
        adxl345.set_reg(REG_INT_ENABLE, 0x00, minclock=clock)
        # if not adxl345.is_measuring():
        #     adxl345.set_reg(REG_POWER_CTL, 0x00)
        if not self._try_clear_tap():
            raise self.printer.command_error(
                    "ADXL345 tap triggered after move, too sensitive?")
            
    # cmd_ACCEL_PROBE_CALIBRATE_help = "Force ADXL345 probe [re-]calibration"
    # def cmd_SET_ADXL_PROBE(self, gcmd):
    #     adxl345 = self.adxl345
    #     if self.inverted:
    #         adxl345.set_reg(REG_DATA_FORMAT, 0x2B)
    #     adxl345.set_reg(REG_INT_MAP, self.int_map)
    #     adxl345.set_reg(REG_TAP_AXES, 0x7)
    #     adxl345.set_reg(REG_THRESH_TAP, int(self.tap_thresh / TAP_SCALE))
    #     adxl345.set_reg(REG_DUR, int(self.tap_dur / DUR_SCALE))
    # cmd_SET_ACCEL_PROBE_help = "Configure ADXL345 parameters related to probing"
    # def cmd_SET_ACCEL_PROBE(self, gcmd):
    #     adxl345 = self.adxl345
        
    #     self.tap_thresh = gcmd.get_float('TAP_THRESH', self.tap_thresh,
    #                                      minval=TAP_SCALE, maxval=100000.)
    #     self.tap_dur = self.config.getfloat('TAP_DUR', self.tap_dur,
    #                                    above=DUR_SCALE, maxval=0.1)
    #     adxl345.set_reg(REG_THRESH_TAP, int(self.tap_thresh / TAP_SCALE))
    #     adxl345.set_reg(REG_DUR, int(self.tap_dur / DUR_SCALE))

# Helper class to obtain measurements
class ADXL345QueryHelper:
    def __init__(self, printer, cconn):
        self.printer = printer
        self.cconn = cconn
        print_time = printer.lookup_object('toolhead').get_last_move_time()
        self.request_start_time = self.request_end_time = print_time
        self.samples = self.raw_samples = []
        self.errors = self.overflows = None
    def finish_measurements(self):
        toolhead = self.printer.lookup_object('toolhead')
        self.request_end_time = toolhead.get_last_move_time()
        toolhead.wait_moves()
        self.cconn.finalize()
    def _get_raw_samples(self):
        raw_samples = self.cconn.get_messages()
        if raw_samples:
            self.raw_samples = raw_samples
        return self.raw_samples
    def has_valid_samples(self):
        raw_samples = self._get_raw_samples()
        for msg in raw_samples:
            data = msg['params']['data']
            first_sample_time = data[0][0]
            last_sample_time = data[-1][0]
            if (first_sample_time > self.request_end_time
                    or last_sample_time < self.request_start_time):
                continue
            # The time intervals [first_sample_time, last_sample_time]
            # and [request_start_time, request_end_time] have non-zero
            # intersection. It is still theoretically possible that none
            # of the samples from raw_samples fall into the time interval
            # [request_start_time, request_end_time] if it is too narrow
            # or on very heavy data losses. In practice, that interval
            # is at least 1 second, so this possibility is negligible.
            return True
        return False
    def get_samples(self):
        raw_samples = self._get_raw_samples()
        if not raw_samples:
            return self.samples
        total = sum([len(m['params']['data']) for m in raw_samples])
        count = 0
        self.samples = samples = [None] * total
        for msg in raw_samples:
            for samp_time, x, y, z in msg['params']['data']:
                if samp_time < self.request_start_time:
                    continue
                if samp_time > self.request_end_time:
                    break
                samples[count] = Accel_Measurement(samp_time, x, y, z)
                count += 1
        del samples[count:]
        return self.samples
    def get_num_errors(self):
        if self.errors is not None:
            return self.errors
        raw_samples = self._get_raw_samples()
        errors = 0
        for msg in raw_samples:
            errors += msg['params']['errors']
        self.errors = errors
        return self.errors
    def get_num_overflows(self):
        if self.overflows is not None:
            return self.overflows
        raw_samples = self._get_raw_samples()
        overflows = 0
        for msg in raw_samples:
            overflows += msg['params']['overflows']
        self.overflows = overflows
        return self.overflows
    def write_to_file(self, filename):
        def write_impl():
            try:
                # Try to re-nice writing process
                os.nice(20)
            except:
                pass
            f = open(filename, "w")
            f.write("#time,accel_x,accel_y,accel_z\n")
            samples = self.samples or self.get_samples()
            for t, accel_x, accel_y, accel_z in samples:
                f.write("%.6f,%.6f,%.6f,%.6f\n" % (
                    t, accel_x, accel_y, accel_z))
            f.close()
        write_proc = multiprocessing.Process(target=write_impl)
        write_proc.daemon = True
        write_proc.start()

class AccelerometerCalibrator:
    def __init__(self, printer, chip):
        self.printer = printer
        self.gcode = self.printer.lookup_object('gcode')
        self.chip = chip
        self.bgr_exec = functools.partial(
                background_process.background_process_exec, printer)
        # Test parameters
        self.max_accel = 500.
        self.freefall_test_sec = 15.
        self.move_test_runs = 25
        self.move_test_len = 8. # Should work well with GT-2 belts
    def _run_wait_test(self, toolhead, gcmd):
        aclient = self.chip.start_internal_client()
        freefall_test_sec = gcmd.get_float('FREEFALL_TEST_SEC',
                                           self.freefall_test_sec,
                                           minval=1, maxval=100)
        toolhead.dwell(freefall_test_sec)
        aclient.finish_measurements()
        return aclient
    def _run_move_test(self, toolhead, axis_dir, gcmd):
        X, Y, Z, E = toolhead.get_position()
        systime = self.printer.get_reactor().monotonic()
        toolhead_info = toolhead.get_status(systime)
        max_accel = gcmd.get_float('ACCEL', toolhead_info['max_accel'],
                                   minval=100)
        test_runs = gcmd.get_int('RUNS', self.move_test_runs, minval=3)
        max_accel_to_decel = toolhead_info['max_accel_to_decel']
        # The test runs as follows:
        # * accelerate for t_seg/2 time
        # * cruise for t_seg time
        # * decelerate for t_seg/2 time
        # * accelerate for t_seg/2 time in reverse direction
        # .....
        L = gcmd.get_float('LENGTH', self.move_test_len, minval=1, maxval=10)
        accel = min(self.max_accel, 6. * max_accel_to_decel, max_accel)
        t_seg = math.sqrt(L / (.75 * accel))
        freq = .25 / t_seg
        max_v = .5 * t_seg * accel
        toolhead.cmd_M204(self.gcode.create_gcode_command(
            'M204', 'M204', {'S': accel}))
        nX = X + axis_dir[0] * L
        nY = Y + axis_dir[1] * L
        aclient = self.chip.start_internal_client()
        print_time = toolhead.get_last_move_time()
        time_points = []
        try:
            for i in range(test_runs):
                toolhead.move([nX, nY, Z, E], max_v)
                prev_print_time = print_time
                print_time = toolhead.get_last_move_time()
                time_points.append((print_time + prev_print_time) * .5)
                toolhead.move([X, Y, Z, E], max_v)
                prev_print_time = print_time
                print_time = toolhead.get_last_move_time()
                time_points.append((print_time + prev_print_time) * .5)
        finally:
            aclient.finish_measurements()
            toolhead.cmd_M204(self.gcode.create_gcode_command(
                'M204', 'M204', {'S': max_accel}))
        return (accel, time_points, aclient)
    def _compute_freefall_accel(self, data):
        samples = self.np.asarray(data.get_samples())
        g = samples[:, 1:].mean(axis=0)
        freefall_accel = self.np.linalg.norm(g)
        # Calculate the standard deviation and coefficient of variance
        accel_cov = self.np.std(samples[:, 1:], axis=0, ddof=1) / freefall_accel
        return freefall_accel, g, accel_cov
    def _compute_measured_accel(self, time_points, data):
        np = self.np
        samples = np.asarray(data.get_samples())
        # Sort all accelerometer readings by their timestamp
        sort_ind = np.argsort(samples[:, 0])
        sorted_samples = samples[sort_ind]
        # Integrate acceleration to obtain velocity change
        dt = sorted_samples[1:, 0] - sorted_samples[:-1, 0]
        avg_accel = .5 * (sorted_samples[1:, 1:] + sorted_samples[:-1, 1:])
        # Find integration boundaries as indices
        time_ind = np.searchsorted(sorted_samples[:, 0], time_points)
        # reduceat applies add to consequtive ranges specified by indices:
        # add(array[indices[0]:indices[1]), add(array[indices[1]:indices[2]),
        # and so forth up to the last entry add(array[indices[-1]:]), which
        # should be discarded
        delta_v = np.add.reduceat(array=(avg_accel * dt[:, np.newaxis]),
                                  indices=time_ind)[:-1]
        # Now calculate the average acceleration over several moves
        delta_t = [t2 - t1 for t1, t2 in zip(time_points[:-1], time_points[1:])]
        a = np.zeros(shape=3)
        sign = -1
        for i in range(delta_v.shape[0]):
            a += sign * delta_v[i] / delta_t[i]
            sign = -sign
        # Acceleration is active only half of the time
        a *= 2. / delta_v.shape[0]
        measured_accel = np.linalg.norm(a)
        return measured_accel, a
    def _calculate_axes_transform(self):
        linalg = self.np.linalg
        A = self.np.zeros(shape=(3, 3))
        if 'x' in self.results:
            A[:,0] = self.results['x']
        else:
            a_y = self.np.asarray(self.results['y'])
            a_g = self.np.asarray(self.results['g'])
            # Exact X axis direction does not matter, so
            # creating the standard right-handed coordinate system
            a_x = self.np.cross(a_y, a_g)
            A[:,0] = a_x / linalg.norm(a_x)
        if 'y' in self.results:
            A[:,1] = self.results['y']
        else:
            a_x = self.np.asarray(self.results['x'])
            a_g = self.np.asarray(self.results['g'])
            # Exact Y axis direction does not matter, so
            # creating the standard right-handed coordinate system
            a_y = self.np.cross(a_g, a_x)
            A[:,1] = a_y / linalg.norm(a_y)
        a_z = self.np.cross(A[:,0], A[:,1])
        A[:,2] = a_z / linalg.norm(a_z)
        self.axes_transform = linalg.inv(A)
    def _get_chip_name(self):
        return self.chip.get_config().get_name()
    def _save_offset(self, gcmd):
        chip_name = self._get_chip_name()
        str_val = ','.join(['%.1f' % (coeff,) for coeff in self.offset])
        configfile = self.printer.lookup_object('configfile')
        configfile.set(chip_name, 'offset', str_val)
        self.chip.set_transform(self.axes_transform, self.offset)
        gcmd.respond_info(
                _("SAVE_CONFIG command will update %s configuration with "
                "offset = %s parameter") % (chip_name, str_val))
    def _save_axes_transform(self, gcmd):
        chip_name = self._get_chip_name()
        configfile = self.printer.lookup_object('configfile')
        if self.chip.get_config().get('axes_map', None, note_valid=False):
            configfile.set(chip_name, 'axes_map', '')
        str_val = '\n'.join([','.join(['%.9f' % (coeff,) for coeff in axis])
                             for axis in self.axes_transform])
        configfile.set(chip_name, 'axes_transform', '\n' + str_val)
        self.chip.set_transform(self.axes_transform, self.offset)
        gcmd.respond_info(
                _("SAVE_CONFIG command will also update %s configuration with "
                "axes_transform =\n%s") % (chip_name, str_val))
    def _calibrate_offset(self, toolhead, gcmd, debug_output):
        gcmd.respond_info(_("Measuring freefall acceleration"))
        data = self._run_wait_test(toolhead, gcmd)
        if not data.has_valid_samples():
            raise gcmd.error(_("No accelerometer measurements found"))
        if debug_output is not None:
            filename = "/tmp/%s-%s-g.csv" % (
                    self._get_chip_name().replace(' ', '-'), debug_output)
            gcmd.respond_info(_("Writing raw debug accelerometer data to %s file")
                              % (filename,))
            data.write_to_file(filename)
        errors, overflows = data.get_num_errors(), data.get_num_overflows()
        if errors > 0:
            gcmd.respond_info(
                    _("WARNING: detected %d accelerometer reading errors. This "
                    "may be caused by electromagnetic interference on the "
                    "cables or problems with the sensor. This may severerly "
                    "impact the calibration and resonance testing results.") % (
                        errors,))
        if overflows > 0:
            gcmd.respond_info(
                    _("WARNING: detected %d accelerometer queue overflows. This "
                    "may happen if the accelerometer is connected to a slow "
                    "board that cannot read data fast enough, or in case of "
                    "communication errors. This may severerly impact the "
                    "calibration and resonance testing results.") % (overflows,))
        helper = shaper_calibrate.ShaperCalibrate(self.printer)
        processed_data = helper.process_accelerometer_data(data)
        self.np = processed_data.get_numpy()
        max_acc = self.bgr_exec(
                lambda: self.np.amax(
                    self.np.asarray(data.get_samples())[:, 1:]), ())
        if max_acc > ACCELERATION_OUTLIER_THRESHOLD:
            gcmd.respond_info(
                    _("WARNING: large acceleration reading detected (%.1f "
                    "mm/sec^2). This generally indicates communication "
                    "errors with the accelerometer (e.g. because of "
                    "electromagnetic interference on the cables or "
                    "problems with the sensor). This may severerly impact "
                    "the calibration and resonance testing results.") % (
                        max_acc,))
        psd = processed_data.get_psd()
        max_noise_ind = psd.argmax()
        if psd[max_noise_ind] > CALIBRATION_NOISE_THRESHOLD:
            gcmd.respond_info(
                    _("WARNING: strong periodic noise detected at %.1f Hz. This "
                    "could be a loud unbalanced fan or some other devices "
                    "working nearby. Please turn off fans (e.g. hotend fan) "
                    "and other sources of vibrations for accelerometer "
                    "calibration and resonance testing for best results.") % (
                        processed_data.get_freq_bins()[max_noise_ind],))
        freefall_accel, g, accel_cov = self.bgr_exec(
                self._compute_freefall_accel, (data,))
        if abs(freefall_accel - FREEFALL_ACCEL) > .2 * FREEFALL_ACCEL:
            chip_name = self._get_chip_name()
            raise gcmd.error(_("%s is defunct: measured invalid freefall accel "
                             "%.3f (mm/sec^2) vs ~ %.3f (mm/sec^2)") % (
                                 chip_name, freefall_accel, FREEFALL_ACCEL))
        gcmd.respond_info(
                _("Accelerometer noise: %s (coefficients of variance)") % (
                    ', '.join(['%.2f%% (%s)' % (val * 100., axis)
                               for val, axis in zip(accel_cov, 'xyz')]),))
        self.results['g'] = g / freefall_accel
        self.offset = g
        gcmd.respond_info(_("Detected freefall acceleration %.2f mm/sec^2 "
                          "in the direction: %s") % (freefall_accel, ', '.join(
                              ['%.6f' % (val,) for val in self.results['g']])))
        self._save_offset(gcmd)
    def _calibrate_xy_axis(self, axis, axis_dir, toolhead, gcmd, debug_output):
        gcmd.respond_info(_("Calibrating %s axis") % (axis,))
        chip_name = self._get_chip_name()
        accel, time_points, data = self._run_move_test(toolhead, axis_dir, gcmd)
        if not data.has_valid_samples():
            raise gcmd.error(_("No accelerometer measurements found"))
        if debug_output is not None:
            filename = "/tmp/%s-%s-%s.csv" % (
                    self._get_chip_name().replace(' ', '-'), debug_output, axis)
            gcmd.respond_info(_("Writing raw debug accelerometer data to %s file")
                              % (filename,))
            data.write_to_file(filename)
        measured_accel, a = self.bgr_exec(self._compute_measured_accel,
                                          (time_points, data))
        if measured_accel > .2 * accel:
            if abs(measured_accel - accel) > .5 * accel:
                raise gcmd.error(
                        _("%s measured spurious acceleration on %s axis: "
                        "%.3f vs %.3f (mm/sec^2)") % (chip_name, axis,
                                                     measured_accel, accel))
            self.results[axis] = a / measured_accel
            gcmd.respond_info(
                    _("Detected %s direction: %s") % (axis, ', '.join(
                        ['%.6f' % (val,) for val in self.results[axis]])))
        else:
            gcmd.respond_info(_("%s is not kinematically connected to the "
                              "movement of %s axis") % (chip_name, axis))
    def calibrate(self, gcmd, debug_output=None):
        toolhead = self.printer.lookup_object('toolhead')
        reactor = self.printer.get_reactor()
        # Reset adxl345 transformations
        self.axes_transform = [[1., 0., 0.],
                               [0., 1., 0.],
                               [0., 0., 1.]]
        self.offset = [0., 0., 0.]
        self.chip.set_transform(self.axes_transform, self.offset)
        self.results = {}
        self._calibrate_offset(toolhead, gcmd, debug_output)
        reactor.pause(reactor.monotonic() + 0.1)
        self._calibrate_xy_axis('x', (1., 0.), toolhead, gcmd, debug_output)
        reactor.pause(reactor.monotonic() + 0.1)
        self._calibrate_xy_axis('y', (0., 1.), toolhead, gcmd, debug_output)
        reactor.pause(reactor.monotonic() + 0.1)
        if 'x' not in self.results and 'y' not in self.results:
            raise gcmd.error(
                    _("%s is not kinematically connected to either of X or "
                    "Y printer axis, impossible to calibrate automatically. "
                    "Please manually set axes_map parameter.") % (
                        self._get_chip_name(),))
        if 'x' in self.results and 'y' in self.results:
            cos_xy = self.np.dot(self.results['x'], self.results['y'])
            angle_xy = math.acos(cos_xy) * 180. / math.pi
            gcmd.respond_info(
                    _("Detected angle between X and Y axes is %.2f degrees") % (
                        angle_xy,))
        gcmd.respond_info(_("Computing axes transform"))
        self._calculate_axes_transform()
        self._save_axes_transform(gcmd)
        
# Helper class to obtain measurements
class AccelQueryHelper:
    def __init__(self, printer):
        self.printer = printer
        self.is_finished = False
        print_time = printer.lookup_object('toolhead').get_last_move_time()
        self.request_start_time = self.request_end_time = print_time
        self.msgs = []
        self.samples = []
    def finish_measurements(self):
        toolhead = self.printer.lookup_object('toolhead')
        self.request_end_time = toolhead.get_last_move_time()
        toolhead.wait_moves()
        self.is_finished = True
    def handle_batch(self, msg):
        if self.is_finished:
            return False
        if len(self.msgs) >= 10000:
            # Avoid filling up memory with too many samples
            return False
        self.msgs.append(msg)
        return True
    def has_valid_samples(self):
        for msg in self.msgs:
            data = msg['data']
            first_sample_time = data[0][0]
            last_sample_time = data[-1][0]
            if (first_sample_time > self.request_end_time
                    or last_sample_time < self.request_start_time):
                continue
            # The time intervals [first_sample_time, last_sample_time]
            # and [request_start_time, request_end_time] have non-zero
            # intersection. It is still theoretically possible that none
            # of the samples from msgs fall into the time interval
            # [request_start_time, request_end_time] if it is too narrow
            # or on very heavy data losses. In practice, that interval
            # is at least 1 second, so this possibility is negligible.
            return True
        return False
    def get_samples(self):
        if not self.msgs:
            return self.samples
        total = sum([len(m['data']) for m in self.msgs])
        count = 0
        self.samples = samples = [None] * total
        for msg in self.msgs:
            for samp_time, x, y, z in msg['data']:
                if samp_time < self.request_start_time:
                    continue
                if samp_time > self.request_end_time:
                    break
                samples[count] = Accel_Measurement(samp_time, x, y, z)
                count += 1
        del samples[count:]
        return self.samples
    def write_to_file(self, filename):
        def write_impl():
            try:
                # Try to re-nice writing process
                os.nice(20)
            except:
                pass
            f = open(filename, "w")
            f.write("#time,accel_x,accel_y,accel_z\n")
            samples = self.samples or self.get_samples()
            for t, accel_x, accel_y, accel_z in samples:
                f.write("%.6f,%.6f,%.6f,%.6f\n" % (
                    t, accel_x, accel_y, accel_z))
            f.close()
        write_proc = multiprocessing.Process(target=write_impl)
        write_proc.daemon = True
        write_proc.start()
        
# Helper class for G-Code commands
class ADXLCommandHelper:
    def __init__(self, config, chip):
        self.printer = config.get_printer()
        self.chip = chip
        self.inverted = False
        int_pin = config.get('int_pin').strip()
        self.int_map = 0x40 if int_pin == 'int2' else 0x0
        if int_pin.startswith('!'):
            self.inverted = True
        self.bg_client = None
        self.name = config.get_name().split()[-1]
        self.tap_thresh = config.getfloat('tap_thresh', 5000,
                                          minval=TAP_SCALE, maxval=100000.)
        self.tap_dur = config.getfloat('tap_dur', 0.01,
                                       above=DUR_SCALE, maxval=0.1)
        self.register_commands(self.name)
        if self.name == "ADXL":
            self.register_commands(None)
    def register_commands(self, name):
        # Register commands
        gcode = self.printer.lookup_object('gcode')
        gcode.register_mux_command("ACCELEROMETER_MEASURE", "CHIP", name,
                                   self.cmd_ACCELEROMETER_MEASURE,
                                   desc=self.cmd_ACCELEROMETER_MEASURE_help)
        gcode.register_mux_command("ACCELEROMETER_QUERY", "CHIP", name,
                                   self.cmd_ACCELEROMETER_QUERY,
                                   desc=self.cmd_ACCELEROMETER_QUERY_help)
        gcode.register_mux_command("ACCELEROMETER_CALIBRATE", "CHIP", name,
                                   self.cmd_ACCELEROMETER_CALIBRATE,
                                   desc=self.cmd_ACCELEROMETER_CALIBRATE_help)
        gcode.register_mux_command("ACCELEROMETER_DEBUG_READ", "CHIP", name,
                                   self.cmd_ACCELEROMETER_DEBUG_READ,
                                   desc=self.cmd_ACCELEROMETER_DEBUG_READ_help)
        gcode.register_mux_command("ACCELEROMETER_DEBUG_WRITE", "CHIP", name,
                                   self.cmd_ACCELEROMETER_DEBUG_WRITE,
                                   desc=self.cmd_ACCELEROMETER_DEBUG_WRITE_help)
        gcode.register_mux_command(
                "SET_ADXL_PROBE", "CHIP", name,
                self.cmd_SET_ADXL_PROBE,
                desc=self.cmd_ACCEL_PROBE_CALIBRATE_help)
        gcode.register_mux_command(
                "SET_ACCEL_PROBE", "CHIP", name, self.cmd_SET_ACCEL_PROBE,
                desc=self.cmd_SET_ACCEL_PROBE_help)
    
    cmd_ACCEL_PROBE_CALIBRATE_help = "Force ADXL345 probe [re-]calibration"
    def cmd_SET_ADXL_PROBE(self, gcmd):
        adxl345 = self.chip
        adxl345.set_reg(0x2e, 0x40)
        adxl345.set_reg(0x2f, 0x40)
        adxl345.set_reg(0x27, 0x10)
        adxl345.set_reg(0x2a, 0x01)
        adxl345.set_reg(0x2d, 0x08)
        adxl345.set_reg(0x28, 0x08)
        adxl345.set_reg(REG_THRESH_TAP, int(self.tap_thresh / TAP_SCALE))
        adxl345.set_reg(REG_DUR, int(self.tap_dur / DUR_SCALE))
        # adxl345.set_reg(REG_INT_ENABLE, 0x40)
        # if self.inverted:
        #     adxl345.set_reg(REG_DATA_FORMAT, 0x2B)
        # adxl345.set_reg(REG_INT_MAP, self.int_map)
        # adxl345.set_reg(REG_TAP_AXES, 0x7)
        # adxl345.set_reg(REG_THRESH_TAP, int(self.tap_thresh / TAP_SCALE))
        # adxl345.set_reg(REG_DUR, int(self.tap_dur / DUR_SCALE))
    cmd_SET_ACCEL_PROBE_help = "Configure ADXL345 parameters related to probing"
    def cmd_SET_ACCEL_PROBE(self, gcmd):
        adxl345 = self.chip
        
        self.tap_thresh = gcmd.get_float('TAP_THRESH', self.tap_thresh,
                                         minval=TAP_SCALE, maxval=100000.)
        self.tap_dur = self.config.getfloat('TAP_DUR', self.tap_dur,
                                       above=DUR_SCALE, maxval=0.1)
        adxl345.set_reg(REG_THRESH_TAP, int(self.tap_thresh / TAP_SCALE))
        adxl345.set_reg(REG_DUR, int(self.tap_dur / DUR_SCALE))
        
    cmd_ACCELEROMETER_MEASURE_help = _("Start/stop accelerometer")
    def cmd_ACCELEROMETER_MEASURE(self, gcmd):
        if self.bg_client is None:
            # Start measurements
            self.bg_client = self.chip.start_internal_client()
            gcmd.respond_info(_("adxl345 measurements started"))
            return
        # End measurements
        name = gcmd.get("NAME", time.strftime("%Y%m%d_%H%M%S"))
        if not name.replace('-', '').replace('_', '').isalnum():
            raise gcmd.error(_("Invalid adxl345 NAME parameter"))
        bg_client = self.bg_client
        self.bg_client = None
        bg_client.finish_measurements()
        # Write data to file
        if self.name == "adxl345":
            filename = "/tmp/adxl345-%s.csv" % (name,)
        else:
            filename = "/tmp/adxl345-%s-%s.csv" % (self.name, name,)
        bg_client.write_to_file(filename)
        gcmd.respond_info(_("Writing raw accelerometer data to %s file")
                          % (filename,))
    cmd_ACCELEROMETER_QUERY_help = _("Query accelerometer for the current values")
    def cmd_ACCELEROMETER_QUERY(self, gcmd):
        aclient = self.chip.start_internal_client()
        self.printer.lookup_object('toolhead').dwell(1.)
        aclient.finish_measurements()
        values = aclient.get_samples()
        if not values:
            raise gcmd.error(_("No adxl345 measurements found"))
        __, accel_x, accel_y, accel_z = values[-1]
        gcmd.respond_info(_("adxl345 values (x, y, z): %.6f, %.6f, %.6f")
                          % (accel_x, accel_y, accel_z))
    cmd_ACCELEROMETER_CALIBRATE_help = _("Automatically calibrate accelerometer")
    def cmd_ACCELEROMETER_CALIBRATE(self, gcmd):
        debug_output = gcmd.get("DEBUG_OUTPUT", None)
        if (debug_output is not None and
                not debug_output.replace('-', '').replace('_', '').isalnum()):
            raise gcmd.error(_("Invalid OUTPUT parameter"))
        AccelerometerCalibrator(self.printer, self.chip).calibrate(gcmd,
                                                                   debug_output)
    cmd_ACCELEROMETER_DEBUG_READ_help = _("Query adxl345 register (for debugging)")
    def cmd_ACCELEROMETER_DEBUG_READ(self, gcmd):
        reg = gcmd.get("REG", minval=29, maxval=57, parser=lambda x: int(x, 0))
        val = self.chip.read_reg(reg)
        gcmd.respond_info(_("ADXL345 REG[0x%x] = 0x%x") % (reg, val))
        
    
    cmd_ACCELEROMETER_DEBUG_WRITE_help = _("Set adxl345 register (for debugging)")
    def cmd_ACCELEROMETER_DEBUG_WRITE(self, gcmd):
        reg = gcmd.get("REG", minval=29, maxval=57, parser=lambda x: int(x, 0))
        val = gcmd.get("VAL", minval=0, maxval=255, parser=lambda x: int(x, 0))
        self.chip.set_reg(reg, val)

# Helper to read the axes_map parameter from the config
def read_axes_transform(config, axes_map):
    axes_transform = config.getlists(
                'axes_transform', None, seps=(',', '\n'), parser=float, count=3)
    if axes_transform is None:
        am = {'x': (0, SCALE_XY), 'y': (1, SCALE_XY), 'z': (2, SCALE_Z),
            '-x': (0, -SCALE_XY), '-y': (1, -SCALE_XY), '-z': (2, -SCALE_Z)}
        if any([a not in am for a in axes_map]):
            raise config.error(_("Invalid adxl345 axes_map parameter"))
        #return [am[a.strip()] for a in axes_map]
        for i, a in enumerate(axes_map):
            ind, val = am[a.strip()]
            axes_transform[i][ind] = val
    return axes_transform
    #(x_pos, x_scale), (y_pos, y_scale), (z_pos, z_scale) = self.axes_map

BYTES_PER_SAMPLE = 5
SAMPLES_PER_BLOCK = bulk_sensor.MAX_BULK_MSG_SIZE // BYTES_PER_SAMPLE
BATCH_UPDATES = 0.100

# Printer class that controls ADXL345 chip
class ADXL345:
    def __init__(self, config):
        self.config = config
        self.printer = config.get_printer()
        self.tap_thresh = config.getfloat('tap_thresh', 5000,
                                          minval=TAP_SCALE, maxval=100000.)
        self.tap_dur = config.getfloat('tap_dur', 0.01,
                                       above=DUR_SCALE, maxval=0.1)
        ADXLCommandHelper(config, self)
        offset = config.getfloatlist('offset', (0., 0., 0.), count=3)
        self.axes_map = config.getlist('axes_map', ('x','y','z'), count=3)
        axes_transform = read_axes_transform(config, self.axes_map)
        self.set_transform(axes_transform, offset)
        self.data_rate = config.getint('rate', 3200)
        if self.data_rate not in QUERY_RATES:
            raise config.error(_("Invalid rate parameter: %d") % (self.data_rate,))
        # Setup mcu sensor_adxl345 bulk query code
        self.spi = bus.MCU_SPI_from_config(config, 3, default_speed=5000000)
        self.mcu = mcu = self.spi.get_mcu()
        self.oid = oid = mcu.create_oid()
        self.query_adxl345_cmd = None
        mcu.add_config_cmd("config_adxl345 oid=%d spi_oid=%d"
                           % (oid, self.spi.get_oid()))
        mcu.add_config_cmd("query_adxl345 oid=%d rest_ticks=0"
                           % (oid,), on_restart=True)
        mcu.register_config_callback(self._build_config)
        self.bulk_queue = bulk_sensor.BulkDataQueue(mcu, oid=oid)
        # Clock tracking
        chip_smooth = self.data_rate * BATCH_UPDATES * 2
        self.clock_sync = bulk_sensor.ClockSyncRegression(mcu, chip_smooth)
        self.clock_updater = bulk_sensor.ChipClockUpdater(self.clock_sync,
                                                          BYTES_PER_SAMPLE)
        self.last_error_count = 0
        # Process messages in batches
        self.batch_bulk = bulk_sensor.BatchBulkHelper(
            self.printer, self._process_batch,
            self._start_measurements, self._finish_measurements, BATCH_UPDATES)
        self.name = config.get_name().split()[-1]
        if config.get('probe_pin', None) is not None:
            adxl345_endstop = ADXL345EndstopWrapper(config, self, self.axes_map)
            self.printer.add_object('probe', probe.PrinterProbe(config, adxl345_endstop))
        hdr = ('time', 'x_acceleration', 'y_acceleration', 'z_acceleration')
        self.batch_bulk.add_mux_endpoint("adxl345/dump_adxl345", "sensor",
                                            self.name, {'header': hdr})
        self.printer.register_event_handler("probe:xy_move", self._xy_move)
        self.printer.register_event_handler("probe:z_move", self._z_move)
    
    def _xy_move(self):
        self.set_reg(0x1D, 0xFF)
        #self.set_reg(REG_DUR, 0xFF)
    def _z_move(self):
        self.set_reg(0x1D, int(self.tap_thresh / TAP_SCALE))
        self.read_reg(0x30)
        #self.set_reg(REG_DUR, int(self.tap_dur / DUR_SCALE))
    def get_tap_thresh(self):
        return self.tap_thresh
    def _build_config(self):
        cmdqueue = self.spi.get_command_queue()
        self.query_adxl345_cmd = self.mcu.lookup_command(
            "query_adxl345 oid=%c rest_ticks=%u", cq=cmdqueue)
        self.clock_updater.setup_query_command(
            self.mcu, "query_adxl345_status oid=%c", oid=self.oid, cq=cmdqueue)
    def read_reg(self, reg):
        params = self.spi.spi_transfer([reg | REG_MOD_READ, 0x00])
        response = bytearray(params['response'])
        return response[1]
    def set_reg(self, reg, val, minclock=0):
        self.spi.spi_send([reg, val & 0xFF], minclock=minclock)
        stored_val = self.read_reg(reg)
        if stored_val != val:
            raise self.printer.command_error(
                    _("Failed to set ADXL345 register [0x%x] to 0x%x: got 0x%x. "
                    "This is generally indicative of connection problems "
                    "(e.g. faulty wiring) or a faulty adxl345 chip.") % (
                        reg, val, stored_val))
    def get_config(self):
        return self.config
    def set_transform(self, axes_transform, offset):
        self.offset = [coeff / SCALE for coeff in offset]
        self.axes_transform = [[coeff * SCALE for coeff in axis]
                               for axis in axes_transform]
    def start_internal_client(self):
        aqh = AccelQueryHelper(self.printer)
        self.batch_bulk.add_client(aqh.handle_batch)
        return aqh
    # Measurement decoding
    def _extract_samples(self, raw_samples):
        # Load variables to optimize inner loop below
        tr_x, tr_y, tr_z = self.axes_transform
        offs_x, offs_y, offs_z = self.offset
        last_sequence = self.clock_updater.get_last_sequence()
        time_base, chip_base, inv_freq = self.clock_sync.get_time_translation()
        # Process every message in raw_samples
        count = seq = 0
        samples = [None] * (len(raw_samples) * SAMPLES_PER_BLOCK)
        for params in raw_samples:
            seq_diff = (params['sequence'] - last_sequence) & 0xffff
            seq_diff -= (seq_diff & 0x8000) << 1
            seq = last_sequence + seq_diff
            d = bytearray(params['data'])
            msg_cdiff = seq * SAMPLES_PER_BLOCK - chip_base
            for i in range(len(d) // BYTES_PER_SAMPLE):
                d_xyz = d[i*BYTES_PER_SAMPLE:(i+1)*BYTES_PER_SAMPLE]
                xlow, ylow, zlow, xzhigh, yzhigh = d_xyz
                if yzhigh & 0x80:
                    self.last_error_count += 1
                    continue
                rx = ((xlow | ((xzhigh & 0x1f) << 8)) - ((xzhigh & 0x10) << 9)
                        - offs_x)
                ry = ((ylow | ((yzhigh & 0x1f) << 8)) - ((yzhigh & 0x10) << 9)
                        - offs_y)
                rz = ((zlow | ((xzhigh & 0xe0) << 3) | ((yzhigh & 0xe0) << 6))
                      - ((yzhigh & 0x40) << 7)) - offs_z
                x = round(tr_x[0] * rx + tr_x[1] * ry + tr_x[2] * rz, 6)
                y = round(tr_y[0] * rx + tr_y[1] * ry + tr_y[2] * rz, 6)
                z = round(tr_z[0] * rx + tr_z[1] * ry + tr_z[2] * rz, 6)
                ptime = round(time_base + (msg_cdiff + i) * inv_freq, 6)
                samples[count] = (ptime, x, y, z)
                count += 1
        self.clock_sync.set_last_chip_clock(seq * SAMPLES_PER_BLOCK + i)
        del samples[count:]
        return samples
    # Start, stop, and process message batches
    def _start_measurements(self):    
        # In case of miswiring, testing ADXL345 device ID prevents treating
        # noise or wrong signal as a correctly initialized device
        dev_id = self.read_reg(REG_DEVID)
        if dev_id != ADXL345_DEV_ID:
            raise self.printer.command_error(
                _("Invalid adxl345 id (got %x vs %x).\n"
                "This is generally indicative of connection problems\n"
                "(e.g. faulty wiring) or a faulty adxl345 chip.")
                % (dev_id, ADXL345_DEV_ID))
        # Setup chip in requested query rate
        self.set_reg(REG_POWER_CTL, 0x00)
        self.set_reg(REG_DATA_FORMAT, 0x0B)
        self.set_reg(REG_FIFO_CTL, 0x00)
        self.set_reg(REG_BW_RATE, QUERY_RATES[self.data_rate])
        self.set_reg(REG_FIFO_CTL, SET_FIFO_CTL)
        # Start bulk reading
        self.bulk_queue.clear_samples()
        rest_ticks = self.mcu.seconds_to_clock(4. / self.data_rate)
        self.query_adxl345_cmd.send([self.oid, rest_ticks])
        self.set_reg(REG_POWER_CTL, 0x08)
        logging.info("ADXL345 starting '%s' measurements", self.name)
        # Initialize clock tracking
        self.clock_updater.note_start()
        self.last_error_count = 0
    
    
    def get_mcu(self):
        return self.spi.get_mcu()
    
       
    def _finish_measurements(self):
        # Halt bulk reading
        self.set_reg(REG_POWER_CTL, 0x00)
        self.query_adxl345_cmd.send_wait_ack([self.oid, 0])
        self.bulk_queue.clear_samples()
        logging.info("ADXL345 finished '%s' measurements", self.name)

    def _process_batch(self, eventtime):
        self.clock_updater.update_clock()
        raw_samples = self.bulk_queue.pull_samples()
        if not raw_samples:
            return {}
        samples = self._extract_samples(raw_samples)
        if not samples:
            return {}
        return {'data': samples, 'errors': self.last_error_count,
                'overflows': self.clock_updater.get_last_overflows()}
    # def _api_startstop(self, is_start):
    #     if is_start:
    #         self._start_measurements()
    #     else:
    #         self._finish_measurements()
    # def start_internal_client(self):
    #     cconn = self.api_dump.add_internal_client()
    #     return ADXL345QueryHelper(self.printer, cconn)

def load_config(config):
    return ADXL345(config)

def load_config_prefix(config):
    return ADXL345(config)