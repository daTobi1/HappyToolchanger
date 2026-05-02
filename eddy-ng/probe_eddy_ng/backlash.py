# Z-axis backlash estimation with Welch's t-test
# Inspired by Cartographer3D's CARTOGRAPHER_ESTIMATE_BACKLASH
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import List, Tuple

logger = logging.getLogger(__name__)


@dataclass
class BacklashResult:
    backlash: float
    mean_up: float
    mean_down: float
    std_up: float
    std_down: float
    t_stat: float
    degrees_of_freedom: float
    significant: bool


def welchs_ttest(a: List[float], b: List[float]) -> Tuple[float, float]:
    """Welch's t-test for two samples with unequal variance.

    Returns (t_statistic, degrees_of_freedom).
    """
    n_a = len(a)
    n_b = len(b)
    if n_a < 2 or n_b < 2:
        return 0.0, 0.0

    mean_a = sum(a) / n_a
    mean_b = sum(b) / n_b

    # Sample variance with Bessel's correction
    var_a = sum((x - mean_a) ** 2 for x in a) / (n_a - 1)
    var_b = sum((x - mean_b) ** 2 for x in b) / (n_b - 1)

    se_a = var_a / n_a
    se_b = var_b / n_b
    se_sum = se_a + se_b

    if se_sum < 1e-15:
        return 0.0, float(n_a + n_b - 2)

    t_stat = (mean_a - mean_b) / math.sqrt(se_sum)

    # Welch-Satterthwaite degrees of freedom
    numerator = se_sum ** 2
    denominator = (se_a ** 2 / (n_a - 1)) + (se_b ** 2 / (n_b - 1))
    if denominator < 1e-15:
        df = float(n_a + n_b - 2)
    else:
        df = numerator / denominator

    return t_stat, df


def estimate_backlash(
    measure_height_func,
    move_func,
    wait_func,
    height: float,
    delta: float = 0.5,
    iterations: int = 10,
    speed: float = 5.0,
) -> BacklashResult:
    """Estimate Z-axis backlash by measuring from both directions.

    Args:
        measure_height_func: Callable that returns current measured height.
        move_func: Callable(z, speed) that moves Z axis.
        wait_func: Callable that waits for moves to complete.
        height: Reference height for measurement.
        delta: Distance to move above/below reference.
        iterations: Number of measurement cycles.
        speed: Movement speed.

    Returns:
        BacklashResult with statistical analysis.
    """
    measurements_up: List[float] = []
    measurements_down: List[float] = []

    # Initial compensating moves to eliminate startup transients
    move_func(height + delta, speed)
    wait_func()
    move_func(height, speed)
    wait_func()
    move_func(height - delta, speed)
    wait_func()
    move_func(height, speed)
    wait_func()

    for _ in range(iterations):
        # Approach from below (moving UP)
        move_func(height - delta, speed)
        wait_func()
        move_func(height, speed)
        wait_func()
        h = measure_height_func()
        measurements_up.append(h)

        # Approach from above (moving DOWN)
        move_func(height + delta, speed)
        wait_func()
        move_func(height, speed)
        wait_func()
        h = measure_height_func()
        measurements_down.append(h)

    # Statistics
    n = len(measurements_up)
    mean_up = sum(measurements_up) / n
    mean_down = sum(measurements_down) / n
    std_up = math.sqrt(sum((x - mean_up) ** 2 for x in measurements_up) / (n - 1)) if n > 1 else 0.0
    std_down = math.sqrt(sum((x - mean_down) ** 2 for x in measurements_down) / (n - 1)) if n > 1 else 0.0

    t_stat, df = welchs_ttest(measurements_down, measurements_up)

    # t >= 2.0 is approximately p <= 0.05 for df > 30
    significant = abs(t_stat) >= 2.0

    if significant:
        backlash = mean_down - mean_up
        if backlash < 0:
            logger.warning("Negative backlash (%.4f mm) is unexpected, "
                           "setting to 0", backlash)
            backlash = 0.0
            significant = False
    else:
        backlash = 0.0

    return BacklashResult(
        backlash=backlash,
        mean_up=mean_up,
        mean_down=mean_down,
        std_up=std_up,
        std_down=std_down,
        t_stat=t_stat,
        degrees_of_freedom=df,
        significant=significant,
    )
