# Tests for ProbeEddyFrequencyMap calibration serialization
from __future__ import annotations

import json
import math
import pickle
import base64

import numpy as np
import numpy.polynomial as npp
import pytest


# We test the serialization logic directly without importing the full module
# (which depends on Klipper). Instead we replicate the static methods.


def poly_to_json(poly):
    """Serialize a numpy Polynomial to a JSON-safe dict."""
    if poly is None:
        return None
    return {
        "coef": [float.hex(float(c)) for c in poly.coef],
        "domain": [float.hex(float(d)) for d in poly.domain],
        "window": [float.hex(float(w)) for w in poly.window],
    }


def poly_from_json(d):
    """Deserialize a numpy Polynomial from a JSON dict."""
    if d is None:
        return None
    coef = [float.fromhex(c) for c in d["coef"]]
    domain = [float.fromhex(v) for v in d["domain"]]
    window = [float.fromhex(v) for v in d["window"]]
    return npp.Polynomial(coef, domain=domain, window=window)


class TestPolySerialization:
    def test_roundtrip_simple_polynomial(self):
        """A polynomial survives JSON roundtrip with exact precision."""
        original = npp.Polynomial([1.0, 2.5, -3.7, 0.001])
        data = poly_to_json(original)
        restored = poly_from_json(data)

        assert len(restored.coef) == len(original.coef)
        np.testing.assert_array_equal(restored.coef, original.coef)
        np.testing.assert_array_equal(restored.domain, original.domain)
        np.testing.assert_array_equal(restored.window, original.window)

    def test_roundtrip_fitted_polynomial(self):
        """A fitted polynomial (with domain/window) survives roundtrip."""
        x = np.linspace(0.001, 0.01, 50)
        y = 5.0 * x**2 + 3.0 * x + 1.0 + np.random.normal(0, 0.001, 50)
        original = npp.Polynomial.fit(x, y, deg=9)

        data = poly_to_json(original)
        restored = poly_from_json(data)

        # Evaluate at test points - should be identical
        test_x = np.linspace(0.002, 0.009, 20)
        np.testing.assert_array_equal(original(test_x), restored(test_x))

    def test_none_polynomial(self):
        assert poly_to_json(None) is None
        assert poly_from_json(None) is None

    def test_json_is_valid_json(self):
        poly = npp.Polynomial([1.0, 2.0, 3.0])
        data = poly_to_json(poly)
        # Ensure it can be serialized and deserialized as JSON
        json_str = json.dumps(data)
        parsed = json.loads(json_str)
        restored = poly_from_json(parsed)
        np.testing.assert_array_equal(restored.coef, poly.coef)

    def test_hex_float_precision(self):
        """Hex floats preserve full double precision."""
        val = 1.23456789012345e-10
        hex_str = float.hex(val)
        restored = float.fromhex(hex_str)
        assert val == restored  # Exact equality, not approximate


class TestCalibrationDataFormat:
    def _make_calibration_data(self):
        """Create realistic calibration data."""
        freqs = np.linspace(500000, 600000, 100)
        heights = np.linspace(0.0, 10.0, 100)
        inv_freqs = 1.0 / freqs

        ftoh = npp.Polynomial.fit(inv_freqs, heights, deg=9)
        htof = npp.Polynomial.fit(heights, inv_freqs, deg=9)
        ftoh_high = npp.Polynomial.fit(inv_freqs[50:], heights[50:], deg=9)

        return {
            "v": 5,
            "dc": 15,
            "h_range": [0.0, 10.0],
            "f_range": [500000.0, 600000.0],
            "ftoh": poly_to_json(ftoh),
            "ftoh_high": poly_to_json(ftoh_high),
            "htof": poly_to_json(htof),
        }

    def test_json_format_is_human_readable(self):
        data = self._make_calibration_data()
        json_str = json.dumps(data, indent=2)
        # Must contain readable keys
        assert '"v": 5' in json_str
        assert '"dc": 15' in json_str
        assert '"h_range"' in json_str
        assert '"coef"' in json_str

    def test_json_format_roundtrip(self):
        data = self._make_calibration_data()
        json_str = json.dumps(data, separators=(",", ":"))

        restored = json.loads(json_str)
        assert restored["v"] == 5
        assert restored["dc"] == 15
        assert restored["h_range"] == [0.0, 10.0]

        ftoh = poly_from_json(restored["ftoh"])
        assert ftoh is not None

    def test_json_format_no_pickle(self):
        """Ensure the JSON format doesn't contain any pickle data."""
        data = self._make_calibration_data()
        json_str = json.dumps(data, separators=(",", ":"))
        # Should start with { (JSON object)
        assert json_str.startswith("{")
        # Should not be base64 encoded pickle
        try:
            pickle.loads(base64.b64decode(json_str))
            pytest.fail("Should not be valid pickle data")
        except Exception:
            pass  # Expected


class TestSlidingWindow:
    """Tests for the sliding window tap sample validation logic."""

    def test_window_limits_samples(self):
        """Only the most recent window of samples should be considered."""
        from itertools import combinations

        # Simulate _compute_tap_z logic with sliding window
        all_zs = [0.100, 0.200, 0.300, 0.010, 0.012, 0.011]  # good cluster at end
        samples = 3
        max_noisy_samples = 2
        window_size = samples + max_noisy_samples
        window = all_zs[-window_size:]

        # Window should be [0.300, 0.010, 0.012, 0.011, 0.011] - only last 5
        assert len(window) == window_size

        # Find best subset in window
        best_std = math.inf
        for combo in combinations(window, samples):
            std = float(np.std(combo))
            if std < best_std:
                best_std = std

        # The best 3 should be from the good cluster
        assert best_std < 0.002

    def test_old_good_samples_ignored(self):
        """Good samples outside the window should not be cherry-picked."""
        from itertools import combinations

        # Good samples at start, noise, then mixed at end
        all_zs = [0.010, 0.011, 0.012, 0.500, 0.600, 0.700, 0.050, 0.060]
        samples = 3
        max_noisy_samples = 2
        window_size = samples + max_noisy_samples

        # With window: only last 5 considered
        window = all_zs[-window_size:]
        best_std_window = math.inf
        for combo in combinations(window, samples):
            best_std_window = min(best_std_window, float(np.std(combo)))

        # Without window: all samples considered (old behavior)
        best_std_all = math.inf
        for combo in combinations(all_zs, samples):
            best_std_all = min(best_std_all, float(np.std(combo)))

        # Without window would find the good early cluster (0.010, 0.011, 0.012)
        # With window should not find it
        assert best_std_all < 0.001
        assert best_std_window > best_std_all


class TestThresholdStepCalculation:
    """Tests for adaptive threshold step size."""

    def test_far_from_target_large_step(self):
        """When range is far from target, step should be 20%."""
        threshold = 500.0
        req_range = 0.010
        range_value = 0.200  # 20x target
        MIN_STEP, MAX_STEP = 10.0, 500.0

        if range_value is None or range_value > req_range * 10:
            step = min(MAX_STEP, max(MIN_STEP, threshold * 0.20))
        else:
            step = min(MAX_STEP, max(MIN_STEP, threshold * 0.10))

        assert step == 100.0  # 500 * 0.20

    def test_close_to_target_small_step(self):
        """When range is close to target, step should be 10%."""
        threshold = 500.0
        req_range = 0.010
        range_value = 0.050  # 5x target (close)
        MIN_STEP, MAX_STEP = 10.0, 500.0

        if range_value is None or range_value > req_range * 10:
            step = min(MAX_STEP, max(MIN_STEP, threshold * 0.20))
        else:
            step = min(MAX_STEP, max(MIN_STEP, threshold * 0.10))

        assert step == 50.0  # 500 * 0.10

    def test_minimum_step(self):
        """Step should not go below MIN_STEP."""
        threshold = 20.0
        req_range = 0.010
        range_value = 0.005
        MIN_STEP, MAX_STEP = 10.0, 500.0

        step = min(MAX_STEP, max(MIN_STEP, threshold * 0.10))
        assert step == MIN_STEP

    def test_maximum_step(self):
        """Step should not exceed MAX_STEP."""
        threshold = 10000.0
        req_range = 0.010
        range_value = None
        MIN_STEP, MAX_STEP = 10.0, 500.0

        if range_value is None or range_value > req_range * 10:
            step = min(MAX_STEP, max(MIN_STEP, threshold * 0.20))
        else:
            step = min(MAX_STEP, max(MIN_STEP, threshold * 0.10))

        assert step == MAX_STEP


class TestDriveCurrentScoring:
    """Tests for the drive current optimization scoring logic."""

    def _score_homing(self, rmse, spread, h_min, h_max):
        """Replicate homing score from cmd_OPTIMIZE_DRIVE_CURRENT."""
        homing_req_min, homing_req_max = 0.5, 5.0
        min_freq_spread, max_rmse = 0.30, 0.025
        if h_min <= homing_req_min and h_max >= homing_req_max and spread >= min_freq_spread and rmse <= max_rmse:
            return (1.0 / (1.0 + rmse * 100.0)) + (spread / 100.0)
        return None

    def _score_tap(self, rmse, spread, h_min, h_max):
        """Replicate tap score from cmd_OPTIMIZE_DRIVE_CURRENT."""
        tap_req_min, tap_req_max = 0.025, 3.0
        min_freq_spread, max_rmse = 0.30, 0.025
        if h_min <= tap_req_min and h_max >= tap_req_max and spread >= min_freq_spread and rmse <= max_rmse:
            return (1.0 / (1.0 + rmse * 100.0)) + (1.0 / (1.0 + h_min * 100.0)) + (spread / 100.0)
        return None

    def test_lower_rmse_wins_homing(self):
        """DC with lower RMSE should score higher for homing."""
        score_good = self._score_homing(rmse=0.005, spread=2.0, h_min=0.1, h_max=10.0)
        score_bad = self._score_homing(rmse=0.020, spread=2.0, h_min=0.1, h_max=10.0)
        assert score_good is not None
        assert score_bad is not None
        assert score_good > score_bad

    def test_higher_spread_wins_homing(self):
        """DC with higher frequency spread should score higher for homing."""
        score_good = self._score_homing(rmse=0.010, spread=5.0, h_min=0.1, h_max=10.0)
        score_bad = self._score_homing(rmse=0.010, spread=1.0, h_min=0.1, h_max=10.0)
        assert score_good > score_bad

    def test_lower_min_height_wins_tap(self):
        """DC with lower min height should score higher for tap."""
        score_good = self._score_tap(rmse=0.010, spread=2.0, h_min=0.005, h_max=5.0)
        score_bad = self._score_tap(rmse=0.010, spread=2.0, h_min=0.020, h_max=5.0)
        assert score_good > score_bad

    def test_rejects_insufficient_height_range_homing(self):
        """DC that doesn't cover required homing height range is rejected."""
        # h_max too low for homing (needs >= 5.0)
        assert self._score_homing(rmse=0.005, spread=2.0, h_min=0.1, h_max=3.0) is None

    def test_rejects_insufficient_height_range_tap(self):
        """DC that doesn't cover required tap height range is rejected."""
        # h_min too high for tap (needs <= 0.025)
        assert self._score_tap(rmse=0.005, spread=2.0, h_min=0.1, h_max=5.0) is None

    def test_rejects_high_rmse(self):
        """DC with RMSE above threshold is rejected."""
        assert self._score_homing(rmse=0.030, spread=2.0, h_min=0.1, h_max=10.0) is None
        assert self._score_tap(rmse=0.030, spread=2.0, h_min=0.01, h_max=5.0) is None

    def test_rejects_low_spread(self):
        """DC with frequency spread below threshold is rejected."""
        assert self._score_homing(rmse=0.010, spread=0.20, h_min=0.1, h_max=10.0) is None
        assert self._score_tap(rmse=0.010, spread=0.20, h_min=0.01, h_max=5.0) is None


class TestBugFixes:
    def test_any_in_pattern(self):
        """Verify the or/in bug fix pattern works correctly."""
        # Old buggy pattern (always True):
        # if "Sensor error" or "Probe completed" or "Probe triggered" in str(err):
        # This was always True because "Sensor error" is truthy

        # New correct pattern:
        err = Exception("Something unrelated happened")
        result = any(x in str(err) for x in ("Sensor error", "Probe completed movement", "Probe triggered prior"))
        assert result is False

        err = Exception("Sensor error: amplitude")
        result = any(x in str(err) for x in ("Sensor error", "Probe completed movement", "Probe triggered prior"))
        assert result is True

        err = Exception("Probe completed movement before triggering")
        result = any(x in str(err) for x in ("Sensor error", "Probe completed movement", "Probe triggered prior"))
        assert result is True
