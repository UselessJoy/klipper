# Z-Probe support
#
# Copyright (C) 2017-2021  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
import pins
from . import manual_probe
import locales
HINT_TIMEOUT =_("""
If the probe did not move far enough to trigger, then
consider reducing the Z axis minimum position so the probe
can travel further (the Z minimum position can be negative).
""")
class PrinterProbe:
    def __init__(self, config, mcu_probe):
        self.printer = config.get_printer()
        self.config = config
        self.name = config.get_name()
        self.is_adjusting = False
        self.mcu_probe = mcu_probe
        self.speed = config.getfloat('speed', 5.0, above=0.)
        self.lift_speed = config.getfloat('lift_speed', self.speed, above=0.)
        self.last_z_position = 0
        self.x_offset = config.getfloat('x_offset', 0.)
        self.y_offset = config.getfloat('y_offset', 0.)
        self.z_offset = config.getfloat('z_offset')
        self.is_using_magnet_probe = False
        self.magnet_x = config.getfloat('magnet_x')
        self.magnet_y = config.getfloat('magnet_y')
        self.speed_base = config.getfloat('speed_base')
        
        self.vsd = self.gcode_move = self.toolhead = None
        self.gcode = self.printer.lookup_object('gcode')
        self.mutex = self.gcode.get_mutex()
        self.parking_magnet_y = config.getfloat('parking_magnet_y')
        self.speed_parking = config.getfloat('speed_parking')

        self.magnet_x_offset = config.getfloat('magnet_x_offset') 
        self.drop_z = config.getfloat('drop_z')
        
        self.probe_calibrate_z = 0.
        self.multi_probe_pending = False
        self.last_z_result = 0.
        self.is_printer_homing = False

        self.gcode_move = self.printer.load_object(config, "gcode_move")
        # Infer Z position to move to during a probe
        if config.has_section('stepper_z'):
            zconfig = config.getsection('stepper_z')
            self.z_position = zconfig.getfloat('position_min', 0.,
                                               note_valid=False)
        else:
            pconfig = config.getsection('printer')
            self.z_position = pconfig.getfloat('minimum_z_position', 0.,
                                               note_valid=False)
        # Multi-sample support (for improved accuracy)
        self.sample_count = config.getint('samples', 1, minval=1)
        self.sample_retract_dist = config.getfloat('sample_retract_dist', 2.,
                                                   above=0.)
        atypes = {'median': 'median', 'average': 'average'}
        self.samples_result = config.getchoice('samples_result', atypes,
                                               'average')
        self.samples_tolerance = config.getfloat('samples_tolerance', 0.100,
                                                 minval=0.)
        self.samples_retries = config.getint('samples_tolerance_retries', 0,
                                             minval=0)
        # Register z_virtual_endstop pin
        self.printer.lookup_object('pins').register_chip('probe', self)
        # Register homing event handlers
        self.printer.register_event_handler("homing:homing_move_begin",
                                            self._handle_homing_move_begin)
        self.printer.register_event_handler("homing:homing_move_end",
                                            self._handle_homing_move_end)
        self.printer.register_event_handler("homing:home_rails_begin",
                                            self._handle_home_rails_begin)
        self.printer.register_event_handler("homing:home_rails_end",
                                            self._handle_home_rails_end)
        self.printer.register_event_handler("gcode:command_error",
                                            self._handle_command_error)
        # Register PROBE/QUERY_PROBE commands
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command('PROBE', self.cmd_PROBE,
                                    desc=self.cmd_PROBE_help)
        self.gcode.register_command('QUERY_PROBE', self.cmd_QUERY_PROBE,
                                    desc=self.cmd_QUERY_PROBE_help)
        self.gcode.register_command('PROBE_CALIBRATE', self.cmd_PROBE_CALIBRATE,
                                    desc=self.cmd_PROBE_CALIBRATE_help)
        self.gcode.register_command('PROBE_ACCURACY', self.cmd_PROBE_ACCURACY,
                                    desc=self.cmd_PROBE_ACCURACY_help)
        self.gcode.register_command('Z_OFFSET_APPLY_PROBE',
                                    self.cmd_Z_OFFSET_APPLY_PROBE,
                                    desc=self.cmd_Z_OFFSET_APPLY_PROBE_help)
        self.gcode.register_command('START_ADJUSTMENT',
                                    self.cmd_START_ADJUSTMENT,
                                    desc=self.cmd_START_ADJUSTMENT_help)
        self.gcode.register_command('ACCEPT_ADJUSTMENT',
                                            self.cmd_ACCEPT_ADJUSTMENT,
                                            desc=self.cmd_ACCEPT_ADJUSTMENT_help)
        self.gcode.register_command('END_ADJUSTMENT',
                                            self.cmd_END_ADJUSTMENT,
                                            desc=self.cmd_END_ADJUSTMENT_help)
        # Регистрация команд на взятие/возврат магнита-пробы
        self.gcode.register_command('GET_MAGNET_PROBE', self.cmd_GET_MAGNET_PROBE,
                                            desc=self.cmd_GET_MAGNET_PROBE_help)
        self.gcode.register_command('RETURN_MAGNET_PROBE', self.cmd_RETURN_MAGNET_PROBE,
                                            desc=self.cmd_RETURN_MAGNET_PROBE_help)
        self.printer.register_event_handler("klippy:ready",
                                            self._on_ready)
        self.printer.register_event_handler("klippy:shutdown",
                                            self._on_shutdown)
        self.magnet_checker_timer = None
        self.reactor = self.printer.get_reactor()

    def is_probe_active(self):
      res = False
      try:
          print_time = self.toolhead.get_last_move_time(False)
          res = not bool(self.mcu_probe.query_endstop(print_time + 0.1))
      except Exception as e:
        logging.error(f"Error on is_probe_active: {e}\nThis often means what toolkead still not initialized")
      return res
    
    def __update_is_using_magnet_probe_field(self, eventtime):
        if not self.vsd.is_active() and not self.is_printer_homing:
          with self.mutex:
            self.is_using_magnet_probe = self.is_probe_active()
        return eventtime + 1
    
    def _on_shutdown(self):
        self.reactor.unregister_timer(self.magnet_checker_timer)
        self.magnet_checker_timer = None

    def _on_ready(self):
        self.vsd = self.printer.lookup_object('virtual_sdcard')
        self.gcode_move = self.printer.lookup_object('gcode_move')
        self.toolhead = self.printer.lookup_object('toolhead')
        self.magnet_checker_timer = self.reactor.register_timer(
                    self.__update_is_using_magnet_probe_field, self.reactor.NOW + 0.1)

    def take_magnet_probe(self):
        self.gcode_move.set_absolute_coord(True)
        self.drop_z_move()
        self.gcode.run_script_from_command(f"\
                            G1 X{self.magnet_x} Y{self.magnet_y} F{self.speed_base}\n\
                            G1 X{self.magnet_x} Y{self.parking_magnet_y} F{self.speed_parking}\n\
                         ")
        if not self.is_probe_active():
            raise self.printer.command_error(_("Couldn't take probe"))

    def return_magnet_probe(self):
        self.gcode_move.set_absolute_coord(True)
        self.drop_z_move()
        if float(self.toolhead.get_position()[1]) > self.parking_magnet_y:
            # Возвращение на парковочный Y
            self.gcode.run_script_from_command(f"G1 Y{self.parking_magnet_y} F{self.speed_base}")
        self.gcode.run_script_from_command(f"\
          G1 X{self.magnet_x} F{self.speed_base}\n\
          G1 Y{self.magnet_y} F{self.speed_parking}\n\
          G1 X{self.magnet_x_offset} F{self.speed_base}\n\
          G1 Y{self.parking_magnet_y} F{self.speed_parking}\n\
        ")
        if self.is_probe_active():
            raise self.printer.command_error(_("Couldn't return probe"))
    
    def _handle_homing_move_begin(self, hmove):
        if self.mcu_probe in hmove.get_mcu_endstops():
            self.mcu_probe.probe_prepare(hmove)
    def _handle_homing_move_end(self, hmove):
        if self.mcu_probe in hmove.get_mcu_endstops():
            self.mcu_probe.probe_finish(hmove)
    def _handle_home_rails_begin(self, homing_state, rails):
        self.is_printer_homing = True
        endstops = [es for rail in rails for es, name in rail.get_endstops()]
        if self.mcu_probe in endstops:
            self.multi_probe_begin()
    def _handle_home_rails_end(self, homing_state, rails):
        endstops = [es for rail in rails for es, name in rail.get_endstops()]
        if self.mcu_probe in endstops:
            self.multi_probe_end()
        self.is_printer_homing = False
    def _handle_command_error(self):
        try:
            self.multi_probe_end()
        except:
            logging.exception("Multi-probe end")
    def multi_probe_begin(self):
        self.mcu_probe.multi_probe_begin()
        self.multi_probe_pending = True
    def multi_probe_end(self):
        if self.multi_probe_pending:
            self.multi_probe_pending = False
            self.mcu_probe.multi_probe_end()
    def setup_pin(self, pin_type, pin_params):
        if pin_type != 'endstop' or pin_params['pin'] != 'z_virtual_endstop':
            raise pins.error(_("Probe virtual endstop only useful as endstop pin"))
        if pin_params['invert'] or pin_params['pullup']:
            raise pins.error(_("Can not pullup/invert probe virtual endstop"))
        return self.mcu_probe
    def get_lift_speed(self, gcmd=None):
        if gcmd is not None:
            return gcmd.get_float("LIFT_SPEED", self.lift_speed, above=0.)
        return self.lift_speed
    def get_offsets(self):
        return self.x_offset, self.y_offset, self.z_offset
    def _probe(self, speed):
        curtime = self.printer.get_reactor().monotonic()
        if 'z' not in self.toolhead.get_status(curtime)['homed_axes']:
            raise self.printer.command_error(_("Must home before probe"))
        phoming = self.printer.lookup_object('homing')
        pos = self.toolhead.get_position()
        pos[2] = self.z_position
        try:
            epos = phoming.probing_move(self.mcu_probe, pos, speed)
        except self.printer.command_error as e:
            reason = str(e)
            if _("Timeout during endstop homing") in reason:
                reason += HINT_TIMEOUT
            raise self.printer.command_error(reason)
        self.gcode.respond_info(_("probe at %.3f,%.3f is z=%.6f")
                                % (epos[0], epos[1], epos[2]))
        return epos[:3]
    def _move(self, coord, speed):
        self.toolhead.manual_move(coord, speed)
        
    # def return_z(self):
    #   self.toolhead.manual_move([None, None, self.last_z_position], self.speed_base)
    
    def drop_z_move(self):
        self.last_z_position = self.toolhead.get_position()[2]
        if float(self.toolhead.get_position()[2]) < self.drop_z:
            self.toolhead.manual_move([None, None, self.drop_z], self.speed_base)

    def _calc_mean(self, positions):
        count = float(len(positions))
        return [sum([pos[i] for pos in positions]) / count
                for i in range(3)]
    def _calc_median(self, positions):
        z_sorted = sorted(positions, key=(lambda p: p[2]))
        middle = len(positions) // 2
        if (len(positions) & 1) == 1:
            # odd number of samples
            return z_sorted[middle]
        # even number of samples
        return self._calc_mean(z_sorted[middle-1:middle+1])
        
    def run_probe(self, gcmd, delete_first_position=False):
        speed = gcmd.get_float("PROBE_SPEED", self.speed, above=0.)
        lift_speed = self.get_lift_speed(gcmd)
        sample_count = gcmd.get_int("SAMPLES", self.sample_count, minval=1)
        sample_retract_dist = gcmd.get_float("SAMPLE_RETRACT_DIST",
                                             self.sample_retract_dist, above=0.)
        samples_tolerance = gcmd.get_float("SAMPLES_TOLERANCE",
                                           self.samples_tolerance, minval=0.)
        samples_retries = gcmd.get_int("SAMPLES_TOLERANCE_RETRIES",
                                       self.samples_retries, minval=0)
        samples_result = gcmd.get("SAMPLES_RESULT", self.samples_result)
        must_notify_multi_probe = not self.multi_probe_pending
        if must_notify_multi_probe:
            self.multi_probe_begin()
        probexy = self.toolhead.get_position()[:2]
        retries = 0
        positions = []
        while len(positions) < sample_count:
            # Probe position
            pos = self._probe(speed)
            positions.append(pos)
            # Check samples tolerance
            z_positions = [p[2] for p in positions]
            if max(z_positions) - min(z_positions) > samples_tolerance:
                if retries >= samples_retries:
                    raise gcmd.error(_("Probe samples exceed samples_tolerance"))
                gcmd.respond_info(_("Probe samples exceed tolerance. Retrying..."))
                retries += 1
                positions = []
            # Retract
            if len(positions) < sample_count:
                self._move(probexy + [pos[2] + sample_retract_dist], lift_speed)
        if must_notify_multi_probe:
            self.multi_probe_end()
        # Calculate and return result
        if samples_result == 'median':
            if delete_first_position:
              positions.pop(0)
            return self._calc_median(positions)
        return self._calc_mean(positions)
    
    cmd_GET_MAGNET_PROBE_help = _("Command to get magnet probe") 
    def cmd_GET_MAGNET_PROBE(self, gcmd):
        self.printer.lookup_object('homing').run_G28_if_unhomed()
        self.take_magnet_probe()
        return
    
    cmd_RETURN_MAGNET_PROBE_help = _("Command to return magnet probe")
    def cmd_RETURN_MAGNET_PROBE(self, gcmd):
        self.printer.lookup_object('homing').run_G28_if_unhomed()
        self.return_magnet_probe()
        return
    
    cmd_START_ADJUSTMENT_help = _("Adjust magnet probe")
    def cmd_START_ADJUSTMENT(self, gcmd):
      self.is_adjusting = True
      self.printer.lookup_object('homing').run_G28_if_unhomed()
      self.gcode.run_script_from_command(f"G1 X{self.magnet_x} Y{self.magnet_y} F{self.speed_base}")
      self.drop_z_move()
    
    cmd_ACCEPT_ADJUSTMENT_help = _("Checking coordinates for magnet capture and saving it on success")
    def cmd_ACCEPT_ADJUSTMENT(self, gcmd):
      max_y = self.config.getsection('stepper_y').getfloat('position_max')
      
      x = gcmd.get_float("X", self.magnet_x)
      y = gcmd.get_float("Y", self.magnet_y)
      if y - max_y > 5:
        messages = self.printer.lookup_object("messages")
        messages.send_message('warning', _("Y coordinate too far from border. Please, return extruder to socket position"))
        return
      if self.run_gcode_check_magnet(x, y):
        self.is_adjusting = False
        configfile = self.printer.lookup_object('configfile')
        probe_section = {f"probe": {"magnet_x": x, "magnet_y": y}}
        self.magnet_x = x
        self.magnet_y = y
        configfile.update_config(setting_sections=probe_section, save_immediatly=True)
        messages = self.printer.lookup_object("messages")
        messages.send_message('success', _("Magnet check successfull. New coordinates saved"))
    
    cmd_END_ADJUSTMENT_help = _("End adjustment")
    def cmd_END_ADJUSTMENT(self, gcmd):
      self.is_adjusting = False
      if not self.is_probe_active():
        self.gcode.run_script_from_command(f"G1 X{self.magnet_x_offset} F{self.speed_base}")
      else:
        self.return_magnet_probe()
      
    def run_gcode_check_magnet(self, x, y):
      self.gcode_move.set_absolute_coord(True)
      self.drop_z_move()
      self.gcode.run_script_from_command(f"G1 X{x} Y{self.parking_magnet_y} F{self.speed_parking}")
      messages = self.printer.lookup_object("messages")
      if not self.is_probe_active():
          messages.send_message('warning', _("Couldn't take probe"))
          self.gcode.run_script_from_command(f"G1 X{x} Y{y} F{self.speed_base}")
          return False
      self.gcode.run_script_from_command(f"\
                            G1 X{x} Y{y} F{self.speed_parking}\n\
                            G1 X{self.magnet_x_offset} F{self.speed_parking}\n\
                            G1 Y{self.parking_magnet_y} F{self.speed_base}\n\
                         ")
      if self.is_probe_active():
          messages.send_message('warning', _("Couldn't return probe"))
          return False
      return True    
      
    def on_start_probe(self, correcting_probe=False):
        self.printer.lookup_object('homing').run_G28_if_unhomed()
        # 1 - open (подключен и стол не тыкнут), 0 - triggered (отключен или стол тыкнут)
        self.drop_z_move()
        if not self.is_probe_active():
            self.take_magnet_probe()
        curtime = self.printer.get_reactor().monotonic()
        toolhead_status = self.toolhead.get_status(curtime)
        if not self.multi_probe_pending:
          if correcting_probe:
            pos = [(toolhead_status['axis_maximum'][i])/2 - offset for i,offset in enumerate([self.x_offset, self.y_offset])]
          else:
            pos = [toolhead_status['axis_maximum'][i]/2 for i in range(0, 2)]
          pos.append(self.drop_z)
        else:
            pos = self.toolhead.get_position()
        self._move(pos, self.speed_base)
         
    cmd_PROBE_help = _("Probe Z-height at current XY position")
    def cmd_PROBE(self, gcmd):
        self.on_start_probe()
        pos = self.run_probe(gcmd)
        gcmd.respond_info(_("Result is z=%.6f") % (pos[2],))
        self.last_z_result = pos[2]
        self.drop_z_move()
        
    cmd_QUERY_PROBE_help = _("Return the status of the z-probe")
    def cmd_QUERY_PROBE(self, gcmd):
        res = self.is_probe_active()
        gcmd.respond_info(_("probe: %s") % (["open", "TRIGGERED"][res],))

    def get_status(self, eventtime):
        return {'last_query': self.is_using_magnet_probe, # Останется до следующего патча флуида
                'last_z_result': self.last_z_result,
                'is_using_magnet_probe': self.is_using_magnet_probe,
                'is_adjusting': self.is_adjusting}

    cmd_PROBE_ACCURACY_help = _("Probe Z-height accuracy at current XY position")
    def cmd_PROBE_ACCURACY(self, gcmd):
        speed = gcmd.get_float("PROBE_SPEED", self.speed, above=0.)
        lift_speed = self.get_lift_speed(gcmd)
        sample_count = gcmd.get_int("SAMPLES", 10, minval=1)
        sample_retract_dist = gcmd.get_float("SAMPLE_RETRACT_DIST",
                                             self.sample_retract_dist, above=0.)
        self.on_start_probe()
        pos = self.toolhead.get_position()
        gcmd.respond_info(_("PROBE_ACCURACY at X:%.3f Y:%.3f Z:%.3f"
                          " (samples=%d retract=%.3f"
                          " speed=%.1f lift_speed=%.1f)\n")
                          % (pos[0], pos[1], pos[2],
                             sample_count, sample_retract_dist,
                             speed, lift_speed))
        # Probe bed sample_count times
        self.multi_probe_begin()
        positions = []
        while len(positions) < sample_count:
            # Probe position
            pos = self._probe(speed)
            positions.append(pos)
            # Retract
            liftpos = [None, None, pos[2] + sample_retract_dist]
            self._move(liftpos, lift_speed)
        self.multi_probe_end()
        # Calculate maximum, minimum and average values
        max_value = max([p[2] for p in positions])
        min_value = min([p[2] for p in positions])
        range_value = max_value - min_value
        avg_value = self._calc_mean(positions)[2]
        median = self._calc_median(positions)[2]
        # calculate the standard deviation
        deviation_sum = 0
        for i in range(len(positions)):
            deviation_sum += pow(positions[i][2] - avg_value, 2.)
        sigma = (deviation_sum / len(positions)) ** 0.5
        # Show information
        gcmd.respond_info(
            _("probe accuracy results: maximum %.6f, minimum %.6f, range %.6f, "
            "average %.6f, median %.6f, standard deviation %.6f") % (
            max_value, min_value, range_value, avg_value, median, sigma))
        self.drop_z_move()

    def probe_calibrate_finalize(self, kin_pos):
      if kin_pos is None:
          return
      self.z_offset = self.probe_calibrate_z - kin_pos[2]
      self.save_z_offset()

    cmd_PROBE_CALIBRATE_help = _("Calibrate the probe's z_offset")
    def cmd_PROBE_CALIBRATE(self, gcmd):
        manual_probe.verify_no_manual_probe(self.printer)
        # Perform initial probe
        lift_speed = self.get_lift_speed(gcmd)
        self.on_start_probe(correcting_probe=True)
        curpos = self.run_probe(gcmd, delete_first_position=True)
        # Move away from the bed
        self.probe_calibrate_z = self.last_z_result = curpos[2]
        curpos[2] += 5.
        self._move(curpos, lift_speed)
        # Move the nozzle over the probe point
        curpos[0] += self.x_offset
        curpos[1] += self.y_offset
        self.return_magnet_probe()
        self._move(curpos, self.speed_base)
        # Start manual probe
        manual_probe.ManualProbeHelper(self.printer, self.config, gcmd,
                                       self.probe_calibrate_finalize)

    cmd_Z_OFFSET_APPLY_PROBE_help = _("Adjust the probe's z_offset")    
    def cmd_Z_OFFSET_APPLY_PROBE(self,gcmd):
        offset = self.gcode_move.get_status()['homing_origin'].z
        configfile = self.printer.lookup_object('configfile')
        if offset == 0:
            self.gcode.respond_info(_("Nothing to do: Z Offset is 0"))
        else:
            self.z_offset -= offset
            self.save_z_offset()

    def save_z_offset(self):
        configfile = self.printer.lookup_object('configfile')
        configfile.update_config({self.name: {'z_offset': f"{self.z_offset:.3f}"}})
        self.printer.lookup_object('messages').send_message("success",_("%s: z_offset: %.3f\n"
              "New position saved") % (self.name, self.z_offset))
# Endstop wrapper that enables probe specific features
class ProbeEndstopWrapper:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.position_endstop = config.getfloat('z_offset')
        self.stow_on_each_sample = config.getboolean(
            'deactivate_on_each_sample', True)
        gcode_macro = self.printer.load_object(config, 'gcode_macro')
        self.activate_gcode = gcode_macro.load_template(
            config, 'activate_gcode', '')
        self.deactivate_gcode = gcode_macro.load_template(
            config, 'deactivate_gcode', '')
        # Create an "endstop" object to handle the probe pin
        ppins = self.printer.lookup_object('pins')
        pin = config.get('pin')
        pin_params = ppins.lookup_pin(pin, can_invert=True, can_pullup=True)
        mcu = pin_params['chip']
        self.mcu_endstop = mcu.setup_pin('endstop', pin_params)
        self.printer.register_event_handler('klippy:mcu_identify',
                                            self._handle_mcu_identify)
        # Wrappers
        self.get_mcu = self.mcu_endstop.get_mcu
        self.add_stepper = self.mcu_endstop.add_stepper
        self.get_steppers = self.mcu_endstop.get_steppers
        self.home_start = self.mcu_endstop.home_start
        self.home_wait = self.mcu_endstop.home_wait
        self.query_endstop = self.mcu_endstop.query_endstop
        # multi probes state
        self.multi = 'OFF'

    def _handle_mcu_identify(self):
        kin = self.printer.lookup_object('toolhead').get_kinematics()
        for stepper in kin.get_steppers():
            if stepper.is_active_axis('z'):
                self.add_stepper(stepper)
    def raise_probe(self):
        toolhead = self.printer.lookup_object('toolhead')
        start_pos = toolhead.get_position()
        self.deactivate_gcode.run_gcode_from_command()
        if toolhead.get_position()[:3] != start_pos[:3]:
            raise self.printer.command_error(
                _("Toolhead moved during probe deactivate_gcode script"))
    def lower_probe(self):
        toolhead = self.printer.lookup_object('toolhead')
        start_pos = toolhead.get_position()
        self.activate_gcode.run_gcode_from_command()
        if toolhead.get_position()[:3] != start_pos[:3]:
            raise self.printer.command_error(
                _("Toolhead moved during probe activate_gcode script"))
    def multi_probe_begin(self):
        if self.stow_on_each_sample:
            return
        self.multi = 'FIRST'
    def multi_probe_end(self):
        if self.stow_on_each_sample:
            return
        self.raise_probe()
        self.multi = 'OFF'
    def probe_prepare(self, hmove):
        if self.multi == 'OFF' or self.multi == 'FIRST':
            self.lower_probe()
            if self.multi == 'FIRST':
                self.multi = 'ON'
    def probe_finish(self, hmove):
        if self.multi == 'OFF':
            self.raise_probe()
    def get_position_endstop(self):
        return self.position_endstop
# Helper code that can probe a series of points and report the
# position at each point.
class ProbePointsHelper:
    def __init__(self, config, finalize_callback, default_points=None, hz = 7., hzs = 7.):
        self.printer = config.get_printer()
        self.config = config
        self.stop = False
        self.finalize_callback = finalize_callback
        self.probe_points = default_points
        self.name = config.get_name()
        stepper_x = config.getsection('stepper_x')
        stepper_y = config.getsection('stepper_y')
        self.min_x = stepper_x.getfloat('position_min')
        self.min_y = stepper_y.getfloat('position_min')
        self.gcode = self.printer.lookup_object('gcode')
        # Read config settings
        if default_points is None or config.get('points', None) is not None:
            self.probe_points = config.getlists('points', seps=(',', '\n'),
                                                parser=float, count=2)
        self.horizontal_move_z = config.getfloat('horizontal_move_z', hz)
        self.horizontal_move_z_start = config.getfloat('horizontal_move_z_start', hzs)
        self.speed = config.getfloat('speed', 50., above=0.)
        self.use_offsets = False
        # Internal probing state
        self.lift_speed = self.speed
        self.probe_offsets = (0., 0., 0.)
        self.results = []
    def minimum_points(self,n):
        if len(self.probe_points) < n:
            raise self.printer.config_error(
                _("Need at least %d probe points for %s") % (n, self.name))
    def update_probe_points(self, points, min_points):
        self.probe_points = points
        self.minimum_points(min_points)
    def use_xy_offsets(self, use_offsets):
        self.use_offsets = use_offsets
    def get_lift_speed(self):
        return self.lift_speed
        
    def _move_next(self, return_probe):
        toolhead = self.printer.lookup_object('toolhead')
        # Lift toolhead
        speed = self.lift_speed
        horizontal_move = self.horizontal_move_z
        if not self.results:
            # Use full speed to first probe position
            speed = self.speed
            horizontal_move = self.horizontal_move_z_start
        toolhead.manual_move([None, None, horizontal_move], speed)
        # Check if done probing
        if len(self.results) >= len(self.probe_points):
            res = self.finalize_callback(self.probe_offsets, self.results)
            if res != "retry":
                if return_probe:
                    self.printer.lookup_object('probe').return_magnet_probe()
                return True
            self.results = []
        # Move to next XY probe point
        nextpos = list(self.probe_points[len(self.results)])
        if self.use_offsets:
            if not (nextpos[0] - self.probe_offsets[0] < self.min_x):
                nextpos[0] -= self.probe_offsets[0]
            if not (nextpos[1] - self.probe_offsets[1] < self.min_y):
                nextpos[1] -= self.probe_offsets[1]
        toolhead.manual_move(nextpos, self.speed)
        return False

    def stop_probe(self):
      self.stop = True

    def start_probe(self, gcmd, return_probe = True):
        manual_probe.verify_no_manual_probe(self.printer)
        # Lookup objects
        probe = self.printer.lookup_object('probe', None)
        method = gcmd.get('METHOD', 'automatic').lower()
        self.results = []
        if probe is None or method != 'automatic':
            # Manual probe
            self.lift_speed = self.speed
            self.probe_offsets = (0., 0., 0.)
            self._manual_probe_start()
            return
        # Perform automatic probing
        self.lift_speed = probe.get_lift_speed(gcmd)
        self.probe_offsets = probe.get_offsets()
        if self.horizontal_move_z < self.probe_offsets[2]:
            raise gcmd.error(_("horizontal_move_z can't be less than"
                             " probe's z_offset"))
        probe.multi_probe_begin()
        self.printer.lookup_object('probe').on_start_probe()
        while not self.stop:
            self.printer.send_event("probe:xy_move")
            done = self._move_next(return_probe)
            if done:
                break
            pos = probe.run_probe(gcmd)
            self.results.append(pos)
        if self.stop:
            self.printer.lookup_object('toolhead').manual_move([None, None, self.horizontal_move_z], self.lift_speed)
        self.stop = False
        probe.multi_probe_end()
        
    def _manual_probe_start(self):
        done = self._move_next()
        if not done:
            gcmd = self.gcode.create_gcode_command("", "", {})
            manual_probe.ManualProbeHelper(self.printer, self.config, gcmd,
                                           self._manual_probe_finalize)
    def _manual_probe_finalize(self, kin_pos):
        if kin_pos is None:
            return
        self.results.append(kin_pos)
        self._manual_probe_start()
def load_config(config):
    return PrinterProbe(config, ProbeEndstopWrapper(config))