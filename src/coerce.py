"""Pydantic-compatible list types that coerce single values into one-element
lists.  Fast / small models often send ``"foo"`` instead of ``["foo"]`` when
calling MCP tools — these annotated types absorb that silently so every tool
Just Works regardless of caller sophistication.

Usage in tool signatures::

    from ..coerce import StrList, FloatList, IntList, DictList

    @mcp.tool()
    def my_tool(names: StrList, color: IntList | None = None) -> str: ...
"""

from __future__ import annotations

import json as _json
from typing import Annotated

from pydantic import BeforeValidator


def _try_json_list(v: str) -> list | None:
    """Try to parse a stringified JSON array like '["a","b"]'."""
    s = v.strip()
    if s.startswith("[") and s.endswith("]"):
        try:
            parsed = _json.loads(s)
            if isinstance(parsed, list):
                return parsed
        except (ValueError, TypeError):
            pass
    return None


def _coerce_str_list(v: object) -> object:
    if isinstance(v, str):
        # '["a","b"]' → ["a", "b"]  (stringified JSON array)
        parsed = _try_json_list(v)
        if parsed is not None:
            return [str(x) for x in parsed]
        # "a,b,c" → ["a", "b", "c"]  (CSV from fast models)
        if "," in v:
            return [s.strip() for s in v.split(",") if s.strip()]
        return [v]
    return v


def _coerce_int_list(v: object) -> object:
    if isinstance(v, str):
        parsed = _try_json_list(v)
        if parsed is not None:
            try:
                return [int(x) for x in parsed]
            except (ValueError, TypeError):
                pass
        # "255,0,0" → [255, 0, 0]
        parts = [s.strip() for s in v.split(",") if s.strip()]
        try:
            return [int(p) for p in parts]
        except ValueError:
            return v  # let Pydantic raise the real error
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return [int(v)]
    return v


def _coerce_float_list(v: object) -> object:
    if isinstance(v, str):
        parsed = _try_json_list(v)
        if parsed is not None:
            try:
                return [float(x) for x in parsed]
            except (ValueError, TypeError):
                pass
        # "1.5,2.0,3.0" → [1.5, 2.0, 3.0]
        parts = [s.strip() for s in v.split(",") if s.strip()]
        try:
            return [float(p) for p in parts]
        except ValueError:
            return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return [float(v)]
    return v


def _coerce_dict_list(v: object) -> object:
    if isinstance(v, str):
        parsed = _try_json_list(v)
        if parsed is not None and all(isinstance(x, dict) for x in parsed):
            return parsed
    if isinstance(v, dict):
        return [v]
    return v


StrList = Annotated[list[str], BeforeValidator(_coerce_str_list)]
IntList = Annotated[list[int], BeforeValidator(_coerce_int_list)]
FloatList = Annotated[list[float], BeforeValidator(_coerce_float_list)]
DictList = Annotated[list[dict], BeforeValidator(_coerce_dict_list)]
