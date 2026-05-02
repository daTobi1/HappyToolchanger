# ProbeEddyFrequencyMap - frequency-to-height calibration map
#
# Copyright (C) 2025  Vladimir Vukicevic <vladimir@pobox.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

import base64
import json
import logging
import math
import pickle
from typing import (
    TYPE_CHECKING,
    List,
    Optional,
    final,
)

import numpy as np
import numpy.polynomial as npp

from ._compat import (
    ConfigWrapper,
    configerror,
    plotly,
)
from .temperature_compensation import TemperatureCompensationModel

if TYPE_CHECKING:
    from .probe import ProbeEddy


def np_rmse(p, x, y):
    y_hat = p(x)
    return np.sqrt(np.mean((y - y_hat) ** 2))


@final
class ProbeEddyFrequencyMap:
    calibration_version = 5
    low_z_threshold = 5.0

    def __init__(self, eddy: ProbeEddy):
        self._eddy = eddy
        self._sensor = eddy._sensor

        self.drive_current = 0
        self.height_range = (math.inf, -math.inf)
        self.freq_range = (math.inf, -math.inf)
        self._ftoh: Optional[npp.Polynomial] = None
        self._ftoh_high: Optional[npp.Polynomial] = None
        self._htof: Optional[npp.Polynomial] = None

    def _str_to_exact_floatlist(self, str):
        return [float.fromhex(v) for v in str.split(",")]

    def _exact_floatlist_to_str(self, vals):
        return str.join(", ", [float.hex(v) for v in vals])

    def _coefs_to_str(self, coefs):
        return ", ".join([format(c, ".3f") for c in coefs])

    def freq_spread(self) -> float:
        return ((self.freq_range[1] / self.freq_range[0]) - 1.0) * 100.0

    @staticmethod
    def _poly_to_json(poly):
        """Serialize a numpy Polynomial to a JSON-safe dict."""
        if poly is None:
            return None
        return {
            "coef": [float.hex(float(c)) for c in poly.coef],
            "domain": [float.hex(float(d)) for d in poly.domain],
            "window": [float.hex(float(w)) for w in poly.window],
        }

    @staticmethod
    def _poly_from_json(d):
        """Deserialize a numpy Polynomial from a JSON dict."""
        if d is None:
            return None
        coef = [float.fromhex(c) for c in d["coef"]]
        domain = [float.fromhex(v) for v in d["domain"]]
        window = [float.fromhex(v) for v in d["window"]]
        return npp.Polynomial(coef, domain=domain, window=window)

    def _load_from_pickle(self, calibstr, drive_current):
        """Legacy loader for old pickle-based calibration data."""
        try:
            data = pickle.loads(base64.b64decode(calibstr))
        except Exception:
            return False
        v = data.get("v", None)
        if v is None or v < self.calibration_version:
            self._eddy._log_info(f"Calibration for dc {drive_current} is old ({v}), needs recalibration")
            return False
        self._ftoh = data.get("ftoh", None)
        self._ftoh_high = data.get("ftoh_high", None)
        self._htof = data.get("htof", None)
        self.height_range = data.get("h_range", (math.inf, -math.inf))
        self.freq_range = data.get("f_range", (math.inf, -math.inf))
        self.drive_current = drive_current
        return True

    def _load_from_json(self, calibstr, drive_current):
        """Load calibration from JSON format."""
        try:
            data = json.loads(calibstr)
        except (json.JSONDecodeError, ValueError):
            return False
        v = data.get("v", None)
        if v is None or v < self.calibration_version:
            self._eddy._log_info(f"Calibration for dc {drive_current} is old ({v}), needs recalibration")
            return False
        dc = data.get("dc", None)
        if dc != drive_current:
            raise configerror(f"ProbeEddyFrequencyMap: drive current mismatch: loaded {dc} != requested {drive_current}")
        self._ftoh = self._poly_from_json(data.get("ftoh"))
        self._ftoh_high = self._poly_from_json(data.get("ftoh_high"))
        self._htof = self._poly_from_json(data.get("htof"))
        h_range = data.get("h_range", [math.inf, -math.inf])
        f_range = data.get("f_range", [math.inf, -math.inf])
        self.height_range = (h_range[0], h_range[1])
        self.freq_range = (f_range[0], f_range[1])
        self.drive_current = drive_current
        return True

    def load_from_config(self, config: ConfigWrapper, drive_current: int):
        calibstr = config.get(f"calibration_{drive_current}", None)
        if calibstr is None:
            self.drive_current = 0
            self._ftoh = None
            self._htof = None
            self.height_range = (math.inf, -math.inf)
            self.freq_range = (math.inf, -math.inf)
            return

        # Try JSON first, fall back to legacy pickle
        calibstr_stripped = calibstr.strip()
        if calibstr_stripped.startswith("{"):
            loaded = self._load_from_json(calibstr_stripped, drive_current)
        else:
            loaded = self._load_from_pickle(calibstr_stripped, drive_current)
            if loaded:
                self._eddy._log_info(
                    f"Loaded legacy pickle calibration for dc {drive_current}. "
                    "Run SAVE_CONFIG to convert to new JSON format."
                )

        if not loaded:
            return False

        self._eddy._log_info(f"Loaded calibration for drive current {drive_current}")
        return True

    def save_calibration(self, model_name: Optional[str] = None):
        if self._ftoh is None or self._htof is None:
            return

        configfile = self._eddy._printer.lookup_object("configfile")
        data = {
            "v": self.calibration_version,
            "dc": self.drive_current,
            "h_range": [self.height_range[0], self.height_range[1]],
            "f_range": [self.freq_range[0], self.freq_range[1]],
            "ftoh": self._poly_to_json(self._ftoh),
            "ftoh_high": self._poly_to_json(self._ftoh_high),
            "htof": self._poly_to_json(self._htof),
        }
        calibstr = json.dumps(data, separators=(",", ":"))
        configfile.set(self._eddy._full_name, f"calibration_{self.drive_current}", calibstr)

        # Also save as named model if requested
        if model_name is not None:
            configfile.set(self._eddy._full_name, f"model_{model_name}", calibstr)
            # Update saved model list
            models = self._get_saved_model_names(configfile)
            if model_name not in models:
                models.append(model_name)
                configfile.set(
                    self._eddy._full_name,
                    "saved_models",
                    ",".join(models),
                )

    def _get_saved_model_names(self, configfile=None) -> List[str]:
        """Get list of saved named model names from autosave config."""
        if configfile is None:
            configfile = self._eddy._printer.lookup_object("configfile")
        asfc = configfile.autosave.fileconfig
        models_str = asfc.get(self._eddy._full_name, "saved_models", fallback="")
        if not models_str:
            return []
        return [m.strip() for m in models_str.split(",") if m.strip()]

    def get_model_names(self) -> List[str]:
        """Return list of all saved model names."""
        return self._get_saved_model_names()

    def load_named_model(self, model_name: str) -> bool:
        """Load a named calibration model."""
        configfile = self._eddy._printer.lookup_object("configfile")
        asfc = configfile.autosave.fileconfig
        calibstr = asfc.get(self._eddy._full_name, f"model_{model_name}", fallback=None)
        if calibstr is None:
            return False
        calibstr = calibstr.strip()
        if not calibstr.startswith("{"):
            return False
        try:
            data = json.loads(calibstr)
        except (json.JSONDecodeError, ValueError):
            return False
        dc = data.get("dc", self.drive_current)
        return self._load_from_json(calibstr, dc)

    def delete_named_model(self, model_name: str) -> bool:
        """Delete a named calibration model."""
        configfile = self._eddy._printer.lookup_object("configfile")
        models = self._get_saved_model_names(configfile)
        if model_name not in models:
            return False
        models.remove(model_name)
        configfile.set(
            self._eddy._full_name,
            "saved_models",
            ",".join(models) if models else "",
        )
        # Clear the model data by setting to empty
        configfile.set(self._eddy._full_name, f"model_{model_name}", "")
        return True

    def calibrate_from_values(
        self,
        drive_current: int,
        raw_times: List[float],
        raw_freqs_list: List[float],
        raw_heights_list: List[float],
        raw_vels_list: List[float],
        report_errors: bool,
        write_debug_files: bool,
    ):
        if len(raw_freqs_list) != len(raw_heights_list):
            raise ValueError("freqs and heights must be the same length")

        if len(raw_freqs_list) == 0:
            self._eddy._log_info("calibrate_from_values: empty list")
            return None, None

        # everything must be a np.array or things get confused below
        times = np.asarray(raw_times)
        freqs = np.asarray(raw_freqs_list)
        heights = np.asarray(raw_heights_list)
        vels = np.asarray(raw_vels_list) if raw_vels_list else None

        if write_debug_files:
            with open("/tmp/eddy-calibration.csv", "w") as data_file:
                data_file.write("time,frequency,avg_freq,z,avg_z,v\n")
                for i in range(len(freqs)):
                    s_t = times[i]
                    s_f = freqs[i]
                    s_z = heights[i]
                    s_v = vels[i] if vels is not None else 0.0
                    data_file.write(f"{s_t},{s_f},{s_z},,{s_v}\n")
                self._eddy._log_info(f"Wrote {len(freqs)} samples to /tmp/eddy-calibration.csv")

        if len(freqs) == 0 or len(heights) == 0:
            if report_errors:
                self._eddy._log_error(
                    f"Drive current {drive_current}: Calibration failed, couldn't compute averages ({len(raw_freqs_list)}, {len(raw_heights_list)}), probably due to no valid samples received."
                )
            return None, None

        max_height = float(heights.max())
        min_height = float(heights.min())
        min_freq = float(freqs.min())
        max_freq = float(freqs.max())
        freq_spread = ((max_freq / min_freq) - 1.0) * 100.0

        # Check if our calibration is good enough
        if report_errors:
            if max_height < 2.5:  # we really can't do anything with this
                self._eddy._log_error(
                    f"Drive current {drive_current} error: max height for valid samples is too low: {max_height:.3f} < 2.5. Possible causes: bad drive current, bad sensor mount height."
                )
                if not self._eddy.params.allow_unsafe:
                    return None, None

            if min_height > 0.65:  # this is a bit arbitrary; but if it's this far off we shouldn't trust it
                self._eddy._log_error(
                    f"Drive current {drive_current} error: min height for valid samples is too high: {min_height:.3f} > 0.65. Possible causes: bad drive current, bad sensor mount height."
                )
                if not self._eddy.params.allow_unsafe:
                    return None, None

            if min_height > 0.025:
                self._eddy._log_msg(
                    f"Drive current {drive_current} warning: min height is {min_height:.3f} (> 0.025) is too high for tap. This calibration will work fine for homing, but may not for tap."
                )

            # somewhat arbitrary spread
            if freq_spread < 0.30:
                extremely = "EXTREMELY " if freq_spread < 0.15 else ""
                self._eddy._log_warning(
                    f"Drive current {drive_current} warning: frequency spread is {extremely}low ({freq_spread:.2f}%, {min_freq:.1f}-{max_freq:.1f}), which will greatly impact accuracy. Your sensor may be too high."
                )

        low_samples = heights <= ProbeEddyFrequencyMap.low_z_threshold
        high_samples = heights >= ProbeEddyFrequencyMap.low_z_threshold - 0.5

        ftoh_low_fn = npp.Polynomial.fit(1.0 / freqs[low_samples], heights[low_samples], deg=9)
        htof_low_fn = npp.Polynomial.fit(heights[low_samples], 1.0 / freqs[low_samples], deg=9)

        if np.count_nonzero(high_samples) > 50:
            ftoh_high_fn = npp.Polynomial.fit(1.0 / freqs[high_samples], heights[high_samples], deg=9)
        else:
            self._eddy._log_debug(f"not computing ftoh_high, not enough high samples")
            ftoh_high_fn = None

        # Calculate rms, only for the low values (where error is most relevant)
        rmse_fth = np_rmse(
            ftoh_low_fn,
            1.0 / freqs[low_samples],
            heights[low_samples],
        )
        rmse_htf = np_rmse(
            htof_low_fn,
            heights[low_samples],
            1.0 / freqs[low_samples],
        )

        if report_errors:
            if rmse_fth > 0.050:
                self._eddy._log_error(
                    f"Drive current {drive_current} error: calibration error margin is too high ({rmse_fth:.3f}). Possible causes: bad drive current, bad sensor mount height."
                )
                if not self._eddy.params.allow_unsafe:
                    return None, None

        self._ftoh = ftoh_low_fn
        self._htof = htof_low_fn
        self._ftoh_high = ftoh_high_fn
        self.drive_current = drive_current
        self.height_range = [min_height, max_height]
        self.freq_range = [min_freq, max_freq]

        self._eddy._log_msg(
            f"Drive current {drive_current}: valid height: {min_height:.3f} to {max_height:.3f}, "
            f"freq spread {freq_spread:.2f}% ({min_freq:.1f} - {max_freq:.1f}), "
            f"Fit {rmse_fth:.4f} ({rmse_htf:.2f})"
        )

        if write_debug_files:
            self._write_calibration_plot(
                times,
                freqs,
                heights,
                rmse_fth,
                rmse_htf,
                vels=vels,
            )

        return rmse_fth, rmse_htf

    def _write_calibration_plot(
        self,
        times,
        freqs,
        heights,
        rmse_fth,
        rmse_htf,
        vels=None,
    ):
        if not plotly:
            return

        if self._ftoh is None or self._htof is None:
            logging.warning(f"write_calibration_plot: null calibration?")
            return

        import plotly.graph_objects as go

        low_samples = heights <= ProbeEddyFrequencyMap.low_z_threshold
        high_samples = heights >= ProbeEddyFrequencyMap.low_z_threshold - 0.5

        f_to_z_low_err = heights[low_samples] - self._ftoh(1.0 / freqs[low_samples])

        if self._ftoh_high is not None:
            f_to_z_high_err = heights[high_samples] - self._ftoh_high(1.0 / freqs[high_samples])
        else:
            f_to_z_high_err = None

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=times, y=heights, mode="lines", name="Z"))

        fig.add_trace(
            go.Scatter(
                x=times[low_samples],
                y=self._ftoh(1.0 / freqs[low_samples]),
                mode="lines",
                name=f"Z {rmse_fth:.4f}",
            )
        )

        if self._ftoh_high is not None:
            fig.add_trace(
                go.Scatter(
                    x=times[high_samples],
                    y=self._ftoh_high(1.0 / freqs[high_samples]),
                    mode="lines",
                    name=f"Z (high)",
                )
            )

        fig.add_trace(go.Scatter(x=times, y=freqs, mode="lines", name="F", yaxis="y2"))

        fig.add_trace(
            go.Scatter(
                x=times[low_samples],
                y=1.0 / self._htof(heights[low_samples]),
                mode="lines",
                name=f"F ({rmse_htf:.2f})",
                yaxis="y2",
            )
        )

        fig.add_trace(
            go.Scatter(
                x=times[low_samples],
                y=f_to_z_low_err,
                mode="lines",
                name="Err",
                yaxis="y3",
            )
        )
        if f_to_z_high_err is not None:
            fig.add_trace(
                go.Scatter(
                    x=times[high_samples],
                    y=f_to_z_high_err,
                    mode="lines",
                    name="Err (high)",
                    yaxis="y3",
                )
            )

        if vels is not None:
            fig.add_trace(go.Scatter(x=times, y=vels, mode="lines", name="V", yaxis="y4"))

        fig.update_layout(
            hovermode="x unified",
            title=f"Calibration for drive current {self.drive_current}",
            yaxis2=dict(title="Freq", overlaying="y", tickformat="d", side="right"),
            yaxis3=dict(overlaying="y", side="right", position=0.1),
            yaxis4=dict(overlaying="y", side="right", position=0.2),
        )
        fig.write_html("/tmp/eddy-calibration.html")

    def get_reference_frequency(self) -> float:
        """Return the frequency corresponding to height=0 (bed surface).

        Used as reference point for temperature compensation calibration.
        """
        if self._htof is None:
            raise self._eddy._printer.command_error(
                "Calling get_reference_frequency on uncalibrated map"
            )
        return self.height_to_freq(0.0)

    def freq_to_height(
        self,
        freq: float,
        temp_comp: Optional[TemperatureCompensationModel] = None,
        current_temp: float = 0.0,
        ref_temp: float = 0.0,
    ) -> float:
        if self._ftoh is None:
            raise self._eddy._printer.command_error("Calling freq_to_height on uncalibrated map")
        if temp_comp is not None and current_temp > 0.0 and ref_temp > 0.0:
            freq = temp_comp.compensate(freq, current_temp, ref_temp)
        invfreq = 1.0 / freq
        if self._ftoh_high is not None and invfreq < self._ftoh.domain[0]:
            return float(self._ftoh_high(invfreq))
        return float(self._ftoh(invfreq))

    def freqs_to_heights_np(
        self,
        freqs: np.array,
        temp_comp: Optional[TemperatureCompensationModel] = None,
        current_temp: float = 0.0,
        ref_temp: float = 0.0,
    ) -> np.array:
        if self._ftoh is None:
            raise self._eddy._printer.command_error("Calling freqs_to_heights on uncalibrated map")
        if temp_comp is not None and current_temp > 0.0 and ref_temp > 0.0:
            freqs = np.array([temp_comp.compensate(f, current_temp, ref_temp) for f in freqs])
        invfreqs = 1.0 / freqs
        if self._ftoh_high is not None:
            heights = np.zeros(len(invfreqs))
            low_freq_vals = invfreqs > self._ftoh.domain[1]
            heights[low_freq_vals] = np.vectorize(self._ftoh_high, otypes=[float])(invfreqs[low_freq_vals])
            heights[~low_freq_vals] = np.vectorize(self._ftoh, otypes=[float])(invfreqs[~low_freq_vals])
        else:
            heights = self._ftoh(invfreqs)
        return heights

    def height_to_freq(self, height: float) -> float:
        if self._htof is None:
            raise self._eddy._printer.command_error("Calling height_to_freq on uncalibrated map")
        return 1.0 / float(self._htof(height))

    def calibrated(self) -> bool:
        return self._ftoh is not None and self._htof is not None
