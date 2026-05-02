# Advanced bed mesh path generators
# Inspired by Cartographer3D's mesh path algorithms
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

import math
import random
from typing import Iterator, List, Tuple

Point = Tuple[float, float]
Vec = Tuple[float, float]

BUFFER = 0.5  # mm from axis limits


def normalize(v: Vec) -> Vec:
    length = math.sqrt(v[0] ** 2 + v[1] ** 2)
    if length < 1e-10:
        return (0.0, 0.0)
    return (v[0] / length, v[1] / length)


def perpendicular(v: Vec, ccw: bool = True) -> Vec:
    if ccw:
        return (-v[1], v[0])
    return (v[1], -v[0])


def angle_deg(v: Vec) -> float:
    return math.degrees(math.atan2(v[1], v[0]))


def arc_points(center: Vec, radius: float, start_angle_deg: float,
               span_deg: float, max_dev: float = 0.1) -> Iterator[Point]:
    """Generate arc points with maximum deviation from true arc."""
    if radius < 1e-6 or abs(span_deg) < 0.1:
        return

    d_theta = math.degrees(math.acos(max(0.0, 1.0 - max_dev / radius)))
    d_theta = max(d_theta, 0.1)
    n_steps = max(1, int(abs(span_deg) / d_theta))
    step = span_deg / n_steps

    for i in range(n_steps + 1):
        angle = math.radians(start_angle_deg + i * step)
        yield (
            center[0] + radius * math.cos(angle),
            center[1] + radius * math.sin(angle),
        )


def cluster_by_axis(points: List[Point], axis: str,
                    tol: float = 1e-3) -> List[List[Point]]:
    """Group points into rows/columns by the given axis."""
    idx = 0 if axis == "x" else 1
    other = 1 if axis == "x" else 0

    sorted_pts = sorted(points, key=lambda p: (p[idx], p[other]))
    clusters: List[List[Point]] = []
    current: List[Point] = []

    for pt in sorted_pts:
        if current and abs(pt[idx] - current[-1][idx]) > tol:
            clusters.append(sorted(current, key=lambda p: p[other]))
            current = []
        current.append(pt)
    if current:
        clusters.append(sorted(current, key=lambda p: p[other]))

    return clusters


# ─── Snake Path ──────────────────────────────────────────────────────────────

def generate_snake_path(
    points: List[Point],
    direction: str = "x",
    axis_min: Tuple[float, float] = (0, 0),
    axis_max: Tuple[float, float] = (300, 300),
    corner_radius: float = -1,
) -> List[Point]:
    """Standard boustrophedon (back-and-forth) path.

    Args:
        points: Mesh probe points.
        direction: Primary axis ("x" or "y").
        axis_min: Printer minimum coordinates.
        axis_max: Printer maximum coordinates.
        corner_radius: Turn radius (-1 for auto).

    Returns:
        Ordered path with smooth U-turns.
    """
    secondary = "y" if direction == "x" else "x"
    rows = cluster_by_axis(points, secondary)

    if not rows:
        return list(points)

    # Auto corner radius
    if corner_radius < 0 and len(rows) > 1:
        d_idx = 1 if secondary == "y" else 0
        row_spacing = abs(rows[1][0][d_idx] - rows[0][0][d_idx])
        max_r_spacing = round(row_spacing / 2, 2)

        mesh_min = min(p[d_idx] for p in points)
        mesh_max = max(p[d_idx] for p in points)
        a_min = axis_min[d_idx]
        a_max = axis_max[d_idx]
        max_r_bounds = min(mesh_min - a_min, a_max - mesh_max) - BUFFER
        corner_radius = max(0, min(max_r_spacing, max_r_bounds))

    path: List[Point] = []
    forward = True

    for i, row in enumerate(rows):
        ordered = row if forward else list(reversed(row))

        if i > 0 and corner_radius > 0.5:
            # Generate smooth U-turn between rows
            prev_end = path[-1]
            next_start = ordered[0]
            turn_pts = _u_turn(prev_end, next_start, corner_radius,
                               direction, forward)
            path.extend(turn_pts)

        path.extend(ordered)
        forward = not forward

    return path


def _u_turn(start: Point, end: Point, radius: float,
            direction: str, was_forward: bool) -> List[Point]:
    """Generate smooth U-turn arc between two rows."""
    pts: List[Point] = []
    mid_x = (start[0] + end[0]) / 2
    mid_y = (start[1] + end[1]) / 2

    if direction == "x":
        # Rows go along X, U-turn is in Y direction
        if was_forward:
            # Was going right, turn down/up
            cx = start[0] + radius if start[0] < end[0] else start[0] - radius
        else:
            cx = start[0] - radius if start[0] > end[0] else start[0] + radius
        cy = (start[1] + end[1]) / 2
        span = 180 if end[1] > start[1] else -180
        start_angle = angle_deg((0, start[1] - cy))
        pts.extend(arc_points((cx, cy), radius, start_angle, span))
    else:
        cy = start[1] + radius if start[1] < end[1] else start[1] - radius
        cx = (start[0] + end[0]) / 2
        span = 180 if end[0] > start[0] else -180
        start_angle = angle_deg((start[0] - cx, 0))
        pts.extend(arc_points((cx, cy), radius, start_angle, span))

    return pts


# ─── Alternating Snake Path ─────────────────────────────────────────────────

def generate_alternating_snake_path(
    points: List[Point],
    direction: str = "x",
    axis_min: Tuple[float, float] = (0, 0),
    axis_max: Tuple[float, float] = (300, 300),
) -> List[Point]:
    """Two-pass snake: first in primary direction, then perpendicular."""
    path1 = generate_snake_path(points, direction, axis_min, axis_max)
    other_dir = "y" if direction == "x" else "x"
    path2 = generate_snake_path(points, other_dir, axis_min, axis_max)
    path2.reverse()
    return path1 + path2


# ─── Spiral Path ────────────────────────────────────────────────────────────

def generate_spiral_path(
    points: List[Point],
    axis_min: Tuple[float, float] = (0, 0),
    axis_max: Tuple[float, float] = (300, 300),
) -> List[Point]:
    """Concentric spiral path from outside to center."""
    if len(points) < 4:
        return list(points)

    # Find bounds
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    cx = (min_x + max_x) / 2
    cy = (min_y + max_y) / 2

    # Sort by angle from center, then by distance (outside in)
    def sort_key(p):
        dx = p[0] - cx
        dy = p[1] - cy
        angle = math.atan2(dy, dx)
        dist = math.sqrt(dx ** 2 + dy ** 2)
        return (-dist, angle)

    # Group into concentric rings
    rows_x = cluster_by_axis(points, "x")
    rows_y = cluster_by_axis(points, "y")

    n_rings = min(len(rows_x), len(rows_y)) // 2
    if n_rings < 1:
        return sorted(points, key=sort_key)

    path: List[Point] = []
    remaining = set(range(len(points)))
    point_list = list(points)

    for ring in range(n_rings):
        # Get border points for this ring
        ring_points = []
        for i in remaining:
            p = point_list[i]
            x_idx = 0
            for j, row in enumerate(rows_x):
                if any(abs(rp[0] - p[0]) < 1e-3 for rp in row):
                    x_idx = j
                    break
            y_idx = 0
            for j, row in enumerate(rows_y):
                if any(abs(rp[1] - p[1]) < 1e-3 for rp in row):
                    y_idx = j
                    break
            if (x_idx == ring or x_idx == len(rows_x) - 1 - ring or
                    y_idx == ring or y_idx == len(rows_y) - 1 - ring):
                ring_points.append((i, p))

        if not ring_points:
            break

        # Sort ring points by angle for continuous path
        ring_points.sort(
            key=lambda ip: math.atan2(ip[1][1] - cy, ip[1][0] - cx)
        )

        for idx, pt in ring_points:
            path.append(pt)
            remaining.discard(idx)

    # Add any remaining center points
    for i in remaining:
        path.append(point_list[i])

    return path


# ─── Random Path ────────────────────────────────────────────────────────────

def generate_random_path(
    points: List[Point],
    min_dist: float = 10.0,
) -> List[Point]:
    """Nearest-neighbor randomized traversal.

    Selects next point with probability weighted by distance to avoid
    long travel moves.
    """
    if len(points) <= 2:
        return list(points)

    remaining = list(range(len(points)))
    point_list = list(points)

    # Start at random point
    current_idx = random.choice(remaining)
    remaining.remove(current_idx)
    path = [point_list[current_idx]]

    while remaining:
        current = path[-1]
        dists = []
        for idx in remaining:
            p = point_list[idx]
            d = math.sqrt((p[0] - current[0]) ** 2 +
                          (p[1] - current[1]) ** 2)
            dists.append(max(d, 0.1))

        # Inverse distance weighting (closer = more likely)
        inv_dists = [1.0 / d for d in dists]
        total = sum(inv_dists)
        weights = [w / total for w in inv_dists]

        # Weighted random selection
        r = random.random()
        cumulative = 0.0
        chosen = 0
        for i, w in enumerate(weights):
            cumulative += w
            if r <= cumulative:
                chosen = i
                break

        next_idx = remaining[chosen]
        remaining.remove(next_idx)
        path.append(point_list[next_idx])

    return path


# ─── Path Selector ──────────────────────────────────────────────────────────

PATH_GENERATORS = {
    "snake": generate_snake_path,
    "alternating_snake": generate_alternating_snake_path,
    "spiral": generate_spiral_path,
    "random": generate_random_path,
}


def generate_mesh_path(
    points: List[Point],
    path_type: str = "snake",
    direction: str = "x",
    axis_min: Tuple[float, float] = (0, 0),
    axis_max: Tuple[float, float] = (300, 300),
) -> List[Point]:
    """Generate mesh path using the specified algorithm.

    Args:
        points: Mesh probe points.
        path_type: One of "snake", "alternating_snake", "spiral", "random".
        direction: Primary axis for snake paths.
        axis_min: Printer minimum coordinates.
        axis_max: Printer maximum coordinates.
    """
    if path_type == "snake":
        return generate_snake_path(points, direction, axis_min, axis_max)
    elif path_type == "alternating_snake":
        return generate_alternating_snake_path(points, direction,
                                               axis_min, axis_max)
    elif path_type == "spiral":
        return generate_spiral_path(points, axis_min, axis_max)
    elif path_type == "random":
        return generate_random_path(points)
    else:
        raise ValueError(f"Unknown path type: {path_type}. "
                         f"Available: {', '.join(PATH_GENERATORS.keys())}")
