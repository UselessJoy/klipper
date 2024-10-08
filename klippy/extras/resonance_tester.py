# A utility class to test resonances of the printer
#
# Copyright (C) 2020  Dmitry Butyugin <dmbutyugin@google.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging, math, os, time
import subprocess
from matplotlib.figure import Figure
import numpy as np
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

class VibrationPulseTest:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.min_freq = config.getfloat('min_freq', 5., minval=1.)
        # Defaults are such that max_freq * accel_per_hz == 10000 (max_accel)
        self.max_freq = config.getfloat('max_freq', 10000. / 75.,
                                        minval=self.min_freq, maxval=200.)
        self.accel_per_hz = config.getfloat('accel_per_hz', 75., above=0.)
        self.hz_per_sec = config.getfloat('hz_per_sec', 1.,
                                          minval=0.1, maxval=2.)

        self.probe_points = config.getlists('probe_points', seps=(',', '\n'),
                                            parser=float, count=3)
    def get_start_test_points(self):
        return self.probe_points
    def prepare_test(self, gcmd):
        self.freq_start = gcmd.get_float("FREQ_START", self.min_freq, minval=1.)
        self.freq_end = gcmd.get_float("FREQ_END", self.max_freq,
                                       minval=self.freq_start, maxval=200.)
        self.hz_per_sec = gcmd.get_float("HZ_PER_SEC", self.hz_per_sec,
                                         above=0., maxval=2.)
    def run_test(self, axis, gcmd):
        toolhead = self.printer.lookup_object('toolhead')
        X, Y, Z, E = toolhead.get_position()
        sign = 1.
        freq = self.freq_start
        # Override maximum acceleration and acceleration to
        # deceleration based on the maximum test frequency
        systime = self.printer.get_reactor().monotonic()
        toolhead_info = toolhead.get_status(systime)
        old_max_accel = toolhead_info['max_accel']
        old_max_accel_to_decel = toolhead_info['max_accel_to_decel']
        max_accel = self.freq_end * self.accel_per_hz
        toolhead.set_velocity_limit(accel=max_accel, accel_to_decel=max_accel)
        input_shaper = self.printer.lookup_object('input_shaper', None)
        if input_shaper is not None and not gcmd.get_int('INPUT_SHAPING', 0):
            input_shaper.disable_shaping()
            gcmd.respond_info(_("Disabled [input_shaper] for resonance testing"))
        else:
            input_shaper = None
        gcmd.respond_info(_("Testing frequency %.0f Hz") % (freq,))
        while freq <= self.freq_end + 0.000001:
            t_seg = .25 / freq
            accel = self.accel_per_hz * freq
            max_v = accel * t_seg
            toolhead.cmd_M204(self.gcode.create_gcode_command(
                "M204", "M204", {"S": accel}))
            L = .5 * accel * t_seg**2
            dX, dY = axis.get_point(L)
            nX = X + sign * dX
            nY = Y + sign * dY
            toolhead.move([nX, nY, Z, E], max_v)
            toolhead.move([X, Y, Z, E], max_v)
            sign = -sign
            old_freq = freq
            freq += 2. * t_seg * self.hz_per_sec
            if math.floor(freq) > math.floor(old_freq):
                gcmd.respond_info(_("Testing frequency %.0f Hz") % (freq,))
        # Restore the original acceleration values
        toolhead.set_velocity_limit(accel=old_max_accel, accel_to_decel=old_max_accel_to_decel)
        # Restore input shaper if it was disabled for resonance testing
        if input_shaper is not None:
            input_shaper.enable_shaping()
            gcmd.respond_info(_("Re-enabled [input_shaper]"))

class ResonanceTester:
    tmp_shaper_graph_r = re.compile(r"calibration_data_[xy]_\d+_\d+.png")
    tmp_belt_tension_r = re.compile(r"belt_tension_\d+_\d+.png")# 1,2 поменять на значение ремня (после отпуска)
    
    def __init__(self, config):
        self.printer = config.get_printer()
        self.move_speed = config.getfloat('move_speed', 50., above=0.)
        self.test = VibrationPulseTest(config)
        self.messages = None
        config_file_path_name = self.printer.get_start_args()['config_file']
        config_dir = os.path.normpath(os.path.join(config_file_path_name, ".."))

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
        self.printer.register_event_handler("klippy:connect", self.connect)
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        
    def _handle_ready(self):
        self.messages = self.printer.lookup_object("messages")
    def connect(self):
        self.accel_chips = [
                (chip_axis, self.printer.lookup_object(chip_name))
                for chip_axis, chip_name in self.accel_chip_names]

    def _calibrate_chips(self, axes, gcmd):
        calibrate_chips = []
        for chip_axis, chip in self.accel_chips:
            for axis in axes:
                if axis.matches(chip_axis):
                    calibrate_chips.append(chip)
                    break
        for chip in calibrate_chips:
            gcmd.respond_info(_("Autocalibrating %s") % (
                chip.get_config().get_name(),))
            adxl345.AccelerometerCalibrator(self.printer, chip).calibrate(gcmd)

    def _run_test(self, gcmd, axes, helper, raw_name_suffix=None):
        toolhead = self.printer.lookup_object('toolhead')
        calibration_data = {axis: None for axis in axes}

        self.test.prepare_test(gcmd)
        test_points = self.test.get_start_test_points()
        for ptx_ind, point in enumerate(test_points):
            toolhead.manual_move(point, self.move_speed)
            if self.autocalibrate and ptx_ind == 0:
                self._calibrate_chips(axes, gcmd)
            if len(test_points) > 1:
                gcmd.respond_info(
                        _("Probing point (%.3f, %.3f, %.3f)") % tuple(point))
            for axis in axes:
                toolhead.wait_moves()
                toolhead.dwell(0.500)
                if len(axes) > 1:
                    gcmd.respond_info(_("Testing axis %s") % axis.get_name())

                raw_values = []
                for chip_axis, chip in self.accel_chips:
                    if axis.matches(chip_axis):
                        aclient = chip.start_internal_client()
                        raw_values.append((chip_axis, aclient))
                # Generate moves
                self.test.run_test(axis, gcmd)
                for chip_axis, aclient in raw_values:
                    aclient.finish_measurements()
                    if raw_name_suffix is not None:
                        raw_name = self.get_filename(
                                'raw_data', raw_name_suffix, axis,
                                point if len(test_points) > 1 else None)
                        aclient.write_to_file(raw_name)
                        gcmd.respond_info(
                                _("Writing raw accelerometer data to "
                                "%s file") % (raw_name,))
                if helper is None:
                    continue
                for chip_axis, aclient in raw_values:
                    if not aclient.has_valid_samples():
                        raise gcmd.error(
                                _("%s-axis accelerometer measured no data" )% (
                                    chip_axis,))
                    new_data = helper.process_accelerometer_data(aclient)
                    if calibration_data[axis] is None:
                        calibration_data[axis] = new_data
                    else:
                        calibration_data[axis].add_data(new_data)
        return calibration_data
    
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
        fig: Figure = self.plot_compare_frequency([belts['left']['data'], belts['right']['data']], ['Left belt', 'Right belt'], plot_freq, 'all')# no locale
        fig.set_size_inches(8, 6)
        belt_tension_path = os.path.join("/tmp/", csv_name.rpartition('/')[2].replace('.csv', '.png'))
        fig.savefig(belt_tension_path)

    def plot_compare_frequency(self, datas, lognames, max_freq, axis):
      fig, ax = matplotlib.pyplot.subplots()
      ax.set_title('Frequency responses comparison')
      ax.set_xlabel('Frequency (Hz)')
      ax.set_ylabel('Power spectral density')

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

        outputs = gcmd.get("OUTPUT", "resonances").lower().split(',')
        for output in outputs:
            if output not in ['resonances', 'raw_data']:
                raise gcmd.error(_("Unsupported output '%s', only 'resonances'"
                                 " and 'raw_data' are supported") % (output,))
        # Недостижимое условие
        # if not outputs:
        #     raise gcmd.error(_("No output specified, at least one of 'resonances'"
        #                      " or 'raw_data' must be set in OUTPUT parameter"))
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
                raw_name_suffix=name_suffix if raw_output else None)[axis]
        if csv_output:
            csv_name = self.save_calibration_data('resonances', name_suffix,
                                                  helper, axis, data)
            gcmd.respond_info(
                    _("Resonances data written to %s file") % (csv_name,))
            
    cmd_SHAPER_CALIBRATE_help = (
        _("Simular to TEST_RESONANCES but suggest input shaper config"))
    def cmd_SHAPER_CALIBRATE(self, gcmd):
        # Parse parameters
        axis = gcmd.get("AXIS", None)
        plot_freq = gcmd.get_float("PLOT_FREQ", 200.)
        if not axis or axis == 'all':
            calibrate_axes = [TestAxis('x'), TestAxis('y')]
        elif axis.lower() not in 'xy':
            raise gcmd.error(_("Unsupported axis '%s'") % (axis,))
        else:
            calibrate_axes = [TestAxis(axis.lower())]

        max_smoothing = gcmd.get_float(
                "MAX_SMOOTHING", self.max_smoothing, minval=0.05)

        name_suffix = gcmd.get("NAME", time.strftime("%Y%m%d_%H%M%S"))
        if not self.is_valid_name_suffix(name_suffix):
            raise gcmd.error(_("Invalid NAME parameter"))

        # Setup shaper calibration
        helper = shaper_calibrate.ShaperCalibrate(self.printer)
        self.printer.lookup_object('homing').run_G28_if_unhomed()
        calibration_data = self._run_test(gcmd, calibrate_axes, helper)

        configfile = self.printer.lookup_object('configfile')
        for axis in calibrate_axes:
            axis_name = axis.get_name()
            gcmd.respond_info(
                    _("Calculating the best input shaper parameters for %s axis")
                    % (axis_name,))
            calibration_data[axis].normalize_to_frequencies()
            best_shaper, all_shapers = helper.find_best_shaper(
                    calibration_data[axis], max_smoothing, gcmd.respond_info)
            gcmd.respond_info(
                    _("Recommended shaper_type_%s = %s, shaper_freq_%s = %.1f Hz")
                    % (axis_name, best_shaper.name,
                       axis_name, best_shaper.freq))
            helper.save_params(configfile, axis_name,
                               best_shaper.name, best_shaper.freq)
            csv_name = self.save_calibration_data(
                    'calibration_data', name_suffix, helper, axis,
                    calibration_data[axis], all_shapers)
            gcmd.respond_info(
                    _("Shaper calibration data written to %s file") % (csv_name,))
            fig: Figure = self.plot_freq_response(csv_name, calibration_data[axis], all_shapers,
                                best_shaper, plot_freq)
            fig.set_size_inches(8, 6)
            shaper_path = os.path.join("/tmp/", csv_name.rpartition('/')[2].replace('.csv', '.png'))
            fig.savefig(shaper_path)
            self.load_shaper_graph([shaper_path[1:]])
        gcmd.respond_info(
            _("The SAVE_CONFIG command will update the printer config file\n"
            "with these parameters and restart the printer."))
    
    def get_status(self, eventtime):
        return {
                  'saved': self.get_saved_shaper_graphs(),
                  'tmp': self.get_tmp_shaper_graphs(),
                  'belt_tensions': self.get_belt_tensions(),
                  'active_belt_tension': self.active_belt_tension,
                  'active': self.active_shaper_graph
        }
    
    def get_belt_tensions(self):
        return [f"tmp/{filename}" for filename in os.listdir("/tmp/") if self.tmp_belt_tension_r.match(filename)]
    
    def get_saved_shaper_graphs(self):
        return [f"config/.shaper-images/{filename}" for filename in os.listdir(self.shaper_graphs_dir) if filename.endswith('.png')]
    
    def get_tmp_shaper_graphs(self):
        return [f"tmp/{filename}" for filename in os.listdir("/tmp/") if self.tmp_shaper_graph_r.match(filename)]
    
    def _handle_set_active_tension(self, web_request):
        new_active_tension = web_request.get('tension', None)
        # if self.get_belt_tensions().count(new_active_tension):
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
        ax.set_xlabel(_("Frequency, Hz")) # no locale
        ax.set_xlim([0, max_freq])
        ax.set_ylabel(_("Power spectral density")) # no locale

        ax.plot(freqs, psd, label='X+Y+Z', color='purple')
        ax.plot(freqs, px, label='X', color='red')
        ax.plot(freqs, py, label='Y', color='green')
        ax.plot(freqs, pz, label='Z', color='blue')

        title = _("Frequency response and shapers (%s)") % (name.split('/').pop()) # no locale
        ax.set_title("\n".join(wrap(title, MAX_TITLE_LENGTH)))
        ax.xaxis.set_minor_locator(matplotlib.ticker.MultipleLocator(5))
        ax.yaxis.set_minor_locator(matplotlib.ticker.AutoMinorLocator())
        ax.ticklabel_format(axis='y', style='scientific', scilimits=(0,0))
        ax.grid(which='major', color='grey')
        ax.grid(which='minor', color='lightgrey')

        ax2 = ax.twinx()
        ax2.set_ylabel(_("Shaper vibration reduction (ratio)")) # no locale
        for shaper in shapers:
            label = _("%s (%.1f Hz, vibr=%.1f%%, sm~=%.2f, accel<=%.f)") % ( # no locale
                    shaper.name.upper(), shaper.freq,
                    shaper.vibrs * 100., shaper.smoothing,
                    round(shaper.max_accel / 100.) * 100.)
            linestyle = 'dotted'
            if shaper.name == selected_shaper:
                linestyle = 'dashdot'
            ax2.plot(freqs, shaper.vals, label=label, linestyle=linestyle)
        ax.plot(freqs, psd * selected_shaper.vals,
                label=_("After\nshaper"), color='cyan') # no locale
        # A hack to add a human-readable shaper recommendation to legend
        ax2.plot([], [], ' ',
                label=_("Recommended shaper: %s") % (selected_shaper.name.upper())) # no locale

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
                              axis, calibration_data, all_shapers=None):
        output = self.get_filename(base_name, name_suffix, axis)
        shaper_calibrate.save_calibration_data(output, calibration_data,
                                               all_shapers)
        return output

def load_config(config):
    return ResonanceTester(config)
