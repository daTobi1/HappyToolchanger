import logging


class HtcFilamentSwitch:
    def __init__(self, printer, gate, pin_name):
        self.printer = printer
        self.gate = gate
        self.filament_present = True

        buttons = printer.load_object(printer.lookup_object('configfile'), 'buttons')
        ppins = printer.lookup_object('pins')
        pin_params = ppins.parse_pin(pin_name, can_invert=True, can_pullup=True)
        buttons.register_buttons([pin_params], self._button_handler)

        printer.register_event_handler('klippy:ready', self._handle_ready)

    def _handle_ready(self):
        htc = self.printer.lookup_object('happy_toolchanger', None)
        if htc and hasattr(htc, 'sensor_manager'):
            htc.sensor_manager.register_sensor(self.gate, self)

    def _button_handler(self, eventtime, state):
        self.filament_present = bool(state)
        htc = self.printer.lookup_object('happy_toolchanger', None)
        if htc and hasattr(htc, 'sensor_manager'):
            htc.sensor_manager.note_filament_present(
                self.gate, self.filament_present, eventtime)


class HtcSensors:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.switches = []

        for i in range(16):
            pin = config.get('sensor_pin_%d' % i, None)
            if pin:
                switch = HtcFilamentSwitch(self.printer, i, pin)
                self.switches.append(switch)
                logging.info("HTC: Sensor registered for gate %d" % i)

    def get_status(self, eventtime=None):
        return {
            'num_sensors': len(self.switches),
        }


def load_config(config):
    return HtcSensors(config)
