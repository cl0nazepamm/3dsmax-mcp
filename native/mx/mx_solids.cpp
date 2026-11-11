// Booleans, quad lofts and vertex edits on Editable Poly cages.
//
// The Boolean modifier publishes its operand functions to MAXScript only (no
// SDK header), so operand work runs as MAXScript on handles resolved here.
// Lofts keep the MCP loft_mesh tool's AppData format and its MAXScript cage
// fingerprint, so a loft made by either side stays updatable from both. Vertex
// reads and writes use the Editable Poly SDK: EPoly::ApplyDelta, as
// polyop.setVert does, so edits join a caller's undo hold and key in animate
// mode.
#include "mx_construction.h"

#include <iEPoly.h>
#include <mnmesh.h>

#include <array>
#include <charconv>
#include <limits>
#include <map>
#include <set>

namespace mx {
inline namespace MX_ABI_TAG {
namespace {

using json = nlohmann::json;

// ── Shared ───────────────────────────────────────────────────────

std::vector<std::string> Split(const std::string& text, char sep) {
    std::vector<std::string> parts;
    size_t start = 0;
    for (size_t at; (at = text.find(sep, start)) != std::string::npos; start = at + 1) parts.push_back(text.substr(start, at - start));
    parts.push_back(text.substr(start));
    return parts;
}

std::string Replace(std::string text, const std::string& from, const std::string& to) {
    const size_t at = text.find(from);
    return at == std::string::npos ? text : text.replace(at, from.size(), to);
}

std::string PyText(nb::handle v) { return nb::cast<std::string>(nb::str(v)); }

bool IsGeometry(INode* node) {
    const ObjectState os = node->EvalWorldState(Now());
    return os.obj && os.obj->SuperClassID() == GEOMOBJECT_CLASS_ID;
}

// ── Booleans ─────────────────────────────────────────────────────

const Class_ID kBooleanModClass(2076314029UL, 29076573UL);  // MAXScript's BooleanMod
constexpr long long kMaxCutters = 200;

struct BooleanRef : ModifierRef {};

BooleanRef MakeBoolean(AnimHandle node, AnimHandle mod) {
    BooleanRef r;
    r.node = node;
    r.mod = mod;
    return r;
}

struct OperandInfo {
    int index = 0;
    std::string name, kind, operation, option;
    bool disabled = false;
};

struct OpToken { const char* token; const char* value; };

const OpToken kOperations[] = {
    {"union", "union"}, {"add", "union"}, {"subtract", "subtraction"}, {"subtraction", "subtraction"},
    {"difference", "subtraction"}, {"intersect", "intersection"}, {"intersection", "intersection"},
    {"merge", "merge"}, {"attach", "attach"}, {"insert", "insert"}, {"split", "split"}};

// The MAXScript operationType name for an agent token: 'subtract' -> subtraction.
std::string OperationEnum(const std::string& token) {
    const std::string key = Lower(Trim(token));
    std::vector<std::string> names;
    for (const OpToken& op : kOperations) {
        if (key == op.token) return op.value;
        names.push_back(op.token);
    }
    throw BadParam("unknown boolean operation '" + token + "'." + Suggest(token, names) +
                   " Use union, subtract, intersect, merge, attach, insert or split");
}

std::string OptionEnum(const std::string& token) {
    const std::string key = Lower(Trim(token));
    if (key.empty() || key == "none") return "none";
    if (key == "imprint" || key == "cookie") return key;
    throw BadParam("option must be 'imprint', 'cookie' or '' (none), not '" + token + "'");
}

// An inline cutter: a scratch primitive built and appended in the same call.
struct Cutter {
    std::string name;
    int shape = 1;          // the creation script's codes: 1 box, 2 cylinder, 3 sphere
    int segments = 32;
    Point3 size, pos, rot;  // rot in degrees; pos is the bounding box center
    std::string operation;
};

struct Repeat {
    long long count = 1;
    Point3 axis{1, 0, 0};
    float spacing = 0;
};

void CheckKeys(const nb::dict& d, const std::vector<std::string>& allowed, const std::string& what) {
    for (auto [key, value] : d) {
        const std::string name = PyText(key);
        if (std::find(allowed.begin(), allowed.end(), name) == allowed.end())
            throw BadParam(what + " has no key '" + name + "'." + Suggest(name, allowed) + " Keys: " + Join(allowed));
    }
}

// (x, y, z), or one number for all three.
Point3 Triple(nb::handle v, const std::string& what) {
    Point3 p;
    if (IsNumber(v)) p = Point3(1, 1, 1) * nb::cast<float>(v);
    else if (IsSequence(v)) p = ToPoint3(v, what);
    else throw BadParam(what + " must be a number or (x, y, z)");
    if (!std::isfinite(p.x) || !std::isfinite(p.y) || !std::isfinite(p.z)) throw BadParam(what + " must be finite");
    return p;
}

Repeat ParseRepeat(nb::handle value) {
    if (!nb::isinstance<nb::dict>(value)) throw BadParam("repeat must be a dict: {'count': 4, 'axis': 'x', 'spacing': 20}");
    const nb::dict d = nb::borrow<nb::dict>(value);
    CheckKeys(d, {"count", "axis", "spacing"}, "repeat");
    Repeat r;
    const nb::object count = d.get("count", nb::none());
    if (!IsInteger(count)) throw BadParam("repeat['count'] must be an integer");
    r.count = nb::cast<long long>(count);
    if (r.count < 1) throw BadParam("repeat['count'] must be >= 1");
    const std::string axis = Lower(Trim(PyText(d.get("axis", nb::str("x")))));
    const std::pair<const char*, Point3> axes[] = {{"x", {1, 0, 0}}, {"y", {0, 1, 0}}, {"z", {0, 0, 1}},
                                                   {"-x", {-1, 0, 0}}, {"-y", {0, -1, 0}}, {"-z", {0, 0, -1}}};
    const auto found = std::find_if(std::begin(axes), std::end(axes), [&](const auto& a) { return axis == a.first; });
    if (found == std::end(axes)) throw BadParam("repeat['axis'] must be x, y, z, -x, -y or -z");
    r.axis = found->second;
    const nb::object spacing = d.get("spacing", nb::float_(0.0));
    if (!IsNumber(spacing)) throw BadParam("repeat['spacing'] must be a number");
    r.spacing = nb::cast<float>(spacing);
    if (!std::isfinite(r.spacing) || (r.count > 1 && r.spacing <= 0))
        throw BadParam("repeat['spacing'] must be > 0; flip the direction with a signed axis such as '-x'");
    return r;
}

Cutter ParseCutter(nb::handle item, const std::string& what, const std::string& defaultOperation) {
    if (!nb::isinstance<nb::dict>(item)) throw BadParam(what + " must be a dict");
    const nb::dict d = nb::borrow<nb::dict>(item);
    CheckKeys(d, {"name", "shape", "size", "pos", "rot", "operation", "segments"}, what);
    Cutter c;
    c.name = Trim(PyText(d.get("name", nb::str(""))));
    if (c.name.empty()) throw BadParam(what + " needs a name; it becomes the operand's name");
    const std::string shape = Lower(Trim(PyText(d.get("shape", nb::str("box")))));
    c.shape = shape == "box" ? 1 : shape == "cylinder" ? 2 : shape == "sphere" ? 3 : 0;
    if (!c.shape) throw BadParam(what + " shape must be box, cylinder or sphere");
    const nb::object segments = d.get("segments", nb::int_(c.shape == 2 ? 64 : 32));
    if (!IsInteger(segments) || nb::cast<long long>(segments) < 4 || nb::cast<long long>(segments) > 256)
        throw BadParam(what + " segments must be an integer from 4 to 256");
    c.segments = nb::cast<int>(segments);
    c.size = Triple(d.get("size", nb::none()), what + " size");
    if (c.size.x <= 0 || c.size.y <= 0 || c.size.z <= 0) throw BadParam(what + " size values must be > 0");
    c.pos = Triple(d.get("pos", nb::int_(0)), what + " pos");
    c.rot = Triple(d.get("rot", nb::int_(0)), what + " rot");
    const std::string operation = Trim(PyText(d.get("operation", nb::str(""))));
    c.operation = operation.empty() ? defaultOperation : OperationEnum(operation);
    return c;
}

// Cutter specs expanded by repeat: instance k of 'Slot' is 'Slot_<k>', moved
// spacing * (k - 1) along the axis.
std::vector<Cutter> ParseCutters(nb::handle cutters, nb::handle repeat, const std::string& defaultOperation) {
    if (!IsSequence(cutters)) throw BadParam("cutters must be a list of dicts");
    const Repeat rep = repeat.is_none() ? Repeat{} : ParseRepeat(repeat);
    const long long total = rep.count > kMaxCutters ? rep.count : rep.count * (long long)nb::len(cutters);
    if (total > kMaxCutters) throw BadParam("cutters and repeat expand to " + std::to_string(total) + " cutters; the limit is 200");
    std::vector<Cutter> out;
    for (nb::handle item : cutters) {
        const Cutter c = ParseCutter(item, "cutters[" + std::to_string(out.size() / rep.count) + "]", defaultOperation);
        for (long long k = 0; k < rep.count; ++k) {
            Cutter instance = c;
            if (rep.count > 1) instance.name += "_" + std::to_string(k + 1);
            instance.pos = c.pos + rep.axis * (rep.spacing * float(k));
            out.push_back(instance);
        }
    }
    return out;
}

struct BooleanSettings {
    std::string option = "none";
    int method = -1;        // -1 keeps the modifier's method; 0 mesh, 1 OpenVDB
    double voxelSize = 0;   // > 0 sets it
    bool live = false;
    bool useOperandMaterial = true;
    bool newModifier = false;
    std::string name;
};

// The tool's apply script without its undo block. A failure deletes the
// cutters not yet appended, and when nothing was appended it leaves the stack
// as it was found (a new modifier removed, an old one's settings restored).
const char* const kApplyScript = R"MXS(
fn mxRevert obj bm madeNew oldProps = (
    if bm == undefined then false
    else if madeNew then (deleteModifier obj bm; true)
    else if oldProps == undefined then false
    else (
        bm.useLiveReference = oldProps[1]; bm.useOperandMaterial = oldProps[2]
        bm.method = oldProps[3]; bm.voxelSize = oldProps[4]; bm.name = oldProps[5]
        true
    )
)
local madeCutters = #()
local appended = #()
local failed = #()
local bm = undefined
local bmHandle = "0"
local madeNew = false
local oldProps = undefined
%INPUTS%
try (
    %FIND%
    if bm == undefined then (bm = BooleanMod(); addModifier obj bm; madeNew = true)
    else try (oldProps = #(bm.useLiveReference, bm.useOperandMaterial, bm.method, bm.voxelSize, bm.name)) catch ()
    bmHandle = formattedPrint ((getHandleByAnim bm) as integer64) format:"d"
    %SETTINGS%
    -- Evaluating the stack initializes the modifier context; a fresh
    -- BooleanMod otherwise loses the relative transforms of non-live operands.
    GetTriMeshFaceCount obj
    for d in cutDefs do (
        local c
        if d[2] == 1 then c = Box width:d[3].x length:d[3].y height:d[3].z
        else if d[2] == 2 then c = Cylinder radius:((amin #(d[3].x, d[3].y)) / 2.0) height:d[3].z sides:d[7] heightsegs:1 capsegs:1
        else c = Sphere radius:((amin #(d[3].x, d[3].y, d[3].z)) / 2.0) segments:d[7]
        append madeCutters c
        c.name = d[1]
        c.material = obj.material
        if d[5] != [0,0,0] do c.rotation = eulerAngles d[5].x d[5].y d[5].z
        c.pos += d[4] - c.center
        append opNodes c
        append opEnums d[6]
    )
    for i = 1 to opNodes.count do (
        local ok = false
        try (ok = bm.appendOperand #single operandNode:opNodes[i] operationType:opEnums[i] operationOption:%OPTION%) catch ()
        if ok == true then append appended opNodes[i] else (
            append failed i
            if (findItem madeCutters opNodes[i]) > 0 and isValidNode opNodes[i] do delete opNodes[i]
        )
    )
) catch (
    for c in madeCutters where isValidNode c and (findItem appended c) == 0 do delete c
    if appended.count == 0 do try (mxRevert obj bm madeNew oldProps) catch ()
    throw()
)
local reverted = false
if appended.count == 0 do reverted = mxRevert obj bm madeNew oldProps
local failedText = ""
for i in failed do failedText += (if failedText == "" then "" else ",") + (i as string)
"OK|" + bmHandle + "|" + (madeNew as string) + "|" + (reverted as string) + "|" + (appended.count as string) + "|" + failedText
)MXS";

std::string ApplyScript(INode* base, const std::vector<INode*>& nodes, const std::vector<std::string>& enums,
                        const std::vector<Cutter>& cutters, const BooleanSettings& s) {
    std::string opNodes, opEnums, cutDefs;
    for (size_t i = 0; i < nodes.size(); ++i) {
        opNodes += (i ? "," : "") + HandlerHelpers::NodeScriptRef(nodes[i]);
        opEnums += (i ? ",#" : "#") + enums[i];
    }
    for (size_t i = 0; i < cutters.size(); ++i) {
        const Cutter& c = cutters[i];
        cutDefs += (i ? ",#(" : "#(") + MxsString(c.name) + "," + std::to_string(c.shape) + "," + MxsPoint(c.size) + "," +
                   MxsPoint(c.pos) + "," + MxsPoint(c.rot) + ",#" + c.operation + "," + std::to_string(c.segments) + ")";
    }
    const std::string inputs = "local obj = " + HandlerHelpers::NodeScriptRef(base) + "\nlocal opNodes = #(" + opNodes +
                               ")\nlocal opEnums = #(" + opEnums + ")\nlocal cutDefs = #(" + cutDefs + ")";
    std::string settings = std::string("bm.useLiveReference = ") + (s.live ? "true" : "false") +
                           "\n    bm.useOperandMaterial = " + (s.useOperandMaterial ? "true" : "false");
    if (s.method >= 0) settings += "\n    bm.method = " + std::to_string(s.method);
    if (s.voxelSize > 0) settings += "\n    bm.voxelSize = " + MxsNumber(s.voxelSize);
    if (!s.name.empty()) settings += "\n    bm.name = " + MxsString(s.name);
    const char* find = "for i = 1 to obj.modifiers.count while bm == undefined do "
                       "(if classof obj.modifiers[i] == BooleanMod do bm = obj.modifiers[i])";
    std::string code = Replace(kApplyScript, "%INPUTS%", inputs);
    code = Replace(code, "%FIND%", s.newModifier ? "" : find);
    code = Replace(code, "%SETTINGS%", settings);
    return "(" + Replace(code, "%OPTION%", "#" + s.option) + ")";
}

std::string NameList(const std::vector<std::string>& names) {
    std::vector<std::string> quoted;
    for (const std::string& name : names) quoted.push_back("'" + name + "'");
    return Join(quoted);
}

// Operand nodes, validated: geometry, not the base, each once.
std::vector<INode*> OperandNodes(INode* base, nb::handle operands) {
    if (operands.is_none() || (IsSequence(operands) && nb::len(operands) == 0)) return {};
    std::vector<INode*> nodes = NodeList(operands, "boolean");
    std::vector<std::string> notGeometry;
    for (size_t i = 0; i < nodes.size(); ++i) {
        if (nodes[i] == base) throw BadParam("'" + NodeName(base) + "' cannot be a boolean operand of itself");
        if (std::find(nodes.begin(), nodes.begin() + i, nodes[i]) != nodes.begin() + i)
            throw BadParam("'" + NodeName(nodes[i]) + "' is listed twice in operands");
        if (!IsGeometry(nodes[i])) notGeometry.push_back(NodeName(nodes[i]) + " (" + ClassOf(nodes[i]) + ")");
    }
    if (!notGeometry.empty()) throw BadParam("boolean operands must be geometry: " + Join(notGeometry));
    return nodes;
}

BooleanSettings ParseSettings(const std::string& option, const std::optional<std::string>& method,
                              std::optional<double> voxelSize, bool live, bool useOperandMaterial, bool newModifier,
                              const std::string& modifierName) {
    BooleanSettings s{OptionEnum(option), -1, 0, live, useOperandMaterial, newModifier, modifierName};
    if (method) {
        const std::string key = Lower(Trim(*method));
        if (key == "mesh") s.method = 0;
        else if (key == "openvdb") s.method = 1;
        else throw BadParam("method must be 'mesh' or 'openvdb', not '" + *method + "'");
    }
    if (voxelSize) {
        if (s.method != 1) throw BadParam("voxel_size applies to method='openvdb'; pass both");
        if (!(*voxelSize > 0) || !std::isfinite(*voxelSize)) throw BadParam("voxel_size must be > 0");
        s.voxelSize = *voxelSize;
    }
    return s;
}

std::vector<std::string> OperandNames(INode* base, const std::vector<INode*>& nodes, const std::vector<Cutter>& cutters);
AnimHandle AppendedModifier(const std::string& reply, const std::vector<std::string>& names);

BooleanRef ApplyBoolean(const Node& n, nb::handle operands, const std::string& operation, nb::handle cutters,
                        nb::handle repeat, const std::optional<std::vector<std::string>>& operations,
                        const std::string& option, const std::optional<std::string>& method,
                        std::optional<double> voxelSize, bool live, bool useOperandMaterial, bool newModifier,
                        const std::string& modifierName) {
    INode* base = Resolve(n);
    const std::vector<INode*> nodes = OperandNodes(base, operands);
    if (!repeat.is_none() && cutters.is_none()) throw BadParam("repeat needs cutters");
    const std::string defaultOperation = OperationEnum(operation);
    const std::vector<Cutter> made = cutters.is_none() ? std::vector<Cutter>{} : ParseCutters(cutters, repeat, defaultOperation);
    if (nodes.empty() && made.empty()) throw BadParam("boolean needs operands (nodes) and/or cutters (inline primitives)");
    if (operations && operations->size() > nodes.size())
        throw BadParam("operations has " + std::to_string(operations->size()) + " entries for " +
                       std::to_string(nodes.size()) + " operands");
    if (!IsGeometry(base)) throw BadParam("'" + NodeName(base) + "' is not geometry; a Boolean needs a geometry base");
    const BooleanSettings settings =
        ParseSettings(option, method, voxelSize, live, useOperandMaterial, newModifier, modifierName);
    std::vector<std::string> enums;
    for (size_t i = 0; i < nodes.size(); ++i) {
        const bool overridden = operations && i < operations->size() && !Trim((*operations)[i]).empty();
        enums.push_back(overridden ? OperationEnum((*operations)[i]) : defaultOperation);
    }
    const std::vector<std::string> names = OperandNames(base, nodes, made);
    std::string reply;
    {
        NoAnimate noAnimate;
        reply = RunMaxScript(ApplyScript(base, nodes, enums, made, settings));
    }
    return MakeBoolean(n.handle, AppendedModifier(reply, names));
}

// Operand names in append order (nodes, then cutters). Operands keep their
// names inside the modifier, so they must be distinct.
std::vector<std::string> OperandNames(INode* base, const std::vector<INode*>& nodes, const std::vector<Cutter>& cutters) {
    std::vector<std::string> names;
    for (INode* node : nodes) names.push_back(NodeName(node));
    for (const Cutter& c : cutters) {
        if (_stricmp(c.name.c_str(), NodeName(base).c_str()) == 0)
            throw BadParam("cutter '" + c.name + "' cannot be named after the base object");
        names.push_back(c.name);
    }
    for (size_t i = 0; i < names.size(); ++i)
        for (size_t j = 0; j < i; ++j)
            if (_stricmp(names[i].c_str(), names[j].c_str()) == 0)
                throw BadParam("operand and cutter names must be unique within one call: '" + names[i] + "' twice");
    return names;
}

// The modifier's handle from the apply script's reply; an operand the
// modifier refused is an error, naming what was and was not appended.
AnimHandle AppendedModifier(const std::string& reply, const std::vector<std::string>& names) {
    const std::vector<std::string> fields = Split(reply, '|');
    if (fields.size() != 6 || fields[0] != "OK") throw MaxError("unexpected Boolean reply: " + reply.substr(0, 200));
    std::vector<std::string> refused, appended;
    std::set<size_t> failed;
    for (const std::string& index : Split(fields[5], ','))
        if (!index.empty()) failed.insert(std::stoul(index) - 1);
    for (size_t i = 0; i < names.size(); ++i) (failed.count(i) ? refused : appended).push_back(names[i]);
    if (appended.empty()) {
        const std::string undone = fields[3] != "true" ? "" : fields[2] == "true" ? " The new Boolean modifier was removed."
                                                                                  : " The modifier's settings were restored.";
        throw MaxError("Boolean appended no operand (refused " + NameList(refused) + ")." + undone +
                       " Refused cutters were deleted; refused operands stay in the scene.");
    }
    if (!refused.empty())
        throw MaxError("Boolean appended " + NameList(appended) + " but refused " + NameList(refused) +
                       "; refused cutters were deleted and refused operands stay in the scene. node.boolean_modifier is the modifier.");
    return (AnimHandle)std::stoull(fields[1]);
}

std::optional<BooleanRef> TopBoolean(const Node& n) {
    for (const ModifierRef& m : NodeModifiers(n)) {
        Animatable* mod = Animatable::GetAnimByHandle(m.mod);
        if (mod && mod->ClassID() == kBooleanModClass) return MakeBoolean(n.handle, m.mod);
    }
    return std::nullopt;
}

Modifier* ResolveBoolean(const BooleanRef& r) {
    for (const ModifierRef& m : NodeModifiers(Node{r.node}))
        if (m.mod == r.mod) return static_cast<Modifier*>(Animatable::GetAnimByHandle(r.mod));
    throw NotFound("this Boolean modifier is no longer on '" + NodeName(Resolve(Node{r.node})) + "'");
}

const char* const kOperandsScript = R"MXS(
fn mxJs s = (
    s = substituteString s "\\" "\\\\"
    s = substituteString s "\"" "\\\""
    s = substituteString s "\n" "\\n"
    s = substituteString s "\r" "\\r"
    s = substituteString s "\t" "\\t"
    "\"" + s + "\""
)
local out = "["
local total = 0
try (total = bm.GetNumOperands()) catch ()
for i = 1 to total do (
    local nm = ""
    local ty = #single
    local op = #union
    local oo = #none
    local dis = false
    try (bm.GetFlatOperandName i &nm) catch ()
    try (bm.GetRootOperandType i &ty) catch ()
    try (bm.GetOperationType i &op) catch ()
    try (bm.GetOperationOption i &oo) catch ()
    try (bm.GetDisable i &dis) catch ()
    if i > 1 do out += ","
    out += "[" + (i as string) + "," + (mxJs (nm as string)) + "," + (mxJs (ty as string)) + "," + (mxJs (op as string)) + "," + (mxJs (oo as string)) + "," + (if dis == true then "true" else "false") + "]"
)
out + "]"
)MXS";

// Operands in flat-index order; the base object (index 1) is left out.
std::vector<OperandInfo> Operands(const BooleanRef& r) {
    ResolveBoolean(r);
    const json rows = NativeJson("maxscript", {{"code", "(local bm = " + MxsAnim(r.mod) + "\n" + kOperandsScript + ")"}});
    if (!(rows.is_array)()) throw MaxError("the Boolean modifier returned an unreadable operand list");
    std::vector<OperandInfo> out;
    for (const json& row : rows) {
        OperandInfo op{row.at(0).get<int>(), row.at(1).get<std::string>(), row.at(2).get<std::string>(),
                   row.at(3).get<std::string>(), row.at(4).get<std::string>(), row.at(5).get<bool>()};
        if (op.kind != "modified") out.push_back(op);
    }
    return out;
}

// Runs `body` on operand `index` after the range check (and, with rejectBase,
// the base-object check) that every operand edit shares.
std::string RunOperandScript(const BooleanRef& r, int index, const std::string& body, bool rejectBase) {
    ResolveBoolean(r);
    if (index < 1) throw BadParam("operand indices start at 1; .operands lists them");
    const std::string i = std::to_string(index);
    std::string code = "(\nlocal obj = " + MxsAnim(r.node) + "\nlocal bm = " + MxsAnim(r.mod) +
                       "\nlocal total = bm.GetNumOperands()\nif " + i + " > total then (\"RANGE|\" + (total as string)) else (\n"
                       "local ty = #single\ntry (bm.GetRootOperandType " + i + " &ty) catch ()\n";
    code += rejectBase ? "if ty == #modified then \"BASE\" else (\n" + body + "\n)" : body;
    const std::string reply = RunMaxScript(code + "\n)\n)");
    if (reply.rfind("RANGE|", 0) == 0)
        throw BadParam("no operand at index " + i + ": this Boolean has " + reply.substr(6) +
                       " operands counting the base at 1; .operands lists the others");
    if (reply == "BASE") throw BadParam("index " + i + " is the base object, not an operand");
    return reply;
}

// One setter call; its name joins `refused` when it throws or returns false.
std::string OperandEdit(const std::string& call, const char* what) {
    return std::string("try (if (") + call + ") == false do refused += \"" + what + ",\") catch (refused += \"" + what + ",\")\n";
}

void SetOperand(const BooleanRef& r, int index, const std::optional<std::string>& operation,
                const std::optional<std::string>& option, const std::optional<std::string>& name,
                std::optional<bool> disabled) {
    if (!operation && !option && !name && disabled == std::nullopt)
        throw BadParam("set_operand needs operation, option, name or disabled");
    const std::string i = std::to_string(index);
    std::string body = "local refused = \"\"\n";
    if (operation) body += OperandEdit("bm.SetOperationType " + i + " #" + OperationEnum(*operation), "operation");
    if (option) body += OperandEdit("bm.SetOperationOption " + i + " #" + OptionEnum(*option), "option");
    if (name) body += OperandEdit("bm.SetRootOperandName " + i + " " + MxsString(*name), "name");
    if (disabled) body += OperandEdit("bm.SetDisable " + i + (*disabled ? " true" : " false"), "disabled");
    const std::string reply = RunOperandScript(r, index, body + "\"OK|\" + refused", true);
    std::vector<std::string> refused;
    for (const std::string& edit : Split(reply.substr(3), ','))
        if (!edit.empty()) refused.push_back(edit);
    if (!refused.empty())
        throw MaxError("operand " + i + " refused " + Join(refused) + "; the other edits were applied (check .operands)");
}

void RemoveOperand(const BooleanRef& r, int index) {
    const std::string i = std::to_string(index);
    const std::string body = "local ok = false\ntry (ok = bm.RemoveOperand " + i + ") catch ()\nif ok == true then \"OK\" else \"FAIL\"";
    if (RunOperandScript(r, index, body, true) != "OK")
        throw MaxError("the Boolean modifier could not remove operand " + i);
}

std::set<AnimHandle> SceneHandles() {
    std::set<AnimHandle> handles;
    for (INode* node : AllNodes()) handles.insert(Animatable::GetHandleByAnim(node));
    return handles;
}

// ExtractAsObject needs the node selected with the Modify panel open; the
// selection and panel are restored afterwards.
Node ExtractOperand(const BooleanRef& r, int index) {
    const std::string i = std::to_string(index);
    const std::string body =
        "local prevSel = selection as array\nlocal prevMode = getCommandPanelTaskMode()\n"
        "select obj\nsetCommandPanelTaskMode #modify\nlocal ok = false\n"
        "try (ok = bm.ExtractAsObject " + i + ") catch ()\n"
        "setCommandPanelTaskMode prevMode\nclearSelection()\nif prevSel.count > 0 do try (select prevSel) catch ()\n"
        "if ok == true then \"OK\" else \"FAIL\"";
    std::string operandName;
    for (const OperandInfo& op : Operands(r))
        if (op.index == index) operandName = op.name;
    const std::set<AnimHandle> before = SceneHandles();
    if (RunOperandScript(r, index, body, false) != "OK")
        throw MaxError("the Boolean modifier could not extract operand " + i);
    std::optional<AnimHandle> newest;
    for (AnimHandle handle : SceneHandles())
        if (!before.count(handle)) newest = handle;
    if (!newest) throw MaxError("operand " + i + " was extracted, but no new node appeared");
    // Max names the copy after the modifier ('Wall_BoolOp3_Time0_001'); the
    // operand's own name is what the caller knows it by, when no node has it.
    if (!operandName.empty() && !GetCOREInterface()->GetINodeByName(Utf8ToWide(operandName).c_str()))
        Resolve(Node{*newest})->SetName(Utf8ToWide(operandName).c_str());
    return Node{*newest};
}

std::string BooleanRepr(const BooleanRef& r) {
    try {
        Modifier* mod = ResolveBoolean(r);
        const size_t count = Operands(r).size();
        return "<Boolean '" + WideToUtf8(mod->GetName(false).data()) + "' on '" + NodeName(Resolve(Node{r.node})) + "', " +
               std::to_string(count) + (count == 1 ? " operand>" : " operands>");
    } catch (const MaxError&) {
        return "<Boolean #" + std::to_string(r.mod) + " unavailable>";
    }
}

std::string OperandRepr(const OperandInfo& op) {
    std::string out = "<Operand " + std::to_string(op.index) + " '" + op.name + "' " + op.operation;
    if (op.option != "none") out += " " + op.option;
    if (op.kind == "folder") out += " folder";
    if (op.disabled) out += " disabled";
    return out + ">";
}

// ── Vertices ─────────────────────────────────────────────────────
// Positions are world space. Edits change the Editable Poly base beneath any
// modifiers, so a TurboSmooth above follows the cage.

struct PolyBase {
    INode* node = nullptr;
    EPoly* epoly = nullptr;
    MNMesh* mesh = nullptr;
    Matrix3 tm;  // object space to world, at Now()
};

PolyBase EditablePoly(const Node& n, bool convert) {
    INode* node = Resolve(n);
    Object* base = BaseObject(node);
    if (!base || base->ClassID() != EPOLYOBJ_CLASS_ID) {
        if (!convert)
            throw BadParam("'" + NodeName(node) + "' has " + WithArticle(ClassOf(node)) +
                           " base, not Editable_Poly; pass convert=True to collapse it to Editable Poly");
        RunMaxScript("convertTo " + HandlerHelpers::NodeScriptRef(node) + " Editable_Poly");
        base = BaseObject(node);
        if (!base || base->ClassID() != EPOLYOBJ_CLASS_ID)
            throw MaxError("3ds Max could not convert '" + NodeName(node) + "' to Editable Poly");
    }
    PolyBase pb{node, static_cast<EPoly*>(base->GetInterface(EPOLY_INTERFACE)), nullptr, node->GetObjectTM(Now())};
    if (pb.epoly) pb.mesh = pb.epoly->GetMeshPtr();
    if (!pb.mesh) throw MaxError("'" + NodeName(node) + "' has no Editable Poly mesh");
    return pb;
}

Point3 WorldPos(const PolyBase& pb, int v) { return pb.mesh->V(v)->p * pb.tm; }

// Zero-based vertex indices from 1-based ones, each once; None is every vertex.
std::vector<int> VertIndices(const PolyBase& pb, const std::optional<std::vector<long long>>& indices) {
    const int count = pb.mesh->VNum();
    std::vector<int> out;
    if (!indices) {
        for (int v = 0; v < count; ++v)
            if (!pb.mesh->V(v)->GetFlag(MN_DEAD)) out.push_back(v);
        return out;
    }
    if (indices->empty()) throw BadParam("indices is empty; pass None for every vertex");
    std::vector<bool> seen(count);
    for (long long index : *indices) {
        if (index < 1 || index > count)
            throw BadParam("vertex " + std::to_string(index) + " is out of range: '" + NodeName(pb.node) + "' has " +
                           std::to_string(count) + " vertices, numbered from 1");
        const int v = int(index - 1);
        if (pb.mesh->V(v)->GetFlag(MN_DEAD)) throw BadParam("vertex " + std::to_string(index) + " was deleted");
        if (!seen[v]) out.push_back(v);
        seen[v] = true;
    }
    return out;
}

// Moves vertices to world positions in one ApplyDelta.
void SetWorld(const PolyBase& pb, const std::vector<std::pair<int, Point3>>& targets) {
    if (targets.empty()) return;
    const Matrix3 toObject = Inverse(pb.tm);
    Tab<Point3> delta;
    delta.SetCount(pb.mesh->VNum());
    for (int v = 0; v < delta.Count(); ++v) delta[v] = Point3(0, 0, 0);
    for (const auto& [v, world] : targets) delta[v] = world * toObject - pb.mesh->V(v)->p;
    pb.epoly->ApplyDelta(delta, pb.epoly, Now());
}

std::pair<Point3, Point3> ParseBox(nb::handle box) {
    if (!IsSequence(box) || nb::len(box) != 2) throw BadParam("bbox expects ((min x, y, z), (max x, y, z)), like node.bbox");
    const Point3 lo = ToPoint3(box[0], "bbox min"), hi = ToPoint3(box[1], "bbox max");
    if (lo.x > hi.x || lo.y > hi.y || lo.z > hi.z) throw BadParam("bbox min must not exceed bbox max");
    return {lo, hi};
}

nb::dict GetVerts(const Node& n, const std::optional<std::vector<long long>>& indices, nb::handle bbox,
                  nb::handle nearPoint, double radius, bool convert) {
    std::optional<std::pair<Point3, Point3>> box;
    if (!bbox.is_none()) box = ParseBox(bbox);
    std::optional<Point3> center;
    if (!nearPoint.is_none()) {
        center = ToPoint3(nearPoint, "near");
        if (!(radius > 0)) throw BadParam("near needs a positive radius");
    } else if (radius != 0) {
        throw BadParam("radius applies to near; pass near=(x, y, z) too");
    }
    const PolyBase pb = EditablePoly(n, convert);
    nb::dict out;
    for (int v : VertIndices(pb, indices)) {
        const Point3 p = WorldPos(pb, v);
        if (box && (p.x < box->first.x || p.y < box->first.y || p.z < box->first.z || p.x > box->second.x ||
                    p.y > box->second.y || p.z > box->second.z))
            continue;
        if (center && Length(p - *center) > radius) continue;
        out[nb::int_(v + 1)] = FromPoint3(p);
    }
    return out;
}

int SetVerts(const Node& n, nb::dict positions, bool convert) {
    if (positions.empty()) throw BadParam("set_verts needs {index: (x, y, z)}");
    std::vector<long long> indices;
    std::vector<Point3> points;
    for (auto [key, value] : positions) {
        if (!IsInteger(key)) throw BadParam("set_verts keys are vertex indices (int), not " + TypeNameOf(key));
        indices.push_back(nb::cast<long long>(key));
        points.push_back(ToPoint3(value, "vertex " + std::to_string(indices.back())));
    }
    const PolyBase pb = EditablePoly(n, convert);
    VertIndices(pb, indices);  // range checks before any change
    std::vector<std::pair<int, Point3>> targets;
    for (size_t i = 0; i < indices.size(); ++i) targets.push_back({int(indices[i] - 1), points[i]});
    SetWorld(pb, targets);
    return int(targets.size());
}

// Soft falloff: a vertex within `falloff` of a moved one moves by
// offset * (1 - d / falloff)^2, d being the distance to the nearest moved vertex.
std::pair<int, int> MoveVerts(const Node& n, nb::handle offset, const std::optional<std::vector<long long>>& indices,
                              double falloff, bool convert) {
    const Point3 shift = ToPoint3(offset, "offset");
    if (!(falloff >= 0)) throw BadParam("falloff must be >= 0");
    const PolyBase pb = EditablePoly(n, convert);
    const std::vector<int> moved = VertIndices(pb, indices);
    std::vector<std::pair<int, Point3>> targets;
    std::vector<Point3> origins;
    Box3 reach;
    for (int v : moved) {
        origins.push_back(WorldPos(pb, v));
        targets.push_back({v, origins.back() + shift});
        reach += origins.back();
    }
    int soft = 0;
    if (falloff > 0 && !moved.empty()) {
        const float r = float(falloff);
        reach.EnlargeBy(r);
        std::vector<bool> inSet(pb.mesh->VNum());
        for (int v : moved) inSet[v] = true;
        for (int v = 0; v < pb.mesh->VNum(); ++v) {
            if (inSet[v] || pb.mesh->V(v)->GetFlag(MN_DEAD)) continue;
            const Point3 p = WorldPos(pb, v);
            if (!reach.Contains(p)) continue;
            float d = std::numeric_limits<float>::max();
            for (const Point3& q : origins) d = std::min(d, Length(p - q));
            if (d >= r) continue;
            const float w = (1.0f - d / r) * (1.0f - d / r);
            targets.push_back({v, p + shift * w});
            ++soft;
        }
    }
    SetWorld(pb, targets);
    return {int(moved.size()), soft};
}

// World closest points on spline `spline` of a shape, or nullopt where
// MAXScript's nearestPathParam/pathInterp found none.
std::vector<std::optional<Point3>> SplinePoints(INode* target, int spline, const std::vector<Point3>& points) {
    std::string list;
    for (size_t i = 0; i < points.size(); ++i) list += (i ? "," : "") + MxsPoint(points[i]);
    const std::string s = std::to_string(spline);
    const std::string code =
        "(at time (" + std::to_string(Now()) + "t) (in coordsys world (\nlocal tgt = " + HandlerHelpers::NodeScriptRef(target) +
        "\nlocal total = numSplines tgt\nif " + s + " > total then (\"FEW|\" + (total as string)) else (\n"
        "local out = stringstream \"\"\nfor p in #(" + list + ") do (\nlocal d = undefined\n"
        "try (d = pathInterp tgt " + s + " (nearestPathParam tgt " + s + " p)) catch ()\n"
        "if d == undefined then format \"-\\n\" to:out\n"
        "else format \"%,%,%\\n\" (formattedPrint d.x format:\".9g\") (formattedPrint d.y format:\".9g\") (formattedPrint d.z format:\".9g\") to:out\n"
        ")\nout as string\n)\n)))";
    const std::string reply = RunMaxScript(code);
    if (reply.rfind("FEW|", 0) == 0)
        throw BadParam("'" + NodeName(target) + "' has " + reply.substr(4) + " splines; spline " + s + " does not exist");
    std::vector<std::optional<Point3>> out;
    for (const std::string& line : Split(reply, '\n')) {
        if (out.size() == points.size()) break;
        const std::vector<std::string> xyz = Split(Trim(line), ',');
        if (xyz.size() != 3) { out.push_back(std::nullopt); continue; }
        float c[3] = {0, 0, 0};
        for (int a = 0; a < 3; ++a) std::from_chars(xyz[a].data(), xyz[a].data() + xyz[a].size(), c[a]);
        out.push_back(Point3(c[0], c[1], c[2]));
    }
    if (out.size() != points.size()) throw MaxError("unexpected spline readback for '" + NodeName(target) + "'");
    return out;
}

struct RayTarget {
    Object* object = nullptr;
    Matrix3 tm, toObject;
    TimeValue t = 0;
};

// The world hit of a ray on the target's evaluated surface, or nullopt. Faces
// pointing away from the ray are not hit, as with MAXScript's intersectRay.
std::optional<Point3> RayHit(const RayTarget& target, const Point3& from, const Point3& dir) {
    Ray ray;
    ray.p = from * target.toObject;
    ray.dir = Normalize(VectorTransform(target.toObject, dir));
    float at = 0;
    Point3 normal;
    if (!target.object->IntersectRay(target.t, ray, at, normal)) return std::nullopt;
    return (ray.p + ray.dir * at) * target.tm;
}

std::pair<int, int> ConformVerts(const Node& n, nb::handle targetKey, const std::optional<std::vector<long long>>& indices,
                                 const std::string& axes, const std::string& axis, double strength, int spline,
                                 bool convert) {
    const std::string mask = Lower(Trim(axes));
    if (mask.empty() || mask.find_first_not_of("xyz") != std::string::npos)
        throw BadParam("axes must be a subset of 'xyz', like 'xz'");
    const std::string ray = Lower(Trim(axis));
    const std::vector<std::string> rays = {"", "x", "y", "z", "+x", "+y", "+z", "-x", "-y", "-z"};
    if (std::find(rays.begin(), rays.end(), ray) == rays.end())
        throw BadParam("axis must be x, y or z (both ways) or +x, -x, +y, -y, +z, -z (one way)");
    if (!std::isfinite(strength)) throw BadParam("strength must be a finite number (1.0 moves all the way)");
    if (spline < 1) throw BadParam("spline numbers start at 1");
    INode* target = Resolve(FindNode(targetKey));
    if (target == Resolve(n)) throw BadParam("conform target must be another node");
    const TimeValue t = Now();
    Object* evaluated = target->EvalWorldState(t).obj;
    const SClass_ID kind = evaluated ? evaluated->SuperClassID() : 0;
    if (kind != SHAPE_CLASS_ID && kind != GEOMOBJECT_CLASS_ID)
        throw BadParam("conform target must be a spline shape or geometry, not " + WithArticle(ClassOf(target)));

    const PolyBase pb = EditablePoly(n, convert);
    const std::vector<int> verts = VertIndices(pb, indices);
    std::vector<Point3> from;
    for (int v : verts) from.push_back(WorldPos(pb, v));
    std::vector<std::optional<Point3>> dest;
    if (kind == SHAPE_CLASS_ID) {
        dest = SplinePoints(target, spline, from);
    } else {
        const int sign = ray.size() == 2 && ray[0] == '-' ? -1 : 1;
        const char letter = ray.empty() ? 'z' : ray.back();
        const Point3 dir = Point3(float(letter == 'x'), float(letter == 'y'), float(letter == 'z')) * float(sign);
        const bool bothWays = ray.size() < 2;
        // Evaluated after a conversion of the source, which can change the scene.
        const ObjectState os = target->EvalWorldState(t);
        if (!os.obj) throw MaxError("'" + NodeName(target) + "' has no surface to conform to");
        const Matrix3 tm = target->GetObjTMAfterWSM(t);
        const RayTarget rt{os.obj, tm, Inverse(tm), t};
        for (const Point3& p : from) {
            std::optional<Point3> best = RayHit(rt, p, dir);
            if (bothWays)
                if (const auto back = RayHit(rt, p, -dir); back && (!best || Length(*back - p) < Length(*best - p))) best = back;
            dest.push_back(best);
        }
    }
    const Point3 keep(float(mask.find('x') != std::string::npos), float(mask.find('y') != std::string::npos),
                      float(mask.find('z') != std::string::npos));
    std::vector<std::pair<int, Point3>> targets;
    for (size_t i = 0; i < verts.size(); ++i)
        if (dest[i]) targets.push_back({verts[i], from[i] + (*dest[i] - from[i]) * keep * float(strength)});
    SetWorld(pb, targets);
    return {int(targets.size()), int(verts.size() - targets.size())};
}

// ── Lofts ────────────────────────────────────────────────────────
// A loft is an Editable Poly of quads between matched cross sections, whose
// coordinates may be arithmetic over named parameters ('-width/2'). The
// definition and up to 16 parameter states, each keyed by a fingerprint of the
// cage it built, live in the node's AppData in the loft_mesh tool's format; a
// geometry undo is recognized by its fingerprint, and a cage edited by hand is
// never rebuilt.

constexpr int kLoftAppData = 1280263764;  // "LOFT"
const char* const kLoftSchema = "3dsmax-mcp/loft";
constexpr size_t kLoftHistory = 16;
constexpr size_t kLoftMaxBytes = 4000000;
const char* const kLoftMarker = "__MCP_LOFT_CAGE_TOKEN__";  // replaced by the cage fingerprint when stored

// A section coordinate: a number, or an expression kept as written.
struct LoftCoord {
    bool isExpr = false;
    std::string text;
    double number = 0;
};

using LoftPoint = std::array<LoftCoord, 3>;
using LoftSections = std::vector<std::vector<LoftPoint>>;
using Vec3 = std::array<double, 3>;

double Coordinate(const LoftCoord& c, const Values& params) {
    return c.isExpr ? Expression(c.text, params) : Finite(c.number, "section coordinate");
}

double PyNumber(nb::handle v, const std::string& label) {
    if (!IsNumber(v)) throw BadParam(label + " must be a finite number with magnitude at most 1e12");
    double d = 0;
    try {
        d = nb::cast<double>(v);
    } catch (const std::exception&) {
        throw BadParam(label + " must be a finite number with magnitude at most 1e12");
    }
    return Finite(d, label);
}

Values ParamsFromPython(nb::handle value) {
    Values out;
    if (value.is_none()) return out;
    if (!nb::isinstance<nb::dict>(value) || nb::len(value) > 64)
        throw BadParam("parameters must be a dict of at most 64 numbers: {'width': 40}");
    for (auto [key, item] : nb::borrow<nb::dict>(value)) {
        const std::string name = nb::isinstance<nb::str>(key) ? nb::cast<std::string>(key) : std::string();
        if (!IsParameterName(name)) throw BadParam(kParameterNameRule);
        out[name] = PyNumber(item, "parameters." + name);
    }
    return out;
}

bool IsJsonNumber(const json& v) { return v.is_number() && !v.is_boolean(); }

Values ParamsFromJson(const json& value) {
    if (!value.is_object() || value.size() > 64) throw MaxError("Stored loft parameters are invalid");
    Values out;
    for (auto it = value.begin(); it != value.end(); ++it) {
        if (!IsParameterName(it.key())) throw MaxError(std::string("Stored loft parameters are invalid: ") + kParameterNameRule);
        if (!IsJsonNumber(it.value())) throw MaxError("Stored loft parameter " + it.key() + " is not a number");
        out[it.key()] = Finite(it.value().get<double>(), "parameters." + it.key());
    }
    return out;
}

json ParamsJson(const Values& params) {
    json out = json::object();
    for (const auto& [name, value] : params) out[name] = value;
    return out;
}

LoftCoord CoordFromPython(nb::handle v) {
    LoftCoord c;
    if (nb::isinstance<nb::str>(v)) {
        c.isExpr = true;
        c.text = nb::cast<std::string>(v);
    } else {
        c.number = PyNumber(v, "section coordinate");
    }
    return c;
}

LoftSections SectionsFromPython(nb::handle sections) {
    if (!IsSequence(sections)) throw BadParam("sections must be a list of cross sections, each a list of (x, y, z) points");
    LoftSections out;
    for (nb::handle section : sections) {
        if (!IsSequence(section)) throw BadParam("each section must be a list of (x, y, z) points");
        std::vector<LoftPoint> points;
        for (nb::handle point : section) {
            if (!IsSequence(point) || nb::len(point) != 3)
                throw BadParam("Section " + std::to_string(out.size() + 1) + " points must contain three coordinates");
            points.push_back({CoordFromPython(point[0]), CoordFromPython(point[1]), CoordFromPython(point[2])});
        }
        out.push_back(std::move(points));
    }
    return out;
}

LoftCoord CoordFromJson(const json& v) {
    LoftCoord c;
    if ((v.is_string)()) {
        c.isExpr = true;
        c.text = v.get<std::string>();
    } else if (IsJsonNumber(v)) {
        c.number = v.get<double>();
    } else {
        throw MaxError("Stored loft section coordinate is neither a number nor an expression");
    }
    return c;
}

LoftSections SectionsFromJson(const json& sections) {
    if (!(sections.is_array)()) throw MaxError("Stored loft sections are invalid");
    LoftSections out;
    for (const json& section : sections) {
        if (!(section.is_array)()) throw MaxError("Stored loft sections are invalid");
        std::vector<LoftPoint> points;
        for (const json& point : section) {
            if (!(point.is_array)() || point.size() != 3)
                throw MaxError("Stored loft section " + std::to_string(out.size() + 1) + " points must contain three coordinates");
            points.push_back({CoordFromJson(point[0]), CoordFromJson(point[1]), CoordFromJson(point[2])});
        }
        out.push_back(std::move(points));
    }
    return out;
}

struct LoftBuild {
    std::vector<Vec3> vertices;           // initial world coordinates
    std::vector<std::vector<int>> faces;  // 0-based vertex indices
    json definition;
};

// Evaluates the sections, then builds the quads between them.
LoftBuild BuildLoft(const LoftSections& sections, const Values& params, const LoftShape& shape) {
    CheckLoftCounts(sections.size(), sections.empty() ? 0 : sections[0].size(), shape);
    std::vector<std::vector<Vec3>> points;
    json normalized = json::array();
    for (const auto& section : sections) {
        if (section.size() != sections[0].size())
            throw BadParam("All sections must contain the same number of corresponding points");
        std::vector<Vec3> evaluated;
        json expressions = json::array();
        for (const LoftPoint& point : section) {
            Vec3 xyz{};
            json coords = json::array();
            for (int a = 0; a < 3; ++a) {
                xyz[a] = Coordinate(point[a], params);
                coords.push_back(point[a].isExpr ? json(point[a].text) : json(Finite(point[a].number, "coordinate")));
            }
            evaluated.push_back(xyz);
            expressions.push_back(std::move(coords));
        }
        points.push_back(std::move(evaluated));
        normalized.push_back(std::move(expressions));
    }
    LoftBuild out;
    out.faces = LoftQuads(points, shape);
    for (const auto& section : points) out.vertices.insert(out.vertices.end(), section.begin(), section.end());
    out.definition = {{"sections", std::move(normalized)}, {"profile_closed", shape.profileClosed},
                      {"close_path", shape.closePath}, {"caps", shape.caps}, {"reverse", shape.reverse}};
    return out;
}

Vec3 BoxCenter(const std::vector<Vec3>& points) {
    Vec3 out{};
    for (int a = 0; a < 3; ++a) {
        const auto [lo, hi] = std::minmax_element(points.begin(), points.end(), [&](const Vec3& p, const Vec3& q) { return p[a] < q[a]; });
        out[a] = ((*lo)[a] + (*hi)[a]) * 0.5;
    }
    return out;
}

// Compact ASCII JSON, sized as stored: the marker becomes a 95-character fingerprint.
std::string StoredText(const json& data) {
    const std::string text = data.dump(-1, ' ', true);
    size_t markers = 0;
    for (size_t at = text.find(kLoftMarker); at != std::string::npos; at = text.find(kLoftMarker, at + 1)) ++markers;
    if (text.size() + markers * (95 - std::strlen(kLoftMarker)) > kLoftMaxBytes)
        throw BadParam("Loft definition exceeds the 4 MB storage limit");
    return text;
}

std::string WithFingerprint(std::string text, const std::string& fingerprint) {
    for (size_t at = text.find(kLoftMarker); at != std::string::npos; at = text.find(kLoftMarker, at + fingerprint.size()))
        text.replace(at, std::strlen(kLoftMarker), fingerprint);
    return text;
}

// The stored definition, checked as the MCP tool checks it on read.
json ParseStored(const std::string& raw) {
    if (raw.size() > kLoftMaxBytes) throw MaxError("Stored loft definition exceeds the 4 MB storage limit");
    json data = json::parse(raw, nullptr, false);
    if (data.is_discarded()) throw MaxError("Stored loft definition is invalid JSON");
    const auto schema = data.is_object() ? data.find("schema") : data.end();
    const auto version = data.is_object() ? data.find("version") : data.end();
    if (!data.is_object() || schema == data.end() || *schema != kLoftSchema || version == data.end() ||
        !IsJsonNumber(*version) || version->get<double>() != 1)
        throw MaxError("Unsupported or missing loft definition version");
    const json& definition = data.value("definition", json());
    const std::set<std::string> keys = {"sections", "profile_closed", "close_path", "caps", "reverse"};
    std::set<std::string> found;
    if (definition.is_object())
        for (auto it = definition.begin(); it != definition.end(); ++it) found.insert(it.key());
    if (found != keys) throw MaxError("Stored loft definition has an invalid construction shape");
    const json& origin = data.value("origin", json());
    if (!(origin.is_array)() || origin.size() != 3 ||
        !std::all_of(origin.begin(), origin.end(), [](const json& v) { return IsJsonNumber(v) && std::isfinite(v.get<double>()); }))
        throw MaxError("Stored loft origin must contain 3 finite numbers");
    const json& history = data.value("history", json());
    if (!(history.is_array)() || history.empty() || history.size() > kLoftHistory)
        throw MaxError("Stored loft parameter history is invalid");
    std::optional<std::set<std::string>> names;
    for (const json& row : history) {
        const json fingerprint = row.is_object() ? row.value("fingerprint", json()) : json();
        if (!row.is_object() || row.size() != 2 || !row.contains("parameters") || !(fingerprint.is_string)() ||
            !IsFingerprint(fingerprint.get<std::string>()))
            throw MaxError("Stored loft cage fingerprint is invalid");
        std::set<std::string> rowNames;
        for (const auto& entry : ParamsFromJson(row["parameters"])) rowNames.insert(entry.first);
        if (names && *names != rowNames) throw MaxError("Stored loft parameter names changed across revisions");
        names = rowNames;
    }
    return data;
}

LoftShape ShapeFromJson(const json& definition) {
    for (const char* key : {"profile_closed", "close_path", "caps", "reverse"})
        if (!definition[key].is_boolean()) throw MaxError(std::string("Stored loft ") + key + " must be boolean");
    return {definition["profile_closed"].get<bool>(), definition["close_path"].get<bool>(), definition["caps"].get<bool>(),
            definition["reverse"].get<bool>()};
}

// The newest history row built by this cage, unless rows with different
// parameters share the fingerprint: geometry alone cannot tell them apart.
std::optional<size_t> ActiveRevision(const json& data, const std::string& fingerprint) {
    const json& history = data["history"];
    std::optional<size_t> matched;
    for (size_t i = history.size(); i-- > 0;) {
        if (history[i]["fingerprint"] != fingerprint) continue;
        if (matched && history[i]["parameters"] != history[*matched]["parameters"]) return std::nullopt;
        if (!matched) matched = i;
    }
    return matched;
}

struct LoftRef { AnimHandle node = 0; };

struct LoftState {
    json data;
    std::string raw;          // the stored text
    std::string fingerprint;  // empty unless the cage was read
    long long instances = 0;
    long long vertices = 0, faces = 0;
};

void RequirePolyBase(INode* node) {
    Object* base = BaseObject(node);
    if (!base || base->ClassID() != EPOLYOBJ_CLASS_ID)
        throw MaxError("'" + NodeName(node) + "' lost its Editable Poly base; its loft definition no longer applies");
}

bool HasLoft(const Node& n) { return GetAppData(Resolve(n), kLoftAppData).has_value(); }

// The node's loft definition; with `cage`, also the cage's fingerprint,
// counts and instance count.
LoftState ReadLoft(const LoftRef& r, bool cage) {
    INode* node = Resolve(Node{r.node});
    if (cage) RequirePolyBase(node);
    std::optional<std::string> raw = GetAppData(node, kLoftAppData);
    if (!raw) throw NotFound("'" + NodeName(node) + "' no longer has a loft definition");
    LoftState s;
    s.data = ParseStored(*raw);
    s.raw = std::move(*raw);
    if (!cage) return s;
    const PolyBase pb = EditablePoly(Node{r.node}, false);
    s.fingerprint = CageFingerprint(*pb.mesh);
    s.instances = InstanceCount(node);
    s.vertices = pb.mesh->VNum();
    s.faces = pb.mesh->FNum();
    return s;
}

std::optional<Values> MatchedParams(const LoftState& s) {
    const auto revision = ActiveRevision(s.data, s.fingerprint);
    if (!revision) return std::nullopt;
    return ParamsFromJson(s.data["history"][*revision]["parameters"]);
}

nb::object ParamsPython(const Values& params) {
    nb::dict out;
    for (const auto& [name, value] : params) out[name.c_str()] = value;
    return out;
}

Node CreateLoft(const std::string& name, nb::handle sections, nb::handle parameters, bool profileClosed, bool closePath,
                bool caps, bool reverse) {
    RequireMain();
    if (Trim(name).empty()) throw BadParam("loft_mesh needs a name for the new node");
    const std::wstring wide = Utf8ToWide(name);
    for (INode* node : AllNodes())
        if (_wcsicmp(node->GetName(), wide.c_str()) == 0)
            throw BadParam("a node named '" + NodeName(node) + "' already exists; loft_mesh needs a new name");
    const Values params = ParamsFromPython(parameters);
    const LoftBuild built = BuildLoft(SectionsFromPython(sections), params, {profileClosed, closePath, caps, reverse});
    const Vec3 origin = BoxCenter(built.vertices);
    const json data = {{"schema", kLoftSchema}, {"version", 1}, {"origin", origin}, {"definition", built.definition},
                       {"history", json::array({json{{"parameters", ParamsJson(params)}, {"fingerprint", kLoftMarker}}})}};
    const std::string text = StoredText(data);

    // Double-precision coordinates go to MAXScript as written, not through Point3.
    auto point = [](double x, double y, double z) { return "[" + MxsNumber(x) + "," + MxsNumber(y) + "," + MxsNumber(z) + "]"; };
    std::string verts, faces;
    for (const Vec3& v : built.vertices) verts += (verts.empty() ? "" : ",") + point(v[0] - origin[0], v[1] - origin[1], v[2] - origin[2]);
    for (const auto& face : built.faces) {
        faces += faces.empty() ? "#(" : ",#(";
        for (size_t i = 0; i < face.size(); ++i) faces += (i ? "," : "") + std::to_string(face[i] + 1);
        faces += ")";
    }
    // No undo hold: a failure deletes the half-built node instead.
    const std::string code =
        "(\nlocal obj = undefined\ntry (\n"
        "obj = Editable_Mesh name:" + MxsString(name) + " pos:" + point(origin[0], origin[1], origin[2]) + "\n"
        "convertToPoly obj\nlocal mesh = obj.baseobject\n"
        "for p in #(" + verts + ") do polyop.createVert mesh p\n"
        "for f in #(" + faces + ") do (local faceId = polyop.createPolygon mesh f; "
        "if faceId == undefined or faceId == 0 do throw \"Could not create polygon; check winding and topology\")\n"
        "update obj\n"
        "\"OK|\" + (formattedPrint ((getHandleByAnim obj) as integer64) format:\"d\")\n"
        ") catch (\nif obj != undefined and isValidNode obj do delete obj\nthrow()\n)\n)";
    NoAnimate noAnimate;
    const std::string reply = RunMaxScript(code);
    if (reply.rfind("OK|", 0) != 0) throw MaxError("unexpected loft creation reply: " + reply.substr(0, 200));
    const Node created{(AnimHandle)std::stoull(reply.substr(3))};
    INode* node = Resolve(created);
    try {
        SetAppData(node, kLoftAppData, WithFingerprint(text, CageFingerprint(*EditablePoly(created, false).mesh)),
                   "loft definition");
    } catch (...) {
        GetCOREInterface()->DeleteNode(node, FALSE);
        throw;
    }
    return created;
}

// The cage's object-space vertex positions, restored exactly when a rebuild
// fails (no undo hold to cancel). LocalDataChanged invalidates the mesh's
// geometry cache and notifies dependents.
void RestoreCage(const PolyBase& pb, const std::vector<Point3>& before) {
    for (int v = 0; v < pb.mesh->VNum() && v < int(before.size()); ++v) pb.mesh->V(v)->p = before[v];
    pb.epoly->LocalDataChanged(GEOM_CHANNEL);
}

// Rebuilds the cage for changed parameters; vertex IDs, the transform,
// modifiers and materials stay. Returns {name: (before, after)} of what changed.
nb::dict UpdateLoft(const LoftRef& r, nb::kwargs values) {
    const Values changes = ParamsFromPython(values);
    if (changes.empty()) throw BadParam("update needs at least one parameter: loft.update(width=20)");
    LoftState s = ReadLoft(r, true);
    const auto revision = ActiveRevision(s.data, s.fingerprint);
    if (!revision)
        throw MaxError("Loft cage changed manually or is outside retained parameter history; inspect before rebuilding");
    if (s.instances > 1) throw MaxError("Loft base is instanced; make it unique before changing construction parameters");
    const Values current = ParamsFromJson(s.data["history"][*revision]["parameters"]);
    std::vector<std::string> names, unknown;
    for (const auto& entry : current) names.push_back(entry.first);
    for (const auto& entry : changes)
        if (!current.count(entry.first)) unknown.push_back(entry.first);
    if (!unknown.empty())
        throw BadParam("Unknown construction parameters: " + Join(unknown) + "." + Suggest(unknown[0], names) +
                       " Parameters: " + Join(names));
    Values updated = current;
    for (const auto& [name, value] : changes) updated[name] = value;
    nb::dict changed;
    for (const auto& [name, value] : changes)
        if (current.at(name) != value) changed[name.c_str()] = nb::make_tuple(current.at(name), value);
    if (changed.empty()) return changed;

    const json& definition = s.data["definition"];
    const LoftBuild built = BuildLoft(SectionsFromJson(definition["sections"]), updated, ShapeFromJson(definition));
    if ((long long)built.vertices.size() != s.vertices || (long long)built.faces.size() != s.faces)
        throw MaxError("Stored loft topology no longer matches the cage");

    // Fingerprints of states with other parameters: reaching one would make
    // a later geometry undo ambiguous.
    std::vector<std::string> aliases;
    json history = s.data["history"];
    for (const json& row : history)
        if (ParamsFromJson(row["parameters"]) != updated) aliases.push_back(row["fingerprint"].get<std::string>());
    history.push_back(json{{"parameters", ParamsJson(updated)}, {"fingerprint", kLoftMarker}});
    if (history.size() > kLoftHistory) history.erase(history.begin(), history.end() - std::ptrdiff_t(kLoftHistory));
    json data = s.data;
    data["history"] = std::move(history);
    const std::string text = StoredText(data);

    const PolyBase pb = EditablePoly(Node{r.node}, false);
    if ((long long)pb.mesh->VNum() != s.vertices) throw MaxError("Stored loft topology no longer matches the cage");
    std::vector<Point3> before;
    Tab<Point3> delta;
    delta.SetCount(pb.mesh->VNum());
    const Vec3 origin = {data["origin"][0].get<double>(), data["origin"][1].get<double>(), data["origin"][2].get<double>()};
    for (int v = 0; v < pb.mesh->VNum(); ++v) {
        before.push_back(pb.mesh->V(v)->p);
        const Vec3& w = built.vertices[v];
        delta[v] = Point3(float(w[0] - origin[0]), float(w[1] - origin[1]), float(w[2] - origin[2])) - before.back();
    }
    NoAnimate noAnimate;
    pb.epoly->ApplyDelta(delta, pb.epoly, Now());
    try {
        const std::string fingerprint = CageFingerprint(*pb.mesh);
        if (std::find(aliases.begin(), aliases.end(), fingerprint) != aliases.end())
            throw MaxError("Different parameter values produce an existing cage state; undo would be ambiguous");
        SetAppData(pb.node, kLoftAppData, WithFingerprint(text, fingerprint), "loft definition update");
    } catch (...) {
        RestoreCage(pb, before);
        try {
            if (GetAppData(pb.node, kLoftAppData) != s.raw) SetAppData(pb.node, kLoftAppData, s.raw, "previous loft definition");
        } catch (const MaxError&) {
        }
        throw;
    }
    return changed;
}

std::string LoftRepr(const LoftRef& r) {
    try {
        const LoftState s = ReadLoft(r, true);
        std::string out = "<LoftMesh '" + NodeName(Resolve(Node{r.node})) + "'";
        const auto params = MatchedParams(s);
        if (!params) return out + " cage edited by hand>";
        for (const auto& [name, value] : *params) out += " " + name + "=" + ReprOf(nb::float_(value));
        return out + ">";
    } catch (const MaxError&) {
        return "<LoftMesh #" + std::to_string(r.node) + " unavailable>";
    }
}

} // namespace

// ── Binding ──────────────────────────────────────────────────────

void BindSolids(nb::module_& m) {
    nb::class_<BooleanRef, ModifierRef>(m, "Boolean",
        "A Boolean modifier (BooleanMod), a Modifier with an operand stack: b.operands, b.set_operand(2, operation='union').")
        .def_prop_ro("operands", &Operands,
            "Operands in stack order (the base object left out); index is what set_operand and friends take.")
        .def("set_operand", &SetOperand, "index"_a, nb::kw_only(), "operation"_a = nb::none(), "option"_a = nb::none(),
             "name"_a = nb::none(), "disabled"_a = nb::none(),
             "Retune operand `index`: b.set_operand(2, operation='union', option='imprint', name='Door', disabled=False).")
        .def("remove_operand", &RemoveOperand, "index"_a, "Delete operand `index` from the stack; later indices shift down.")
        .def("extract_operand", &ExtractOperand, "index"_a,
             "Copy operand `index` back out as a new scene node and return it; the operand stays.")
        .def("__repr__", &BooleanRepr);

    nb::class_<OperandInfo>(m, "Operand", "A Boolean operand (a snapshot): index, name, kind, operation, option, disabled.")
        .def_ro("index", &OperandInfo::index, "Flat index in the operand stack; the base object is 1.")
        .def_ro("name", &OperandInfo::name, "The operand's name in the Boolean's operand list.")
        .def_ro("kind", &OperandInfo::kind, "'single' or 'folder'.")
        .def_ro("operation", &OperandInfo::operation, "union, subtraction, intersection, merge, attach, insert or split.")
        .def_ro("option", &OperandInfo::option, "none, imprint (edges only) or cookie (cut without adding faces).")
        .def_ro("disabled", &OperandInfo::disabled, "Whether the operand is switched off and left out of the result.")
        .def("__repr__", &OperandRepr);

    AddMethod<Node>("boolean", &ApplyBoolean, "operands"_a = nb::none(), "operation"_a = "subtract", nb::kw_only(),
        "cutters"_a = nb::none(), "repeat"_a = nb::none(), "operations"_a = nb::none(), "option"_a = "",
        "method"_a = nb::none(), "voxel_size"_a = nb::none(), "live"_a = false, "use_operand_material"_a = true,
        "new_modifier"_a = false, "modifier_name"_a = "",
        "Cut or fuse with a Boolean modifier and return it: wall.boolean(['Door', 'Window']) subtracts both. "
        "operation: union, subtract, intersect, merge, attach, insert or split; operations overrides it per operand; "
        "option: imprint or cookie; method: mesh or openvdb (with voxel_size). Reuses the topmost Boolean unless "
        "new_modifier=True. Non-live operands are consumed: their nodes are deleted and they keep their names in the "
        "modifier; live=True keeps them in the scene, hidden. cutters are primitives built in place: "
        "[{'name': 'Hole', 'shape': 'box'|'cylinder'|'sphere', 'size': (x, y, z) or n, 'pos': bbox center, "
        "'rot': degrees, 'operation': ..., 'segments': 4-256}] (cylinder size is (diameter, diameter, height), "
        "Z up); repeat={'count': 4, 'axis': 'x'|'-x'|..., 'spacing': 20} arrays them as Hole_1..Hole_4.");
    AddProperty<Node>("boolean_modifier", &TopBoolean, "The topmost Boolean modifier on the stack, or None.");

    AddMethod<Node>("get_verts", &GetVerts, "indices"_a = nb::none(), nb::kw_only(), "bbox"_a = nb::none(),
        "near"_a = nb::none(), "radius"_a = 0.0, "convert"_a = false,
        "World positions of the Editable Poly base's vertices as {index: (x, y, z)}, 1-based; filter by indices, "
        "bbox=((min), (max)) and near=(x, y, z) with radius. convert=True collapses a non-poly node to Editable Poly.");
    AddMethod<Node>("set_verts", &SetVerts, "positions"_a, nb::kw_only(), "convert"_a = false,
        "Place base vertices at world positions: node.set_verts({1: (0, 0, 10), 4: (5, 0, 10)}); returns how many.");
    AddMethod<Node>("move_verts", &MoveVerts, "offset"_a, "indices"_a = nb::none(), nb::kw_only(), "falloff"_a = 0.0,
        "convert"_a = false,
        "Move base vertices (all by default) by a world offset; falloff > 0 drags neighbours within that distance "
        "with (1 - d/falloff)^2 weight. Returns (moved, soft_moved).");
    AddMethod<Node>("conform_verts", &ConformVerts, "target"_a, "indices"_a = nb::none(), nb::kw_only(),
        "axes"_a = "xyz", "axis"_a = "", "strength"_a = 1.0, "spline"_a = 1, "convert"_a = false,
        "Pull base vertices onto a spline (closest point on spline number `spline`) or a surface (ray along axis: "
        "x/y/z both ways, +x/-x/... one way, default z); axes masks the world axes that move, strength the fraction. "
        "node.conform_verts('Profile', axes='xz'). Returns (conformed, skipped).");

    nb::class_<LoftRef>(m, "LoftMesh",
        "A parametric quad loft's definition, stored on its node; node.loft_mesh.update(width=20) rebuilds the cage.")
        .def_prop_ro("node", [](const LoftRef& r) { Resolve(Node{r.node}); return Node{r.node}; },
            "The node that carries this loft.")
        .def_prop_ro("parameters", [](const LoftRef& r) -> nb::object {
            const auto params = MatchedParams(ReadLoft(r, true));
            return params ? ParamsPython(*params) : nb::none();
        }, "The parameters that built the current cage, or None when the cage was edited by hand (update refuses then).")
        .def_prop_ro("definition", [](const LoftRef& r) { return ToPython(ReadLoft(r, false).data["definition"]); },
            "sections as written (numbers and expressions) and the profile_closed, close_path, caps, reverse options.")
        .def_prop_ro("origin", [](const LoftRef& r) {
            const LoftState s = ReadLoft(r, false);
            const json& o = s.data["origin"];
            return nb::make_tuple(o[0].get<double>(), o[1].get<double>(), o[2].get<double>());
        }, "World point the initial sections were centered on; the cage follows the node's transform since.")
        .def("update", &UpdateLoft, "parameters"_a,
             "Rebuild the cage for new parameter values, keeping vertex IDs, transform, modifiers and materials; "
             "returns {name: (before, after)} of what changed.")
        .def("__repr__", &LoftRepr)
        .def("__eq__", [](const LoftRef& a, nb::handle b) { return nb::isinstance<LoftRef>(b) && a.node == nb::cast<LoftRef>(b).node; }, nb::arg("other").none())
        .def("__hash__", [](const LoftRef& r) { return (Py_hash_t)r.node; });

    AddProperty<Node>("loft_mesh",
        [](const Node& n) -> std::optional<LoftRef> { return HasLoft(n) ? std::optional<LoftRef>(LoftRef{n.handle}) : std::nullopt; },
        "The node's parametric loft definition (LoftMesh), or None.");
    m.def("loft_mesh", &CreateLoft, "name"_a, "sections"_a, "parameters"_a = nb::none(), nb::kw_only(),
          "profile_closed"_a = true, "close_path"_a = false, "caps"_a = false, "reverse"_a = false,
          "Create a parametric quad loft (Editable Poly) through matched cross sections and return its node: "
          "loft_mesh('Rail', [[(0, 0, 0), ('w', 0, 0), ('w', 'd', 0)], [(0, 0, 'h'), ('w', 0, 'h'), ('w', 'd', 'h')]], "
          "{'w': 10, 'd': 4, 'h': 50}). Sections have the same point count in corresponding order, in world space; "
          "coordinates are numbers or expressions over parameters (+ - * / % **, sin/cos in radians, sqrt, abs, "
          "min, max, pi). profile_closed wraps each section, close_path joins the last section to the first, caps "
          "adds end faces (closed profiles, open path), reverse flips the winding.");
}

} // namespace MX_ABI_TAG
} // namespace mx
