import logging

SENSOR_DETECTED = "detected"
SENSOR_EMPTY = "empty"
SENSOR_DISABLED = "disabled"


class SensorManager:
    def __init__(self, printer, num_tools, debounce_time, runout_callback):
        self.printer = printer
        self.reactor = printer.get_reactor()
        self.num_tools = num_tools
        self.debounce_time = debounce_time
        self.runout_callback = runout_callback
        self.sensors = {}
        self.sensor_states = [SENSOR_DISABLED] * num_tools
        self._pending_runouts = {}

    def register_sensor(self, gate, sensor):
        self.sensors[gate] = sensor
        self.sensor_states[gate] = SENSOR_DETECTED

    def note_filament_present(self, gate, is_present, eventtime):
        if gate not in self.sensors:
            return
        if is_present:
            self.sensor_states[gate] = SENSOR_DETECTED
            if gate in self._pending_runouts:
                self.reactor.unregister_timer(self._pending_runouts[gate])
                del self._pending_runouts[gate]
        else:
            if gate not in self._pending_runouts:
                timer = self.reactor.register_timer(
                    lambda et, g=gate: self._confirm_runout(g, et),
                    eventtime + self.debounce_time)
                self._pending_runouts[gate] = timer

    def _confirm_runout(self, gate, eventtime):
        if gate in self._pending_runouts:
            del self._pending_runouts[gate]
        self.sensor_states[gate] = SENSOR_EMPTY
        self.runout_callback(gate)
        return self.reactor.NEVER

    def get_status(self, eventtime=None):
        return {
            'sensor_states': list(self.sensor_states),
            'num_sensors': len(self.sensors),
        }
