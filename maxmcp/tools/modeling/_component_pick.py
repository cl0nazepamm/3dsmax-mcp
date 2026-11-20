"""Screen-space distances for base-cage targeting; these do not test visibility."""
from __future__ import annotations

import math


def segment_distance(point, start, end):
    """Return pixel distance and nearest point on a closed 2D segment."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0,
        ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length2))
    closest = [start[0] + t * dx, start[1] + t * dy]
    return math.dist(point, closest), closest


def polygon_distance(point, vertices):
    """Distance to a projected polygon, including concave polygon interiors.

    Even/odd containment is deliberately geometric: an inside result says
    nothing about backface culling, occlusion, or an evaluated modifier surface.
    """
    best = (float("inf"), None)
    inside = False
    for a, b in zip(vertices, vertices[1:] + vertices[:1]):
        distance, closest = segment_distance(point, a, b)
        if distance < best[0]:
            best = distance, closest
        if (a[1] > point[1]) != (b[1] > point[1]):
            crossing = a[0] + (point[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if point[0] < crossing:
                inside = not inside
    return (0.0, list(point)) if inside else best


def component_distance(point, pixels, level):
    if level == "vertex":
        return math.dist(point, pixels[0]), list(pixels[0])
    if level == "edge":
        return segment_distance(point, pixels[0], pixels[1])
    return polygon_distance(point, pixels)


def _world_segment(point, start, end):
    delta = [b - a for a, b in zip(start, end)]
    length2 = sum(v * v for v in delta)
    t = 0.0 if length2 == 0 else max(0.0, min(1.0,
        sum((p - a) * v for p, a, v in zip(point, start, delta)) / length2))
    closest = [a + t * v for a, v in zip(start, delta)]
    return math.dist(point, closest), closest


def surface_distance(point, vertices, level):
    """Nearest base-component distance to an evaluated surface hit.

    A polygon uses its Newell plane and boundary. Nonplanar/degenerate faces are
    marked approximate; this is correspondence evidence, never a visibility test.
    """
    if level == "vertex":
        return math.dist(point, vertices[0]), list(vertices[0]), False
    if level == "edge":
        distance, closest = _world_segment(point, vertices[0], vertices[1])
        return distance, closest, False
    boundary = min((_world_segment(point, a, b)
        for a, b in zip(vertices, vertices[1:] + vertices[:1])), key=lambda row: row[0])
    local = [[v - origin for v, origin in zip(vertex, vertices[0])] for vertex in vertices]
    normal = [0.0, 0.0, 0.0]
    for a, b in zip(local, local[1:] + local[:1]):
        normal[0] += a[1] * b[2] - a[2] * b[1]
        normal[1] += a[2] * b[0] - a[0] * b[2]
        normal[2] += a[0] * b[1] - a[1] * b[0]
    length = math.sqrt(sum(v * v for v in normal))
    if length < 1e-20:
        return *boundary, True
    normal = [v / length for v in normal]
    center = [sum(v[axis] for v in vertices) / len(vertices) for axis in range(3)]
    signed = sum((p - c) * n for p, c, n in zip(point, center, normal))
    projected = [p - signed * n for p, n in zip(point, normal)]
    scale = max(math.dist(center, vertex) for vertex in vertices)
    approximate = any(abs(sum((p - c) * n for p, c, n in zip(vertex, center, normal)))
        > max(scale * 1e-6, 1e-12) for vertex in vertices)
    drop = max(range(3), key=lambda axis: abs(normal[axis]))
    axes = [axis for axis in range(3) if axis != drop]
    point2 = [projected[axis] for axis in axes]
    polygon2 = [[vertex[axis] for axis in axes] for vertex in vertices]
    if polygon_distance(point2, polygon2)[0] == 0.0:
        return abs(signed), projected, approximate
    return *boundary, approximate
