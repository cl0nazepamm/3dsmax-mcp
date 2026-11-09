"""The sdk (_mx) reference, read from the module itself: mx.help("sdk...") and the generated docs.

Agents and people read the same text: mx.help("sdk") inside a script, and
docs/mx-sdk-reference.md, which scripts/codegen/gen_mx_reference.py writes from a running Max.
"""

from __future__ import annotations

import difflib
import inspect
import re
from typing import Any

_SIGNATURE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\(.*\)( -> .+)?$")


def _split(doc: str | None) -> tuple[list[str], str]:
    """(signature lines, text) of a nanobind docstring: signatures first, then a blank line."""
    lines = (doc or "").strip().splitlines()
    signatures: list[str] = []
    while lines and _SIGNATURE.match(lines[0].strip()):
        signatures.append(lines.pop(0).strip())
    return signatures, "\n".join(lines).strip()


def _public(names: list[str]) -> list[str]:
    return [name for name in names if not name.startswith("_")]


def _kind(value: Any) -> str:
    if inspect.isclass(value):
        return "exception" if issubclass(value, BaseException) else "class"
    if isinstance(value, property):
        return "property"
    if callable(value):
        return "function"
    return "value"


def _summary(text: str) -> str:
    first = text.strip().split("\n\n")[0].replace("\n", " ")
    return first if len(first) <= 160 else first[:157].rstrip() + "..."


_GETTER = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*)?\(self\) -> (.+)$")


def _property_type(cls: type, name: str, prop: property) -> str:
    for text in (getattr(getattr(cls, f"_get_{name}", None), "__doc__", None),
                 getattr(prop.fget, "__doc__", None), prop.__doc__):
        first = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
        match = _GETTER.match(first)
        if match:
            return match.group(1)
    return ""


def _property_doc(prop: property) -> str:
    """The property's description; a bare getter signature is not one."""
    text = (prop.__doc__ or "").strip()
    return "" if _GETTER.match(text.splitlines()[0] if text else "") else text


def _property_line(cls: type, name: str, prop: property) -> str:
    details = [part for part in (_property_type(cls, name, prop), "settable" if prop.fset is not None else "")
               if part]
    doc = _summary(_property_doc(prop))
    return f"- `.{name}`" + (f" ({', '.join(details)})" if details else "") + (f": {doc}" if doc else "")


def _member_lines(cls: type) -> list[str]:
    lines: list[str] = []
    for name in _public(sorted(vars(cls))):
        value = vars(cls)[name]
        if isinstance(value, property):
            lines.append(_property_line(cls, name, value))
        elif callable(value) and not inspect.isclass(value):
            signatures, text = _split(getattr(value, "__doc__", None))
            shown = re.sub(r"\(self(, )?", "(", signatures[0]) if signatures else f"{name}(...)"
            lines.append(f"- `.{shown}`: {_summary(text)}")
        elif not inspect.isclass(value):
            lines.append(f"- `.{name}` = {value!r}")
    return lines


def _function_block(name: str, fn: Any) -> list[str]:
    signatures, text = _split(getattr(fn, "__doc__", None))
    out = [f"### `{signatures[0] if signatures else name + '(...)'}`"]
    out += [f"`{extra}`  " for extra in signatures[1:]]
    out += ["", text or "(no description)", ""]
    return out


def reference(module: Any, title: str = "sdk") -> str:
    """The whole module as Markdown: functions, classes with their members, errors, values."""
    names = _public(sorted(dir(module)))
    groups: dict[str, list[str]] = {"function": [], "class": [], "exception": [], "value": []}
    for name in names:
        groups[_kind(getattr(module, name))].append(name)
    out = [f"# {title} reference", "", (module.__doc__ or "").strip(), "",
           f"Build `{getattr(module, 'BUILD', '?')}`, 3ds Max SDK {getattr(module, 'MAX_VERSION', '?')}.", ""]
    out += ["## Functions", ""]
    for name in groups["function"]:
        out += _function_block(name, getattr(module, name))
    out += ["## Classes", ""]
    for name in groups["class"]:
        cls = getattr(module, name)
        out += [f"### {name}", "", (cls.__doc__ or "").strip() or "(no description)", ""]
        out += _member_lines(cls) + [""]
    out += ["## Errors", ""]
    out += [f"- `{name}`: {_summary(getattr(module, name).__doc__ or '')}" for name in groups["exception"]] + [""]
    if groups["value"]:
        out += ["## Values", ""] + [f"- `{name}` = {getattr(module, name)!r}" for name in groups["value"]] + [""]
    return "\n".join(out).rstrip() + "\n"


def help_text(module: Any, path: str) -> str:
    """mx.help("sdk"), mx.help("sdk.create"), mx.help("sdk.Node"), mx.help("sdk.Node.pos")."""
    parts = path.split(".")[1:]
    if not parts:
        lines = [(module.__doc__ or "").strip(), "",
                 "mx.help('sdk.<name>') shows one function or class; mx.help('sdk.<Class>.<member>') one member.", ""]
        for name in _public(sorted(dir(module))):
            value = getattr(module, name)
            kind = _kind(value)
            if kind == "function":
                signatures, text = _split(value.__doc__)
                lines.append(f"{signatures[0] if signatures else name}: {_summary(text)}")
            elif kind in ("class", "exception"):
                lines.append(f"{kind} {name}: {_summary(value.__doc__ or '')}")
        return "\n".join(lines)
    owner: Any = module
    for index, part in enumerate(parts):
        if not hasattr(owner, part):
            names = _public(sorted(dir(owner)))
            close = difflib.get_close_matches(part, names, n=1)
            where = ".".join(["sdk"] + parts[:index])
            raise LookupError(f"{'.'.join(['sdk'] + parts[:index + 1])} does not exist"
                              + (f"; did you mean {where}.{close[0]}?" if close else f"; {where} has: {', '.join(names)}"))
        parent, owner = owner, getattr(owner, part)
    name = parts[-1]
    if inspect.isclass(owner):
        return "\n".join([f"class {name}: {(owner.__doc__ or '').strip()}", ""] + _member_lines(owner))
    member = vars(parent).get(name) if inspect.isclass(parent) else None
    if isinstance(member, property):
        kind = _property_type(parent, name, member)
        settable = "settable" if member.fset is not None else "read-only"
        return (f".{name}" + (f" -> {kind}" if kind else "") + f" ({settable})\n\n{_property_doc(member)}").strip()
    signatures, text = _split(getattr(owner, "__doc__", None))
    return "\n".join(signatures + ["", text]).strip() if signatures or text else repr(owner)
