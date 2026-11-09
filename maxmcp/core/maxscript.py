"""Shared escaping helpers for building MAXScript strings."""

import json
import re


def safe_string(s: str) -> str:
    """Escape a Python string for embedding in a MAXScript double-quoted string literal.

    Handles backslash and double-quote — the two characters that break
    MAXScript "..." strings.
    """
    return s.replace("\\", "\\\\").replace('"', '\\"')


def safe_name(s: str) -> str:
    """Escape a Python string for use in a MAXScript $'...' name selector.

    Handles backslash, double-quote, and single-quote.
    """
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'")


def safe_value(val: str) -> str:
    """Auto-protect file paths in MAXScript value expressions.

    Detects quoted strings containing backslashes and converts them to
    MAXScript verbatim strings (@"...") so backslashes aren't interpreted
    as escape sequences (e.g. \\t becoming tab).
    """
    v = val.strip()
    if v.startswith('@"'):
        return v
    if v.startswith('"') and v.endswith('"') and '\\' in v:
        return '@' + v
    return val


# ── Node lookup by name ─────────────────────────────────────────
# 3ds Max lets several objects share a name, and getNodeByName then returns
# whichever one it meets first; with its default exact:false it also matches
# names that differ in spaces or underscores. A name therefore resolves only
# when exactly one object carries it: exact-case matches first, and when there
# are none, names that differ only in letter case. The native bridge applies
# the same rule.

def name_literal(name: str) -> str:
    """MAXScript double-quoted string literal for a Python string."""
    return '"' + safe_string(name) + '"'


def node_by_name_expr(name_expr: str, what: str = "Object", *, missing_ok: bool = False) -> str:
    """MAXScript block evaluating to the one node named by `name_expr`.

    `name_expr` is a MAXScript expression yielding the name. Several objects
    carrying the name throw "Ambiguous object name: ..." with their handles.
    No object throws "<what> not found: <name>", or yields undefined with
    missing_ok so the caller keeps its own not-found reply.
    """
    missing = (
        "" if missing_ok
        else f'    if mcpHits.count == 0 do throw ({name_literal(what + " not found: ")} + mcpName)\n'
    )
    return (
        "(\n"
        f"    local mcpName = {name_expr}\n"
        "    local mcpHits = getNodeByName mcpName exact:true ignoreCase:false all:true\n"
        "    if mcpHits.count == 0 do mcpHits = getNodeByName mcpName exact:true ignoreCase:true all:true\n"
        "    if mcpHits.count > 1 do (\n"
        '        local mcpHandles = ""\n'
        '        for mcpHit in mcpHits do mcpHandles += (if mcpHandles == "" then "" else ", ")'
        ' + (formattedPrint ((getHandleByAnim mcpHit) as integer64) format:"d")\n'
        '        throw ("Ambiguous object name: " + mcpName + " (" + (mcpHits.count as string)'
        ' + " objects share it, handles " + mcpHandles + "; pass a handle or rename them)")\n'
        "    )\n"
        f"{missing}"
        "    if mcpHits.count == 1 then mcpHits[1] else undefined\n"
        ")"
    )


def node_by_name(name: str, what: str = "Object", *, missing_ok: bool = False) -> str:
    """MAXScript block evaluating to the one node named `name` (see node_by_name_expr)."""
    return node_by_name_expr(name_literal(name), what, missing_ok=missing_ok)


def name_in_use(name_expr: str) -> str:
    """MAXScript test that any node carries the name, in any letter case."""
    return f"((getNodeByName ({name_expr}) exact:true ignoreCase:true all:true).count > 0)"


def node_script_ref(node_var: str) -> str:
    """MAXScript string expression naming the node in `node_var` for execute().

    execute() code reaches the resolved node by its animatable handle instead
    of a $'name' path, which picks one of several objects sharing the name.
    """
    return (
        f'("(getAnimByHandle " + (formattedPrint ((getHandleByAnim {node_var}) as integer64)'
        ' format:"d") + ")")'
    )


# MAXScript functions for resolving names inside loops. Embed once, inside the
# script's outer block so they stay local to it.
#   mcpUniqueNode nm what   the one node, or throws like node_by_name_expr
#   mcpBatchNode nm missing ambiguous
#                           the one node, or undefined after appending nm to
#                           missing (no object) or #(nm, nodes) to ambiguous
#                           (several objects; each name once)
#   mcpAmbiguousJson ambiguous
#                           JSON for the ambiguous entries:
#                           [{"name", "candidates": [{name, handle, class, layer}]}]
#   mcpAmbiguousReply ambiguous
#                           "" or the tagged JSON a text reply ends with; see
#                           batch_reply
#   mcpThrowUnresolved missing ambiguous consequence
#                           fails a batch that resolved no name while some
#                           were shared; consequence says what was left unchanged
NODE_LOOKUP_FNS = r'''
fn mcpNamedNodes nm = (
    local hits = getNodeByName nm exact:true ignoreCase:false all:true
    if hits.count == 0 do hits = getNodeByName nm exact:true ignoreCase:true all:true
    hits
)
fn mcpHandleText n = formattedPrint ((getHandleByAnim n) as integer64) format:"d"
fn mcpUniqueNode nm what = (
    local hits = mcpNamedNodes nm
    if hits.count > 1 do (
        local handles = ""
        for n in hits do handles += (if handles == "" then "" else ", ") + (mcpHandleText n)
        throw ("Ambiguous object name: " + nm + " (" + (hits.count as string) + " objects share it, handles " + handles + "; pass a handle or rename them)")
    )
    if hits.count == 0 do throw (what + " not found: " + nm)
    hits[1]
)
fn mcpBatchNode nm missing ambiguous = (
    local hits = mcpNamedNodes nm
    if hits.count == 1 then hits[1] else (
        if hits.count == 0 then append missing nm
        else if (for a in ambiguous where a[1] == nm collect a).count == 0 do append ambiguous #(nm, hits)
        undefined
    )
)
fn mcpJsonText s = (
    s = substituteString s "\\" "\\\\"
    s = substituteString s "\"" "\\\""
    s = substituteString s "\n" "\\n"
    s = substituteString s "\r" "\\r"
    s = substituteString s "\t" "\\t"
    "\"" + s + "\""
)
fn mcpAmbiguousJson ambiguous = (
    local out = "["
    for i = 1 to ambiguous.count do (
        if i > 1 do out += ","
        out += "{\"name\":" + (mcpJsonText ambiguous[i][1]) + ",\"candidates\":["
        local hits = ambiguous[i][2]
        for j = 1 to hits.count do (
            local n = hits[j]
            if j > 1 do out += ","
            out += "{\"name\":" + (mcpJsonText n.name) + ",\"handle\":" + (mcpHandleText n) + ",\"class\":" + (mcpJsonText ((classOf n) as string)) + ",\"layer\":" + (mcpJsonText n.layer.name) + "}"
        )
        out += "]}"
    )
    out + "]"
)
fn mcpAmbiguousReply ambiguous = if ambiguous.count == 0 then "" else ("\n__MCP_AMBIGUOUS__:" + (mcpAmbiguousJson ambiguous))
fn mcpThrowUnresolved missing ambiguous consequence = (
    local s = (if missing.count == 0 then "Each requested name is shared by several objects; " else "No requested name matched exactly one object; ") + consequence + ". Ambiguous object names: "
    for i = 1 to ambiguous.count do (
        local handles = ""
        for n in ambiguous[i][2] do handles += (if handles == "" then "" else ", ") + (mcpHandleText n)
        s += (if i == 1 then "" else "; ") + ambiguous[i][1] + " (handles " + handles + ")"
    )
    throw s
)
'''

_AMBIGUOUS_TAG = "\n__MCP_AMBIGUOUS__:"


def split_ambiguous_reply(raw):
    """Split a text reply from the ambiguous-name JSON mcpAmbiguousReply appended."""
    if not isinstance(raw, str) or _AMBIGUOUS_TAG not in raw:
        return raw, []
    text, _, tail = raw.rpartition(_AMBIGUOUS_TAG)
    try:
        ambiguous = json.loads(tail)
    except ValueError:
        return raw, []
    return text, ambiguous if isinstance(ambiguous, list) else []


def batch_reply(raw):
    """A batch fallback's text reply, or a partial result when it skipped shared names."""
    text, ambiguous = split_ambiguous_reply(raw)
    if not ambiguous:
        return text
    return report_ambiguous({"message": text}, ambiguous)


def ambiguous_names_warning(ambiguous: list) -> str:
    """Warning for batch names skipped because several objects share them."""
    names = [str(entry.get("name", "")) if isinstance(entry, dict) else str(entry) for entry in ambiguous]
    shown = ", ".join(names[:10]) + (", ..." if len(names) > 10 else "")
    return (f"{len(ambiguous)} requested name(s) are shared by several objects and were skipped: "
            f"{shown}. See ambiguous for each object's handle.")


def report_ambiguous(result: dict, ambiguous: list) -> dict:
    """Mark a batch result partial for the shared names it skipped, as the native tools do."""
    if ambiguous:
        result["ambiguous"] = ambiguous
        result["status"] = "partial"
        warnings = result.get("warnings")
        if not isinstance(warnings, list):
            warnings = [warnings] if warnings else []
        warnings.append(ambiguous_names_warning(ambiguous))
        result["warnings"] = warnings
    return result


def normalize_subanim_path(path: str) -> str:
    """Normalize a sub-anim path for MAXScript execute() compatibility.

    Sub-anim names from getSubAnimName may contain spaces (e.g. "Z Position",
    "X Rotation") which break MAXScript's execute("$'name'[#Z Position]").
    This normalizes tokens inside [#...] brackets:
      - Spaces → underscores
      - Lowercase

    Examples:
        "[#Transform][#Position][#Z Position]" → "[#transform][#position][#z_position]"
        "baseObject[#radius]" → "baseObject[#radius]"
        "[#transform][#position]" → "[#transform][#position]"
    """
    def _fix_token(m: re.Match) -> str:
        token = m.group(1)
        token = token.replace(" ", "_").lower()
        return f"[#{token}]"

    return re.sub(r'\[#([^\]]+)\]', _fix_token, path)
