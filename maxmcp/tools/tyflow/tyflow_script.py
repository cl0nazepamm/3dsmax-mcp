"""tyFlow Script operator (C#): API, compile check, apply and run with feedback.

tyFlow compiles a Script operator's C# with Roslyn the first time the operator
simulates, and only then: changing its `script` property later is ignored until
the code is re-evaluated in tyFlow's editor. tyFlow also swallows compile
failures and runtime exceptions without a message. This tool closes the loop
for agents:

- check: compiles the code against the installed tyFlow's own script API
  (assets/tyflow_checker/tyflow_script_checker.cs, compiled once per Max session) and returns
  diagnostics with line numbers. Nothing runs.
- set: refuses code with errors, then applies it. An operator that already
  compiled is replaced in place (same name, position, settings and wiring) so
  tyFlow compiles the new code.
- run: resets and simulates, then reports whether tyFlow compiled the operator,
  Print() output from the listener, and exceptions the callbacks threw.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ...assets import ASSETS
from ...core.maxscript import NODE_LOOKUP_FNS, node_by_name, safe_string
from ...core.max_client import CALL_STATE_ERRORS
from ...server import client, mcp
from ._tyflow_core import (
    MAXSCRIPT_ERROR,
    canon_name,
    refresh_ledger_after_mutation,
    scene_graph,
)

_CHECKER_DIR = ASSETS / "tyflow_checker"
_CHECKER_SOURCE = (_CHECKER_DIR / "tyflow_script_checker.cs").read_text(encoding="utf-8")
_CHECKER_GLOBAL = "mcpTyScriptChecker_" + hashlib.sha1(_CHECKER_SOURCE.encode("utf-8")).hexdigest()[:10]
# The same checker prebuilt with its own Roslyn (netstandard2.0), for 3ds Max versions
# without CSharpUtilities.CSharpCompilationHelper; rebuild from tyflow_checker/*.csproj.
_PREBUILT_CHECKER = _CHECKER_DIR / "lib" / "McpTyScriptChecker.dll"
_BOOTSTRAP_FLOW = "zzz_mcp_script_bootstrap"
_NO_SCRIPT_LOADED = "No compiled tyFlow script is loaded"
_MAX_OUTPUT_LINES = 200


class ScriptToolError(Exception):
    """A refusal with the details an agent needs to correct the request."""

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details


def _checker_prelude() -> str:
    """MAXScript that compiles the checker once per Max session (keyed by its source)."""
    return f"""
global {_CHECKER_GLOBAL}
if {_CHECKER_GLOBAL} == undefined do (
    local root = (getDir #maxroot) + "bin\\\\assemblies\\\\"
    local helper = try (dotNetClass "CSharpUtilities.CSharpCompilationHelper") catch undefined
    local asm = if helper != undefined and doesFileExist (root + "Microsoft.CodeAnalysis.CSharp.dll") then (
        helper.Compile "{safe_string(_CHECKER_SOURCE)}" #(root + "Microsoft.CodeAnalysis.dll", root + "Microsoft.CodeAnalysis.CSharp.dll")
    ) else (
        -- Max 2023-2025 ship neither the helper nor Roslyn: load the prebuilt checker.
        (dotNetClass "System.Reflection.Assembly").LoadFrom "{safe_string(str(_PREBUILT_CHECKER))}"
    )
    {_CHECKER_GLOBAL} = asm.GetType "McpTyScriptChecker"
)
fn mcpTyChecker methodName args = (({_CHECKER_GLOBAL}.GetMethod methodName).Invoke undefined args)
"""


def _run(maxscript: str) -> str:
    response = client.send_command(maxscript)
    raw = str(response.get("result", ""))
    if raw.startswith(MAXSCRIPT_ERROR):
        raise ScriptToolError("MAXScript error: " + raw[len(MAXSCRIPT_ERROR):].strip())
    if raw.startswith("__ERROR__|"):
        raise ScriptToolError(raw.split("|", 1)[1])
    return raw


def _checker(method: str, *args: str) -> Any:
    literal = "#(" + ", ".join(f'"{safe_string(arg)}"' for arg in args) + ")"
    raw = _run(f"(\n{_checker_prelude()}\nmcpTyChecker \"{method}\" {literal}\n)")
    return json.loads(raw)


def _bootstrap_script_assembly() -> None:
    """Have tyFlow compile one default script so its API assembly is loaded.

    Two round trips: tyFlow's Qt cleanup must not run inside the program that
    simulated the flow.
    """
    from .tyflow import HELPERS  # lazy: avoids an import cycle

    # The flow is deleted in the second round trip even when tyFlow refused the
    # operators, so a failed bootstrap leaves nothing in the scene.
    status = _run(f"""(
{HELPERS}
local flow = tyFlow name:"{_BOOTSTRAP_FLOW}" pos:[0,0,-100000]
local eh = flow.tyFlow.addEvent()
local addError = ""
try (mcpTyAddOperator eh "Birth" -1; mcpTyAddOperator eh "Script" -1) catch (addError = mcpTyThrown())
if addError == "" do (
    flow.baseobject.reset_simulation()
    flow.updateParticles 1
)
if addError == "" then "OK" else ("__ADD_FAILED__|" + addError)
)""")
    _run(f"""(
{NODE_LOOKUP_FNS}
for n in (mcpNamedNodes "{_BOOTSTRAP_FLOW}") do (try (delete n) catch ())
"OK"
)""")
    if status.startswith("__ADD_FAILED__|"):
        raise ScriptToolError(
            "Could not load tyFlow's script API: the bootstrap flow needs a Birth and a Script operator. "
            + status.split("|", 1)[1]
        )


def _checker_with_api(method: str, *args: str) -> Any:
    result = _checker(method, *args)
    if isinstance(result, dict) and _NO_SCRIPT_LOADED in str(result.get("error", "")):
        _bootstrap_script_assembly()
        result = _checker(method, *args)
    return result


def _script_operator(name: str, event: str, operator: str) -> dict[str, Any]:
    """The Script operator as saved: uid, anim handle, outgoing edge."""
    scene = scene_graph(name)
    ev_name, op_name = canon_name(event), canon_name(operator)
    event_entry = next((ev for ev in scene["events"] if ev["name"] == ev_name), None)
    if event_entry is None:
        raise ScriptToolError(f"Event not found: {event}")
    matches = [op for op in event_entry["operators"] if op["name"] == op_name]
    if not matches:
        raise ScriptToolError(f"Operator not found: {operator} in {event}")
    if len(matches) > 1:
        raise ScriptToolError(f"{len(matches)} operators in {event} are named {operator}; rename one first")
    op = matches[0]
    if op.get("type") != "Script":
        raise ScriptToolError(f"{operator} is a {op.get('type')} operator, not a Script operator")
    edge = next(
        (e for e in scene["edges"] if e["from_event"] == ev_name and e["from_op"] == op_name), None
    )
    return {"uid": str(op["uid"]), "enabled": op.get("enabled"), "target": edge["to_event"] if edge else None}


def _operator_ref(name: str, event: str, operator: str) -> str:
    """MAXScript locals flow/ev/op for the operator (SubAnim values)."""
    from .tyflow import HELPERS  # lazy: avoids an import cycle

    return f"""{HELPERS}
local flow = {node_by_name(name, missing_ok=True)}
if flow == undefined do throw "Object not found: {safe_string(name)}"
local ev = findEventSubAnim flow "{safe_string(event)}"
if ev == undefined do throw "Event not found: {safe_string(event)}"
local op = findOperatorSubAnim ev "{safe_string(operator)}"
if op == undefined do throw "Operator not found: {safe_string(operator)}"
"""


_JSON_HELPERS = r"""
fn mcpJs s = (
    local t = s as string
    t = substituteString t "\\" "\\\\"
    t = substituteString t "\"" "\\\""
    t = substituteString t "\n" "\\n"
    t = substituteString t "\r" ""
    t = substituteString t "\t" "\\t"
    "\"" + t + "\""
)
fn mcpJoin items = (
    local s = ""
    for i = 1 to items.count do (if i > 1 do s += ","; s += items[i])
    s
)
"""


def _read_operator(name: str, event: str, operator: str) -> dict[str, Any]:
    """Code, script objects and texmaps, enabled floats and settings, as JSON."""
    raw = _run(f"""(
{_operator_ref(name, event, operator)}
{_JSON_HELPERS}
local objs = for n in op.objectList collect (if isValidNode n then mcpJs n.name else "null")
local texs = for m in op.texMapList collect (if m != undefined then mcpJs m.name else "null")
local floats = #()
for i = 1 to 15 do (
    local slot = formattedPrint i format:"02d"
    if (getProperty op (("floatsEnable" + slot) as name)) do (
        append floats ("{{\\"slot\\":" + (i as string) + ",\\"name\\":" + (mcpJs (getProperty op (("floatsName" + slot) as name))) + ",\\"value\\":" + ((getProperty op (("floatsValue" + slot) as name)) as string) + "}}")
    )
)
local settings = for p in #(#loopProtection, #staticEvaluation, #timingPostPhysX, #optimizeCustomDataCalls, #displayMarkers) collect ("\\"" + (p as string) + "\\":" + ((getProperty op p) as string))
"{{\\"script\\":" + (mcpJs op.script) + ",\\"objects\\":[" + (mcpJoin objs) + "],\\"texmaps\\":[" + (mcpJoin texs) + "],\\"assemblies\\":" + (mcpJs op.assemblies) + ",\\"floats\\":[" + (mcpJoin floats) + "],\\"settings\\":{{" + (mcpJoin settings) + "}}}}"
)""")
    return json.loads(raw)


def _members(info: dict[str, Any]) -> str:
    """Declarations tyFlow generates for the operator's script objects and texmaps."""
    lines = [f"public tfObj obj{i:03d};" for i in range(1, len(info.get("objects") or []) + 1)]
    lines += [f"public tfTex tex{i:03d};" for i in range(1, len(info.get("texmaps") or []) + 1)]
    return "\n".join(lines)


def _compiled_classes(uid: str) -> list[str]:
    raw = _run(f"(\n{_checker_prelude()}\nmcpTyChecker \"CompiledClasses\" #(\"{uid}\")\n)")
    return [item for item in raw.split("|") if item]


def _simulate(name: str, uid: str, frames: int) -> dict[str, Any]:
    """Reset and simulate to `frames`, recording listener output and callback exceptions."""
    raw = _run(f"""(
{_checker_prelude()}
local flow = {node_by_name(name, missing_ok=True)}
if flow == undefined do throw "Object not found: {safe_string(name)}"
local logPath = (getDir #temp) + "\\\\mcp_tyflow_script_" + (timestamp() as string) + ".log"
local simError = ""
mcpTyChecker "Arm" #()
openLog logPath mode:"w" outputOnly:true
try (flow.baseobject.reset_simulation(); flow.updateParticles {int(frames)}) catch (simError = getCurrentException())
flushLog()
closeLog()
local trapped = mcpTyChecker "Disarm" #()
local text = ""
local files = dotNetClass "System.IO.File"
try (text = files.ReadAllText logPath) catch ()
try (files.Delete logPath) catch ()
local count = 0
try (count = flow.numParticles()) catch ()
local sep = "\\n<<MCP_SPLIT>>\\n"
(count as string) + sep + simError + sep + trapped + sep + text
)""")
    count_tok, sim_error, trapped, text = (raw.split("\n<<MCP_SPLIT>>\n", 3) + ["", "", ""])[:4]
    summary = re.compile(r"^" + re.escape(name) + r": \[frames:")
    lines = [line for line in text.replace("\r", "").split("\n") if line and not summary.match(line)]
    after = _compiled_classes(uid)
    exceptions = [e for e in json.loads(trapped or "[]") if str(e.get("class", "")).startswith(f"TF_OP_script_{uid}_")]
    result: dict[str, Any] = {
        "frames": int(frames),
        "particles": int(count_tok) if count_tok.strip().isdigit() else None,
        "compiled": bool(after),
        "output": lines[:_MAX_OUTPUT_LINES],
        "exceptions": exceptions,
    }
    if len(lines) > _MAX_OUTPUT_LINES:
        result["output_truncated"] = len(lines)
    if sim_error:
        result["simulation_error"] = sim_error
    return result


def _replace_operator(name: str, event: str, operator: str, code: str, target: str | None) -> list[str]:
    """New Script operator at the same position with the old one's settings and
    wiring and the new code; the old one is removed. Returns skipped properties."""
    connect = ""
    if target:
        connect = f"""
local dst = findEventSubAnim flow "{safe_string(target)}"
if dst != undefined do newOp.Operator.connect dst.object"""
    raw = _run(f"""(
{_operator_ref(name, event, operator)}
-- Sub-anim values are addressed by index; inserting the new operator shifts
-- them, so the old operator is held by its object from here on.
local oldObj = op.object
local index = 0
for i = 1 to ev.numsubs do (if ev[i].object == oldObj do index = i)
local enabled = oldObj.Operator.getEnabled()
local newOp = mcpTyAddOperator ev "Script" (index - 1)
-- The code goes in first: tyFlow compiles an operator once, and some settings
-- make it compile on the spot.
newOp.script = "{safe_string(code)}"
local skipped = #()
for p in getPropNames oldObj where p != #script and p != #texmapButton do (
    try (setProperty newOp p (getProperty oldObj p)) catch (
        try (setProperty newOp p ((getProperty oldObj p) as array)) catch (append skipped (p as string))
    )
)
oldObj.Operator.remove()
newOp.Operator.setName "{safe_string(operator)}"
newOp.Operator.setEnabled enabled{connect}
local out = ""
for s in skipped do out += s + "|"
out
)""")
    return [item for item in raw.split("|") if item]


def _authorize(allow_executable: bool) -> None:
    from .tyflow_patch import _authorize_executable  # lazy: avoids an import cycle

    denied = _authorize_executable(allow_executable)
    if denied:
        raise ScriptToolError(denied)


_PRINT_CALL = re.compile(r"\b(Print|PrintAll)\s*\(")
_PERCENT_FORMAT = re.compile(r"^[Pp]\d*$|\{[^}]*:[Pp]\d*\}")


def _call_literals(code: str, open_paren: int) -> list[str]:
    """String literals inside the call whose '(' is at open_paren."""
    literals: list[str] = []
    depth, i = 0, open_paren
    while i < len(code):
        ch = code[i]
        if ch in "\"'":
            verbatim = i > 0 and code[i - 1] == "@"
            j, text = i + 1, []
            while j < len(code):
                if code[j] == "\\" and not verbatim and ch == '"':
                    text.append(code[j:j + 2])
                    j += 2
                    continue
                if code[j] == ch:
                    if verbatim and code[j + 1:j + 2] == ch:
                        text.append(ch)
                        j += 2
                        continue
                    break
                text.append(code[j])
                j += 1
            if ch == '"':
                literals.append("".join(text))
            i = j + 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return literals


def _print_hazards(code: str) -> list[dict[str, Any]]:
    """tyFlow hands Print/PrintAll text to a printf-style formatter: a '%' in it
    makes the C runtime abort 3ds Max (live: two crashes). Literal text is checked;
    text built at run time cannot be."""
    hazards = []
    for match in _PRINT_CALL.finditer(code):
        if any("%" in text or _PERCENT_FORMAT.search(text) for text in _call_literals(code, match.end() - 1)):
            line = code.count("\n", 0, match.start()) + 1
            column = match.start() - (code.rfind("\n", 0, match.start()) + 1) + 1
            hazards.append({
                "severity": "error", "id": "TYFLOW002", "line": line, "column": column,
                "message": f"{match.group(1)} text must not contain '%' (or a P percent format): tyFlow passes it "
                           "to a printf-style formatter and 3ds Max crashes. Write 'pct' or a ratio instead.",
            })
    return hazards


def _with_hazards(check: Any, code: str) -> Any:
    """Add the Print hazards to a checker result."""
    hazards = _print_hazards(code)
    if not hazards or not isinstance(check, dict) or "error" in check:
        return check
    check = dict(check)
    check["diagnostics"] = hazards + list(check.get("diagnostics") or [])
    check["errors"] = int(check.get("errors") or 0) + len(hazards)
    check["ok"] = False
    return check


def _diagnostics_failed(check: dict[str, Any]) -> bool:
    return not check.get("ok") or bool(check.get("error"))


@mcp.tool()
def tyflow_script(
    name: str = "",
    event: str = "",
    operator: str = "",
    action: str = "get",
    code: str = "",
    frames: int = 30,
    allow_executable: bool = False,
) -> str:
    """Write, check and run tyFlow Script operator C# with full error feedback.

    action:
      api   - the installed tyFlow's script API (callbacks, script-class fields and
              helpers like tf, f, eventParticleCount, Print, GetFloat; types such as
              tyFlow, Point3, Matrix3, tfObj). Read it before writing code.
      get   - the operator's code, script objects (obj001...), texmaps (tex001...),
              enabled floats (read with GetFloat(name)) and settings.
      check - compile `code` (or the operator's current code) against tyFlow's API.
              Returns diagnostics with line/column; nothing runs.
      set   - check, then apply `code` to the operator. Needs safe mode off and
              allow_executable=true: the code runs inside 3ds Max. Code with errors
              is refused unchanged. With frames > 0 it also runs (see run).
      run   - reset and simulate the flow to `frames`; returns `compiled` (tyFlow
              compiled this operator), `output` (Print lines), `exceptions` thrown
              by the callbacks (tyFlow itself reports none) and the particle count.
    A script must declare simulationStart(), simulationStep() and
    simulationStepThreaded(int startInx, int endInx, int threadInx);
    postSimulationStep() is optional. Never put '%' in Print/PrintAll text:
    tyFlow formats it printf-style and 3ds Max crashes (check refuses literals).
    Edit until check is clean and run shows compiled=true with no exceptions.
    """
    action_key = action.strip().lower()
    try:
        if action_key == "api":
            result = _checker_with_api("Api")
            if "error" in result:
                return json.dumps(result)
            return json.dumps({"base": result.get("base"), "api": result.get("api")})

        if not (name and event and operator):
            raise ScriptToolError("name, event and operator are required for this action")

        if action_key == "get":
            # The graph names the operator's type, so a non-Script operator is refused clearly.
            target = _script_operator(name, event, operator)
            info = _read_operator(name, event, operator)
            info["object_names"] = {f"obj{i:03d}": n for i, n in enumerate(info.pop("objects"), start=1)}
            info["texmap_names"] = {f"tex{i:03d}": n for i, n in enumerate(info.pop("texmaps"), start=1)}
            info["compiled_by_tyflow"] = bool(_compiled_classes(target["uid"]))
            info["sends_to"] = target["target"]
            return json.dumps(info)

        if action_key == "check":
            _script_operator(name, event, operator)
            info = _read_operator(name, event, operator)
            source = code or info["script"]
            check = _checker_with_api("Check", source, _members(info), info.get("assemblies") or "")
            return json.dumps(_with_hazards(check, source))

        if action_key == "set":
            if not code.strip():
                raise ScriptToolError("code is required for set")
            _authorize(allow_executable)
            hazards = _print_hazards(code)
            if hazards:
                # Refused before anything reaches Max: running it would crash Max.
                raise ScriptToolError(
                    "The code would crash 3ds Max; the operator was not changed. Fix the diagnostics and retry.",
                    check={"ok": False, "errors": len(hazards), "warnings": 0, "diagnostics": hazards},
                )
            target = _script_operator(name, event, operator)
            info = _read_operator(name, event, operator)
            check = _checker_with_api("Check", code, _members(info), info.get("assemblies") or "")
            if _diagnostics_failed(check):
                raise ScriptToolError(
                    "The code does not compile; the operator was not changed. Fix the diagnostics and retry.",
                    check=check,
                )
            replaced = bool(_compiled_classes(target["uid"]))
            skipped: list[str] = []
            if replaced:
                skipped = _replace_operator(name, event, operator, code, target["target"])
            else:
                _run(f"""(
{_operator_ref(name, event, operator)}
op.script = "{safe_string(code)}"
"OK"
)""")
            payload: dict[str, Any] = {
                "applied": True,
                "operator_replaced": replaced,
                "check": {"errors": check.get("errors", 0), "warnings": check.get("warnings", 0),
                          "diagnostics": check.get("diagnostics", [])},
            }
            if skipped:
                payload["warnings"] = [f"Settings not copied to the new operator: {', '.join(skipped)}"]
            ledger = refresh_ledger_after_mutation(name)
            if isinstance(ledger, dict) and ledger.get("hash"):
                payload["graph_hash"] = ledger["hash"]
            if frames > 0:
                payload["run"] = _simulate(name, _script_operator(name, event, operator)["uid"], frames)
                _flag_run(payload["run"])
            return json.dumps(payload)

        if action_key == "run":
            target = _script_operator(name, event, operator)
            run = _simulate(name, target["uid"], max(1, int(frames)))
            _flag_run(run)
            return json.dumps(run)

        raise ScriptToolError(f"Unknown action: {action}. Use api, get, check, set or run.")
    except CALL_STATE_ERRORS:
        raise
    except ScriptToolError as exc:
        return json.dumps({"error": str(exc), **exc.details})


def _flag_run(run: dict[str, Any]) -> None:
    """Spell out what went wrong in a run, so the agent reads it before the output."""
    problems = []
    if not run.get("compiled"):
        problems.append(
            "tyFlow did not compile this operator's script; run check and fix every diagnostic"
        )
    if run.get("exceptions"):
        problems.append(
            "The script threw during the simulation (tyFlow stops calling a callback after it throws): "
            + "; ".join(f"{e['method']}: {e['type']}: {e['message']}" for e in run["exceptions"])
        )
    if problems:
        run["problems"] = problems
