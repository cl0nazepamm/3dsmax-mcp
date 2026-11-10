"""Every 3dsmax-mcp tool as a Python function, found by name and imported on first use.

maxmcp/tools holds one folder per subject. In each, the modules without a leading
underscore declare tools with @mcp.tool(), the underscore modules are their private
helpers, and the docstring of __init__.py describes the group. MCP clients see only
run_python and the instance routing tools; scripts call the rest as
mx.tools.<name>(**arguments). The MCP server process and maxmcp.inmax inside 3ds Max
each hold one ToolRegistry, so a call is validated and wrapped the same way in both.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import threading
import types
from difflib import get_close_matches
from functools import wraps
from importlib import import_module
from pathlib import Path
from typing import Annotated, Any, Callable, Iterable, Literal, Union, get_args, get_origin, get_type_hints

TOOLS_DIR = Path(__file__).resolve().parent / "tools"


class InvalidArguments(ValueError):
    """A tool was called with arguments its signature does not take."""

    code = "BAD_PARAM"


def _decorated_tool_name(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """The name an @mcp.tool() function registers under, or None for other functions."""
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        target = decorator.func
        if not (
            isinstance(target, ast.Attribute)
            and target.attr == "tool"
            and isinstance(target.value, ast.Name)
            and target.value.id == "mcp"
        ):
            continue
        for keyword in decorator.keywords:
            if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                if isinstance(keyword.value.value, str):
                    return keyword.value.value
        return node.name
    return None


def module_tools(path: Path) -> tuple[tuple[str, str], ...]:
    """(name, docstring) of each @mcp.tool() function one source file declares, in order, without importing it."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = _decorated_tool_name(node)
            if name:
                found.append((node.lineno, name, ast.get_docstring(node) or ""))
    return tuple((name, doc) for _, name, doc in sorted(found))


def module_tool_names(path: Path) -> tuple[str, ...]:
    """The @mcp.tool() names one source file declares, in order."""
    return tuple(name for name, _ in module_tools(path))


def summary_line(doc: str) -> str:
    """A docstring's first non-empty line."""
    return next((line.strip() for line in doc.splitlines() if line.strip()), "")


_WORD = re.compile(r"[a-z0-9]+")
_STOP_WORDS = frozenset("a an and as at by for from in into is it of on or the to with 3ds max tool tools".split())


def _search_words(text: str) -> set[str]:
    """Lowercase search terms with stop words dropped and a plural 's' trimmed."""
    words = (word[:-1] if len(word) > 3 and word.endswith("s") else word for word in _WORD.findall(text.lower()))
    return {word for word in words if word not in _STOP_WORDS}


_TYPE_NAMES = {"string": "str", "integer": "int", "number": "float", "boolean": "bool", "null": "None"}
_NULL = {"type": "null"}


def _type_text(schema: Any, models: set[str]) -> str:
    if not isinstance(schema, dict) or not schema:
        return "Any"
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        models.add(name)
        return name
    if "enum" in schema:
        return "|".join(repr(value) for value in schema["enum"])
    if "const" in schema:
        return repr(schema["const"])
    for key in ("anyOf", "oneOf"):
        if isinstance(schema.get(key), list):
            variants = schema[key]
            # `X | None = None` reads as X: leaving the argument out already means None.
            if schema.get("default", 0) is None and _NULL in variants and len(variants) > 1:
                variants = [variant for variant in variants if variant != _NULL]
            return "|".join(_type_text(variant, models) for variant in variants)
    kind = schema.get("type")
    if isinstance(kind, list):
        return "|".join(_TYPE_NAMES.get(item, item) for item in kind)
    if kind == "array":
        return f"list[{_type_text(schema.get('items', {}), models)}]"
    if kind == "object":
        extra = schema.get("additionalProperties")
        return f"dict[str,{_type_text(extra, models)}]" if isinstance(extra, dict) and extra else "dict"
    return _TYPE_NAMES.get(kind, "Any")


def _parameters_text(schema: dict[str, Any], models: set[str]) -> str:
    required = set(schema.get("required", []))
    rendered = []
    for name, prop in (schema.get("properties") or {}).items():
        text = f"{name}:{_type_text(prop, models)}"
        if name not in required:
            text += f"={prop.get('default')!r}"
        rendered.append(text)
    return ", ".join(rendered)


def signature_text(fn: Callable[..., Any]) -> str:
    """A tool's call signature on one line, then one line per model its arguments nest.

        (lights:list[LightSpec], renderer:str='current')
        LightSpec={name:str='', kind:'area'|'point', size:LightSize=None}
    """
    from pydantic import TypeAdapter

    try:
        schema = TypeAdapter(fn, config={"arbitrary_types_allowed": True}).json_schema()
    except Exception:  # noqa: BLE001 - a signature pydantic cannot describe still has a Python one
        return str(inspect.signature(fn))
    models: set[str] = set()
    lines = [f"({_parameters_text(schema, models)})"]
    definitions = schema.get("$defs") or {}
    done: set[str] = set()
    while models - done:
        name = sorted(models - done)[0]
        done.add(name)
        lines.append(f"{name}={{{_parameters_text(definitions.get(name) or {}, models)}}}")
    return "\n".join(lines)


def _takes_text(hint: Any) -> bool:
    """Whether a parameter of this type can be meant as a string."""
    if hint is Any or hint is str:
        return True
    origin = get_origin(hint)
    if origin is Annotated:
        return _takes_text(get_args(hint)[0])
    if origin in (Union, types.UnionType):
        return any(_takes_text(arg) for arg in get_args(hint))
    if origin is Literal:
        return any(isinstance(arg, str) for arg in get_args(hint))
    return False


def _validated(fn: Callable[..., Any]) -> Callable[..., Any]:
    """fn with its arguments checked against its signature; a mistake is answered with the signature.

    A list or dict written as JSON text is accepted for a parameter that takes no string, as
    FastMCP accepted it when the tools were called directly.
    """
    from pydantic import ValidationError, validate_call

    checked = validate_call(fn, config={"arbitrary_types_allowed": True})
    try:
        hints = get_type_hints(fn)
    except Exception:  # noqa: BLE001 - an annotation that does not resolve only loses the JSON-text courtesy
        hints = {}
    no_text = frozenset(name for name, hint in hints.items() if name != "return" and not _takes_text(hint))

    @wraps(fn)
    def call(*args: Any, **kwargs: Any) -> Any:
        for name in no_text.intersection(kwargs):
            if isinstance(kwargs[name], str):
                try:
                    parsed = json.loads(kwargs[name])
                except ValueError:
                    continue
                if isinstance(parsed, (list, dict)):
                    kwargs[name] = parsed
        try:
            return checked(*args, **kwargs)
        except ValidationError as exc:
            if exc.title not in (fn.__name__, fn.__qualname__):  # raised inside the tool, not by its arguments
                raise
            problems = "; ".join(
                f"{'.'.join(map(str, error['loc'])) or 'arguments'}: {error['msg']}" for error in exc.errors())
            raise InvalidArguments(
                f"Invalid arguments for {fn.__name__}: {problems}. "
                f"Signature: {fn.__name__}{signature_text(fn)}") from None

    return call


class ToolRegistry:
    """Tool name -> function, imported from maxmcp/tools the first time it is needed.

    call() validates the arguments against the function's signature and returns the
    ToolEnvelope dict every tool call produces. Inside 3ds Max the registry also stands in
    for FastMCP, so tool modules decorate with @mcp.tool() in both processes.
    """

    def __init__(
        self,
        client: Any,
        *,
        skip: Iterable[str] = (),
        tools_dir: Path = TOOLS_DIR,
        package: str = "maxmcp.tools",
    ) -> None:
        self.client = client
        self.functions: dict[str, Callable[..., Any]] = {}
        self._skip = frozenset(skip)
        self._tools_dir = tools_dir
        self._package = package
        self._index: dict[str, str] | None = None
        self._docs: dict[str, str] = {}
        self._groups: dict[str, tuple[str, tuple[str, ...]]] = {}
        self._wrapped: dict[str, Callable[..., dict[str, Any]]] = {}
        self._lock = threading.RLock()

    def add(self, fn: Callable[..., Any], name: str | None = None) -> Callable[..., Any]:
        self.functions[name or fn.__name__] = fn
        return fn

    def tool(self, *args: Any, **kwargs: Any) -> Any:
        """@mcp.tool() and @mcp.tool(name=...)."""
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return self.add(args[0])
        return lambda fn: self.add(fn, kwargs.get("name"))

    def resource(self, *args: Any, **kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """MCP resources and prompts exist only in the MCP server process."""
        return lambda fn: fn

    prompt = resource

    @property
    def index(self) -> dict[str, str]:
        """Tool name -> the module that declares it ("modeling.booleans"), read from source."""
        with self._lock:
            if self._index is None:
                index: dict[str, str] = {}
                for folder in sorted(self._tools_dir.iterdir()):
                    init = folder / "__init__.py"
                    if folder.name.startswith("_") or not init.is_file():
                        continue
                    names: list[str] = []
                    for path in sorted(folder.glob("*.py")):
                        if path.stem.startswith("_") or path.stem in self._skip:
                            continue
                        for name, doc in module_tools(path):
                            if name in index:
                                raise RuntimeError(
                                    f"Tool {name!r} is declared by both {index[name]!r} and {folder.name}.{path.stem}")
                            index[name] = f"{folder.name}.{path.stem}"
                            self._docs[name] = doc
                            names.append(name)
                    if names:
                        description = ast.get_docstring(ast.parse(init.read_text(encoding="utf-8"))) or ""
                        self._groups[folder.name] = (" ".join(description.split()), tuple(names))
                self._index = index
            return self._index

    def groups(self) -> dict[str, tuple[str, tuple[str, ...]]]:
        """Group name -> (what the group covers, its tool names); one group per folder of maxmcp/tools."""
        self.index
        return self._groups

    def summary(self, name: str) -> str:
        """The first line of a tool's manual, read from source."""
        self.index
        return summary_line(self._docs.get(name, ""))

    def search(self, query: str) -> list[str]:
        """Tool names ranked by how well their name, manual and group match the words of `query`."""
        terms = _search_words(query)
        scored: list[tuple[int, str]] = []
        for group, (description, names) in self.groups().items():
            group_words = _search_words(f"{group.replace('_', ' ')} {description}")
            for name in names:
                name_words = _search_words(name.replace("_", " "))
                doc_words = _search_words(self._docs[name])
                score = 0
                for term in terms:
                    if term in name_words:
                        score += 4
                    elif len(term) >= 4 and any(
                            word.startswith(term) or term.startswith(word) for word in name_words if len(word) >= 4):
                        score += 2
                    score += (term in doc_words) + (term in group_words)
                if score:
                    scored.append((-score, name))
        return [name for _, name in sorted(scored)]

    def function(self, name: str) -> Callable[..., Any] | None:
        """The tool's own function, importing its module if this is its first use."""
        module = self.index.get(name)
        if module is None:
            return None
        if name not in self.functions:
            import_module(f"{self._package}.{module}")
        return self.functions.get(name)

    def _tool(self, name: str) -> Callable[..., dict[str, Any]] | None:
        with self._lock:
            if name not in self._wrapped:
                fn = self.function(name)
                if fn is None:
                    return None
                from .core.tool_response import make_structured_tool

                self._wrapped[name] = make_structured_tool(
                    _validated(fn),
                    before_call=self.client.clear_last_response,
                    transport_provider=self.client.get_last_transport,
                    # The routing tools pick the target, so they take no expected_pid.
                    pid_guard=not self.index[name].endswith(".routing"),
                )
            return self._wrapped[name]

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run one tool and return its ToolEnvelope dict."""
        wrapped = self._tool(name)
        if wrapped is None:
            close = get_close_matches(name, list(self.index), n=3, cutoff=0.6)
            message = f"Unknown tool {name!r}." + (f" Did you mean {', '.join(close)}?" if close else "")
            return {"ok": False, "error": {"type": "UnknownTool", "code": "NOT_FOUND", "message": message,
                                           "retryable": False}}
        return wrapped(**(arguments or {}))

    __call__ = call

    def describe(self, name: str) -> tuple[str, str, inspect.Signature] | None:
        """(signature text, manual, Python signature) of one tool, or None for an unknown name."""
        fn = self.function(name)
        if fn is None:
            return None
        return signature_text(fn), inspect.getdoc(fn) or "", inspect.signature(fn)

    def names(self) -> list[str]:
        return sorted(self.index)
