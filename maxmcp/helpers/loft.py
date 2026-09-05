"""Bounded arithmetic-only construction expressions and matching-section lofts."""
from __future__ import annotations

import ast
import math
import re
from typing import Any


FUNCTIONS = {"sin": math.sin, "cos": math.cos, "sqrt": math.sqrt,
             "abs": abs, "min": min, "max": max}
MAX_COORDINATE = 1e12


def number(value: Any, label: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or abs(value) > MAX_COORDINATE):
        raise ValueError(f"{label} must be a finite number with magnitude at most 1e12")
    return float(value)


def validate_parameters(parameters: dict | None) -> dict[str, float]:
    if parameters is None:
        return {}
    if not isinstance(parameters, dict) or len(parameters) > 64:
        raise ValueError("parameters must be an object with at most 64 numeric values")
    result = {}
    for key, value in parameters.items():
        if (not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", key)
                or key in {*FUNCTIONS, "pi"}):
            raise ValueError("Parameter names must be ASCII identifiers and cannot shadow pi or math functions")
        result[key] = number(value, f"parameters.{key}")
    return result


def coordinate(value: Any, parameters: dict[str, float]) -> float:
    if not isinstance(value, str):
        return number(value, "section coordinate")
    if not value or len(value) > 256:
        raise ValueError("Coordinate expressions must contain 1-256 characters")
    try:
        tree = ast.parse(value.strip(), mode="eval")
    except (SyntaxError, ValueError) as exc:
        raise ValueError(f"Invalid coordinate expression: {value!r}") from exc
    if sum(1 for _ in ast.walk(tree)) > 128:
        raise ValueError("Coordinate expression is too complex")

    def visit(node: ast.AST) -> float:
        if isinstance(node, ast.Constant):
            return number(node.value, "expression constant")
        if isinstance(node, ast.Name):
            if node.id == "pi":
                return math.pi
            if node.id not in parameters:
                raise ValueError(f"Unknown construction parameter: {node.id}")
            return parameters[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            operand = visit(node.operand)
            return operand if isinstance(node.op, ast.UAdd) else -operand
        if isinstance(node, ast.BinOp):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add): result = left + right
            elif isinstance(node.op, ast.Sub): result = left - right
            elif isinstance(node.op, ast.Mult): result = left * right
            elif isinstance(node.op, ast.Div): result = left / right
            elif isinstance(node.op, ast.Mod): result = left % right
            elif isinstance(node.op, ast.Pow):
                if abs(right) > 16:
                    raise ValueError("Expression exponents must have magnitude at most 16")
                result = left ** right
            else:
                raise ValueError("Only +, -, *, /, %, ** arithmetic is supported")
            return number(result, "expression result")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            if name not in FUNCTIONS or node.keywords:
                raise ValueError("Only sin, cos, sqrt, abs, min and max calls without keywords are allowed")
            if not ((name in {"min", "max"} and 2 <= len(node.args) <= 8)
                    or (name not in {"min", "max"} and len(node.args) == 1)):
                raise ValueError("Math calls need one argument, or 2-8 for min/max")
            return number(FUNCTIONS[name](*(visit(arg) for arg in node.args)), "function result")
        raise ValueError("Only numbers, parameter names, arithmetic and named math calls are allowed")

    try:
        return visit(tree.body)
    except (ArithmeticError, TypeError) as exc:
        raise ValueError(f"Cannot evaluate coordinate expression {value!r}: {exc}") from exc


def build_loft(
    sections: list,
    parameters: dict | None = None,
    *,
    profile_closed: bool = True,
    close_path: bool = False,
    caps: bool = False,
    reverse: bool = False,
) -> tuple[list[list[float]], list[list[int]], dict[str, Any]]:
    """Return initial-world vertices and quads; no implicit correspondence/weld."""
    for key, value in (("profile_closed", profile_closed), ("close_path", close_path),
                       ("caps", caps), ("reverse", reverse)):
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be boolean")
    if caps and (not profile_closed or close_path):
        raise ValueError("caps requires closed profiles and an open path")
    minimum_sections = 3 if close_path else 2
    if not isinstance(sections, list) or not minimum_sections <= len(sections) <= 1000:
        raise ValueError(f"Use {minimum_sections}-1000 ordered cross sections")
    minimum_points = 3 if profile_closed else 2
    if not isinstance(sections[0], list) or not minimum_points <= len(sections[0]) <= 1000:
        raise ValueError(f"Each section needs {minimum_points}-1000 points")
    points_per_section = len(sections[0])
    if len(sections) * points_per_section > 50000:
        raise ValueError("Use at most 50000 loft vertices")
    values = validate_parameters(parameters)
    vertices = []
    normalized = []
    for si, section in enumerate(sections):
        if not isinstance(section, list) or len(section) != points_per_section:
            raise ValueError("All sections must contain the same number of corresponding points")
        points = []
        expressions = []
        for point in section:
            if not isinstance(point, (list, tuple)) or len(point) != 3:
                raise ValueError(f"Section {si + 1} points must contain three coordinates")
            points.append([coordinate(value, values) for value in point])
            expressions.append([value if isinstance(value, str) else number(value, "coordinate") for value in point])
        if profile_closed and points[0] == points[-1]:
            raise ValueError("Closed profiles wrap automatically; do not repeat the first point at the end")
        segment_count = points_per_section if profile_closed else points_per_section - 1
        if any(points[j] == points[(j + 1) % points_per_section] for j in range(segment_count)):
            raise ValueError("A section contains a zero-length segment")
        if vertices and points == vertices[-points_per_section:]:
            raise ValueError("Consecutive sections must not be identical")
        vertices.extend(points)
        normalized.append(expressions)
    if close_path and vertices[:points_per_section] == vertices[-points_per_section:]:
        raise ValueError("Closed paths wrap automatically; do not repeat the first section")
    paths = len(sections) if close_path else len(sections) - 1
    around = points_per_section if profile_closed else points_per_section - 1
    faces = []
    for si in range(paths):
        next_section = (si + 1) % len(sections)
        for j in range(around):
            k = (j + 1) % points_per_section
            faces.append([si * points_per_section + j + 1, si * points_per_section + k + 1,
                          next_section * points_per_section + k + 1, next_section * points_per_section + j + 1])
    if caps:
        faces.append(list(range(points_per_section, 0, -1)))
        faces.append(list(range((len(sections) - 1) * points_per_section + 1, len(vertices) + 1)))
    if reverse:
        faces = [list(reversed(face)) for face in faces]
    if len(faces) > 50000:
        raise ValueError("Use at most 50000 loft faces")
    definition = {"sections": normalized, "profile_closed": profile_closed,
                  "close_path": close_path, "caps": caps, "reverse": reverse}
    return vertices, faces, definition
