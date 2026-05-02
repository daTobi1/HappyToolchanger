# Alpha-Beta tracking filter for measurement smoothing
# Ported from Cartographer3D (cartographer3d-plugin)
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

MIN_DT = 1e-4


class AlphaBetaFilter:
    """Recursive tracking filter that estimates position and velocity.

    Alpha controls position smoothing (0-1), beta controls velocity
    estimation (0-1). Higher alpha tracks measurements more closely,
    higher beta reacts to velocity changes faster.
    """

    def __init__(self, alpha: float = 0.5, beta: float = 1e-6):
        if not 0 <= alpha <= 1:
            raise ValueError(f"alpha must be in [0, 1], got {alpha}")
        if not 0 <= beta <= 1:
            raise ValueError(f"beta must be in [0, 1], got {beta}")
        self.alpha = alpha
        self.beta = beta
        self.position: float | None = None
        self.velocity: float = 0.0
        self.last_time: float = 0.0

    def reset(self):
        self.position = None
        self.velocity = 0.0
        self.last_time = 0.0

    def update(self, measurement: float, time: float) -> float:
        if self.position is None:
            self.position = measurement
            self.last_time = time
            return self.position

        dt = time - self.last_time
        self.last_time = time

        # Predict
        predicted_position = self.position + self.velocity * dt

        # Residual
        residual = measurement - predicted_position

        # Correct position
        self.position = predicted_position + self.alpha * residual

        # Correct velocity (avoid division by near-zero dt)
        if dt > MIN_DT:
            self.velocity = self.velocity + (self.beta * residual) / dt

        return self.position
