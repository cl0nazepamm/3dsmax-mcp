"""The sdk (_mx) reference, read from the module itself: mx.help("sdk...") and the generated docs.

Agents and people read the same text: mx.help("sdk") inside a script, and
docs/mx-sdk-reference.md, which scripts/codegen/gen_mx_reference.py writes from a running Max.
The same walk checks the module against the API conventions (lint, check); the
generator writes nothing while check() reports a problem.
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


def _brief(text: str, limit: int = 84) -> str:
    """The first sentence of a description, cut at a word when it is longer than `limit`."""
    first = text.strip().split("\n\n")[0].replace("\n", " ")
    sentence = first.split(". ")[0].rstrip(".")
    if len(sentence) <= limit:
        return sentence
    return sentence[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "..."


def _index(module: Any) -> str:
    """What sdk has, one short line each: enough to pick a name, which mx.help then explains."""
    lines = [(module.__doc__ or "").strip(), "",
             "One line per name. mx.help('sdk.<name>') gives a function's signature and manual or a class's "
             "members, mx.help('sdk.<Class>.<member>') one member, mx.help('sdk.*') every signature.", ""]
    names = _public(sorted(dir(module)))
    for title, kind in (("Functions", "function"), ("Classes", "class"), ("Errors", "exception")):
        lines.append(title)
        for name in names:
            value = getattr(module, name)
            if _kind(value) != kind:
                continue
            text = _split(value.__doc__)[1] if kind == "function" else (value.__doc__ or "")
            lines.append(f"  {name}: {_brief(text)}")
    return "\n".join(lines)


def help_text(module: Any, path: str) -> str:
    """mx.help("sdk"), mx.help("sdk.*"), mx.help("sdk.create"), mx.help("sdk.Node"), mx.help("sdk.Node.pos")."""
    parts = path.split(".")[1:]
    if not parts:
        return _index(module)
    if parts == ["*"]:
        lines = [(module.__doc__ or "").strip(), ""]
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


# ── Conventions ──────────────────────────────────────────────────
# native/mx/mx_common.h states the rules every sdk name follows. lint() checks the
# ones a walk over the loaded module can see.

# What every wrapper of a plugin object offers (Node, Modifier, Material, Texmap,
# Controller, Effect, Object and their subclasses). A class binds what is its own; the
# rest is bound once for all of them (native/mx/mx_plugins.cpp, the object protocol).
OBJECT_MEMBERS = ("handle", "exists", "class_name", "class_info", "params", "interfaces", "interface", "references")

# A name has one shape: a property on every class that has it, or a method on every
# one. These are different things that share a word.
HOMONYMS = {
    "edges": "Viewport.edges is the edged-faces display flag; Poly.edges() lists the cage's edges",
    "frame": "Key.frame is a time; Viewport.frame() fits nodes in the view",
    "keys": "Params.keys() is dict's; Track.keys are the animation keys",
    "open": "Group.open is a state; Spline.open() is an edit, with close()",
    "value": "Key.value is the key's own value; Track.value(frame) evaluates the track",
}

# Signatures with an annotation that says nothing about the value: `object`, or a bare
# dict, list or tuple as a result. Value types and typed results replace them. The
# number only goes down: check() fails when the module has more, and when it has
# fewer until this is lowered to match.
UNTYPED_BUDGET = 43

_OBJECT = re.compile(r"\bobject\b")
_BARE_RESULTS = frozenset({"dict", "list", "tuple"})

# A report is a dict whose description ends with its top-level keys: "Keys: {'a', 'b', 'c'?}."
# A ? follows a key that is not always there. Such a dict says what it holds, so it is
# not counted as untyped, and the live sweep (scripts/live/run_sdk_sweep.py) compares the
# list with what the call returns.
_KEYS = re.compile(r"Keys: \{([^{}]*)\}")
_KEY = re.compile(r"'([^']+)'(\?)?")


def report_keys(text: str | None) -> tuple[set[str], set[str]] | None:
    """(keys always there, keys sometimes there) that a description names, or None when it names none."""
    match = _KEYS.search(text or "")
    if not match:
        return None
    required: set[str] = set()
    optional: set[str] = set()
    for name, mark in _KEY.findall(match.group(1)):
        (optional if mark else required).add(name)
    return (required, optional) if required or optional else None


def _untyped(annotations: str, result: str, text: str = "") -> str:
    """Why a signature's annotations are untyped, or "" when they say what the values are."""
    why = []
    count = len(_OBJECT.findall(annotations))
    if count:
        why.append("`object`" + (f" x{count}" if count > 1 else ""))
    bare = result.strip()
    if bare in _BARE_RESULTS and not (bare == "dict" and report_keys(text)):
        why.append(f"a bare {bare} result")
    return " and ".join(why)


def _signature_untyped(signature: str, text: str = "") -> str:
    _, arrow, result = signature.rpartition(" -> ")
    return _untyped(signature, result if arrow else "", text)


def lint(module: Any) -> dict[str, list[str]]:
    """Where the module departs from the conventions, by rule: one line per finding.

    doc: a function, class, property or method without a description.
    object: a plugin-object wrapper (a class with params) that lacks one of OBJECT_MEMBERS.
    shape: a name that is a property on one class and a method on another (HOMONYMS excepted).
    untyped: a signature with an untyped annotation (see UNTYPED_BUDGET). A dict result whose
        description names its keys (report_keys) is a report, not an untyped value.
    """
    found: dict[str, list[str]] = {"doc": [], "object": [], "shape": [], "untyped": []}
    shapes: dict[str, dict[str, list[str]]] = {}

    def note_untyped(where: str, why: str) -> None:
        if why:
            found["untyped"].append(f"{where}: {why}")

    for name in _public(sorted(dir(module))):
        value = getattr(module, name)
        kind = _kind(value)
        if kind == "function":
            signatures, text = _split(value.__doc__)
            if not text:
                found["doc"].append(f"{name}(): no description")
            note_untyped(f"{name}()", _signature_untyped(signatures[0], text) if signatures else "")
        elif kind in ("class", "exception"):
            if not (value.__doc__ or "").strip():
                found["doc"].append(f"{name}: no description")
            if kind == "exception":
                continue
            if hasattr(value, "params"):
                missing = [member for member in OBJECT_MEMBERS if not hasattr(value, member)]
                if missing:
                    found["object"].append(f"{name}: lacks {', '.join(missing)}")
            for member in _public(sorted(vars(value))):
                attribute = vars(value)[member]
                if isinstance(attribute, property):
                    shape = "property"
                    if not _property_doc(attribute):
                        found["doc"].append(f"{name}.{member}: no description")
                    kind_text = _property_type(value, member, attribute)
                    note_untyped(f"{name}.{member}", _untyped(kind_text, kind_text, _property_doc(attribute)))
                elif callable(attribute) and not inspect.isclass(attribute):
                    shape = "method"
                    signatures, text = _split(getattr(attribute, "__doc__", None))
                    if not text:
                        found["doc"].append(f"{name}.{member}(): no description")
                    note_untyped(f"{name}.{member}()", _signature_untyped(signatures[0], text) if signatures else "")
                else:
                    continue
                shapes.setdefault(member, {}).setdefault(shape, []).append(name)
    for member, by_shape in sorted(shapes.items()):
        if len(by_shape) > 1 and member not in HOMONYMS:
            found["shape"].append(f"{member}: a property on {', '.join(by_shape['property'])} and a method on "
                                  f"{', '.join(by_shape['method'])}")
    return found


def check(module: Any) -> list[str]:
    """What has to be fixed before the reference is written; [] when the module follows the conventions."""
    found = lint(module)
    problems = [f"{rule}: {line}" for rule in ("doc", "object", "shape") for line in found[rule]]
    untyped = len(found["untyped"])
    if untyped > UNTYPED_BUDGET:
        problems.append(f"untyped: {untyped} signatures have an untyped annotation, {untyped - UNTYPED_BUDGET} more "
                        f"than UNTYPED_BUDGET allows; lint(module)['untyped'] lists them")
    elif untyped < UNTYPED_BUDGET:
        problems.append(f"untyped: down to {untyped}; lower UNTYPED_BUDGET in maxmcp/mx/_reference.py to match")
    return problems


def write(module: Any, path: str, title: str = "sdk") -> dict[str, Any]:
    """Write the reference to `path`, unless the module breaks a convention: then nothing is written.

    Returns {"build", "chars", "problems"}; problems is check()'s list, empty when the file was written.
    """
    problems = check(module)
    text = "" if problems else reference(module, title)
    if text:
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    return {"build": getattr(module, "BUILD", "?"), "chars": len(text), "problems": problems}
