# Temperature compensation for eddy current frequency drift
# Inspired by Cartographer3D's coil temperature compensation model
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Tuple

if TYPE_CHECKING:
    from ._compat import ConfigWrapper

logger = logging.getLogger(__name__)


@dataclass
class TempCompCoefficients:
    """Temperature compensation model coefficients.

    The model uses frequency-dependent quadratic interpolation:
      a_interp = a_a * (freq - ref_freq) + a_b
      b_interp = b_a * (freq - ref_freq) + b_b

    Then frequency is modeled as:
      freq = a_interp * temp^2 + b_interp * temp + c
    """
    a_a: float
    a_b: float
    b_a: float
    b_b: float
    ref_frequency: float  # baseline frequency at calibration
    ref_temperature: float  # temperature at calibration


def _param_linear(freq_offset: float, slope: float, intercept: float) -> float:
    return slope * freq_offset + intercept


class TemperatureCompensationModel:
    """Compensates frequency readings for temperature drift.

    Uses a quadratic model fitted across multiple heights and temperatures
    to adjust raw frequency to what it would be at the reference temperature.
    """

    def __init__(self, coefficients: TempCompCoefficients):
        self.coeff = coefficients

    def compensate(self, frequency: float, temp_source: float,
                   temp_target: float) -> float:
        """Adjust frequency from temp_source to temp_target."""
        if abs(temp_source - temp_target) < 0.1:
            return frequency

        c = self.coeff
        freq_offset = frequency - c.ref_frequency

        # Interpolate quadratic parameters for this frequency
        param_a = _param_linear(freq_offset, c.a_a, c.a_b)
        param_b = _param_linear(freq_offset, c.b_a, c.b_b)

        # Try quadratic solution first
        result = self._compensate_quadratic(
            frequency, freq_offset, param_a, param_b,
            temp_source, temp_target
        )
        if result is not None:
            return result

        # Fallback to linear compensation
        return self._compensate_linear(
            frequency, param_a, param_b, temp_source, temp_target
        )

    def _compensate_quadratic(self, frequency: float, freq_offset: float,
                              param_a: float, param_b: float,
                              temp_source: float, temp_target: float
                              ) -> Optional[float]:
        c = self.coeff

        # Build quadratic equation for freq_offset solution
        quad_a = (4 * (temp_source * c.a_a) ** 2
                  + 4 * temp_source * c.a_a * c.b_a
                  + c.b_a ** 2 + 4 * c.a_a)
        quad_b = (8 * temp_source ** 2 * c.a_a * c.a_b
                  + 4 * temp_source * (c.a_a * c.b_b + c.a_b * c.b_a)
                  + 2 * c.b_a * c.b_b + 4 * c.a_b
                  - 4 * freq_offset * c.a_a)
        quad_c = (4 * (temp_source * c.a_b) ** 2
                  + 4 * temp_source * c.a_b * c.b_b
                  + c.b_b ** 2 - 4 * freq_offset * c.a_b)

        discriminant = quad_b ** 2 - 4 * quad_a * quad_c
        if discriminant < 0:
            return None

        if abs(quad_a) < 1e-15:
            return None

        ax = (math.sqrt(discriminant) - quad_b) / (2 * quad_a)

        # Get parameters at solution point
        a_at_ax = _param_linear(ax, c.a_a, c.a_b)
        b_at_ax = _param_linear(ax, c.b_a, c.b_b)

        if abs(a_at_ax) > 1e-12:
            temp_offset = b_at_ax / (2 * a_at_ax)
            return a_at_ax * (temp_target + temp_offset) ** 2 + ax + c.ref_frequency
        else:
            return b_at_ax * temp_target + ax + c.ref_frequency

    def _compensate_linear(self, frequency: float,
                           param_a: float, param_b: float,
                           temp_source: float, temp_target: float) -> float:
        # Extract constant c from: freq = a*temp_src^2 + b*temp_src + c
        param_c = frequency - param_a * temp_source ** 2 - param_b * temp_source
        # Apply at target temperature
        return param_a * temp_target ** 2 + param_b * temp_target + param_c


def fit_temperature_model(
    data_per_height: dict,
    ref_frequency: float,
    ref_temperature: float,
) -> Optional[TempCompCoefficients]:
    """Fit temperature compensation model from calibration data.

    Args:
        data_per_height: Dict mapping height (mm) to list of
            (frequency, temperature) tuples.
        ref_frequency: Baseline frequency from initial calibration.
        ref_temperature: Temperature at initial calibration.

    Returns:
        TempCompCoefficients if fitting succeeds, None otherwise.
    """
    try:
        import numpy as np
        from scipy.optimize import curve_fit
    except ImportError:
        logger.error("Temperature calibration requires scipy. "
                     "Install with: pip install scipy")
        return None

    if len(data_per_height) < 2:
        logger.error("Need at least 2 heights for temperature calibration, "
                     "got %d", len(data_per_height))
        return None

    coefficients_a = []
    coefficients_b = []
    frequencies_at_vertex = []

    for height, samples in sorted(data_per_height.items()):
        if len(samples) < 50:
            logger.warning("Skipping height %.1f mm: only %d samples "
                           "(need >= 50)", height, len(samples))
            continue

        freqs = np.array([s[0] for s in samples])
        temps = np.array([s[1] for s in samples])

        # Downsample if too many points (>1000 → 800)
        if len(samples) > 1000:
            freqs, temps = _downsample_by_temp_bins(freqs, temps, 800)

        # Fit: freq = a*temp^2 + b*temp + c
        try:
            def quad_func(t, a, b, c):
                return a * t ** 2 + b * t + c

            popt, _ = curve_fit(
                quad_func, temps, freqs,
                bounds=([0, -np.inf, -np.inf], [np.inf, np.inf, np.inf]),
                maxfev=100000,
            )
            a, b, c = popt
        except Exception as e:
            logger.warning("Quadratic fit failed for height %.1f: %s",
                           height, e)
            continue

        # Check vertex position
        if abs(a) < 1e-15:
            vertex_temp = 60.0  # default
        else:
            vertex_temp = -b / (2 * a)

        # Constrain vertex to reasonable range
        if vertex_temp > 120:
            # Re-fit with vertex at 120
            try:
                def line120(t, a_c, c_c):
                    return a_c * t ** 2 - 240 * a_c * t + c_c
                popt2, _ = curve_fit(line120, temps, freqs, maxfev=100000)
                a, b = popt2[0], -240 * popt2[0]
                freq_at_vertex = quad_func(120, a, b, popt2[1])
            except Exception:
                freq_at_vertex = float(np.mean(freqs))
        elif vertex_temp < 0:
            # Re-fit with vertex at 0
            try:
                def line0(t, a_c, c_c):
                    return a_c * t ** 2 + c_c
                popt2, _ = curve_fit(line0, temps, freqs, maxfev=100000)
                a, b = popt2[0], 0.0
                freq_at_vertex = quad_func(0, a, b, popt2[1])
            except Exception:
                freq_at_vertex = float(np.mean(freqs))
        else:
            freq_at_vertex = quad_func(vertex_temp, a, b, c)

        coefficients_a.append(a)
        coefficients_b.append(b)
        frequencies_at_vertex.append(freq_at_vertex)

    if len(coefficients_a) < 2:
        logger.error("Not enough valid heights for temperature model")
        return None

    # Fit linear relationships: coeff = slope * (freq - ref_freq) + intercept
    freq_array = np.array(frequencies_at_vertex) - ref_frequency

    def linear(x, slope, intercept):
        return slope * x + intercept

    try:
        params_a, _ = curve_fit(linear, freq_array, coefficients_a)
        params_b, _ = curve_fit(linear, freq_array, coefficients_b)
    except Exception as e:
        logger.error("Linear fit failed: %s", e)
        return None

    return TempCompCoefficients(
        a_a=float(params_a[0]),
        a_b=float(params_a[1]),
        b_a=float(params_b[0]),
        b_b=float(params_b[1]),
        ref_frequency=ref_frequency,
        ref_temperature=ref_temperature,
    )


def _downsample_by_temp_bins(freqs, temps, target_count):
    """Downsample by evenly distributing across temperature bins."""
    import numpy as np
    temp_min, temp_max = temps.min(), temps.max()
    n_bins = target_count
    bin_edges = np.linspace(temp_min, temp_max, n_bins + 1)

    indices = []
    for i in range(n_bins):
        mask = (temps >= bin_edges[i]) & (temps < bin_edges[i + 1])
        bin_indices = np.where(mask)[0]
        if len(bin_indices) > 0:
            indices.append(bin_indices[len(bin_indices) // 2])

    indices = np.array(indices)
    return freqs[indices], temps[indices]


def load_temp_comp_from_config(config) -> Optional[TempCompCoefficients]:
    """Load temperature compensation from printer config."""
    cal_str = config.get("temperature_compensation", None)
    if cal_str is None:
        return None
    try:
        vals = [float(v.strip()) for v in cal_str.split(",")]
        if len(vals) != 6:
            logger.error("temperature_compensation needs 6 values, got %d",
                         len(vals))
            return None
        return TempCompCoefficients(
            a_a=vals[0], a_b=vals[1],
            b_a=vals[2], b_b=vals[3],
            ref_frequency=vals[4], ref_temperature=vals[5],
        )
    except Exception as e:
        logger.error("Failed to parse temperature_compensation: %s", e)
        return None


def save_temp_comp_to_config(configfile, section: str,
                             coeff: TempCompCoefficients):
    """Save temperature compensation to printer config."""
    val = ",".join([
        f"{coeff.a_a:.10e}", f"{coeff.a_b:.10e}",
        f"{coeff.b_a:.10e}", f"{coeff.b_b:.10e}",
        f"{coeff.ref_frequency:.6f}", f"{coeff.ref_temperature:.3f}",
    ])
    configfile.set(section, "temperature_compensation", val)
