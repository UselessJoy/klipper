# A utility class to test resonances of the printer
#
# Copyright (C) 2020  Dmitry Butyugin <dmbutyugin@google.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
import math, os, time
from matplotlib.figure import Figure
from . import adxl345, shaper_calibrate
import matplotlib
matplotlib.rcParams.update({'figure.autolayout': True})
matplotlib.use('Agg')
import matplotlib.pyplot, matplotlib.dates, matplotlib.font_manager
import matplotlib.ticker
from textwrap import wrap
import locales
import re

MAX_TITLE_LENGTH=65

def _parse_axis(gcmd, raw_axis):
    if raw_axis is None:
        return None
    raw_axis = raw_axis.lower()
    if raw_axis in ['x', 'y']:
        return TestAxis(axis=raw_axis)
    dirs = raw_axis.split(',')
    if len(dirs) != 2:
        raise gcmd.error(_("Invalid format of axis '%s'") % (raw_axis,))
    try:
        dir_x = float(dirs[0].strip())
        dir_y = float(dirs[1].strip())
    except:
        raise gcmd.error(
                _("Unable to parse axis direction '%s'") % (raw_axis,))
    return TestAxis(vib_dir=(dir_x, dir_y))

class TestAxis:
    def __init__(self, axis=None, vib_dir=None):
        if axis is None:
            self._name = "axis=%.3f,%.3f" % (vib_dir[0], vib_dir[1])
        else:
            self._name = axis
        if vib_dir is None:
            self._vib_dir = (1., 0.) if axis == 'x' else (0., 1.)
        else:
            s = math.sqrt(sum([d*d for d in vib_dir]))
            self._vib_dir = [d / s for d in vib_dir]
    def matches(self, chip_axis):
        if self._vib_dir[0] and 'x' in chip_axis:
            return True
        if self._vib_dir[1] and 'y' in chip_axis:
            return True
        return False
    def get_name(self):
        return self._name
    def get_point(self, l):
        return (self._vib_dir[0] * l, self._vib_dir[1] * l)

class VibrationPulseTestGenerator:
    def __init__(self, config):
        self.min_freq = config.getfloat('min_freq', 5., minval=1.)
        self.max_freq = config.getfloat('max_freq', 135.,
                                        minval=self.min_freq, maxval=300.)
        self.accel_per_hz = config.getfloat('accel_per_hz', 60., above=0.)
        self.hz_per_sec = config.getfloat('hz_per_sec', 1.,
                                          minval=0.1, maxval=2.)
        self.freq_start = self.freq_end = self.test_accel_per_hz = self.test_hz_per_sec = None

    def prepare_test(self, gcmd):
        self.freq_start = gcmd.get_float("FREQ_START", self.min_freq, minval=1.)
        self.freq_end = gcmd.get_float("FREQ_END", self.max_freq,
                                       minval=self.freq_start, maxval=300.)
        self.test_accel_per_hz = gcmd.get_float("ACCEL_PER_HZ",
                                                self.accel_per_hz, above=0.)
        self.test_hz_per_sec = gcmd.get_float("HZ_PER_SEC", self.hz_per_sec,
                                              above=0., maxval=2.)

    def gen_test(self):
        freq = self.freq_start
        res = []
        sign = 1.
        time = 0.
        while freq <= self.freq_end + 0.000001:
            t_seg = .25 / freq
            accel = self.test_accel_per_hz * freq
            time += t_seg
            res.append((time, sign * accel, freq))
            time += t_seg
            res.append((time, -sign * accel, freq))
            freq += 2. * t_seg * self.test_hz_per_sec
            sign = -sign
        return res
    def get_max_freq(self):
        return self.freq_end

class SweepingVibrationsTestGenerator:
    def __init__(self, config):
        self.vibration_generator = VibrationPulseTestGenerator(config)
        self.sweeping_accel = config.getfloat('sweeping_accel', 400., above=0.)
        self.sweeping_period = config.getfloat('sweeping_period', 1.2,
                                               minval=0.)
    def prepare_test(self, gcmd):
        self.vibration_generator.prepare_test(gcmd)
        self.test_sweeping_accel = gcmd.get_float(
                "SWEEPING_ACCEL", self.sweeping_accel, above=0.)
        self.test_sweeping_period = gcmd.get_float(
                "SWEEPING_PERIOD", self.sweeping_period, minval=0.)
    def gen_test(self):
        test_seq = self.vibration_generator.gen_test()
        accel_fraction = math.sqrt(2.0) * 0.125
        if self.test_sweeping_period:
            t_rem = self.test_sweeping_period * accel_fraction
            sweeping_accel = self.test_sweeping_accel
        else:
            t_rem = float('inf')
            sweeping_accel = 0.
        res = []
        last_t = 0.
        sig = 1.
        accel_fraction += 0.25
        for next_t, accel, freq in test_seq:
            t_seg = next_t - last_t
            while t_rem <= t_seg:
                last_t += t_rem
                res.append((last_t, accel + sweeping_accel * sig, freq))
                t_seg -= t_rem
                t_rem = self.test_sweeping_period * accel_fraction
                accel_fraction = 0.5
                sig = -sig
            t_rem -= t_seg
            res.append((next_t, accel + sweeping_accel * sig, freq))
            last_t = next_t
        return res
    def get_max_freq(self):
        return self.vibration_generator.get_max_freq()

class ResonanceTestExecutor:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
    def run_test(self, test_seq, axis, gcmd, stop_shaper = [False]):
        reactor = self.printer.get_reactor()
        toolhead = self.printer.lookup_object('toolhead')
        X, Y, Z, E = toolhead.get_position()
        # Override maximum acceleration and acceleration to
        # deceleration based on the maximum test frequency
        systime = reactor.monotonic()
        toolhead_info = toolhead.get_status(systime)
        old_max_accel = toolhead_info['max_accel']
        old_minimum_cruise_ratio = toolhead_info['minimum_cruise_ratio']
        max_accel = max([abs(a) for _, a, _ in test_seq])
        self.gcode.run_script_from_command(
            "SET_VELOCITY_LIMIT ACCEL=%.3f MINIMUM_CRUISE_RATIO=0"
            % (max_accel,))
        input_shaper = self.printer.lookup_object('input_shaper', None)
        if input_shaper is not None and not gcmd.get_int('INPUT_SHAPING', 0):
            input_shaper.disable_shaping()
            gcmd.respond_info(_("Disabled [input_shaper] for resonance testing"))
        else:
            input_shaper = None
        last_v = last_t = last_accel = last_freq = 0.
        for next_t, accel, freq in test_seq:
            # Может вызвать timer_too_close
            if stop_shaper[0]:
              stop_shaper[0] = False
              return False
            t_seg = next_t - last_t
            toolhead.cmd_M204(self.gcode.create_gcode_command(
                "M204", "M204", {"S": abs(accel)}))
            v = last_v + accel * t_seg
            abs_v = abs(v)
            if abs_v < 0.000001:
                v = abs_v = 0.
            abs_last_v = abs(last_v)
            v2 = v * v
            last_v2 = last_v * last_v
            half_inv_accel = .5 / accel
            d = (v2 - last_v2) * half_inv_accel
            dX, dY = axis.get_point(d)
            nX = X + dX
            nY = Y + dY
            toolhead.limit_next_junction_speed(abs_last_v)
            if v * last_v < 0:
                # The move first goes to a complete stop, then changes direction
                d_decel = -last_v2 * half_inv_accel
                decel_X, decel_Y = axis.get_point(d_decel)
                toolhead.move([X + decel_X, Y + decel_Y, Z, E], abs_last_v)
                toolhead.move([nX, nY, Z, E], abs_v)
            else:
                toolhead.move([nX, nY, Z, E], max(abs_v, abs_last_v))
            if math.floor(freq) > math.floor(last_freq):
                gcmd.respond_info(_("Testing frequency %.0f Hz") % (freq,))
                reactor.pause(reactor.monotonic() + 0.01)
            X, Y = nX, nY
            last_t = next_t
            last_v = v
            last_accel = accel
            last_freq = freq
        if last_v:
            d_decel = -.5 * last_v2 / old_max_accel
            decel_X, decel_Y = axis.get_point(d_decel)
            toolhead.cmd_M204(self.gcode.create_gcode_command(
                "M204", "M204", {"S": old_max_accel}))
            toolhead.move([X + decel_X, Y + decel_Y, Z, E], abs(last_v))
        # Restore the original acceleration values
        self.gcode.run_script_from_command(
            "SET_VELOCITY_LIMIT ACCEL=%.3f MINIMUM_CRUISE_RATIO=%.3f"
            % (old_max_accel, old_minimum_cruise_ratio))
        # Restore input shaper if it was disabled for resonance testing
        if input_shaper is not None:
            input_shaper.enable_shaping()
            gcmd.respond_info(_("Re-enabled [input_shaper]"))
        return True

class ResonanceTester:
    tmp_shaper_graph_r = re.compile(r"calibration_data_[xy]_\d+_\d+.png")
    tmp_belt_tension_r = re.compile(r"belt_tension_\d+_\d+.png")# 1,2 поменять на значение ремня (после отпуска)
    
    def __init__(self, config):
        self.printer = config.get_printer()
        self.move_speed = config.getfloat('move_speed', 50., above=0.)
        self.generator = SweepingVibrationsTestGenerator(config)
        self.executor = ResonanceTestExecutor(config)
        self.stop_shaper = [False]
        self.shaping = False
        config_file_path_name = self.printer.get_start_args()['config_file']
        config_dir = os.path.normpath(os.path.join(config_file_path_name, ".."))
        if not config.get('accel_chip_x', None):
            self.accel_chip_names = [('xy', config.get('accel_chip').strip())]
        else:
            self.accel_chip_names = [
                ('x', config.get('accel_chip_x').strip()),
                ('y', config.get('accel_chip_y').strip())]
            if self.accel_chip_names[0][1] == self.accel_chip_names[1][1]:
                self.accel_chip_names = [('xy', self.accel_chip_names[0][1])]
        self.max_smoothing = config.getfloat('max_smoothing', None, minval=0.05)
        self.probe_points = config.getlists('probe_points', seps=(',', '\n'),
                                            parser=float, count=3)
        self.messages = None
        # Параметры для графиков шейпера
        self.active_shaper_graph = ""
        self.active_belt_tension = ""
        self.shaper_graphs_dir = os.path.join(config_dir, ".shaper-images/")
        if not os.path.isdir(self.shaper_graphs_dir):
                os.mkdir(self.shaper_graphs_dir)
        if not config.get('accel_chip_x', None):
            self.accel_chip_names = [('xy', config.get('accel_chip').strip())]
        else:
            self.accel_chip_names = [
                ('x', config.get('accel_chip_x').strip()),
                ('y', config.get('accel_chip_y').strip())]
            if self.accel_chip_names[0][1] == self.accel_chip_names[1][1]:
                self.accel_chip_names = [('xy', self.accel_chip_names[0][1])]
        self.autocalibrate = config.getboolean('autocalibrate', False)
        self.max_smoothing = config.getfloat('max_smoothing', None, minval=0.05)

        # Регистрация вебхуков
        webhooks = self.printer.lookup_object('webhooks')
        webhooks.register_endpoint("resonance_tester/shaper_graph",
                                   self._handle_shaper_graph)
        webhooks.register_endpoint("resonance_tester/set_active_tension",
                                   self._handle_set_active_tension)
        # Регистрация команд 
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command("MEASURE_AXES_NOISE",
                                    self.cmd_MEASURE_AXES_NOISE,
                                    desc=self.cmd_MEASURE_AXES_NOISE_help)
        self.gcode.register_command("TEST_RESONANCES",
                                    self.cmd_TEST_RESONANCES,
                                    desc=self.cmd_TEST_RESONANCES_help)
        self.gcode.register_command("SHAPER_CALIBRATE",
                                    self.cmd_SHAPER_CALIBRATE,
                                    desc=self.cmd_SHAPER_CALIBRATE_help)
        self.gcode.register_command("BELT_TENSION",
                                    self.cmd_belt_tension,
                                    desc=self.cmd_BELT_TENSION_help)
        self.gcode.register_async_command("ASYNC_STOP_SHAPER",
                                    self.cmd_async_STOP_SHAPER,
                                    desc=self.cmd_ASYNC_STOP_SHAPER_help)
        self.printer.register_event_handler("klippy:connect", self.connect)
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        
    def _handle_ready(self):
        self.messages = self.printer.lookup_object("messages")
        # Не уверен, что есть необходимость им присваивать False на старте
        self.shaping = False
        self.stop_shaper[0] = False

    def connect(self):
        self.accel_chips = [
                (chip_axis, self.printer.lookup_object(chip_name))
                for chip_axis, chip_name in self.accel_chip_names]

    cmd_ASYNC_STOP_SHAPER_help = _("Stop shaper calibrate")
    def cmd_async_STOP_SHAPER(self, gcmd):
        if self.shaping:
          self.stop_shaper[0] = True

    def _run_test(self, gcmd, axes, helper, raw_name_suffix=None,
                  accel_chips=None, test_point=None):
        toolhead = self.printer.lookup_object('toolhead')
        calibration_data = {axis: None for axis in axes}
        self.generator.prepare_test(gcmd)
        test_points = [test_point] if test_point else self.probe_points
        for point in test_points:
            toolhead.manual_move(point, self.move_speed)
            if len(test_points) > 1 or test_point is not None:
                gcmd.respond_info(
                        "Probing point (%.3f, %.3f, %.3f)" % tuple(point))
            for axis in axes:
                toolhead.wait_moves()
                toolhead.dwell(0.500)
                if len(axes) > 1:
                    gcmd.respond_info(_("Testing axis %s") % axis.get_name())

                raw_values = []
                if accel_chips is None:
                    for chip_axis, chip in self.accel_chips:
                        if axis.matches(chip_axis):
                            aclient = chip.start_internal_client()
                            raw_values.append((chip_axis, aclient, chip.name))
                else:
                    for chip in accel_chips:
                        aclient = chip.start_internal_client()
                        raw_values.append((axis, aclient, chip.name))

                # Generate moves
                test_seq = self.generator.gen_test()
                # Здесь фулл тест оси
                done = self.executor.run_test(test_seq, axis, gcmd, self.stop_shaper)
                if not done:
                  self.messages.send_message('warning', _("Calibrating stoped"))
                  return False
                for chip_axis, aclient, chip_name in raw_values:
                    aclient.finish_measurements()
                    if raw_name_suffix is not None:
                        raw_name = self.get_filename(
                                'raw_data', raw_name_suffix, axis,
                                point if len(test_points) > 1 else None,
                                chip_name if accel_chips is not None else None,)
                        aclient.write_to_file(raw_name)
                        gcmd.respond_info(
                                "Writing raw accelerometer data to "
                                "%s file" % (raw_name,))
                if helper is None:
                    continue
                for chip_axis, aclient, chip_name in raw_values:
                    if not aclient.has_valid_samples():
                        raise gcmd.error(
                            "accelerometer '%s' measured no data" % (
                                chip_name,))
                    new_data = helper.process_accelerometer_data(aclient)
                    if calibration_data[axis] is None:
                        calibration_data[axis] = new_data
                    else:
                        calibration_data[axis].add_data(new_data)
        return calibration_data

    def _parse_chips(self, accel_chips):
        parsed_chips = []
        for chip_name in accel_chips.split(','):
            chip = self.printer.lookup_object(chip_name.strip())
            parsed_chips.append(chip)
        return parsed_chips
    def _get_max_calibration_freq(self):
        return 1.5 * self.generator.get_max_freq()

    cmd_BELT_TENSION_help = _("Runs the resonance test for belts to check their equals") # no locale
    def cmd_belt_tension(self, gcmd): 
        plot_freq = gcmd.get_float("PLOT_FREQ", 200.)
        belts = {'left': {'axis': '1,1', 'data': None}, 'right': {'axis': '1,-1', 'data': None}}
        for belt in belts:
          axis = _parse_axis(gcmd, belts[belt]['axis'])
          name_suffix = time.strftime("%Y%m%d_%H%M%S")
          # Setup calculation of resonances
          helper = shaper_calibrate.ShaperCalibrate(self.printer)
          self.printer.lookup_object('homing').run_G28_if_unhomed()
          belts[belt]['data'] = self._run_test(
                  gcmd, [axis], helper,
                  raw_name_suffix=None)[axis]
          
          csv_name = self.save_calibration_data('belt_tension', name_suffix,
                                                helper, None, belts[belt]['data'])
          gcmd.respond_info(
                  _("Resonances data written to %s file") % (csv_name,))
        fig: Figure = self.plot_compare_frequency([belts['left']['data'], belts['right']['data']], [_("Left belt"), _("Right belt")], plot_freq, 'all')
        fig.set_size_inches(8, 6)
        belt_tension_path = os.path.join("/tmp/", csv_name.rpartition('/')[2].replace('.csv', '.png'))
        fig.savefig(belt_tension_path)

    def plot_compare_frequency(self, datas, lognames, max_freq, axis):
      fig, ax = matplotlib.pyplot.subplots()
      ax.set_title(_("Frequency responses comparison"))
      ax.set_xlabel(_("Frequency (Hz)"))
      ax.set_ylabel(_("Power spectral density"))

      for data, logname in zip(datas, lognames):
          freqs = data.freq_bins
          psd = data.get_psd(axis)[freqs <= max_freq]
          freqs = freqs[freqs <= max_freq]
          ax.plot(freqs, psd, label="\n".join(wrap(logname, 60)), alpha=0.6)

      ax.xaxis.set_minor_locator(matplotlib.ticker.AutoMinorLocator())
      ax.yaxis.set_minor_locator(matplotlib.ticker.AutoMinorLocator())
      ax.grid(which='major', color='grey')
      ax.grid(which='minor', color='lightgrey')
      fontP = matplotlib.font_manager.FontProperties()
      fontP.set_size('x-small')
      ax.legend(loc='best', prop=fontP)
      fig.tight_layout()
      return fig
    
    cmd_TEST_RESONANCES_help = _("Runs the resonance test for a specifed axis")
    def cmd_TEST_RESONANCES(self, gcmd):
        # Parse parameters
        axis = _parse_axis(gcmd, gcmd.get("AXIS").lower())
        chips_str = gcmd.get("CHIPS", None)
        test_point = gcmd.get("POINT", None)
        if test_point:
          test_coords = test_point.split(',')
          if len(test_coords) != 3:
              raise gcmd.error(_("Invalid POINT parameter, must be 'x,y,z'"))
          try:
              test_point = [float(p.strip()) for p in test_coords]
          except ValueError:
              raise gcmd.error(_("Invalid POINT parameter, must be 'x,y,z'"
              " where x, y and z are valid floating point numbers"))
        accel_chips = self._parse_chips(chips_str) if chips_str else None
        outputs = gcmd.get("OUTPUT", "resonances").lower().split(',')
        for output in outputs:
            if output not in ['resonances', 'raw_data']:
                raise gcmd.error(_("Unsupported output '%s', only 'resonances' and 'raw_data' are supported") % (output,))
        if not outputs:
            raise gcmd.error(_("No output specified, at least one of 'resonances' or 'raw_data' must be set in "
                               "OUTPUT parameter"))
        name_suffix = gcmd.get("NAME", time.strftime("%Y%m%d_%H%M%S"))
        if not self.is_valid_name_suffix(name_suffix):
            raise gcmd.error(_("Invalid NAME parameter"))
        csv_output = 'resonances' in outputs
        raw_output = 'raw_data' in outputs

        # Setup calculation of resonances
        if csv_output:
            helper = shaper_calibrate.ShaperCalibrate(self.printer)
        else:
            helper = None
        self.printer.lookup_object('homing').run_G28_if_unhomed()
        data = self._run_test(
                gcmd, [axis], helper,
                raw_name_suffix=name_suffix if raw_output else None,
                accel_chips=accel_chips, test_point=test_point)[axis]
        if csv_output:
            csv_name = self.save_calibration_data(
                    'resonances', name_suffix, helper, axis, data,
                    point=test_point, max_freq=self._get_max_calibration_freq())
            gcmd.respond_info(
                    _("Resonances data written to %s file") % (csv_name,))
            
    cmd_SHAPER_CALIBRATE_help = (
        _("Simular to TEST_RESONANCES but suggest input shaper config"))
    def cmd_SHAPER_CALIBRATE(self, gcmd):
        self.shaping = True
        # Parse parameters
        axis = gcmd.get("AXIS", None)
        if not axis or axis == 'all':
            calibrate_axes = [TestAxis('x'), TestAxis('y')]
        elif axis.lower() not in 'xy':
            raise gcmd.error(_("Unsupported axis '%s'") % (axis,))
        else:
            calibrate_axes = [TestAxis(axis.lower())]

        chips_str = gcmd.get("CHIPS", None)
        accel_chips = self._parse_chips(chips_str) if chips_str else None
        max_smoothing = gcmd.get_float(
                "MAX_SMOOTHING", self.max_smoothing, minval=0.05)

        name_suffix = gcmd.get("NAME", time.strftime("%Y%m%d_%H%M%S"))
        if not self.is_valid_name_suffix(name_suffix):
            raise gcmd.error(_("Invalid NAME parameter"))
        input_shaper = self.printer.lookup_object('input_shaper', None)
        # Setup shaper calibration
        helper = shaper_calibrate.ShaperCalibrate(self.printer)
        self.printer.lookup_object('homing').run_G28_if_unhomed()
        calibration_data = self._run_test(gcmd, calibrate_axes, helper)
        if not calibration_data:
            self.shaping = False
            return
        configfile = self.printer.lookup_object('configfile')
        for axis in calibrate_axes:
            axis_name = axis.get_name()
            gcmd.respond_info(
                    _("Calculating the best input shaper parameters for %s axis")
                    % (axis_name,))
            calibration_data[axis].normalize_to_frequencies()
            systime = self.printer.get_reactor().monotonic()
            toolhead = self.printer.lookup_object('toolhead')
            toolhead_info = toolhead.get_status(systime)
            scv = toolhead_info['square_corner_velocity']
            max_freq = self._get_max_calibration_freq()
            best_shaper, all_shapers = helper.find_best_shaper(
                    calibration_data[axis], max_smoothing=max_smoothing,
                    scv=scv, max_freq=max_freq, logger=gcmd.respond_info)
            gcmd.respond_info(
                    _("Recommended shaper_type_%s = %s, shaper_freq_%s = %.1f Hz")
                    % (axis_name, best_shaper.name,
                       axis_name, best_shaper.freq))
            if input_shaper is not None:
                helper.apply_params(input_shaper, axis_name,
                                    best_shaper.name, best_shaper.freq)
            helper.save_params(configfile, axis_name,
                               best_shaper.name, best_shaper.freq)
            csv_name = self.save_calibration_data(
                    'calibration_data', name_suffix, helper, axis,
                    calibration_data[axis], all_shapers, max_freq=max_freq)
            gcmd.respond_info(
                    _("Shaper calibration data written to %s file") % (csv_name,))
            fig: Figure = self.plot_freq_response(csv_name, calibration_data[axis], all_shapers,
                                best_shaper, max_freq)
            fig.set_size_inches(8, 6)
            shaper_path = os.path.join("/tmp/", csv_name.rpartition('/')[2].replace('.csv', '.png'))
            fig.savefig(shaper_path)
            self.load_shaper_graph([shaper_path[1:]])
        gcmd.respond_info(
            _("The SAVE_CONFIG command will update the printer config file\n"
            "with these parameters and restart the printer."))
        self.shaping = False
    
    def get_status(self, eventtime):
        return {
                  'saved': self.get_saved_shaper_graphs(),
                  'tmp': self.get_tmp_shaper_graphs(),
                  'belt_tensions': self.get_belt_tensions(),
                  'active_belt_tension': self.active_belt_tension,
                  'active': self.active_shaper_graph,
                  'shaping': self.shaping
        }

    def get_belt_tensions(self):
        return [f"tmp/{filename}" for filename in os.listdir("/tmp/") if self.tmp_belt_tension_r.match(filename)]

    def get_saved_shaper_graphs(self):
        return [f"config/.shaper-images/{filename}" for filename in os.listdir(self.shaper_graphs_dir) if filename.endswith('.png')]

    def get_tmp_shaper_graphs(self):
        return [f"tmp/{filename}" for filename in os.listdir("/tmp/") if self.tmp_shaper_graph_r.match(filename)]

    def _handle_set_active_tension(self, web_request):
        new_active_tension = web_request.get('tension', None)
        self.active_belt_tension = new_active_tension

    def _handle_shaper_graph(self, web_request):
        action = web_request.get('action')
        webhook_func = {
            'save': self.save_shaper_graph,
            'delete': self.remove_shaper_graph,
            'load': self.load_shaper_graph,
            'unload': self.unload_shaper_graph,
            'rename': self.rename_shaper_graph
        }
        if action not in webhook_func:
            return
        args = web_request.get('args')
        # args должны иметь следующий вид:
        # config/.shaper-images/{filename} - (1)
        # tmp/{filename} - (2)
        # filename - (3)
        # Такие пути необходимы, чтобы fluidd мог их без проблем прочитать и загрузить картинку (см. createFileUrlWithToken в исходниках fluidd)
        # где filename - имя графика
        webhook_func[action](args)

    def load_shaper_graph(self, args):
        # Для load аргумент должен быть либо (1), либо (2)
        saved = self.get_saved_shaper_graphs()
        tmp = self.get_tmp_shaper_graphs()
        if args[0] not in saved and \
            args[0] not in tmp:
                self.messages.send_message("warning", _("Cannot find graph %s") % args[0].rpartition('/')[2])# no locale
                return
        self.active_shaper_graph = args[0]

    def unload_shaper_graph(self, args=None):
        self.active_shaper_graph = ""
  
    def save_shaper_graph(self, args):
        # На save первым аргументом должен идти (2), вторым - (3)
        tmp = self.get_tmp_shaper_graphs()
        if args[0] not in tmp:
            self.messages.send_message("warning", _("Cannot find graph %s") % args[0].rpartition('/')[2])# no locale
            return
        saved = self.get_saved_shaper_graphs()
        if f"\"config/.shaper-images/{args[1]}\"" in saved:
            self.messages.send_message("warning", _("Graph %s already exist") % args[0].rpartition('/')[2])# no locale
            return
        saving_new_graph_path = f"\"{self.shaper_graphs_dir}/{args[1]}\""
        os.system(f"cp /{args[0]} {saving_new_graph_path}")
        os.system(f"rm /{args[0]}")
    
    def remove_shaper_graph(self, args):
        # Для remove аргумент должен быть либо (1), либо (2)
        removing_path = None
        if args[0].startswith('tmp'):
            tmp = self.get_tmp_shaper_graphs()
            if args[0] not in tmp:
                self.messages.send_message("warning", _("Cannot find graph %s") % args[0].rpartition('/')[2])# no locale
                return
            removing_path = f"/{args[0]}"
        elif args[0].startswith('config'):
            saved = self.get_saved_shaper_graphs()
            if args[0] not in saved:
                self.messages.send_message("warning", _("Cannot find graph %s") % args[0].rpartition('/')[2])# no locale
                return
            removing_path = f"\"{self.shaper_graphs_dir}/{args[0].rpartition('/')[2]}\"" # Поскольку в dir не хватает только имени
        if removing_path:
            if args[0] == self.active_shaper_graph:
                self.active_shaper_graph = ""
            os.system(f"rm {removing_path}")
        else:
            self.messages.send_message("warning", _("Unsupported path for graph %s") % args[0].rpartition('/')[2])# no locale

    def rename_shaper_graph(self, args):
        # Для rename первый аргумент должен быть (2), второй - (3)
        # При переименовании графиков в /tmp, они будут теряться из-за регулярки
        saved = self.get_saved_shaper_graphs()
        if args[0] not in saved:
            self.messages.send_message("warning", _("Graph %s doesn't saved") % args[0].rpartition('/')[2])# no locale
            return
        if f"\"config/.shaper-images/{args[1]}\"" in saved:
            self.messages.send_message("warning", _("Graph %s already exist") % args[0].rpartition('/')[2])# no locale
            return
        renamed_path = f"\"{self.shaper_graphs_dir}/{args[0].rpartition('/')[2]}\"" # Поскольку в dir не хватает только имени
        os.system(f"mv {renamed_path} \"{self.shaper_graphs_dir}/{args[1]}\"")

    def plot_freq_response(self, name: str, calibration_data, shapers,
                       selected_shaper, max_freq):
        freqs = calibration_data.freq_bins
        psd = calibration_data.psd_sum[freqs <= max_freq]
        px = calibration_data.psd_x[freqs <= max_freq]
        py = calibration_data.psd_y[freqs <= max_freq]
        pz = calibration_data.psd_z[freqs <= max_freq]
        freqs = freqs[freqs <= max_freq]

        fontP = matplotlib.font_manager.FontProperties()
        fontP.set_size('x-small')

        fig, ax = matplotlib.pyplot.subplots()
        ax.set_xlabel(_("Frequency, Hz"))
        ax.set_xlim([0, max_freq])
        ax.set_ylabel(_("Power spectral density"))

        ax.plot(freqs, psd, label='X+Y+Z', color='purple')
        ax.plot(freqs, px, label='X', color='red')
        ax.plot(freqs, py, label='Y', color='green')
        ax.plot(freqs, pz, label='Z', color='blue')

        title = _("Frequency response and shapers (%s)") % (name.split('/').pop())
        ax.set_title("\n".join(wrap(title, MAX_TITLE_LENGTH)))
        ax.xaxis.set_minor_locator(matplotlib.ticker.MultipleLocator(5))
        ax.yaxis.set_minor_locator(matplotlib.ticker.AutoMinorLocator())
        ax.ticklabel_format(axis='y', style='scientific', scilimits=(0,0))
        ax.grid(which='major', color='grey')
        ax.grid(which='minor', color='lightgrey')

        ax2 = ax.twinx()
        ax2.set_ylabel(_("Shaper vibration reduction (ratio)"))
        for shaper in shapers:
            label = _("%s (%.1f Hz, vibr=%.1f%%, sm~=%.2f, accel<=%.f)") % (
                    shaper.name.upper(), shaper.freq,
                    shaper.vibrs * 100., shaper.smoothing,
                    round(shaper.max_accel / 100.) * 100.)
            linestyle = 'dotted'
            if shaper.name == selected_shaper:
                linestyle = 'dashdot'
            ax2.plot(freqs[:len(shaper.vals)], shaper.vals, label=label, linestyle=linestyle)
        ax.plot(freqs[:len(shaper.vals)], psd[:len(shaper.vals)] * selected_shaper.vals,
                label=_("After\nshaper"), color='cyan')
        # A hack to add a human-readable shaper recommendation to legend
        ax2.plot([], [], ' ',
                label=_("Recommended shaper: %s") % (selected_shaper.name.upper()))

        ax.legend(loc='upper left', prop=fontP)
        ax2.legend(loc='upper right', prop=fontP)

        fig.tight_layout()
        return fig

    cmd_MEASURE_AXES_NOISE_help = (
        _("Measures noise of all enabled accelerometer chips"))
    def cmd_MEASURE_AXES_NOISE(self, gcmd):
        meas_time = gcmd.get_float("MEAS_TIME", 2.)
        self.printer.lookup_object('homing').run_G28_if_unhomed()
        raw_values = [(chip_axis, chip.start_internal_client())
                      for chip_axis, chip in self.accel_chips]
        self.printer.lookup_object('toolhead').dwell(meas_time)
        for chip_axis, aclient in raw_values:
            aclient.finish_measurements()
        helper = shaper_calibrate.ShaperCalibrate(self.printer)
        for chip_axis, aclient in raw_values:
            if not aclient.has_valid_samples():
                raise gcmd.error(
                        _("%s-axis accelerometer measured no data") % (chip_axis,))
            data = helper.process_accelerometer_data(aclient)
            vx = data.psd_x.mean()
            vy = data.psd_y.mean()
            vz = data.psd_z.mean()
            gcmd.respond_info(_("Axes noise for %s-axis accelerometer: "
                              "%.6f (x), %.6f (y), %.6f (z)") % (
                                  chip_axis, vx, vy, vz))

    def is_valid_name_suffix(self, name_suffix):
        return name_suffix.replace('-', '').replace('_', '').isalnum()

    def get_filename(self, base, name_suffix, axis=None, point=None):
        name = base
        if axis:
            name += '_' + axis.get_name()
        if point:
            name += "_%.3f_%.3f_%.3f" % (point[0], point[1], point[2])
        name += '_' + name_suffix
        return os.path.join("/tmp", name + ".csv")

    def save_calibration_data(self, base_name, name_suffix, shaper_calibrate,
                              axis, calibration_data,
                              all_shapers=None, point=None, max_freq=None):
        output = self.get_filename(base_name, name_suffix, axis, point)
        shaper_calibrate.save_calibration_data(output, calibration_data,
                                               all_shapers, max_freq)
        return output

def load_config(config):
    return ResonanceTester(config)
