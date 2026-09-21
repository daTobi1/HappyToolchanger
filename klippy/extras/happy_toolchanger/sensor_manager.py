import logging

# Zahlen, nicht Strings: Mainsail (HtcSensorStatus.vue) prueft `state === 1`.
SENSOR_DETECTED = 1
SENSOR_EMPTY = 0
SENSOR_DISABLED = -1


class SensorManager:
    def __init__(self, printer, num_tools, debounce_time, runout_callback,
                 insert_callback=None):
        self.printer = printer
        self.reactor = printer.get_reactor()
        self.num_tools = num_tools
        self.debounce_time = debounce_time
        self.runout_callback = runout_callback
        self.insert_callback = insert_callback
        self.sensors = {}
        self.sensor_states = [SENSOR_DISABLED] * num_tools
        self._pending_runouts = {}

    def register_sensor(self, gate, sensor):
        if gate < 0 or gate >= self.num_tools:
            logging.warning(
                "HTC: sensor_pin_%d ignored (num_tools=%d)", gate, self.num_tools)
            return
        self.sensors[gate] = sensor
        # Den Zustand vom Sensor uebernehmen, nicht "detected" annehmen:
        # Klippers buttons melden beim Start nur Pins, die logisch 1 sind.
        # Ein beim Booten leerer Sensor meldet sich also nie.
        if sensor.filament_present:
            self.sensor_states[gate] = SENSOR_DETECTED
        else:
            self.sensor_states[gate] = SENSOR_EMPTY

    def is_present(self, gate):
        return self.sensor_states[gate] == SENSOR_DETECTED

    def note_filament_present(self, gate, is_present, eventtime):
        if gate not in self.sensors:
            return
        if is_present:
            self._cancel_pending(gate)
            was_empty = self.sensor_states[gate] != SENSOR_DETECTED
            self.sensor_states[gate] = SENSOR_DETECTED
            if was_empty and self.insert_callback is not None:
                self.insert_callback(gate)
        else:
            if self.sensor_states[gate] == SENSOR_EMPTY:
                return
            if gate not in self._pending_runouts:
                timer = self.reactor.register_timer(
                    lambda et, g=gate: self._confirm_runout(g, et),
                    eventtime + self.debounce_time)
                self._pending_runouts[gate] = timer

    def _cancel_pending(self, gate):
        timer = self._pending_runouts.pop(gate, None)
        if timer is not None:
            self.reactor.unregister_timer(timer)

    def _confirm_runout(self, gate, eventtime):
        self._cancel_pending(gate)
        self.sensor_states[gate] = SENSOR_EMPTY
        self.runout_callback(gate)
        return self.reactor.NEVER

    def get_status(self, eventtime=None):
        return {
            'sensor_states': list(self.sensor_states),
            'num_sensors': len(self.sensors),
        }
