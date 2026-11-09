"""RailClone 7.3.5 XML style and evaluated-output access."""
from __future__ import annotations

import base64
import re
import xml.etree.ElementTree as ET
from typing import Any

from ..helpers.maxscript import NODE_LOOKUP_FNS
from ..helpers.mesh import integer
from ..server import client, mcp


MAX_XML_CHARS = 4_000_000
TOKEN = re.compile(r"[0-9A-F]{2}(?:-[0-9A-F]{2}){31}")


class RailCloneError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.retryable = code in {"USER_BUSY", "STALE_STYLE"}


def _xml(text: str, root: str) -> ET.Element:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("XML must be a nonempty string")
    if len(text) > MAX_XML_CHARS:
        raise ValueError(f"XML exceeds {MAX_XML_CHARS} characters; partial styles are not accepted")
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.I):
        raise ValueError("XML DTDs and entity declarations are not supported")
    try:
        element = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML: {exc}") from exc
    if element.tag != root:
        raise ValueError(f"Expected <{root}> XML, got <{element.tag}>")
    return element


_FUNCTIONS = r'''
fn rcEncode text = (dotNetClass "System.Convert").ToBase64String ((dotNetClass "System.Text.Encoding").UTF8.GetBytes text)
fn rcDecode text = (dotNetClass "System.Text.Encoding").UTF8.GetString ((dotNetClass "System.Convert").FromBase64String text)
fn rcHash text = (
    local sha = (dotNetClass "System.Security.Cryptography.SHA256").Create()
    local digest = (dotNetClass "System.BitConverter").ToString (sha.ComputeHash ((dotNetClass "System.Text.Encoding").UTF8.GetBytes text))
    sha.Dispose(); digest as string
)
fn rcApi base method = (
    local api = getInterface base #railclone
    if api == undefined do throw "NOT_RAILCLONE: target has no RailClone interface"
    if not isProperty api method do throw "XML_API_UNAVAILABLE: RailClone 7.3.5 or newer is required"
    api
)
fn rcToken obj xml = rcHash ((formattedPrint ((getHandleByAnim obj) as integer64) format:"d") + "|" + (formattedPrint ((getHandleByAnim obj.baseObject) as integer64) format:"d") + "|" + xml)
fn rcResult obj xml token = (
    "RCXML|" + (formattedPrint ((getHandleByAnim obj) as integer64) format:"d") + "|" + (rcEncode obj.name) + "|" + token + "|" + (rcEncode xml)
)
'''


def _target(name: str, handle: int) -> str:
    if not isinstance(name, str) or "\x00" in name:
        raise ValueError("name must be a string without NUL characters")
    integer(handle, "handle", low=0, high=2**63 - 1)
    # Keep Unicode, quotes and newlines out of MAXScript syntax.
    encoded = base64.b64encode(name.encode("utf-8")).decode("ascii")
    code = f'local wantedName = rcDecode "{encoded}"\nlocal obj = undefined\n'
    if handle:
        code += f'obj = getAnimByHandle {handle}\n'
        code += 'if not isValidNode obj do throw "NOT_FOUND: node handle is no longer valid"\n'
        if name:
            code += 'if obj.name != wantedName do throw "NODE_REF_MISMATCH: handle and name disagree"\n'
    elif name:
        code += 'local matches = mcpNamedNodes wantedName\n'
        code += 'if matches.count == 0 do throw "NOT_FOUND: node not found"\n'
        code += 'if matches.count != 1 do throw "AMBIGUOUS: use a handle for duplicate node names"\nobj = matches[1]\n'
    else:
        raise ValueError("Provide a RailClone node name or handle")
    return code


def _run(body: str) -> dict[str, Any]:
    response = client.send_command('(' + _FUNCTIONS + NODE_LOOKUP_FNS + '\ntry (\n' + body +
                                   '\n) catch ("RCERROR|" + (getCurrentException() as string))\n)')
    if response.get("error") or response.get("ok") is False:
        error = response.get("error") or {}
        if isinstance(error, dict):
            raise RailCloneError(error.get("code", "BRIDGE_ERROR"), error.get("message", str(error)))
        raise RailCloneError("BRIDGE_ERROR", str(error))
    raw = response.get("result")
    if isinstance(raw, str) and raw.startswith("RCERROR|"):
        message = raw.split("|", 1)[1]
        match = re.search(r"\b([A-Z][A-Z_]+):", message)
        raise RailCloneError(match[1] if match else "RAILCLONE_ERROR", message)
    if not isinstance(raw, str) or not raw.startswith("RCXML|"):
        raise RailCloneError("INVALID_READBACK", f"RailClone returned no XML readback: {str(raw)[:250]}")
    try:
        _, handle, name, token, xml = raw.split("|", 4)
        return {"handle": int(handle), "name": base64.b64decode(name, validate=True).decode("utf-8"),
                "style_token": token, "xml": base64.b64decode(xml, validate=True).decode("utf-8")}
    except (ValueError, UnicodeError) as exc:
        raise RailCloneError("INVALID_READBACK", "Incomplete XML response; inspect before retrying") from exc


def _style_result(result: dict[str, Any]) -> dict[str, Any]:
    root = _xml(result["xml"], "scene")
    result.update(schema=dict(root.attrib), node_count=sum(1 for _ in root.iter("node")))
    return result


@mcp.tool()
def get_railclone_style(name: str = "", handle: int = 0) -> dict[str, Any]:
    """Read complete RailClone style XML and a token for guarded replacement.

    Requires RailClone 7.3.5+. Target by unique name or handle; both are cross-checked.
    XML is verbatim, including nested graphs and unknown fields. No reconstruction
    or truncation. Pass style_token as set_railclone_style.expected_style.
    Style XML is graph data, not a portable geometry/material asset package.
    """
    result = _run(_target(name, handle) + f'''
local api = rcApi obj.baseObject #getXMLStyle
local xml = api.getXMLStyle()
if xml.count > {MAX_XML_CHARS} do throw "XML_TOO_LARGE: style exceeds the XML response limit"
rcResult obj xml (rcToken obj xml)
''')
    return _style_result(result)


@mcp.tool()
def set_railclone_style(
    xml: str,
    expected_style: str,
    name: str = "",
    handle: int = 0,
) -> dict[str, Any]:
    """Replace a RailClone graph using setXMLStyle and return actual XML readback.

    Read first; expected_style is the returned style_token, checked in Max before
    applying. Requires well-formed <scene> XML. RailClone's Ok means XML acceptance,
    not graph/geometry correctness; verify generated results with get_railclone_output.
    setXMLStyle itself is not undoable, so the edit is staged on a copy of the
    RailClone base and adopted in one undo step. Node identity, transform, material
    and modifier stack stay intact; base identity changes. Instanced bases and
    master/slave style links are refused. Source geometry is not created here.
    Rejected styles never replace the original base. Maximum XML: 4 million chars.
    """
    _xml(xml, "scene")
    if not isinstance(expected_style, str) or TOKEN.fullmatch(expected_style) is None:
        raise ValueError("expected_style must be the style_token from get_railclone_style")
    target = _target(name, handle)
    encoded = base64.b64encode(xml.encode("utf-8")).decode("ascii")
    result = _run(target + f'''
if theHold.Holding() or theHold.IsSuspended() do throw "USER_BUSY: an undo operation is active"
local base = obj.baseObject
local api = rcApi base #setXMLStyle
rcApi base #getXMLStyle
local before = api.getXMLStyle()
if rcToken obj before != "{expected_style}" do throw "STALE_STYLE: RailClone changed; read the style again"
for n in objects where n != obj do (
    if n.baseObject == base do throw "SHARED_BASE: make the RailClone base unique before replacing its style"
    if isProperty n.baseObject #stylelink do (
        if n.baseObject.stylelink == obj do throw "SHARED_STYLE: this RailClone is a style master"
    )
)
if isProperty base #stylelink do (
    if base.stylelink != undefined do throw "SHARED_STYLE: this RailClone uses a linked master style"
)
local staged = undefined
local readback = undefined
with undo off (
    staged = copy base
    local stagedApi = rcApi staged #setXMLStyle
    local inputXml = rcDecode "{encoded}"
    local status = stagedApi.setXMLStyle inputXml
    if status != "Ok" do throw ("STYLE_REJECTED: " + (status as string))
    readback = stagedApi.getXMLStyle()
)
if readback == undefined or readback.count == 0 do throw "INVALID_READBACK: RailClone returned an empty style"
if readback.count > {MAX_XML_CHARS} do throw "XML_TOO_LARGE: resulting style exceeds the XML response limit"
local doc = dotNetObject "System.Xml.XmlDocument"
doc.XmlResolver = undefined
doc.LoadXml readback
if doc.DocumentElement.Name != "scene" do throw "INVALID_READBACK: expected scene XML"
if theHold.Holding() or theHold.IsSuspended() do throw "USER_BUSY: an undo operation is active"
if obj.baseObject != base or rcToken obj (api.getXMLStyle()) != "{expected_style}" do throw "STALE_STYLE: RailClone changed during staging"
local ownsHold = false
local result = undefined
try (
    theHold.Begin(); ownsHold = true
    obj.baseObject = staged
    -- Recompile with the owning node's transform. A detached base evaluates
    -- spline references against identity and otherwise double-applies placement.
    -- Only the new base is changed; cancelling still restores the original base.
    local adoptedApi = rcApi obj.baseObject #setXMLStyle
    local adoptedXml = readback
    local adoptedStatus = adoptedApi.setXMLStyle adoptedXml
    if adoptedStatus != "Ok" do throw ("STYLE_REJECTED: " + (adoptedStatus as string))
    local actual = (rcApi obj.baseObject #getXMLStyle).getXMLStyle()
    if actual != readback do throw "READBACK_MISMATCH: adopted style differs from staged style"
    result = rcResult obj actual (rcToken obj actual)
    theHold.Accept "MCP RailClone XML style"; ownsHold = false
) catch (
    if ownsHold do theHold.Cancel()
    throw()
)
result
''')
    result = _style_result(result)
    result.update(status="Ok", undoable=True, base_replaced=True)
    return result


@mcp.tool()
def get_railclone_output(
    name: str = "",
    handle: int = 0,
    offset: int = 0,
    limit: int = 100,
    include_xml: bool = False,
) -> dict[str, Any]:
    """Inspect RailClone getXMLOutput at the current frame, without rendering.

    Returns paged item attributes verbatim: index, source_segment, instanced, tm,
    box, tags, plus future fields. Matrix/bounds strings keep RailClone's native
    convention; no coordinate conversion is inferred. offset is a zero-based row
    offset. total and truncated describe pagination. include_xml adds the full,
    unpaginated XML. Each call reevaluates output; pages are not a snapshot.
    Empty output is valid. Complete XML is capped at 4 million characters.
    """
    integer(offset, "offset", low=0, high=2**31 - 1)
    integer(limit, "limit", high=10000)
    if type(include_xml) is not bool:
        raise ValueError("include_xml must be boolean")
    result = _run(_target(name, handle) + f'''
local api = rcApi obj.baseObject #getXMLOutput
local xml = api.getXMLOutput()
if xml.count > {MAX_XML_CHARS} do throw "XML_TOO_LARGE: output exceeds the XML response limit"
rcResult obj xml ""
''')
    root = _xml(result["xml"], "RailClone")
    items = root.findall("item")
    result.pop("style_token")
    if not include_xml:
        result.pop("xml")
    end = min(offset + limit, len(items))
    result.update(schema=dict(root.attrib), total=len(items), offset=offset,
                  items=[dict(item.attrib) for item in items[offset:end]],
                  truncated=end < len(items), next_offset=end if end < len(items) else None)
    return result
