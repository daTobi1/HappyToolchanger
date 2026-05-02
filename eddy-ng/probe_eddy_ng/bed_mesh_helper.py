from __future__ import annotations

import logging
import numpy as np
from functools import cmp_to_key
from typing import List, TYPE_CHECKING, final

from ._compat import bed_mesh, ConfigWrapper
from .mesh_paths import generate_mesh_path

if TYPE_CHECKING:
    from .probe import ProbeEddy


@final
class BedMeshScanHelper:
    def __init__(self, eddy: ProbeEddy, config: ConfigWrapper):
        self._eddy = eddy
        self._printer = eddy._printer

        bmc = config.getsection("bed_mesh")
        self._bed_mesh = eddy._printer.load_object(bmc, "bed_mesh")
        self._x_points, self._y_points = bmc.getintlist("probe_count", count=2, note_valid=False)
        self._x_min, self._y_min = bmc.getfloatlist("mesh_min", count=2, note_valid=False)
        self._x_max, self._y_max = bmc.getfloatlist("mesh_max", count=2, note_valid=False)
        self._speed = bmc.getfloat("speed", 100.0, above=0.0, note_valid=False)
        self._scan_z = bmc.getfloat("horizontal_move_z", self._eddy.params.mesh_height, above=0.0, note_valid=False)

        self._x_offset = self._eddy.params.x_offset
        self._y_offset = self._eddy.params.y_offset

        # Get axis limits for path generation
        th_config = config.getsection("stepper_x")
        try:
            self._axis_min = (
                th_config.getfloat("position_min", 0.0, note_valid=False),
                config.getsection("stepper_y").getfloat("position_min", 0.0, note_valid=False),
            )
            self._axis_max = (
                th_config.getfloat("position_max", 300.0, note_valid=False),
                config.getsection("stepper_y").getfloat("position_max", 300.0, note_valid=False),
            )
        except Exception:
            self._axis_min = (0.0, 0.0)
            self._axis_max = (300.0, 300.0)

        self._mesh_points, self._mesh_path = self._generate_path()

    def _generate_path(self):
        x_vals = np.linspace(self._x_min, self._x_max, self._x_points)
        y_vals = np.linspace(self._y_min, self._y_max, self._y_points)

        # Generate the grid of probe points
        probe_points = []
        for y in y_vals:
            for x in x_vals:
                probe_points.append((float(x), float(y)))

        # Use configured path algorithm
        path_type = self._eddy.params.mesh_path
        direction = self._eddy.params.mesh_direction

        ordered = generate_mesh_path(
            probe_points,
            path_type=path_type,
            direction=direction,
            axis_min=self._axis_min,
            axis_max=self._axis_max,
        )

        # Convert to (x, y, include) format for compatibility
        path = [(x, y, True) for x, y in ordered]
        return path, path

    def _scan_path(self):
        th = self._eddy._toolhead
        times = []

        for pt in self._mesh_path:
            # TODO bounds
            th.manual_move([pt[0] - self._x_offset, pt[1] - self._y_offset, None], self._speed)
            th.register_lookahead_callback(lambda t: times.append(t))

        th.wait_moves()

        return times

    def _set_bed_mesh(self, heights):
        # heights is in the order of the _mesh_path points; convert to
        # be ordered min_y..max_y, min_x..max_x, then pull out the heights
        indexed_points = []
        i = 0
        for x, y, include in self._mesh_path:
            if not include:
                continue
            indexed_points.append((x, y, i))
            i += 1

        def sort_points(a, b):
            if a[1] < b[1]: # y first
                return -1
            if a[1] > b[1]:
                return 1
            if a[0] < b[0]: # then x
                return -1
            if a[0] > b[0]:
                return 1
            return 0

        indices = [ki for _, _, ki in sorted(indexed_points, key=cmp_to_key(sort_points))]

        ki = 0
        matrix = []
        for _ in range(self._y_points):
            row = []
            for _ in range(self._x_points):
                v = heights[indices[ki]]
                row.append(self._scan_z - v)
                ki += 1
            matrix.append(row)

        params = self._bed_mesh.bmc.mesh_config.copy()
        params.update({
            "min_x": self._x_min,
            "max_x": self._x_max,
            "min_y": self._y_min,
            "max_y": self._y_max,
            "x_count": self._x_points,
            "y_count": self._y_points,
        })
        mesh = bed_mesh.ZMesh(params, None)
        try:
            mesh.build_mesh(matrix)
        except bed_mesh.BedMeshError as e:
            raise self._printer.command_error(str(e))
        self._bed_mesh.set_mesh(mesh)
        self._eddy._log_msg("Mesh scan complete")

    def _apply_axis_twist_compensation(self, heights: List[float]) -> List[float]:
        """Apply axis twist compensation corrections to scan heights.

        Looks up Klipper's axis_twist_compensation module and uses it
        to correct probe readings based on X position along the gantry.
        """
        atc = self._printer.lookup_object("axis_twist_compensation", None)
        if atc is None:
            return heights

        try:
            compensations = atc.get_z_compensation_value
        except AttributeError:
            logging.info("axis_twist_compensation found but "
                         "get_z_compensation_value not available")
            return heights

        corrected = []
        for i, (x, y, include) in enumerate(self._mesh_path):
            if not include or i >= len(heights):
                continue
            try:
                z_comp = compensations(x)
                corrected.append(heights[i] - z_comp)
            except Exception:
                corrected.append(heights[i])

        if len(corrected) != len(heights):
            logging.warning("axis_twist_compensation: point count mismatch, "
                            "skipping compensation")
            return heights

        logging.info(f"Applied axis twist compensation to {len(corrected)} points")
        return corrected

    def _run_single_scan(self) -> List[float]:
        """Execute a single scan pass and return heights."""
        th = self._eddy._toolhead
        sample_time = self._eddy.params.scan_sample_time

        # Reset alpha-beta filter for each pass
        if self._eddy._ab_filter is not None:
            self._eddy._ab_filter.reset()

        with self._eddy.start_sampler() as sampler:
            path_times = self._scan_path()
            sampler.wait_for_sample_at_time(path_times[-1] + sample_time * 2.0)
            sampler.finish()

            heights = sampler.find_heights_at_times(
                [(t - sample_time / 2.0, t + sample_time / 2.0) for t in path_times]
            )
            heights = [h + self._eddy._tap_offset for h in heights]
            return heights, path_times

    def scan(self):
        th = self._eddy._toolhead
        mesh_runs = self._eddy.params.mesh_runs

        # Move to the start point
        v = self._mesh_path[0]
        th.manual_move([None, None, 10.0], self._eddy.params.lift_speed)
        th.manual_move([v[0] - self._x_offset, v[1] - self._y_offset, None], self._speed)
        th.manual_move([None, None, self._scan_z], self._eddy.params.probe_speed)
        th.wait_moves()

        all_heights = []

        for run in range(mesh_runs):
            if run > 0:
                # Return to start for subsequent passes
                v = self._mesh_path[0]
                th.manual_move([v[0] - self._x_offset, v[1] - self._y_offset, None], self._speed)
                th.wait_moves()
                self._eddy._log_msg(f"Starting mesh pass {run + 1}/{mesh_runs}")

            heights, path_times = self._run_single_scan()
            all_heights.append(heights)

        # Average across passes
        if mesh_runs > 1:
            heights_np = np.array(all_heights)
            heights = np.median(heights_np, axis=0).tolist()
            self._eddy._log_msg(
                f"Averaged {mesh_runs} mesh passes (median)"
            )
        else:
            heights = all_heights[0]

        # Apply axis twist compensation if available
        heights = self._apply_axis_twist_compensation(heights)

        with open("/tmp/mesh.csv", "w") as mfile:
            mfile.write("time,x,y,z\n")
            for i in range(len(self._mesh_points)):
                t = path_times[i]
                x = self._mesh_points[i][0]
                y = self._mesh_points[i][1]
                z = heights[i]
                mfile.write(f"{t},{x},{y},{z}\n")

        self._set_bed_mesh(heights)


def bed_mesh_ProbeManager_start_probe_override(self, gcmd):
    method = gcmd.get("METHOD", "automatic").lower()
    can_scan = False
    pprobe = self.printer.lookup_object("probe", None)
    if pprobe is not None:
        probe_name = pprobe.get_status(None).get("name", "")
        can_scan = "eddy" in probe_name
    if method == "rapid_scan" and can_scan:
        self.rapid_scan_helper.perform_rapid_scan(gcmd)
    else:
        self.probe_helper.start_probe(gcmd)
