// Scene identity and state: hierarchy paths and NodeRefs, the change journal
// (marks and the edits after them), preflighted batch edits (scene_patch),
// scene QA, queries over every node, instances, dependents, reference trees
// and undo.
//
// Batch edits, QA, the journal, undo and the class reports reuse the bridge's
// handlers in-process. Per-node reads walk the SDK, so their class names are
// the ones Node.class_name reports.
#include "mx_common.h"

#include "mcp_bridge/node_ref.h"

#include <iInstanceMgr.h>
#include <ilayermanager.h>

#include <climits>
#include <map>
#include <set>
#include <unordered_set>

namespace mx {
inline namespace MX_ABI_TAG {

using json = nlohmann::json;

namespace {

struct SceneMark {
    unsigned long long seq = 0;        // every journal event, selection included
    unsigned long long scene_seq = 0;  // persistent edits only: what expected= compares
};

struct SceneChange {
    unsigned long long seq = 0;
    unsigned long long scene_seq = 0;
    std::string type;
    std::string name;
    std::optional<Node> node;
    nb::object details;
};

// Public classes of some superclasses that share one name.
struct ClassMatch {
    std::vector<std::pair<SClass_ID, Class_ID>> ids;
    std::vector<std::string> names;  // every MAXScript name seen, for suggestions
};

// The nodes whose references reach a target with no other node in between:
// the owner of a modifier, material or controller, or the user of a
// constraint target, but not nodes that depend on that user in turn.
class DirectNodes : public DependentEnumProc {
public:
    explicit DirectNodes(ReferenceTarget* from) : from_(from) {}
    std::vector<INode*> nodes;

    int proc(ReferenceMaker* maker) override {
        // The enumeration starts with the target itself.
        if (!maker || maker == from_ || maker->SuperClassID() != BASENODE_CLASS_ID) return DEP_ENUM_CONTINUE;
        INode* node = static_cast<INode*>(maker);
        if (InScene(node) && !node->IsRootNode()) nodes.push_back(node);
        return DEP_ENUM_SKIP;
    }

private:
    ReferenceTarget* from_;
};

using NodeRead = nb::object (*)(INode*);

// A node-level property find() can match.
struct NodeProperty {
    const char* name;
    NodeRead read;
};

const NodeProperty kNodeProperties[] = {
    {"hidden", [](INode* n) -> nb::object { return nb::bool_(n->IsHidden() != 0); }},
    {"frozen", [](INode* n) -> nb::object { return nb::bool_(n->IsFrozen() != 0); }},
    {"renderable", [](INode* n) -> nb::object { return nb::bool_(n->Renderable() != 0); }},
    {"cast_shadows", [](INode* n) -> nb::object { return nb::bool_(n->CastShadows() != 0); }},
    {"receive_shadows", [](INode* n) -> nb::object { return nb::bool_(n->RcvShadows() != 0); }},
    {"primary_visibility", [](INode* n) -> nb::object { return nb::bool_(n->GetPrimaryVisibility()); }},
    {"secondary_visibility", [](INode* n) -> nb::object { return nb::bool_(n->GetSecondaryVisibility()); }},
    {"material", [](INode* n) -> nb::object { return nb::cast(NodeMaterial(Wrap(n))); }},
};

// One keyword filter of find().
struct PropertyFilter {
    std::string key;
    nb::object wanted;
    NodeRead read = nullptr;  // null: a base object parameter
    bool seen = false;        // some node had the property
};

constexpr size_t kJournalPage = 256;  // events per scene_delta query

const std::vector<std::string> kPatchOps = {"rename", "transform", "set_flags", "set_parent"};

// As scene_qa_handlers.cpp names them.
const std::vector<std::string> kChecks = {"name_collisions", "empty_names", "invalid_transforms",
                                          "degenerate_transforms", "transform_risks", "far_from_origin",
                                          "hierarchy_cycles", "group_integrity", "timeline"};
const std::vector<std::string> kFixes = {"name_collisions", "empty_names"};

// As learning_handlers.cpp names them for map_class_relationships.
const std::vector<std::string> kRelationSupers = {"geometry", "modifier", "material", "texturemap",
                                                  "helper", "light", "camera"};

const std::vector<SClass_ID> kObjectSupers = {GEOMOBJECT_CLASS_ID, SHAPE_CLASS_ID, HELPER_CLASS_ID,
                                              LIGHT_CLASS_ID, CAMERA_CLASS_ID, WSM_OBJECT_CLASS_ID};
// Everything find(uses=...) can look for in a node's references.
const std::vector<SClass_ID> kUsedSupers = {
    OSM_CLASS_ID, WSM_CLASS_ID, MATERIAL_CLASS_ID, TEXMAP_CLASS_ID, CTRL_FLOAT_CLASS_ID, CTRL_POINT3_CLASS_ID,
    CTRL_POSITION_CLASS_ID, CTRL_ROTATION_CLASS_ID, CTRL_SCALE_CLASS_ID, CTRL_MATRIX3_CLASS_ID,
    GEOMOBJECT_CLASS_ID, SHAPE_CLASS_ID, HELPER_CLASS_ID, LIGHT_CLASS_ID, CAMERA_CLASS_ID};

} // namespace

// ── Reports ──────────────────────────────────────────────────────

// "sceneSeq" -> "scene_seq"; snake_case keys stay as they are.
static std::string SnakeCase(const std::string& key) {
    std::string out;
    for (const char c : key) {
        if (std::isupper((unsigned char)c)) {
            if (!out.empty()) out += '_';
            out += (char)std::tolower((unsigned char)c);
        } else {
            out += c;
        }
    }
    return out;
}

static bool IsPoint(const json& v) {
    return v.type() == json::value_t::array && v.size() == 3 &&
           std::all_of(v.begin(), v.end(), [](const json& x) { return x.is_number_float(); });
}

static bool IsIdentity(const json& v) {
    return v.is_object() && v.contains("handle") && v["handle"].is_number_integer() && v.contains("name");
}

// A handler's reply as Python values with snake_case keys. [x, y, z] floats
// become tuples and node identities ({handle, name, ...}) the Nodes they name,
// except under before/after, which record what a node was.
static nb::object ToReport(const json& v, const std::string& key = std::string()) {
    if (IsIdentity(v) && key != "before" && key != "after")
        return nb::cast(Node{(AnimHandle)v["handle"].get<unsigned long long>()});
    if (v.is_object()) {
        nb::dict out;
        for (auto it = v.begin(); it != v.end(); ++it) {
            const std::string name = SnakeCase(it.key());
            out[name.c_str()] = ToReport(it.value(), name);
        }
        return out;
    }
    if (IsPoint(v)) return nb::make_tuple(v[0].get<double>(), v[1].get<double>(), v[2].get<double>());
    if (v.type() == json::value_t::array) {
        nb::list items;
        for (const json& item : v) items.append(ToReport(item, key));
        return items;
    }
    return ToPython(v);
}

// A name or a list of names, each one of `known`, lowercased.
static std::vector<std::string> Tokens(nb::handle value, const std::vector<std::string>& known, const std::string& what) {
    std::vector<std::string> names;
    if (nb::isinstance<nb::str>(value)) {
        names.push_back(nb::cast<std::string>(value));
    } else if (nb::isinstance<nb::list>(value) || nb::isinstance<nb::tuple>(value)) {
        for (nb::handle item : value) {
            if (!nb::isinstance<nb::str>(item)) throw BadParam(what + " names are strings, not " + TypeNameOf(item));
            names.push_back(nb::cast<std::string>(item));
        }
    } else {
        throw BadParam(what + " takes a name or a list of names: " + Join(known));
    }
    for (std::string& name : names) {
        const std::string token = Lower(name);
        if (std::find(known.begin(), known.end(), token) == known.end())
            throw BadParam("unknown " + what + " '" + name + "'." + Suggest(name, known) + " Known: " + Join(known));
        name = token;
    }
    return names;
}

static bool Contains(const std::vector<std::string>& names, const char* name) {
    return std::find(names.begin(), names.end(), name) != names.end();
}

// ── Node references ──────────────────────────────────────────────

// NodeRefs (node_ref.h) reports errors as JSON payloads: {code, message, hint}.
[[noreturn]] static void ThrowRefError(const char* text) {
    const json payload = json::parse(text, nullptr, false);
    if (!payload.is_object()) throw BadParam(text);
    std::string message = payload.value("message", std::string(text));
    const std::string code = payload.value("code", "");
    const json hint = payload.value("hint", json::object());
    if (code == "NOT_FOUND") throw NotFound(message);
    if (code == "AMBIGUOUS") {
        std::vector<std::string> handles;
        for (const json& candidate : hint.value("candidates", json::array()))
            handles.push_back("#" + std::to_string(candidate.value("handle", 0ULL)));
        throw Ambiguous(message + " (" + Join(handles) + "); pass the handle");
    }
    const json resolved = hint.value("resolved", json::object());
    if (resolved.contains("path")) message += " (it resolves to '" + resolved.value("path", "") + "')";
    throw BadParam(message);
}

// An absolute hierarchy path: one exact child name per segment.
static INode* ResolvePath(const std::string& path) {
    std::vector<std::string> segments;
    try {
        segments = NodeRefs::ParsePath(path);
    } catch (const std::runtime_error& e) {
        ThrowRefError(e.what());
    }
    INode* current = GetCOREInterface()->GetRootNode();
    std::string walked;
    for (const std::string& segment : segments) {
        std::vector<INode*> matches;
        std::vector<std::string> names;
        for (int i = 0; i < current->NumberOfChildren(); ++i) {
            INode* child = current->GetChildNode(i);
            names.push_back(NodeName(child));
            if (names.back() == segment) matches.push_back(child);
        }
        const std::string under = walked.empty() ? "the scene root" : "'" + walked + "'";
        if (matches.empty())
            throw NotFound("no child '" + segment + "' under " + under + "." + Suggest(segment, names));
        if (matches.size() > 1) {
            std::vector<std::string> handles;
            for (INode* node : matches) handles.push_back("#" + std::to_string(Animatable::GetHandleByAnim(node)));
            throw Ambiguous(std::to_string(matches.size()) + " children of " + under + " are named '" + segment +
                            "' (" + Join(handles) + "); pass the handle");
        }
        current = matches.front();
        walked += "/" + NodeRefs::EncodePathSegment(segment);
    }
    return current;
}

// A Node, name, handle, '/Parent/Child' path, or a {handle, name, path} dict
// whose parts must name the same node (the bridge's NodeRef rules).
static INode* ResolveRef(nb::handle ref) {
    RequireMain();
    if (nb::isinstance<nb::str>(ref)) {
        const std::string text = nb::cast<std::string>(ref);
        if (!text.empty() && text[0] == '/') return ResolvePath(text);
        return Resolve(FindNode(ref));
    }
    if (nb::isinstance<nb::dict>(ref)) {
        INode* node = nullptr;
        try {
            node = NodeRefs::Resolve(FromPython(ref));
        } catch (const MaxError&) {
            throw;
        } catch (const std::runtime_error& e) {
            ThrowRefError(e.what());
        }
        if (!InScene(node)) throw NotFound("node " + ReprOf(ref) + " is no longer in the scene");
        return node;
    }
    if (nb::isinstance<Node>(ref) || (nb::isinstance<nb::int_>(ref) && !nb::isinstance<nb::bool_>(ref)))
        return Resolve(FindNode(ref));
    throw BadParam("a node is a Node, a name, a handle, a '/Parent/Child' path or a {'handle', 'name', 'path'} dict, "
                   "not " + TypeNameOf(ref));
}

// f(), with errors prefixed by where they happened ("operation 2: ").
template <typename F>
static auto InContext(const std::string& where, F&& f) -> decltype(f()) {
    try {
        return f();
    } catch (const NotFound& e) {
        throw NotFound(where + e.what());
    } catch (const Ambiguous& e) {
        throw Ambiguous(where + e.what());
    } catch (const BadParam& e) {
        throw BadParam(where + e.what());
    }
}

// ── The change journal ───────────────────────────────────────────
// The bridge journals node events (scene_journal.cpp) and is read through its
// handlers: scene_qa's scan with no checks flushes pending events and reports
// scene_seq; scene_delta's journal query reports seq and the events after one.

static SceneMark CurrentMark() {
    const json scan = NativeJson("native:scene_qa_scan", {{"checks", json::array()}, {"scope", "selection"}});
    // No event is newer than the largest seq, so the reply only reports the current one.
    const json delta = NativeJson("native:scene_delta", {{"unchanged_since", ULLONG_MAX}});
    return SceneMark{delta.value("currentSeq", 0ULL), scan.value("scene_seq", 0ULL)};
}

// A Mark's seq or scene_seq, or that number itself.
static unsigned long long SeqOf(nb::handle value, bool scene, const std::string& what) {
    if (nb::isinstance<SceneMark>(value)) {
        const SceneMark mark = nb::cast<SceneMark>(value);
        return scene ? mark.scene_seq : mark.seq;
    }
    if (nb::isinstance<nb::int_>(value) && !nb::isinstance<nb::bool_>(value) && nb::cast<long long>(value) >= 0)
        return (unsigned long long)nb::cast<long long>(value);
    throw BadParam(what + " is a Mark from mark() or its " + (scene ? "scene_seq" : "seq") + ", not " + ReprOf(value));
}

static void CheckExpected(unsigned long long expected) {
    const unsigned long long now = CurrentMark().scene_seq;
    if (now != expected)
        throw MaxError("the scene changed since scene_seq " + std::to_string(expected) + " (now " + std::to_string(now) +
                       "); re-read what the edit depends on, then pass expected=mark()");
}

static SceneChange ToChange(const json& event) {
    SceneChange change;
    change.seq = event.value("seq", 0ULL);
    change.scene_seq = event.value("mutationSeq", 0ULL);
    change.type = event.value("type", "");
    change.name = event.value("name", "");
    if (event.contains("handle")) change.node = Node{(AnimHandle)event["handle"].get<unsigned long long>()};
    change.details = event.contains("details") ? ToReport(event["details"]) : nb::none();
    return change;
}

static std::vector<SceneChange> Changes(nb::handle since) {
    const unsigned long long from = SeqOf(since, false, "since");
    const SceneMark now = CurrentMark();
    if (from > now.seq)
        throw BadParam("seq " + std::to_string(from) + " is ahead of the journal (now " + std::to_string(now.seq) +
                       "); pass a Mark from mark()");
    if (from == 0 && now.seq > 0)
        throw MaxError("the journal lists changes after seq 1 at the earliest; take a new mark()");
    std::vector<SceneChange> out;
    unsigned long long cursor = from;
    unsigned long long listed = 0;
    while (cursor < now.seq) {
        const json reply = NativeJson("native:scene_delta", {{"unchanged_since", cursor}});
        const json events = reply.value("changes", json::array());
        for (const json& event : events) {
            ++listed;
            cursor = event.value("seq", cursor);
            if (event.contains("mutationSeq")) out.push_back(ToChange(event));
        }
        if (events.size() < kJournalPage) break;
    }
    // Each event has its own seq: fewer events than seqs means some were dropped.
    if (listed != now.seq - from)
        throw MaxError("the journal no longer holds every change since seq " + std::to_string(from) +
                       " (a reset or file open clears it, and it keeps the last 4096 events); take a new mark()");
    return out;
}

// ── Batch edits ──────────────────────────────────────────────────

// One scene_patch operation, its target and parent resolved to handles.
static json PatchOp(nb::handle op, size_t index) {
    return InContext("operation " + std::to_string(index) + ": ", [&] {
        if (!nb::isinstance<nb::dict>(op))
            throw BadParam("an operation is a dict such as {'op': 'rename', 'target': node, 'name': 'Wall'}");
        nb::dict fields = nb::borrow<nb::dict>(op);
        const nb::object kind = fields.contains("op") ? nb::object(fields["op"]) : nb::none();
        if (!nb::isinstance<nb::str>(kind)) throw BadParam("'op' is one of " + Join(kPatchOps));
        const std::string name = Lower(nb::cast<std::string>(kind));
        if (!Contains(kPatchOps, name.c_str()))
            throw BadParam("unknown op '" + name + "'." + Suggest(name, kPatchOps) + " Ops: " + Join(kPatchOps));
        if (!fields.contains("target")) throw BadParam(name + " needs a 'target' node");
        json out = FromPython(op);
        const nb::object target = fields["target"];
        out["target"] = HandlerHelpers::NodeHandle(ResolveRef(target));
        const nb::object parent = fields.contains("parent") ? nb::object(fields["parent"]) : nb::none();
        if (!parent.is_none()) out["parent"] = HandlerHelpers::NodeHandle(ResolveRef(parent));
        return out;
    });
}

static nb::object Patch(nb::handle ops, nb::handle expected, bool dryRun, const std::string& label) {
    RequireMain();
    if (!nb::isinstance<nb::list>(ops) && !nb::isinstance<nb::tuple>(ops))
        throw BadParam("patch takes a list of operations, not " + TypeNameOf(ops));
    json operations = json::array();
    size_t index = 0;
    for (nb::handle op : ops) operations.push_back(PatchOp(op, index++));
    if (operations.empty()) throw BadParam("patch needs at least one operation");
    json args = {{"operations", operations}, {"dry_run", dryRun}, {"label", label}};
    if (!expected.is_none()) {
        const unsigned long long seq = SeqOf(expected, true, "expected");
        CheckExpected(seq);
        args["expected_scene_seq"] = seq;
    }
    return ToReport(NativeJson("native:scene_patch", args));
}

static std::vector<Node> Rename(nb::dict renames) {
    RequireMain();
    json operations = json::array();
    std::vector<Node> nodes;
    for (auto [key, value] : renames) {
        const nb::handle target = key;
        const std::string where = "rename " + ReprOf(target) + ": ";
        INode* node = InContext(where, [&] { return ResolveRef(target); });
        if (!nb::isinstance<nb::str>(value)) throw BadParam(where + "the new name is a string, not " + TypeNameOf(value));
        operations.push_back({{"op", "rename"}, {"target", HandlerHelpers::NodeHandle(node)},
                              {"name", nb::cast<std::string>(value)}});
        nodes.push_back(Wrap(node));
    }
    if (operations.empty()) throw BadParam("rename takes {node: new_name, ...} with at least one entry");
    NativeJson("native:scene_patch", {{"operations", operations}, {"label", "MCP Rename"}});
    return nodes;
}

// ── Scene QA ─────────────────────────────────────────────────────

static nb::object SceneQA(nb::handle checks, nb::handle nodes, nb::handle fix, bool dryRun, nb::handle expected,
                          std::optional<double> farFromOrigin) {
    RequireMain();
    const bool fixing = !fix.is_none();
    json args = {{"action", fixing ? "fix" : "scan"}, {"dry_run", dryRun}, {"scope", nodes.is_none() ? "scene" : "targets"}};
    if (!checks.is_none()) {
        const std::vector<std::string> names = Tokens(checks, kChecks, "check");
        if (Contains(names, "far_from_origin") && !farFromOrigin)
            throw BadParam("the far_from_origin check needs far_from_origin=<distance>");
        args["checks"] = names;
    }
    if (fixing) {
        const std::vector<std::string> names = Tokens(fix, kFixes, "fix");
        if (names.empty()) throw BadParam("fix needs a repair: " + Join(kFixes));
        args["fixes"] = names;
    }
    if (farFromOrigin) {
        if (!std::isfinite(*farFromOrigin) || *farFromOrigin <= 0) throw BadParam("far_from_origin is a distance above 0");
        args["far_origin_threshold"] = *farFromOrigin;
    }
    if (!nodes.is_none()) {
        json handles = json::array();
        for (INode* node : NodeList(nodes, "scene_qa")) handles.push_back(HandlerHelpers::NodeHandle(node));
        args["handles"] = handles;
    }
    const bool apply = fixing && !dryRun;
    if (!expected.is_none()) {
        if (!apply) throw BadParam("expected guards a fix that applies; a scan or a dry run changes nothing");
        const unsigned long long seq = SeqOf(expected, true, "expected");
        CheckExpected(seq);
        args["expected_scene_seq"] = seq;
    }
    return ToReport(NativeJson(apply ? "native:scene_qa_fix" : "native:scene_qa_scan", args));
}

// ── Classes ──────────────────────────────────────────────────────

// Every public class of the superclasses named `name`: by MAXScript name, else
// by UI name. A name can belong to classes of several kinds; a query takes all.
static ClassMatch MatchClasses(const std::string& name, const std::vector<SClass_ID>& supers) {
    const std::wstring wanted = Utf8ToWide(name);
    ClassMatch match;
    ScopedMaxScriptEvaluationContext context;
    auto& directory = ClassDirectory::GetInstance();
    for (bool scriptNames : {true, false}) {
        for (SClass_ID super : supers) {
            SubClassList* list = directory.GetClassList(super);
            if (!list) continue;
            for (int i = list->GetFirst(ACC_PUBLIC); i >= 0; i = list->GetNext(ACC_PUBLIC)) {
                ClassEntry& entry = (*list)[i];
                bool matches = false;
                if (scriptNames) {
                    Class_ID lookup = entry.ClassID();
                    MAXClass* cls = MAXClass::lookup_class(&lookup, super, false);
                    const MCHAR* script = cls && cls->name ? cls->name->to_string() : nullptr;
                    if (script) match.names.push_back(WideToUtf8(script));
                    matches = script && _wcsicmp(script, wanted.c_str()) == 0;
                } else {
                    matches = _wcsicmp(entry.ClassName().data(), wanted.c_str()) == 0 ||
                              _wcsicmp(entry.NonLocalizedClassName().data(), wanted.c_str()) == 0;
                }
                if (matches) match.ids.push_back({super, entry.ClassID()});
            }
        }
        if (!match.ids.empty()) break;
    }
    return match;
}

static ClassMatch RequireClasses(const std::string& name, const std::vector<SClass_ID>& supers, const std::string& kind) {
    ClassMatch match = MatchClasses(name, supers);
    if (match.ids.empty()) throw NotFound("no " + kind + " class '" + name + "'." + Suggest(name, match.names));
    return match;
}

static bool HasClass(const ClassMatch& match, Animatable* anim) {
    for (const auto& [super, id] : match.ids)
        if (anim->SuperClassID() == super && anim->ClassID() == id) return true;
    return false;
}

// The script name, else the class name, of anything a node references.
static std::string ClassLabel(ReferenceTarget* target) {
    const std::string name = HandlerHelpers::MaxScriptVisibleClassName(target);
    return name.empty() ? HandlerHelpers::SanitizeScriptName(WideToUtf8(target->ClassName().data())) : name;
}

static const char* SuperLabel(SClass_ID id) {
    switch (id) {
    case BASENODE_CLASS_ID: return "node";
    case GEOMOBJECT_CLASS_ID: return "geometry";
    case SHAPE_CLASS_ID: return "shape";
    case HELPER_CLASS_ID: return "helper";
    case LIGHT_CLASS_ID: return "light";
    case CAMERA_CLASS_ID: return "camera";
    case MATERIAL_CLASS_ID: return "material";
    case TEXMAP_CLASS_ID: return "texturemap";
    case OSM_CLASS_ID: return "modifier";
    case WSM_CLASS_ID: return "space_warp_modifier";
    case WSM_OBJECT_CLASS_ID: return "space_warp";
    case GEN_DERIVOB_CLASS_ID: case DERIVOB_CLASS_ID: case WSM_DERIVOB_CLASS_ID: return "derived_object";
    case LAYER_CLASS_ID: return "layer";
    case CTRL_FLOAT_CLASS_ID: case CTRL_POINT2_CLASS_ID: case CTRL_POINT3_CLASS_ID: case CTRL_POINT4_CLASS_ID:
    case CTRL_FRGBA_CLASS_ID: case CTRL_MATRIX3_CLASS_ID: case CTRL_POSITION_CLASS_ID: case CTRL_ROTATION_CLASS_ID:
    case CTRL_SCALE_CLASS_ID: case CTRL_MORPH_CLASS_ID: return "controller";
    default: return "other";
    }
}

static std::string TargetName(ReferenceTarget* target) {
    switch (target->SuperClassID()) {
    case BASENODE_CLASS_ID: return NodeName(static_cast<INode*>(target));
    case MATERIAL_CLASS_ID: case TEXMAP_CLASS_ID: return WideToUtf8(static_cast<MtlBase*>(target)->GetName().data());
    case OSM_CLASS_ID: case WSM_CLASS_ID: return WideToUtf8(static_cast<Modifier*>(target)->GetName(false).data());
    default: return std::string();
    }
}

// ── Node relations ───────────────────────────────────────────────

static std::vector<INode*> DirectDependents(ReferenceTarget* target) {
    DirectNodes proc(target);
    target->DoEnumDependents(&proc);
    return proc.nodes;
}

static std::vector<Node> Instances(const Node& n) {
    INode* node = Resolve(n);
    INodeTab tab;
    IInstanceMgr::GetInstanceMgr()->GetInstances(*node, tab);
    std::vector<Node> out;
    for (int i = 0; i < tab.Count(); ++i)
        if (InScene(tab[i])) out.push_back(Wrap(tab[i]));
    return out;
}

static std::vector<Node> Dependents(const Node& n) {
    std::vector<Node> out;
    for (INode* node : DirectDependents(Resolve(n))) out.push_back(Wrap(node));
    return out;
}

// A target and its references, depth more levels down. A target met before
// is marked seen, and nodes other than the root are listed, not expanded.
static nb::dict RefTree(ReferenceTarget* target, int index, int depth, bool root,
                        std::unordered_set<ReferenceTarget*>& seen) {
    nb::dict entry;
    if (index >= 0) entry["index"] = index;
    entry["class"] = ClassLabel(target);
    entry["superclass"] = SuperLabel(target->SuperClassID());
    const std::string name = TargetName(target);
    if (!name.empty()) entry["name"] = name;
    if (!seen.insert(target).second) {
        entry["seen"] = true;
        return entry;
    }
    if (!root && target->SuperClassID() == BASENODE_CLASS_ID) return entry;
    nb::list refs;
    int count = 0;
    for (int i = 0; i < target->NumRefs(); ++i) {
        ReferenceTarget* ref = target->GetReference(i);
        if (!ref) continue;
        ++count;
        if (depth > 0) refs.append(RefTree(ref, i, depth - 1, false, seen));
    }
    if (nb::len(refs)) entry["references"] = refs;
    else if (count) entry["ref_count"] = count;
    return entry;
}

static nb::dict References(const Node& n, int depth) {
    INode* node = Resolve(n);
    if (depth < 0 || depth > 8) throw BadParam("depth is 0-8, not " + std::to_string(depth));
    std::unordered_set<ReferenceTarget*> seen;
    return RefTree(node, -1, depth, true, seen);
}

// ── Queries ──────────────────────────────────────────────────────

// Nodes whose references reach an instance of a matched class, directly or
// through modifiers, materials and controllers, but not through other nodes.
static std::unordered_set<INode*> Users(const ClassMatch& match, const std::vector<INode*>& nodes) {
    std::unordered_set<ReferenceTarget*> visited;
    std::vector<ReferenceTarget*> stack;
    std::vector<ReferenceTarget*> found;
    for (INode* node : nodes) {
        stack.push_back(node);
        while (!stack.empty()) {
            ReferenceTarget* target = stack.back();
            stack.pop_back();
            for (int i = 0; i < target->NumRefs(); ++i) {
                ReferenceTarget* ref = target->GetReference(i);
                if (!ref || ref->SuperClassID() == BASENODE_CLASS_ID || !visited.insert(ref).second) continue;
                if (HasClass(match, ref)) found.push_back(ref);
                stack.push_back(ref);
            }
        }
    }
    std::unordered_set<INode*> users;
    for (ReferenceTarget* target : found)
        for (INode* node : DirectDependents(target)) users.insert(node);
    return users;
}

static std::string Bare(const std::string& name) {
    std::string out;
    for (const char c : Lower(name))
        if (c != '_') out += c;
    return out;
}

// A node-level property by its snake_case or MAXScript name (cast_shadows, isHidden, ...).
static NodeRead NodePropertyRead(const std::string& key) {
    std::string bare = Bare(key);
    if (bare == "ishidden" || bare == "isfrozen") bare = bare.substr(2);
    for (const NodeProperty& property : kNodeProperties)
        if (Bare(property.name) == bare) return property.read;
    return nullptr;
}

// A base object parameter or MAXScript property, or nullopt when it has none.
static std::optional<nb::object> BaseValue(Object* base, const std::string& key, TimeValue t) {
    if (!base) return std::nullopt;
    if (auto ref = FindParam(base, key)) return GetParam(*ref, t);
    return MxsGet(base, key, t);
}

static bool IsNumber(nb::handle v) { return nb::isinstance<nb::int_>(v) || nb::isinstance<nb::float_>(v); }

static std::vector<nb::handle> Items(nb::handle sequence) {
    std::vector<nb::handle> items;
    for (nb::handle item : sequence) items.push_back(item);
    return items;
}

// find()'s comparison: numbers within a relative 1e-4, text ignoring case (a
// node or material by its name), sequences item by item, else Python's ==.
static bool Same(nb::handle actual, nb::handle wanted) {
    if (nb::isinstance<nb::str>(wanted)) {
        nb::object text = nb::borrow(actual);
        if (!nb::isinstance<nb::str>(actual)) {
            if (actual.is_none() || !nb::hasattr(actual, "name")) return false;
            text = actual.attr("name");
        }
        return nb::isinstance<nb::str>(text) &&
               _stricmp(nb::cast<std::string>(text).c_str(), nb::cast<std::string>(wanted).c_str()) == 0;
    }
    if (IsNumber(wanted) && IsNumber(actual)) {
        const double a = nb::cast<double>(actual);
        const double b = nb::cast<double>(wanted);
        return std::fabs(a - b) <= 1e-4 * std::max({1.0, std::fabs(a), std::fabs(b)});
    }
    const bool sequences = (nb::isinstance<nb::list>(wanted) || nb::isinstance<nb::tuple>(wanted)) &&
                           (nb::isinstance<nb::list>(actual) || nb::isinstance<nb::tuple>(actual));
    if (sequences) {
        const std::vector<nb::handle> xs = Items(actual);
        const std::vector<nb::handle> ys = Items(wanted);
        if (xs.size() != ys.size()) return false;
        for (size_t i = 0; i < xs.size(); ++i)
            if (!Same(xs[i], ys[i])) return false;
        return true;
    }
    return actual.equal(wanted);
}

static std::vector<std::string> LayerNames() {
    std::vector<std::string> names;
    ILayerManager* manager = GetCOREInterface13()->GetLayerManager();
    for (int i = 0; manager && i < manager->GetLayerCount(); ++i)
        if (ILayer* layer = manager->GetLayer(i)) names.push_back(WideToUtf8(layer->GetName().data()));
    return names;
}

static void RequireLayer(const std::string& name) {
    const std::vector<std::string> names = LayerNames();
    for (const std::string& layer : names)
        if (_stricmp(layer.c_str(), name.c_str()) == 0) return;
    throw NotFound("no layer '" + name + "'." + Suggest(name, names));
}

[[noreturn]] static void ThrowUnknownProperty(const PropertyFilter& filter, const std::vector<Object*>& bases) {
    std::vector<std::string> names;
    for (const NodeProperty& property : kNodeProperties) names.push_back(property.name);
    for (Object* base : bases)
        for (const std::string& name : ParamNames(base)) names.push_back(name);
    throw UnknownParam("no node matching the other filters has a property '" + filter.key + "'." +
                       Suggest(filter.key, names));
}

static std::vector<Node> Find(const std::optional<std::string>& pattern, const std::optional<std::string>& cls,
                              const std::optional<std::string>& layer, bool rootsOnly,
                              const std::optional<std::string>& uses, nb::kwargs props) {
    RequireMain();
    const std::vector<INode*> all = AllNodes();
    // A class name without wildcards is looked up, so a typo fails instead of matching nothing.
    std::optional<ClassMatch> classes;
    if (cls && cls->find('*') == std::string::npos) classes = RequireClasses(*cls, kObjectSupers, "object");
    if (layer) RequireLayer(*layer);
    std::unordered_set<INode*> users;
    if (uses) users = Users(RequireClasses(*uses, kUsedSupers, "modifier, material, texmap, controller or object"), all);
    std::vector<PropertyFilter> filters;
    for (auto [key, value] : props) {
        const std::string name = nb::cast<std::string>(key);
        filters.push_back({name, nb::borrow(value), NodePropertyRead(name)});
    }

    const TimeValue t = Now();
    std::vector<Node> out;
    std::vector<Object*> bases;  // one per class among the candidates, for suggestions
    for (INode* node : all) {
        if (pattern && !HandlerHelpers::WildcardMatch(NodeName(node), *pattern)) continue;
        if (rootsOnly && !node->GetParentNode()->IsRootNode()) continue;
        if (layer && _stricmp(HandlerHelpers::NodeLayerName(node).c_str(), layer->c_str()) != 0) continue;
        Object* base = BaseObject(node);
        if (classes && !(base && HasClass(*classes, base))) continue;
        if (cls && !classes && !HandlerHelpers::WildcardMatch(ClassOf(node), *cls)) continue;
        if (uses && !users.count(node)) continue;
        if (base && bases.size() < 32 &&
            std::none_of(bases.begin(), bases.end(), [&](Object* b) { return b->ClassID() == base->ClassID(); }))
            bases.push_back(base);
        // Every filter is read, so `seen` tells a misspelled property from an unmatched value.
        bool keep = true;
        for (PropertyFilter& filter : filters) {
            const std::optional<nb::object> value =
                filter.read ? std::optional<nb::object>(filter.read(node)) : BaseValue(base, filter.key, t);
            filter.seen = filter.seen || value.has_value();
            keep = keep && value && Same(*value, filter.wanted);
        }
        if (keep) out.push_back(Wrap(node));
    }
    if (!bases.empty())
        for (const PropertyFilter& filter : filters)
            if (!filter.seen) ThrowUnknownProperty(filter, bases);
    return out;
}

static nb::dict Overview() {
    RequireMain();
    std::map<std::string, int> classes, materials, modifiers;
    std::set<std::string> layers;
    int hidden = 0, frozen = 0, roots = 0;
    const std::vector<INode*> nodes = AllNodes();
    for (INode* node : nodes) {
        hidden += node->IsHidden() ? 1 : 0;
        frozen += node->IsFrozen() ? 1 : 0;
        roots += node->GetParentNode()->IsRootNode() ? 1 : 0;
        ++classes[ClassOf(node)];
        if (Mtl* mtl = node->GetMtl()) ++materials[WideToUtf8(mtl->GetName().data())];
        for (const ModifierRef& ref : NodeModifiers(Wrap(node)))
            ++modifiers[HandlerHelpers::MaxScriptVisibleClassName(Animatable::GetAnimByHandle(ref.mod))];
        layers.insert(HandlerHelpers::NodeLayerName(node));
    }
    auto counts = [](const std::map<std::string, int>& values) {
        nb::dict out;
        for (const auto& [name, count] : values) out[name.c_str()] = count;
        return out;
    };
    nb::dict out;
    out["object_count"] = nodes.size();
    out["hidden_count"] = hidden;
    out["frozen_count"] = frozen;
    out["root_count"] = roots;
    out["class_counts"] = counts(classes);
    out["materials"] = counts(materials);
    out["modifiers"] = counts(modifiers);
    out["layers"] = std::vector<std::string>(layers.begin(), layers.end());
    return out;
}

static nb::object ClassRelationships(const std::optional<std::string>& pattern, const std::optional<std::string>& superclass) {
    json args = {{"limit", 100000}};
    if (pattern) args["pattern"] = *pattern;
    if (superclass) args["superclass"] = Tokens(nb::str(superclass->c_str()), kRelationSupers, "superclass").front();
    return ToReport(NativeJson("native:map_class_relationships", args).value("relationships", json::array()));
}

static nb::dict ScenePatterns() {
    const json result = NativeJson("native:learn_scene_patterns", json::object());
    nb::dict out;
    for (auto it = result.begin(); it != result.end(); ++it) {
        const std::string key = SnakeCase(it.key());
        if (it.value().type() != json::value_t::array) {
            out[key.c_str()] = ToPython(it.value());
            continue;
        }
        // [{pattern, count}], most common first, as {pattern: count}.
        nb::dict counts;
        for (const json& row : it.value()) {
            const std::string pattern = row.value("pattern", "");
            counts[pattern.c_str()] = row.value("count", 0);
        }
        out[key.c_str()] = counts;
    }
    return out;
}

// ── Binding ──────────────────────────────────────────────────────

void BindScene(nb::module_& m) {
    nb::class_<SceneMark>(m, "Mark",
        "A point in the scene's change journal: seq counts every event, scene_seq only persistent edits.")
        .def_ro("seq", &SceneMark::seq)
        .def_ro("scene_seq", &SceneMark::scene_seq)
        .def("__repr__", [](const SceneMark& mark) {
            return "<Mark seq=" + std::to_string(mark.seq) + " scene_seq=" + std::to_string(mark.scene_seq) + ">";
        });

    nb::class_<SceneChange>(m, "Change",
        "A journal entry (a snapshot): seq, scene_seq, type ('added', 'name_changed', 'deleted', ...), node, "
        "name (when it happened), details.")
        .def_ro("seq", &SceneChange::seq)
        .def_ro("scene_seq", &SceneChange::scene_seq)
        .def_ro("type", &SceneChange::type)
        .def_ro("name", &SceneChange::name)
        .def_prop_ro("node", [](const SceneChange& change) { return change.node; })
        .def_ro("details", &SceneChange::details)
        .def("__repr__", [](const SceneChange& change) {
            const std::string name = change.name.empty() ? "" : " '" + change.name + "'";
            return "<Change " + std::to_string(change.seq) + " " + change.type + name + ">";
        });

    AddProperty<Node>("path", [](const Node& n) { return NodeRefs::BuildPath(Resolve(n)); },
        "Hierarchy path from the scene root, '/Rig/Camera'; '/' in a name is written '~1' and '~' is '~0'.");
    AddProperty<Node>("instances", &Instances,
        "Nodes sharing this node's object (instances and references), itself included: instanced when len > 1.");
    AddProperty<Node>("dependents", &Dependents,
        "Nodes whose references reach this one directly: constraint, look-at and skin users.");
    AddMethod<Node>("references", &References, "depth"_a = 3,
        "Reference tree below the node: {'class', 'superclass', 'name', 'references': [{'index', ...}]}; "
        "'seen' marks a target listed above, and other nodes are not expanded.");

    m.def("resolve", [](nb::handle ref) { return Wrap(ResolveRef(ref)); }, "ref"_a,
          "Node by '/Parent/Child' path, name, handle or Node, or by a {'handle', 'name', 'path'} dict whose "
          "parts must agree.");
    m.def("mark", &CurrentMark,
          "Mark the change journal: m = mark(); later, changes(m) lists the edits since and patch(ops, expected=m) "
          "refuses to run if there were any.");
    m.def("changes", &Changes, "since"_a,
          "Persistent scene edits after a Mark (or its seq), oldest first; selection changes are left out.");
    m.def("patch", &Patch, "ops"_a, "expected"_a = nb::none(), "dry_run"_a = false, "label"_a = "MCP Scene Patch",
          "Validate, then apply node edits as one undo step, all or nothing: patch([{'op': 'rename', 'target': n, "
          "'name': 'Wall'}, {'op': 'transform', 'target': 'Box001', 'move': (0, 0, 10)}]). Ops: rename (name); "
          "transform (relative move, rotate in degrees, scale; coordinate_system 'world' or 'local'); set_flags "
          "(hidden, frozen, renderable, cast_shadows, receive_shadows); set_parent (parent: a node or None, "
          "keep_transform). expected=mark() taken earlier (or its scene_seq) refuses a scene changed since; dry_run=True "
          "validates only. Returns a report.");
    m.def("rename", &Rename, "renames"_a,
          "Rename nodes as one undo step, all or nothing: rename({'Box001': 'Wall_A', node: 'Wall_B'}); "
          "refuses names that would be shared.");
    m.def("scene_qa", &SceneQA, "checks"_a = nb::none(), "nodes"_a = nb::none(), "fix"_a = nb::none(),
          "dry_run"_a = false, "expected"_a = nb::none(), "far_from_origin"_a = nb::none(),
          "Scan scene hygiene (names, transforms, hierarchy, groups, timeline; no mesh checks) or repair it: "
          "scene_qa(fix=['name_collisions', 'empty_names']). nodes= limits the scope; expected= guards a fix as in patch().");
    m.def("find", &Find, "pattern"_a = nb::none(), "cls"_a = nb::none(), "layer"_a = nb::none(),
          "roots_only"_a = false, "uses"_a = nb::none(), "props"_a,
          "Nodes matching every filter: find('Wall*', cls='Box', layer='Walls', uses='TurboSmooth', hidden=False, "
          "height=50). Keywords compare node flags (hidden, frozen, renderable, cast_shadows, material, ...) or "
          "base object parameters; numbers within 1e-4, text ignoring case.");
    m.def("overview", &Overview,
          "Scene summary: object, hidden, frozen and root counts, nodes per class, material and modifier counts, "
          "layers in use.");
    m.def("class_relationships", &ClassRelationships, "pattern"_a = nb::none(), "superclass"_a = nb::none(),
          "Classes whose parameters take nodes, materials, texmaps or other references: "
          "class_relationships('*Scatter*', superclass='geometry').");
    m.def("scene_patterns", &ScenePatterns,
          "How the scene combines classes, as {pattern: count}: classes, modifier stacks and material, modifier "
          "and texmap pairings, by 3ds Max's display names.");
    m.def("undo", []() -> std::optional<std::string> {
        RequireMain();
        // Max ignores an undo while a hold is open, and the handler would then
        // report an empty stack. run_python makes the whole script one step with
        // SuperBegin, which Holding() does not report.
        if (theHold.Holding() || theHold.GetSuperBeginDepth() > 0)
            throw MaxError("undo() cannot run while an undo step is open, as it is for the rest of a run_python "
                           "script; undo from a separate script or call");
        const json result = NativeJson("native:undo_last", json::object());
        if (!result.value("undone", false)) return std::nullopt;
        return result.value("entry", std::string());
    }, "Undo the last scene operation; returns the undo entry's name ('' if unnamed), or None when nothing was undone.");
}

} // namespace MX_ABI_TAG
} // namespace mx
