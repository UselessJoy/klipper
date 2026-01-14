# Helper script to adjust bed screws tilt using Z probe
#
# Copyright (C) 2019  Rui Caridade <rui.mcbc@gmail.com>
# Copyright (C) 2021  Matthew Lloyd <github@matthewlloyd.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import math
from . import probe
import locales 
import logging
# Factors used for CW-M3, CCW-M3, CW-M4, CCW-M4, CW-M5 and CCW-M5
THREADS_FACTOR = {0: 0.5, 1: 0.5, 2: 0.7, 3: 0.7, 4: 0.8, 5: 0.8}

class ScrewsTiltAdjust:
    def __init__(self, config):
        self.config = config
        self.printer = config.get_printer()
        self.screws = {}
        self.results = {}
        self.max_diff = None
        self.minutes_deviation = 3
        self.search_highest = False
        self.i_base = self.z_base = self.base_screw = self.calibrating_screw = self.calibrating_screws = self.next_screw = None
        self.stop_screw = self.stop_calibrate = self.is_calibrating = False
        self.max_diff_error = False
        self.success = False
        self.multi_tap = False
        self.adjusted_screws = 0
        # Read config
        for i in range(99):
            prefix = "screw%d" % (i + 1,)
            if config.get(prefix, None) is None:
                break
            screw_coord = config.getfloatlist(prefix, count=2)
            screw_name = _("screw at %.3f, %.3f") % screw_coord
            screw_name = config.get(prefix + "_name", screw_name)
            # i надо поменять
            self.screws[prefix] = {'prefix': prefix, 'coord': screw_coord, 'name': screw_name}
        if len(self.screws) < 3:
            raise config.error(_("screws_tilt_adjust: Must have "
                               "at least three screws"))
        self.base_screw = next(iter(self.screws))
        self.i_base = int(self.base_screw[-1])
        self.threads = {'CW-M3': 0, 'CCW-M3': 1, 'CW-M4': 2, 'CCW-M4': 3,
                        'CW-M5': 4, 'CCW-M5': 5}
        self.thread = config.getchoice('screw_thread', self.threads,
                                       default='CW-M3')
        # Initialize ProbePointsHelper
        points = [self.screws[name]['coord'] for name in self.screws]
        self.probe_helper = probe.ProbePointsHelper(self.config,
                                                    self.probe_finalize,
                                                    default_points=points)
        self.probe_helper.minimum_points(3)
        # Register command
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command("SCREWS_TILT_CALCULATE",
                                    self.cmd_SCREWS_TILT_CALCULATE,
                                    desc=self.cmd_SCREWS_TILT_CALCULATE_help)
        self.gcode.register_command("SCREWS_TILT_CALIBRATE",
                                    self.cmd_SCREWS_TILT_CALIBRATE,
                                    desc=self.cmd_SCREWS_TILT_CALIBRATE_help)
        self.gcode.register_command("SET_BASE_SCREW",
                                    self.cmd_SET_BASE_SCREW,
                                    desc=self.cmd_SET_BASE_SCREW_help)
        self.gcode.register_command("RESET_BASE_SCREW",
                                    self.cmd_RESET_BASE_SCREW,
                                    desc=self.cmd_RESET_BASE_SCREW_help)
        # Регистрация асинхронных команд
        self.gcode.register_async_command("ASYNC_STOP_SCREW_CALIBRATE",
                                    self.cmd_async_STOP_SCREW_CALIBRATE,
                                    desc=self.cmd_ASYNC_STOP_SCREW_CALIBRATE_help)
        self.gcode.register_async_command("ASYNC_STOP_SCREWS_TILT_CALIBRATE",
                                    self.cmd_async_STOP_SCREWS_TILT_CALIBRATE,
                                    desc=self.cmd_ASYNC_STOP_SCREWS_TILT_CALIBRATE_help)
        self.gcode.register_async_command("ASYNC_SET_CALIBRATING_SCREW",
                                    self.cmd_async_SET_CALIBRATING_SCREW,
                                    desc=self.cmd_ASYNC_SET_CALIBRATING_SCREW_help)
    
    cmd_ASYNC_STOP_SCREW_CALIBRATE_help = _("Stop the current screw calibrating")
    def cmd_async_STOP_SCREW_CALIBRATE(self, gcmd):
        self.stop_screw = True

    cmd_ASYNC_STOP_SCREWS_TILT_CALIBRATE_help = _("Stop SCREWS_TILT_CALIBRATE command")
    def cmd_async_STOP_SCREWS_TILT_CALIBRATE(self, gcmd):
        self.stop_calibrate = True
    
    cmd_ASYNC_SET_CALIBRATING_SCREW_help = _("Go to the given screw to calibrate")
    def cmd_async_SET_CALIBRATING_SCREW(self, gcmd):
        messages = self.printer.lookup_object('messages')
        if self.next_screw:
            messages.send_message("warning", _("Cannot set new calibrating screw: already wait move to next screw %s") % self.next_screw)
            return
        elif self.search_highest:
            messages.send_message("error", _("Cannot set new calibrating screw: searching highest screw"))
            return
        elif not self.is_calibrating:
            messages.send_message("error", _("Cannot set new calibrating screw: calibration is not performed"))
            return
        screw = gcmd.get_commandline().strip().rpartition("=")[2]
        if screw == "":
            messages.send_message("error", _("Cannot set new calibrating screw: screw is not defined"))
            return
        elif screw == self.base_screw:
            messages.send_message("error", _("Cannot set new calibrating screw: screw %s is base screw") % screw)
            return
        elif screw not in self.screws:
            messages.send_message("error", _("Cannot set new calibrating screw: screw %s not in defined screws") % screw)
            return
        self.next_screw = screw
        self.stop_screw = True
      
    cmd_SET_BASE_SCREW_help = _("Set the given screw as base screw")
    def cmd_SET_BASE_SCREW(self, gcmd):
        messages = self.printer.lookup_object('messages')
        if self.is_calibrating:
            messages.send_message("error", _("Cannot set new base screw: calibration already performed"))
            return
        screw = gcmd.get_commandline().strip().rpartition("=")[2]
        if screw == "":
            return
        elif screw not in self.screws:
            messages.send_message("error", _("Cannot set new base screw: screw %s is not defined") % screw)
            return
        self.base_screw = screw
        self.i_base = int(self.base_screw[-1])
        
    cmd_RESET_BASE_SCREW_help = _("Reset base screw")
    def cmd_RESET_BASE_SCREW(self, gcmd):
        messages = self.printer.lookup_object('messages')
        if self.is_calibrating:
            messages.send_message("error", _("Cannot reset base screw: calibration already performed"))
            return
        self.base_screw = next(iter(self.screws))
        
    cmd_SCREWS_TILT_CALCULATE_help = _("Tool to help adjust bed leveling\nscrews by calculating the number\nof turns to level it.")
    def cmd_SCREWS_TILT_CALCULATE(self, gcmd):
        self.is_calibrating = True
        self.results = {}
        self.max_diff = gcmd.get_float("MAX_DEVIATION", None)
        return_probe = gcmd.get_boolean("RETURN_PROBE", False)
        # Option to force all turns to be in the given direction (CW or CCW)
        self.direction = self.get_direction(gcmd)
        try:
            self.probe_helper.start_probe(gcmd, return_probe)
        except Exception as e:
            self.is_calibrating = False
            raise e
        self.is_calibrating = False
    
    
    cmd_SCREWS_TILT_CALIBRATE_help = _("Calibrate screws until SCREWS_TILT_CALIBRATE_STOP send or increase minimal deviation of screws")
    def cmd_SCREWS_TILT_CALIBRATE(self, gcmd):
        messages = self.printer.lookup_object('messages')
        self.minutes_deviation = gcmd.get_int("MAX_MINUTES_DEVIATION", 3)
        self.is_calibrating = True
        self.results = {}
        self.calibrating_screw = None
        self.stop_screw = self.stop_calibrate = False
        self.direction = self.get_direction(gcmd)
        self.multi_tap = False
        adjusting = False
        self.adjusted_screws = 0
        probe_obj = self.printer.lookup_object("probe")
        try:
            self.search_highest = True
            self.find_highest_screw(gcmd, False)
            self.search_highest = False
            logging.info(f"highest base screw is {self.base_screw}")
            while not self.stop_calibrate:
                if not adjusting:
                  if not self.direction: 
                      self.calibrating_screw = self.screws[self.base_screw]
                      probe_screw = probe.ProbePointsHelper(self.config, self.on_base_screw_finalize, default_points=[self.calibrating_screw['coord']])
                      probe_screw.start_probe(gcmd, False)
                  elif len(self.results) == 0:
                      self.probe_helper.start_probe(gcmd, False)

                  self.calibrating_screws = self.screws.copy()
                  self.calibrating_screws.pop(self.base_screw)
                  adjusting = True

                if self.next_screw:
                    self.calibrating_screw = self.calibrating_screws[self.next_screw]
                    self.next_screw = None
                else:
                    next_i  = (int(self.calibrating_screw['prefix'][-1]) + 1) % (len(self.screws) + 1)
                    cycle_i = 0
                    while f"screw{next_i}" not in self.calibrating_screws or next_i == self.i_base:
                      if cycle_i > 4:
                        raise gcmd.error("Error on select next screw: iterations more than screws")
                      next_i = (next_i + 1) % (len(self.screws) + 1)
                      cycle_i += 1
                    # next_i = (int(self.calibrating_screw['prefix'][-1]) + 1) % (len(self.screws) + 1)
                    # if self.i_base == next_i:
                    #     next_i = (int(self.calibrating_screw['prefix'][-1]) + 2) % (len(self.screws) + 1)
                    # next_i = next_i + 1 if next_i == 0 else next_i
                    self.calibrating_screw = self.calibrating_screws[f"screw{next_i}"]
                probe_screw = probe.ProbePointsHelper(self.config, self.on_screw_finalize, default_points=[self.calibrating_screw['coord']])
                probe_screw.start_probe(gcmd, False)
                if self.adjusted_screws == len(self.calibrating_screws):
                    self.adjusted_screws = 0
                    adjusting = False
                    if not self.multi_tap:
                        self.success = True
                        self.stop_calibrate = True
                    self.multi_tap = False
            if self.success:
                self.success = False
                messages.send_message("success", _("Successfull calibrated screws!"))
            else:
                messages.send_message("//", _("Screws calibrating stoped"))
        except Exception as e:
            self.calibrating_screws = None
            self.is_calibrating = False
            # probe_obj.drop_z_move()
            # probe_obj.return_magnet_probe()
            raise e
        self.calibrating_screws = None
        self.is_calibrating = False
        probe_obj.drop_z_move()
        probe_obj.return_magnet_probe()
    
    def find_highest_screw(self, gmcd, return_probe):
        points = [self.screws[name]['coord'] for name in self.screws]
        # По таске нам нужен двойной тык при первой пробе 
        points.insert(1, points[0])
        probe_helper = probe.ProbePointsHelper(self.config,
                                                    self._highest_i_finalize,
                                                    default_points=points)
        probe_helper.start_probe(gmcd, return_probe)
    
    def _highest_i_finalize(self, offsets, positions: list[tuple]):
        positions.pop(0)
        max_z = positions[0][2]
        screw_iter = iter(self.screws)
        for position in positions:
            next_screw = next(screw_iter)
            if max_z < position[2]:
                max_z = position[2]
                self.base_screw = next_screw
                self.i_base = int(self.base_screw[-1])
                logging.info(f"highest position {position[2]} on screw {self.base_screw}")
        logging.info(f"find highest screw {self.base_screw}")
            
    def probe_finalize(self, offsets, positions: list[tuple]):
        self.max_diff_error = False
        is_clockwise_thread = (self.thread & 1) == 0
        screw_diff = []
        self.i_base, self.z_base = self.find_base_screw(is_clockwise_thread, positions)
        # Provide the user some information on how to read the results
        self.gcode.respond_info(_("01:20 means 1 full turn and 20 minutes, "
                                "CW=clockwise, CCW=counter-clockwise"))
        for i, screw in enumerate(self.screws):
            z = positions[i][2]
            coord, name = self.screws[screw]['coord'], self.screws[screw]['name']
            if self.screws[screw]['prefix'] in self.results:
                del self.results[self.screws[screw]['prefix']]
            if screw == self.base_screw:
                # Show the results
                self.gcode.respond_info(
                    "%s : x=%.1f, y=%.1f, z=%.5f" %
                    (name + ' (base)', coord[0], coord[1], z))
                sign = "CW" if is_clockwise_thread else "CCW"
                self.results[self.screws[screw]['prefix']] = {'x': coord[0], 'y': coord[1],'z': z, 
                    'sign': sign, 'adjust': '00:00', 'is_base': True
                }
            else:
                # Calculate how knob must be adjusted for other positions
                sign, full_turns, minutes, diff = self.calculate_adjust(positions[i][2])
                screw_diff.append(abs(diff))
                # Show the results
                self.gcode.respond_info(
                    "%s : x=%.1f, y=%.1f, z=%.5f : adjust %s %02d:%02d" %
                    (name, coord[0], coord[1], z, sign, full_turns, minutes))
                self.results[self.screws[screw]['prefix']] = {'x': coord[0], 'y': coord[1],'z': z, 
                    'sign': sign, 'adjust':"%02d:%02d" % (full_turns, minutes), 'is_base': False
                }
        if self.max_diff and any((d > self.max_diff) for d in screw_diff):
            self.max_diff_error = True
            raise self.gcode.error(
                _("bed level exceeds configured limits ({}mm)! " 
                "Adjust screws and restart print.").format(self.max_diff))
        self.printer.send_event("screw_tilt_adjust:end_probe", self.results)

    def get_direction(self, gcmd):
        direction = gcmd.get("DIRECTION", default=None)
        if direction is not None:
            direction = direction.upper()
            if direction not in ('CW', 'CCW'):
                raise gcmd.error(
                    _("Error on '%s': DIRECTION must be either CW or CCW") % (
                        gcmd.get_commandline(),))
        return direction
      
    def on_base_screw_finalize(self, offsets, positions):
        self.z_base = positions[0][2]
        is_clockwise_thread = (self.thread & 1) == 0
        sign = "CW" if is_clockwise_thread else "CCW"
        if self.calibrating_screw['prefix'] in self.results:
                del self.results[self.calibrating_screw['prefix']]
        self.results[self.calibrating_screw['prefix']] = {'x': self.calibrating_screw['coord'][0], 'y': self.calibrating_screw['coord'][1], 'z': self.z_base, 
            'sign': sign, 'adjust': '00:00', 'is_base': True
        }
    
    def on_screw_finalize(self, offsets, positions):
        if not self.stop_screw and not self.stop_calibrate:
            sign, full_turns, minutes, diff = self.calculate_adjust(positions[0][2])
            if self.calibrating_screw['prefix'] in self.results:
                del self.results[self.calibrating_screw['prefix']]
            self.results[self.calibrating_screw['prefix']] = {'x': self.calibrating_screw['coord'][0], 'y': self.calibrating_screw['coord'][1], 'z': positions[0][2], 
                'sign': sign, 'adjust':"%02d:%02d" % (full_turns, minutes), 'is_base': False
            }
            if not (full_turns == 0 and minutes <= self.minutes_deviation):
                self.gcode.respond_info(_("adjust %s %02d:%02d") % (sign, full_turns, minutes))# no locale
                self.multi_tap = True
                return "retry"
            self.gcode.respond_info(_("Successfull calibrated screw %s") % self.calibrating_screw['name'])
            self.adjusted_screws += 1
        else:
            self.stop_screw = False
            self.gcode.respond_info(_("Current screw calibrating stoped"))
    
    def calculate_adjust(self, z_screw):
        is_clockwise_thread = (self.thread & 1) == 0
        diff = self.z_base - z_screw
        if abs(diff) < 0.001:
            adjust = 0
        else:
            adjust = diff / THREADS_FACTOR.get(self.thread, 0.5)
        if is_clockwise_thread:
            sign = "CW" if adjust >= 0 else "CCW"
        else:
            sign = "CCW" if adjust >= 0 else "CW"
        adjust = abs(adjust)
        full_turns = math.trunc(adjust)
        decimal_part = adjust - full_turns
        minutes = round(decimal_part * 60, 0)
        return sign, full_turns, minutes, diff
            
    def find_base_screw(self, is_clockwise_thread, positions):        
        # Process the read Z values
        if self.direction is not None:
            # Lowest or highest screw is the base position used for comparison
            use_max = ((is_clockwise_thread and self.direction == 'CW')
                    or (not is_clockwise_thread and self.direction == 'CCW'))
            min_or_max = max if use_max else min
            base_coord = min_or_max(enumerate([pos for pos in positions]), key=lambda v: v[2])
            z_base  = base_coord[2]
            for screw in self.screws:
                if self.screws[screw]['coord'] == (base_coord[0], base_coord[1]):
                    self.base_screw = screw
        else:
            # First screw is the base position used for comparison
            z_base = positions[0][2]
            self.base_screw = next(iter(self.screws))
        i_base = int(self.base_screw[-1])
        return i_base, z_base
            
    def get_status(self, eventtime):
        # Копируем results, поскольку может вылететь 400-я ошибка во время запроса
        return{
                'error': self.max_diff_error,
                'results': self.results.copy(),
                'base_screw': self.base_screw,
                'calibrating_screw': self.calibrating_screw,
                'is_calibrating': self.is_calibrating,
                'search_highest': self.search_highest
              }
    
    def probe_finalize(self, offsets, positions):
        self.max_diff_error = False
        is_clockwise_thread = (self.thread & 1) == 0
        screw_diff = []
        self.i_base, self.z_base = self.find_base_screw(is_clockwise_thread, positions)
        # Provide the user some information on how to read the results
        self.gcode.respond_info(_("01:20 means 1 full turn and 20 minutes, "
                                "CW=clockwise, CCW=counter-clockwise"))
        for i, screw in enumerate(self.screws):
            z = positions[i][2]
            coord, name = self.screws[screw]['coord'], self.screws[screw]['name']
            if self.screws[screw]['prefix'] in self.results:
                del self.results[self.screws[screw]['prefix']]
            if screw == self.base_screw:
                # Show the results
                self.gcode.respond_info(
                    "%s : x=%.1f, y=%.1f, z=%.5f" %
                    (name + ' (base)', coord[0], coord[1], z))
                sign = "CW" if is_clockwise_thread else "CCW"
                self.results[self.screws[screw]['prefix']] = {'x': coord[0], 'y': coord[1],'z': z, 
                    'sign': sign, 'adjust': '00:00', 'is_base': True
                }
            else:
                # Calculate how knob must be adjusted for other positions
                sign, full_turns, minutes, diff = self.calculate_adjust(positions[i][2])
                screw_diff.append(abs(diff))
                # Show the results
                self.gcode.respond_info(
                    "%s : x=%.1f, y=%.1f, z=%.5f : adjust %s %02d:%02d" %
                    (name, coord[0], coord[1], z, sign, full_turns, minutes))
                self.results[self.screws[screw]['prefix']] = {'x': coord[0], 'y': coord[1],'z': z, 
                    'sign': sign, 'adjust':"%02d:%02d" % (full_turns, minutes), 'is_base': False
                }
        if self.max_diff and any((d > self.max_diff) for d in screw_diff):
            self.max_diff_error = True
            raise self.gcode.error(
                _("bed level exceeds configured limits ({}mm)! " 
                "Adjust screws and restart print.").format(self.max_diff))
        self.printer.send_event("screw_tilt_adjust:end_probe", self.results)

def load_config(config):
    return ScrewsTiltAdjust(config)
