# Helper script for manual z height probing
#
# Copyright (C) 2019  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging, bisect
import locales
class ManualProbe:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.config = config
        self.drop_z = config.getfloat('drop_z', 5)
        self.manual_speed = config.getfloat('manual_speed', 3000)
        # Register commands
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode_move = self.printer.load_object(config, "gcode_move")
        self.gcode.register_command('MANUAL_PROBE', self.cmd_MANUAL_PROBE,
                                    desc=self.cmd_MANUAL_PROBE_help)
        # Endstop value for cartesian printers with separate Z axis
        zconfig = config.getsection('stepper_z')
        self.z_min, self.z_max = (zconfig.getfloat('position_min'), zconfig.getfloat('position_max'))
        self.z_position_endstop = zconfig.getfloat('position_endstop', None,
                                                   note_valid=False)
        # Endstop values for linear delta printers with vertical A,B,C towers
        a_tower_config = config.getsection('stepper_a')
        self.a_position_endstop = a_tower_config.getfloat('position_endstop',
                                                          None,
                                                          note_valid=False)
        b_tower_config = config.getsection('stepper_b')
        self.b_position_endstop = b_tower_config.getfloat('position_endstop',
                                                          None,
                                                          note_valid=False)
        c_tower_config = config.getsection('stepper_c')
        self.c_position_endstop = c_tower_config.getfloat('position_endstop',
                                                          None,
                                                          note_valid=False)
        # Conditionally register appropriate commands depending on printer
        # Cartestian printers with separate Z Axis
        if self.z_position_endstop is not None:
            self.gcode.register_command(
                'Z_ENDSTOP_CALIBRATE', self.cmd_Z_ENDSTOP_CALIBRATE,
                desc=self.cmd_Z_ENDSTOP_CALIBRATE_help)
            self.gcode.register_command(
                'Z_OFFSET_APPLY_ENDSTOP',
                self.cmd_Z_OFFSET_APPLY_ENDSTOP,
                desc=self.cmd_Z_OFFSET_APPLY_ENDSTOP_help)
        # Linear delta printers with A,B,C towers
        if 'delta' == config.getsection('printer').get('kinematics'):
            self.gcode.register_command(
                'Z_OFFSET_APPLY_ENDSTOP',
                self.cmd_Z_OFFSET_APPLY_DELTA_ENDSTOPS,
                desc=self.cmd_Z_OFFSET_APPLY_ENDSTOP_help)
        self.reset_status()
    def manual_probe_finalize(self, kin_pos):
        if kin_pos is not None:
            self.gcode.respond_info("Z position is %.3f" % (kin_pos[2],))

    def reset_status(self):
        self.status = {
            'is_active': False,
            'z_position': None,
            'z_position_lower': None,
            'z_position_upper': None,
            'command': None,
            'z_position_endstop': self.z_position_endstop
        }

    def update_status(self, dict):
      for field in dict:
        if field in self.status:
          self.status[field] = dict[field]

    def get_status(self, eventtime):
        return self.status

    cmd_MANUAL_PROBE_help = _("Start manual probe helper script")
    def cmd_MANUAL_PROBE(self, gcmd):
        ManualProbeHelper(self.printer, self.config, gcmd, self.manual_probe_finalize)

    def in_range_min_max(self, z_pos):
        check_pos = self.z_position_endstop - z_pos
        return check_pos > self.z_min and check_pos < self.z_max
    def z_endstop_finalize(self, kin_pos):
        if kin_pos is None:
            return
        self.z_position_endstop -= kin_pos[2]
        self.save_z_position_endstop()

    cmd_Z_ENDSTOP_CALIBRATE_help = _("Calibrate a Z endstop")
    def cmd_Z_ENDSTOP_CALIBRATE(self, gcmd):
        self.printer.lookup_object('homing').run_G28_if_unhomed()
        toolhead = self.printer.lookup_object('toolhead')
        curtime = self.printer.get_reactor().monotonic()
        toolhead_status = toolhead.get_status(curtime)
        pos = ([toolhead_status['axis_maximum'][i]/2 for i in range(0, 2)])
        pos.append(None)
        toolhead.manual_move([None, None, self.drop_z], self.manual_speed)
        toolhead.manual_move(pos, self.manual_speed)
        ManualProbeHelper(self.printer, self.config, gcmd, self.z_endstop_finalize)
        
    def cmd_Z_OFFSET_APPLY_ENDSTOP(self,gcmd):
        babystep_offset = -1*self.gcode_move.get_status()['homing_origin'].z
        if babystep_offset == 0:
            self.gcode.respond_info(_("Nothing to do: Z Offset is 0"))
        else:
            self.z_position_endstop += babystep_offset
            self.save_z_position_endstop()
            self.gcode_move.homing_position[2] = 0.

    def save_z_position_endstop(self):
      toolhead = self.printer.lookup_object('toolhead')
      toolhead.get_kinematics().get_rails()[2].set_position_endstop(self.z_position_endstop)
      is_active = self.printer.lookup_object('virtual_sdcard').is_active()
      if is_active:
          self.printer.lookup_object('messages').send_message("warning", _("You must home after printing for apply change"))
      else:
        self.gcode.run_script_from_command("G28 Z")
      configfile = self.printer.lookup_object('configfile')
      configfile.update_config({'stepper_z': {'position_endstop': f"{self.z_position_endstop:.3f}"}})
      self.update_status({'z_position_endstop': self.z_position_endstop})
      if not is_active:
        self.printer.lookup_object('messages').send_message("success",_("stepper_z: position_endstop: %.3f\n"
              "New position saved") % self.z_position_endstop)

    def cmd_Z_OFFSET_APPLY_DELTA_ENDSTOPS(self,gcmd):
        offset = self.gcode_move.get_status()['homing_origin'].z
        configfile = self.printer.lookup_object('configfile')
        if offset == 0:
            self.gcode.respond_info(_("Nothing to do: Z Offset is 0"))
        else:
            new_a_calibrate = self.a_position_endstop - offset
            new_b_calibrate = self.b_position_endstop - offset
            new_c_calibrate = self.c_position_endstop - offset
            self.gcode.respond_info(
                _("stepper_a: position_endstop: %.3f\n"
                "stepper_b: position_endstop: %.3f\n"
                "stepper_c: position_endstop: %.3f\n"
                "The SAVE_CONFIG command will update the printer config file\n"
                "with the above and restart the printer.") % (new_a_calibrate,
                                                             new_b_calibrate,
                                                             new_c_calibrate))
            configfile.set('stepper_a', 'position_endstop',
                "%.3f" % (new_a_calibrate,))
            configfile.set('stepper_b', 'position_endstop',
                "%.3f" % (new_b_calibrate,))
            configfile.set('stepper_c', 'position_endstop',
                "%.3f" % (new_c_calibrate,))
    cmd_Z_OFFSET_APPLY_ENDSTOP_help = _("Adjust the z endstop_position")

# Verify that a manual probe isn't already in progress
def verify_no_manual_probe(printer):
    gcode = printer.lookup_object('gcode')
    try:
        gcode.register_command('ACCEPT', 'dummy')
    except printer.config_error as e:
        raise gcode.error(
            _("Already in a manual Z probe. Use ABORT to abort it."))
    gcode.register_command('ACCEPT', None)

#Z_BOB_MINIMUM = 0.500
BISECT_MAX = 0.200

# Helper script to determine a Z height
class ManualProbeHelper:
    def __init__(self, printer, config, gcmd, finalize_callback):
        self.printer = printer
        self.finalize_callback = finalize_callback
        stepper_z = config.getsection('stepper_z')
        self.max_z = stepper_z.getfloat('position_max')
        self.min_z = stepper_z.getfloat('position_min')
        self.gcode = self.printer.lookup_object('gcode')
        self.toolhead = self.printer.lookup_object('toolhead')
        self.manual_probe: ManualProbe = self.printer.lookup_object('manual_probe')
        self.speed = gcmd.get_float("SPEED", 5.)
        self.command = gcmd.get_command()
        self.past_positions = []
        self.last_toolhead_pos = self.last_kinematics_pos = None
        # Register commands
        verify_no_manual_probe(printer)
        self.gcode.register_command('ACCEPT', self.cmd_ACCEPT,
                                    desc=self.cmd_ACCEPT_help)
        self.gcode.register_command('NEXT', self.cmd_ACCEPT)
        self.gcode.register_command('ABORT', self.cmd_ABORT,
                                    desc=self.cmd_ABORT_help)
        self.gcode.register_command('TESTZ', self.cmd_TESTZ,
                                    desc=self.cmd_TESTZ_help)
        self.gcode.respond_info(
            _("Starting manual Z probe. Use TESTZ to adjust position.\n"
            "Finish with ACCEPT or ABORT command."))
        self.start_position = self.toolhead.get_position()
        self.report_z_status()
    def get_kinematics_pos(self):
        toolhead_pos = self.toolhead.get_position()
        if toolhead_pos == self.last_toolhead_pos:
            return self.last_kinematics_pos
        self.toolhead.flush_step_generation()
        kin = self.toolhead.get_kinematics()
        kin_spos = {s.get_name(): s.get_commanded_position()
                    for s in kin.get_steppers()}
        kin_pos = kin.calc_position(kin_spos)
        self.last_toolhead_pos = toolhead_pos
        self.last_kinematics_pos = kin_pos
        return kin_pos
    def move_z(self, z_pos):
        messages = self.printer.lookup_object('messages')
        try:
            if z_pos > self.max_z:
                messages.send_message('warning', _("WARNING: Reached stepper maximum position"))
                self.toolhead.manual_move([None, None, self.max_z], self.speed)
            elif z_pos < self.min_z:
                messages.send_message('warning', _("WARNING: Reached stepper minimum position")) 
                self.toolhead.manual_move([None, None, self.min_z], self.speed)
            else:
                self.toolhead.manual_move([None, None, z_pos], self.speed)
        except self.printer.command_error as e:
            self.finalize(False)
            raise
    def report_z_status(self, warn_no_change=False, prev_pos=None):
        # Get position
        kin_pos = self.get_kinematics_pos()
        z_pos = kin_pos[2]
        if warn_no_change and z_pos == prev_pos:
            self.printer.lookup_object('messages').send_message('warning',
                _("WARNING: No change in position (reached stepper resolution)"))
        # Find recent positions that were tested
        pp = self.past_positions
        next_pos = bisect.bisect_left(pp, z_pos)
        prev_pos = next_pos - 1
        if next_pos < len(pp) and pp[next_pos] == z_pos:
            next_pos += 1
        prev_pos_val = next_pos_val = None
        prev_str = next_str = "??????"
        if prev_pos >= 0:
            prev_pos_val = pp[prev_pos]
            prev_str = "%.3f" % (prev_pos_val,)
        if next_pos < len(pp):
            next_pos_val = pp[next_pos]
            next_str = "%.3f" % (next_pos_val,)
        update_dict = {
            'is_active': True,
            'z_position': z_pos,
            'z_position_lower': prev_pos_val,
            'z_position_upper': next_pos_val,
            'command': self.command
        }
        self.manual_probe.update_status(update_dict)
        # Find recent positions
        self.gcode.respond_info(_("Z position: %s --> %.3f <-- %s")
                                % (prev_str, z_pos, next_str))
    cmd_ACCEPT_help = _("Accept the current Z position")
    def cmd_ACCEPT(self, gcmd):
        if self.manual_probe.in_range_min_max(self.get_kinematics_pos()[2]):
            self.finalize(True)
        else:
            self.printer.lookup_object('messages').send_message('error', _("Z position must be in range [ %.3f ; %.3f ]") % (self.manual_probe.z_min, self.manual_probe.z_max))
    cmd_ABORT_help = _("Abort manual Z probing tool")
    def cmd_ABORT(self, gcmd):
        self.finalize(False)
    cmd_TESTZ_help = _("Move to new Z height")
    def cmd_TESTZ(self, gcmd):
        # Store current position for later reference
        kin_pos = self.get_kinematics_pos()
        z_pos = kin_pos[2]
        pp = self.past_positions
        insert_pos = bisect.bisect_left(pp, z_pos)
        if insert_pos >= len(pp) or pp[insert_pos] != z_pos:
            pp.insert(insert_pos, z_pos)
        # Determine next position to move to
        req = gcmd.get("Z")
        if req in ('+', '++'):
            check_z = 9999999999999.9
            if insert_pos < len(self.past_positions) - 1:
                check_z = self.past_positions[insert_pos + 1]
            if req == '+':
                check_z = (check_z + z_pos) / 2.
            next_z_pos = min(check_z, z_pos + BISECT_MAX)
        elif req in ('-', '--'):
            check_z = -9999999999999.9
            if insert_pos > 0:
                check_z = self.past_positions[insert_pos - 1]
            if req == '-':
                check_z = (check_z + z_pos) / 2.
            next_z_pos = max(check_z, z_pos - BISECT_MAX)
        else:
            next_z_pos = z_pos + gcmd.get_float("Z")
        # Move to given position and report it
        self.move_z(next_z_pos)
        self.report_z_status(next_z_pos != z_pos, z_pos)
    def finalize(self, success):
        self.manual_probe.reset_status()
        self.gcode.register_command('ACCEPT', None)
        self.gcode.register_command('NEXT', None)
        self.gcode.register_command('ABORT', None)
        self.gcode.register_command('TESTZ', None)
        kin_pos = None
        if success:
            kin_pos = self.get_kinematics_pos()
        self.finalize_callback(kin_pos)

def load_config(config):
    return ManualProbe(config)
