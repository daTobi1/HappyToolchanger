# Data streaming with session management and CSV export
# Inspired by Cartographer3D's streaming system
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class StreamSample:
    time: float
    frequency: float
    temperature: float = 0.0
    position_x: float = 0.0
    position_y: float = 0.0
    position_z: float = 0.0
    has_position: bool = False


class StreamSession:
    """A data collection session that accumulates samples."""

    def __init__(self):
        self.samples: List[StreamSample] = []
        self.active: bool = True
        self.start_time: float = time.time()

    def add_sample(self, sample: StreamSample):
        if self.active:
            self.samples.append(sample)

    def stop(self):
        self.active = False

    @property
    def duration(self) -> float:
        return time.time() - self.start_time

    @property
    def count(self) -> int:
        return len(self.samples)


class DataStreamer:
    """Manages data streaming sessions with CSV export.

    Usage:
        streamer = DataStreamer()
        session = streamer.start_session("/tmp/output.csv")
        # ... collect data via add_sample() ...
        streamer.stop_session()  # writes CSV
    """

    def __init__(self):
        self._session: Optional[StreamSession] = None
        self._output_file: Optional[str] = None

    @property
    def is_active(self) -> bool:
        return self._session is not None and self._session.active

    @property
    def session(self) -> Optional[StreamSession]:
        return self._session

    def start_session(self, output_file: Optional[str] = None) -> StreamSession:
        if self.is_active:
            raise RuntimeError(
                "Stream already active. Stop current stream first."
            )

        self._output_file = output_file or _generate_filepath("eddy_ng_stream")
        _validate_output_path(self._output_file)

        self._session = StreamSession()
        logger.info("Started streaming session, will save to: %s",
                    self._output_file)
        return self._session

    def add_sample(self, sample: StreamSample):
        if self._session and self._session.active:
            self._session.add_sample(sample)

    def stop_session(self) -> Optional[str]:
        """Stop session and write CSV. Returns output file path."""
        if self._session is None:
            return None

        self._session.stop()
        output = None

        if self._output_file and self._session.samples:
            _write_csv(self._session.samples, self._output_file)
            output = self._output_file
            logger.info("Stopped streaming. %d samples saved to: %s",
                        len(self._session.samples), self._output_file)
        elif not self._session.samples:
            logger.warning("No samples collected during streaming session")

        self._session = None
        self._output_file = None
        return output

    def cancel_session(self):
        """Cancel session without saving."""
        if self._session:
            self._session.stop()
            logger.info("Cancelled streaming session (%d samples discarded)",
                        len(self._session.samples))
        self._session = None
        self._output_file = None

    def get_status(self) -> str:
        if not self.is_active:
            return "No active streaming session"
        s = self._session
        return (f"Streaming active: {s.count} samples collected "
                f"over {s.duration:.1f}s → {self._output_file}")


def _generate_filepath(label: str) -> str:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"{label}_{timestamp}.csv"
    return os.path.join("/tmp", filename)


def _validate_output_path(path: str):
    parent = os.path.dirname(path)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)


def _write_csv(samples: List[StreamSample], output_file: str):
    with open(output_file, "w") as f:
        f.write("time,frequency,temperature,position_x,position_y,position_z\n")
        for s in samples:
            if s.has_position:
                f.write(f"{s.time:.6f},{s.frequency:.3f},{s.temperature:.2f},"
                        f"{s.position_x:.4f},{s.position_y:.4f},"
                        f"{s.position_z:.6f}\n")
            else:
                f.write(f"{s.time:.6f},{s.frequency:.3f},{s.temperature:.2f}"
                        f",,,\n")
