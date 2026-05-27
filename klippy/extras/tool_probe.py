# Per-tool Z-Probe support
#
# Copyright (C) 2023 Viesturs Zarins <viesturz@gmail.com>
# Z-probe routing extensions (C) 2026
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
from . import probe

_UNSET = object()

class ToolProbe:
    def __init__(self, config):
        self.tool = config.getint('tool')
        self.printer = config.get_printer()
        self.name = config.get_name()
        self.probe_offsets = probe.ProbeOffsetsHelper(config)
        self.param_helper = probe.ProbeParameterHelper(config)
        self.mcu_probe = probe.ProbeEndstopWrapper(
            config, self.probe_offsets, self.param_helper)
        self.probe_session = probe.SampleAveragingHelper(
            config, self.param_helper, self.mcu_probe.start_probe_session)

        # Crash detection stuff
        pin = config.get('pin')
        buttons = self.printer.load_object(config, 'buttons')
        ppins = self.printer.lookup_object('pins')
        ppins.allow_multi_use_pin(pin.replace('^', '').replace('!', ''))
        buttons.register_buttons([pin], self._button_handler)

        #Register with the endstop
        self.endstop = self.printer.load_object(config, "tool_probe_endstop")
        self.endstop.add_probe(config, self)

        # External Z probe (e.g. Eddy, Cartographer) that overrides this
        # tool_probe for probing/homing.  Tap remains for crash/tool detection.
        # z_probe: default for all operations
        # z_probe_home/qgl/mesh: per-operation overrides (fall back to z_probe)
        # Use "none" to explicitly disable for an operation.
        self.z_probe_name = config.get('z_probe', None)
        self.z_probe = None
        self._z_probe_home_name = config.get('z_probe_home', None)
        self._z_probe_qgl_name = config.get('z_probe_qgl', None)
        self._z_probe_mesh_name = config.get('z_probe_mesh', None)
        self._z_probe_home = _UNSET
        self._z_probe_qgl = _UNSET
        self._z_probe_mesh = _UNSET
        if any(n is not None for n in [
                self.z_probe_name, self._z_probe_home_name,
                self._z_probe_qgl_name, self._z_probe_mesh_name]):
            self.printer.register_event_handler(
                "klippy:connect", self._resolve_z_probes)

    def _resolve_z_probes(self):
        self.z_probe = self._resolve_one('z_probe', self.z_probe_name)
        if self._z_probe_home_name is not None:
            self._z_probe_home = self._resolve_one(
                'z_probe_home', self._z_probe_home_name)
        if self._z_probe_qgl_name is not None:
            self._z_probe_qgl = self._resolve_one(
                'z_probe_qgl', self._z_probe_qgl_name)
        if self._z_probe_mesh_name is not None:
            self._z_probe_mesh = self._resolve_one(
                'z_probe_mesh', self._z_probe_mesh_name)

    def _resolve_one(self, label, name):
        if not name or name.lower() == 'none':
            return None
        obj = self.printer.lookup_object(name, None)
        if obj is None:
            logging.error(
                "%s: %s '%s' not found", self.name, label, name)
            return None
        for method in ('get_probe_params', 'get_offsets',
                       'start_probe_session'):
            if not hasattr(obj, method):
                logging.error(
                    "%s: %s '%s' missing method %s()",
                    self.name, label, name, method)
                return None
        logging.info("%s: %s set to '%s'", self.name, label, name)
        return obj

    def get_z_probe_for(self, operation='default'):
        """Get the z_probe for a specific operation.
        Falls back to z_probe if no per-operation override is set.
        Returns probe object or None (None = use tool probe/Tap)."""
        if operation == 'home' and self._z_probe_home is not _UNSET:
            return self._z_probe_home
        if operation == 'qgl' and self._z_probe_qgl is not _UNSET:
            return self._z_probe_qgl
        if operation == 'mesh' and self._z_probe_mesh is not _UNSET:
            return self._z_probe_mesh
        return self.z_probe

    def _button_handler(self, eventtime, is_triggered):
        self.endstop.note_probe_triggered(self, eventtime, is_triggered)

    def get_probe_params(self, gcmd=None):
        return self.param_helper.get_probe_params(gcmd)
    def get_offsets(self, gcmd=None):
        return self.probe_offsets.get_offsets(gcmd)
    def start_probe_session(self, gcmd):
        return self.probe_session.start_probe_session(gcmd)


def load_config_prefix(config):
    return ToolProbe(config)
