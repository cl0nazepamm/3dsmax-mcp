"""A function's type hints on every Python the tool layer runs on.

The MCP server runs Python 3.13, and inside 3ds Max the same tool code runs on the Python
that Max embeds, down to 3.9 in 3ds Max 2023. There `X | None` and `list[int] | None` in an
annotation do not evaluate; eval_type_backport rewrites them, as pydantic does for the
same annotations when it validates a call.
"""

from __future__ import annotations

import sys
import types
import typing
from typing import Any, Callable, ForwardRef, Union

# What typing.get_origin returns for a union: also types.UnionType once `X | Y` exists.
UNION_ORIGINS: tuple[Any, ...] = (Union, types.UnionType) if hasattr(types, "UnionType") else (Union,)


def type_hints(fn: Callable[..., Any]) -> dict[str, Any]:
    """typing.get_type_hints(fn, include_extras=True)."""
    try:
        return typing.get_type_hints(fn, include_extras=True)
    except TypeError:
        if sys.version_info >= (3, 10):
            raise
    from eval_type_backport import eval_type_backport

    target = fn
    while hasattr(target, "__wrapped__"):
        target = target.__wrapped__
    namespace = getattr(target, "__globals__", {})
    hints: dict[str, Any] = {}
    for name, value in dict(getattr(fn, "__annotations__", {})).items():
        if value is None:
            value = type(None)
        elif isinstance(value, str):
            value = ForwardRef(value, is_argument=name != "return")
        hints[name] = eval_type_backport(value, namespace, None)
    return hints
