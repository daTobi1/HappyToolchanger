# EDDY-ng ProbeEddySampler
#
# Copyright (C) 2025  Vladimir Vukicevic <vladimir@pobox.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

import bisect
import traceback

import numpy as np

from typing import (
    List,
    Optional,
    TYPE_CHECKING,
    final,
)

from ._compat import Printer
from .alpha_beta_filter import AlphaBetaFilter
from .streaming import DataStreamer, StreamSample

if TYPE_CHECKING:
    from .probe import ProbeEddy
    from .temperature_compensation import TemperatureCompensationModel


@final
class ProbeEddySampler:
    def __init__(
        self,
        eddy: ProbeEddy,
        calculate_heights: bool = True,
    ):
        self.eddy = eddy
        self._sensor = eddy._sensor
        self._printer: Printer = self.eddy._printer
        self._reactor = self._printer.get_reactor()
        self._mcu = self._sensor.get_mcu()
        self._stopped = False
        self._started = False
        self._errors = 0
        self._fmap = eddy.map_for_drive_current() if calculate_heights else None

        # Alpha-beta filter (uses eddy's configured instance)
        self._ab_filter: Optional[AlphaBetaFilter] = eddy._ab_filter
        # Data streamer reference
        self._streamer: DataStreamer = eddy._streamer
        # Temperature compensation reference
        self._temp_comp = eddy._temp_comp

        self.times: List[float] = []
        self.raw_freqs: List[float] = []
        self.freqs: List[float] = []
        self.heights: Optional[List[float]] = [] if self._fmap is not None else None

        self.memos: dict = dict()

    def start(self):
        if self._stopped:
            raise self._printer.command_error("ProbeEddySampler.start() called after finish()")
        if not self._started:
            self._sensor.add_bulk_sensor_data_client(self._add_hw_measurement)
            self._started = True

    def finish(self):
        if self._stopped:
            return
        if not self._started:
            raise self._printer.command_error("ProbeEddySampler.finish() called without start()")
        if self.eddy._sampler is not self:
            raise self._printer.command_error("ProbeEddySampler.finish(): eddy._sampler is not us!")
        self._update_samples()
        self.eddy._sampler_finished(self)
        self._stopped = True

    def _update_samples(self):
        if len(self.freqs) == len(self.raw_freqs):
            return

        conv_ratio = self._sensor.freqval_conversion_value()

        start_idx = len(self.freqs)
        freqs_np = np.asarray(self.raw_freqs[start_idx:]) * conv_ratio
        self.freqs.extend(freqs_np.tolist())

        if self._fmap is not None:
            heights_np = self._fmap.freqs_to_heights_np(
                freqs_np,
                temp_comp=self._temp_comp,
                current_temp=self._get_current_temp(),
                ref_temp=self._get_ref_temp(),
            )

            # Apply alpha-beta filter if configured
            if self._ab_filter is not None and self._ab_filter.alpha > 0:
                filtered = []
                for i, h in enumerate(heights_np):
                    t = self.times[start_idx + i] if start_idx + i < len(self.times) else 0.0
                    filtered.append(self._ab_filter.update(float(h), t))
                self.heights.extend(filtered)
            else:
                self.heights.extend(heights_np.tolist())

            # Feed active streaming session
            if self._streamer.is_active:
                for i in range(len(freqs_np)):
                    idx = start_idx + i
                    t = self.times[idx] if idx < len(self.times) else 0.0
                    h = self.heights[idx] if idx < len(self.heights) else 0.0
                    self._streamer.add_sample(StreamSample(
                        time=t,
                        frequency=float(freqs_np[i]),
                        temperature=self._get_current_temp(),
                    ))

    def _get_current_temp(self) -> float:
        """Get current coil/sensor temperature if available."""
        if self._temp_comp is None:
            return 0.0
        try:
            return self.eddy._get_coil_temperature()
        except Exception:
            return 0.0

    def _get_ref_temp(self) -> float:
        """Get reference temperature from temp comp model."""
        if self._temp_comp is None:
            return 0.0
        return self._temp_comp.coeff.ref_temperature

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.finish()

    def active(self):
        return self._started and not self._stopped

    # bulk sample callback for when new data arrives
    # from the probe
    def _add_hw_measurement(self, msg):
        if self._stopped:
            return False

        self._errors += msg["errors"]
        data = msg["data"]

        # data is (t, fv)
        if data:
            times, raw_freqs = zip(*data)
        else:
            times, raw_freqs = [], []

        self.times.extend(times)
        self.raw_freqs.extend(raw_freqs)

        return True

    def get_last_freq(self) -> Optional[float]:
        """Get the last sampled frequency, or None if no samples yet."""
        self._update_samples()
        if len(self.freqs) == 0:
            return None
        return self.freqs[-1]

    # get the last sampled height
    def get_last_height(self) -> float:
        if self.heights is None:
            raise self._printer.command_error("ProbeEddySampler: no height mapping")
        self._update_samples()
        if len(self.heights) == 0:
            raise self._printer.command_error("ProbeEddySampler: no samples")
        return self.heights[-1]

    # wait for a sample for the current time and get a new height
    def get_height_now(self) -> Optional[float]:
        now = self.eddy._print_time_now()
        if not self.wait_for_sample_at_time(now, max_wait_time=1.000, raise_error=False):
            return None
        return self.get_last_height()

    # Wait until a sample for the given time arrives
    def wait_for_sample_at_time(self, sample_print_time, max_wait_time=0.250, raise_error=True) -> bool:
        def report_no_samples():
            if raise_error:
                raise self._printer.command_error(f"No samples received for time {sample_print_time:.3f} (waited for {max_wait_time:.3f})")
            return False

        if self._stopped:
            # if we're not getting any more samples, we can check directly
            if len(self.times) == 0:
                return report_no_samples()
            return self.times[-1] >= sample_print_time

        # quick check
        if self.times and self.times[-1] >= sample_print_time:
            return True

        wait_start_time = self.eddy._print_time_now()

        # if sample_print_time is in the future, make sure to wait max_wait_time
        # past the expected time
        if sample_print_time > wait_start_time:
            max_wait_time = max_wait_time + (sample_print_time - wait_start_time)

        # this is just a sanity check, there shouldn't be any reason to ever wait this long
        if max_wait_time > 30.0:
            traceback.print_stack()
            msg = f"ProbeEddyFrequencySampler: max_wait_time {max_wait_time:.3f} is too far into the future"
            raise self._printer.command_error(msg)

        self.eddy._log_debug(
            f"EDDYng waiting for sample at {sample_print_time:.3f} (now: {wait_start_time:.3f}, max_wait_time: {max_wait_time:.3f})"
        )
        now = self.eddy._print_time_now()
        while len(self.times) == 0 or self.times[-1] < sample_print_time:
            now = self.eddy._print_time_now()
            if now - wait_start_time > max_wait_time:
                return report_no_samples()
            self._reactor.pause(self._reactor.monotonic() + 0.010)

        if now - wait_start_time > 1.0:
            self.eddy._log_info(f"note: waited {now - wait_start_time:.3f}s for sample")

        return True

    # Wait for some samples to be collected, even if errors
    # TODO: there's a minimum wait time -- we need to fill up the buffer before data is sent, and that
    # depends on the data rate
    def wait_for_samples(
        self,
        max_wait_time=0.300,
        count_errors=False,
        min_samples=1,
        new_only=False,
        raise_error=True,
    ):
        # Make sure enough samples have been collected
        wait_start_time = self.eddy._print_time_now()

        start_error_count = self._errors
        start_count = 0
        if new_only:
            start_count = len(self.raw_freqs) + (self._errors if count_errors else 0)

        while (len(self.raw_freqs) + (self._errors if count_errors else 0)) - start_count < min_samples:
            now = self.eddy._print_time_now()
            if now - wait_start_time > max_wait_time:
                if raise_error:
                    raise self._printer.command_error(
                        f"probe_eddy_ng sensor outage: no samples for {max_wait_time:.2f}s (got {self._errors - start_error_count} errors)"
                    )
                return False
            self._reactor.pause(self._reactor.monotonic() + 0.010)

        return True

    def find_heights_at_times(self, intervals):
        self._update_samples()
        times = self.times
        heights = np.asarray(self.heights)
        num_samples = len(times)

        interval_heights = []
        i = 0
        for iv_start, iv_end in intervals:
            while i < num_samples and times[i] < iv_start:
                i += 1
            istart = i

            while i < num_samples and times[i] < iv_end:
                i += 1
            iend = i

            if istart == iend:
                # no samples in this range
                raise self._printer.command_error(f"No samples in time range {iv_start}-{iv_end}")

            median = np.median(heights[istart:iend])
            interval_heights.append(float(median))

        return interval_heights

    def find_height_at_time(self, start_time, end_time):
        if end_time < start_time:
            raise self._printer.command_error("find_height_at_time: end_time is before start_time")

        self._update_samples()

        if len(self.times) == 0:
            raise self._printer.command_error("No samples at all, so none in time range")

        if not self.heights:
            raise self._printer.command_error("Update samples didn't compute heights")

        self.eddy._log_debug(
                f"find_height_at_time: looking between {start_time:.3f}s-{end_time:.3f}s, inside {len(self.times)} samples, time range {self.times[0]:.3f}s to {self.times[-1]:.3f}s"
        )

        # find the first sample that is >= start_time
        start_idx = bisect.bisect_left(self.times, start_time)
        if start_idx >= len(self.times):
            raise self._printer.command_error("Nothing after start_time?")

        # find the last sample that is < end_time
        end_idx = start_idx
        while end_idx < len(self.times) and self.times[end_idx] < end_time:
            end_idx += 1

        # average the heights of the samples in the range
        heights = self.heights[start_idx:end_idx]
        if len(heights) == 0:
            raise self._printer.command_error(f"no samples between time {start_time:.1f} and {end_time:.1f}!")
        hmin, hmax = np.min(heights), np.max(heights)
        mean = np.mean(heights)
        median = np.median(heights)
        self.eddy._log_debug(
            f"find_height_at_time: {len(heights)} samples, median: {median:.3f}, mean: {mean:.3f} (range {hmin:.3f}-{hmax:.3f})"
        )

        return float(median)

    @property
    def raw_count(self):
        return len(self.times)

    @property
    def height_count(self):
        return len(self.heights) if self.heights else 0

    @property
    def error_count(self):
        return self._errors

    # this is just a handy way to communicate values between different parts of the system,
    # specifically to record things like trigger times for plotting
    def memo(self, name, value):
        self.memos[name] = value
