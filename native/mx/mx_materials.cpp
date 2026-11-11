// Materials and texture maps: creation with parameters, map and sub-material
// slots, the nodes that use them, replacement, graph inspection, texture
// replication and the material library.
//
// Like nodes, materials and maps are held by anim handle. 3ds Max deletes one
// that nothing references once its last user lets go (clearing the slot that
// held it, for one); it then raises NotFound.
#include "mx_common.h"
#include "mx_collection.h"

// A list that holds materials and maps together.
MX_RESULT_TYPE(MaterialOrMap, "_mx.Material | _mx.Texmap")
// What a map slot holds: a map, None, or a list of them for a list parameter.
MX_RESULT_TYPE(MapSlot, "_mx.Texmap | list[_mx.Texmap | None] | None")
// Where replicate and remap_files send texture files (ReplicateArgs): {old path
// or file name: new path}.
MX_ARG_TYPE(PathMapArg, "dict[str, str]")
// What an entry of remap_files holds (RemapFiles): 'map', the material or map
// that reads the file (None when the graph does not name it), 'param', 'old'
// and 'new' text, 'exists' a bool, and 'tiles' the count of a UDIM tile list.
MX_RESULT_TYPE(RemapField, "_mx.Material | _mx.Texmap | str | int | bool | None")
// What backup_material_library saved (BackupLibrary; the handler's SaveLibrary
// builds each entry): {'source', 'path', 'count', 'saved', 'skipped', 'error'}
// per library file.
MX_RESULT_TYPE(LibraryBackups, "list[dict[str, str | int | bool]]")

#include <filesystem>
#include <fstream>
#include <functional>
#include <regex>
#include <set>
#include <unordered_map>

namespace mx {
inline namespace MX_ABI_TAG {

using json = nlohmann::json;

// ── Identity ─────────────────────────────────────────────────────

// The live material or map behind a handle; a deleted one lingers for undo.
static Animatable* Live(AnimHandle handle, SClass_ID super) {
    Animatable* anim = Animatable::GetAnimByHandle(handle);
    return anim && anim->SuperClassID() == super && !anim->TestAFlag(A_IS_DELETED) ? anim : nullptr;
}

static Mtl* ResolveMaterial(const MaterialRef& ref) {
    RequireMain();
    if (Animatable* anim = Live(ref.mtl, MATERIAL_CLASS_ID)) return static_cast<Mtl*>(anim);
    throw NotFound("material #" + std::to_string(ref.mtl) + " was deleted");
}

static Texmap* ResolveTexmap(const TexmapRef& ref) {
    RequireMain();
    if (Animatable* anim = Live(ref.tex, TEXMAP_CLASS_ID)) return static_cast<Texmap*>(anim);
    throw NotFound("texture map #" + std::to_string(ref.tex) + " was deleted");
}

static MaterialRef Wrap(Mtl* mtl) { return MaterialRef{Animatable::GetHandleByAnim(mtl)}; }
static TexmapRef Wrap(Texmap* tex) { return TexmapRef{Animatable::GetHandleByAnim(tex)}; }

static std::string NameOf(MtlBase* base) { return WideToUtf8(base->GetName().data()); }

// "'Brick' PhysicalMaterial", as reprs and network trees show one.
static std::string Label(MtlBase* base) {
    return "'" + NameOf(base) + "' " + HandlerHelpers::MaxScriptVisibleClassName(base);
}

static std::string Repr(const char* kind, AnimHandle handle, SClass_ID super) {
    RequireMain();
    Animatable* anim = Live(handle, super);
    if (!anim) return std::string("<") + kind + " #" + std::to_string(handle) + " deleted>";
    return std::string("<") + kind + " " + Label(static_cast<MtlBase*>(anim)) + ">";
}

// The live reference target behind an Object.
static ReferenceTarget* ResolveObject(const ObjectRef& ref) {
    RequireMain();
    Animatable* anim = Animatable::GetAnimByHandle(ref.handle);
    if (!anim || anim->TestAFlag(A_IS_DELETED) || !anim->IsRefMaker() || !static_cast<ReferenceMaker*>(anim)->IsRefTarget())
        throw NotFound("object #" + std::to_string(ref.handle) + " was deleted");
    return static_cast<ReferenceTarget*>(anim);
}

nb::object RefToPython(ReferenceTarget* target) {
    if (!target) return nb::none();
    switch (target->SuperClassID()) {
    case MATERIAL_CLASS_ID: return nb::cast(Wrap(static_cast<Mtl*>(target)));
    case TEXMAP_CLASS_ID: return nb::cast(Wrap(static_cast<Texmap*>(target)));
    case BASENODE_CLASS_ID: {
        INode* node = static_cast<INode*>(target);
        return InScene(node) ? nb::cast(Wrap(node)) : nb::none();
    }
    default: return nb::cast(ObjectRef{Animatable::GetHandleByAnim(target)});
    }
}

// What PythonToRef accepts for a superclass, for its errors.
static const char* RefKinds(SClass_ID super) {
    switch (super) {
    case 0: return "a Material, Texmap, Node or Object";
    case MATERIAL_CLASS_ID: return "a Material";
    case TEXMAP_CLASS_ID: return "a Texmap";
    case BASENODE_CLASS_ID: return "a Node";
    default: return "an Object of the slot's kind";
    }
}

ReferenceTarget* PythonToRef(nb::handle value, SClass_ID super, const std::string& what) {
    if (value.is_none()) return nullptr;
    ReferenceTarget* target = nullptr;
    if (nb::isinstance<MaterialRef>(value)) target = ResolveMaterial(nb::cast<MaterialRef>(value));
    else if (nb::isinstance<TexmapRef>(value)) target = ResolveTexmap(nb::cast<TexmapRef>(value));
    else if (nb::isinstance<Node>(value)) target = Resolve(nb::cast<Node>(value));
    else if (nb::isinstance<ObjectRef>(value)) target = ResolveObject(nb::cast<ObjectRef>(value));
    if (!target || (super && target->SuperClassID() != super))
        throw BadParam(what + " expects " + RefKinds(super) + " (or None), got " + TypeNameOf(value));
    return target;
}

// ── Classes ──────────────────────────────────────────────────────

// MAXScript names of a superclass's public classes, sorted. A class MAXScript
// has not met yet (its plugin is not loaded) shows its non-localized name,
// which FindClass takes as well.
static std::vector<std::string> ScriptClassNames(SClass_ID super) {
    RequireMain();
    ScopedMaxScriptEvaluationContext context;
    std::vector<std::string> names;
    SubClassList* list = ClassDirectory::GetInstance().GetClassList(super);
    for (int i = list ? list->GetFirst(ACC_PUBLIC) : -1; i >= 0; i = list->GetNext(ACC_PUBLIC)) {
        ClassEntry& entry = (*list)[i];
        Class_ID id = entry.ClassID();
        MAXClass* cls = MAXClass::lookup_class(&id, super, false);
        const std::string name = cls && cls->name ? WideToUtf8(cls->name->to_string())
                                                  : WideToUtf8(entry.NonLocalizedClassName().data());
        if (!name.empty()) names.push_back(HandlerHelpers::SanitizeScriptName(name));
    }
    std::sort(names.begin(), names.end(), [](const std::string& a, const std::string& b) { return Lower(a) < Lower(b); });
    names.erase(std::unique(names.begin(), names.end()), names.end());
    return names;
}

static std::vector<std::string> MatchingClasses(SClass_ID super, const std::optional<std::string>& pattern) {
    std::vector<std::string> names = ScriptClassNames(super);
    if (pattern)
        names.erase(std::remove_if(names.begin(), names.end(),
                                   [&](const std::string& name) { return !HandlerHelpers::WildcardMatch(name, *pattern); }),
                    names.end());
    return names;
}

// ── Creation ─────────────────────────────────────────────────────

// A material or map of class `cls` with parameters set without keys. The
// lookup stays inside `super`: "Physical" is also a camera, and treating that
// as a Mtl crashes 3ds Max. Unnamed ones get Max's own "Material #3" and
// "Map #3" names. A failed create leaves nothing behind.
template <typename T>
static T* CreateMtlBase(SClass_ID super, const char* kind, const std::string& cls, const std::string& name,
                        nb::kwargs params) {
    RequireMain();
    ClassDesc* cd = FindClass(cls, {super});
    if (!cd) throw NotFound(std::string("no ") + kind + " class '" + cls + "'." + Suggest(cls, ScriptClassNames(super)));
    Interface* ip = GetCOREInterface();
    auto* created = static_cast<Animatable*>(ip->CreateInstance(super, cd->ClassID()));
    if (!created || created->SuperClassID() != super) {
        FreeUnused(created);
        throw MaxError("3ds Max could not create " + WithArticle(cls));
    }
    T* base = static_cast<T*>(created);
    if (name.empty()) ip->AssignNewName(base);
    else base->SetName(MSTR(Utf8ToWide(name).c_str()));
    NoAnimate noAnimate;
    const TimeValue t = Now();
    try {
        for (auto [key, value] : params) SetParam(base, nb::cast<std::string>(key), value, t);
    } catch (...) {
        base->DeleteMe();  // also drops the maps it already references
        throw;
    }
    return base;
}

static MaterialRef CreateMaterial(const std::string& cls, const std::string& name, nb::kwargs params) {
    return Wrap(CreateMtlBase<Mtl>(MATERIAL_CLASS_ID, "material", cls, name, params));
}

static TexmapRef CreateTexmap(const std::string& cls, const std::string& name, nb::kwargs params) {
    return Wrap(CreateMtlBase<Texmap>(TEXMAP_CLASS_ID, "texture map", cls, name, params));
}

// The function name after `shader` in OSL source, else "OSL".
static std::string ShaderName(const std::string& code) {
    static const std::regex declaration(R"(\bshader\s+([A-Za-z_]\w*))");
    std::smatch match;
    return std::regex_search(code, match, declaration) ? match[1].str() : std::string("OSL");
}

// %TEMP%\osl_shaders\<name>.osl, where the bridge's write_osl_shader puts them.
static std::filesystem::path WriteShaderFile(const std::string& name, const std::string& code) {
    std::string file = name;
    for (char& c : file)
        if ((unsigned char)c < 32 || std::strchr("\\/:*?\"<>|", c)) c = '_';
    std::error_code ec;
    const std::filesystem::path dir = std::filesystem::temp_directory_path(ec) / L"osl_shaders";
    std::filesystem::create_directories(dir, ec);
    const std::filesystem::path path = dir / (Utf8ToWide(file) + L".osl");
    std::ofstream out(path, std::ios::binary);
    out << code;
    if (!out) throw MaxError("could not write the shader to " + WideToUtf8(path.c_str()));
    return path;
}

// An OSLMap running `code`: the inline code compiles at once, and OSLPath
// keeps the source file beside it. Shader parameters exist only after the
// compile, so they are set last.
static TexmapRef CreateOsl(const std::string& code, const std::string& name, nb::kwargs params) {
    if (code.find_first_not_of(" \t\r\n") == std::string::npos) throw BadParam("osl() needs the shader's OSL source code");
    const std::string shader = name.empty() ? ShaderName(code) : name;
    Texmap* osl = CreateMtlBase<Texmap>(TEXMAP_CLASS_ID, "texture map", "OSLMap", shader, nb::kwargs());
    NoAnimate noAnimate;
    const TimeValue t = Now();
    try {
        const std::filesystem::path file = WriteShaderFile(shader, code);
        SetParam(osl, "OSLCode", nb::str(code.c_str()), t);
        SetParam(osl, "OSLAutoUpdate", nb::bool_(true), t);
        SetParam(osl, "OSLPath", nb::str(WideToUtf8(file.c_str()).c_str()), t);
        const nb::object compiled = GetValue(osl, "OSLShaderName", t);
        const std::string compiledName = nb::isinstance<nb::str>(compiled) ? nb::cast<std::string>(compiled) : std::string();
        if (compiledName.empty() || compiledName == "Example")
            throw BadParam("the OSL code did not compile (the map still runs its 'Example' shader); check the shader's syntax");
        for (auto [key, value] : params) SetParam(osl, nb::cast<std::string>(key), value, t);
    } catch (...) {
        osl->DeleteMe();
        throw;
    }
    return Wrap(osl);
}

// ── Slots and users ──────────────────────────────────────────────

// Texmap parameters by name (a list for a list parameter). An owner without
// any lists its sub-map slots by slot name instead.
static nb::dict Maps(MtlBase* owner) {
    nb::dict out;
    const TimeValue t = Now();
    ForEachParam(owner, [&](IParamBlock2* block, const ParamDef& def) {
        if (BaseType(def.type) == TYPE_TEXMAP) out[ParamName(block, def).c_str()] = GetParam(ParamRef{block, &def}, t);
    });
    if (nb::len(out) > 0) return out;
    for (int i = 0; i < owner->NumSubTexmaps(); ++i) {
        std::string slot = WideToUtf8(owner->GetSubTexmapSlotName(i, false).data());
        if (slot.empty()) slot = "map" + std::to_string(i + 1);
        out[slot.c_str()] = RefToPython(owner->GetSubTexmap(i));
    }
    return out;
}

static std::vector<std::optional<MaterialRef>> SubMaterials(const MaterialRef& r) {
    Mtl* mtl = ResolveMaterial(r);
    std::vector<std::optional<MaterialRef>> out;
    for (int i = 0; i < mtl->NumSubMtls(); ++i) {
        Mtl* sub = mtl->GetSubMtl(i);
        out.push_back(sub ? std::optional<MaterialRef>(Wrap(sub)) : std::nullopt);
    }
    return out;
}

// Sub-material `index` as Mtl::SetSubMtl counts it: the slot, or for a
// Multi/Sub-Object the material ID minus one (it adds a slot for a new ID).
static void SetSubMaterial(const MaterialRef& r, int index, std::optional<MaterialRef> material) {
    Mtl* mtl = ResolveMaterial(r);
    Mtl* sub = material ? ResolveMaterial(*material) : nullptr;
    const int count = mtl->NumSubMtls();
    if (mtl->IsMultiMtl() && (index < 0 || index >= 65535))
        throw BadParam("a Multi/Sub-Object index is the material ID minus one (0-65534), not " + std::to_string(index));
    if (!mtl->IsMultiMtl() && (index < 0 || index >= count)) {
        if (count == 0) throw BadParam(Label(mtl) + " has no sub-materials");
        throw BadParam(Label(mtl) + " has sub-materials 0-" + std::to_string(count - 1) + ", not " + std::to_string(index));
    }
    if (sub && sub->TestForLoop(FOREVER, mtl) != REF_SUCCEED)
        throw BadParam(Label(sub) + " contains " + Label(mtl) + ", so it cannot be its sub-material");
    mtl->SetSubMtl(index, sub);
    if (mtl->GetSubMtl(index) != sub)
        throw BadParam("3ds Max refused sub-material " + std::to_string(index) + " of " + Label(mtl));
}

// Whether `target` is `base` or in its sub-material and sub-map tree. `known`
// holds finished answers; a branch that leads back up counts as not found.
static bool Uses(MtlBase* base, MtlBase* target, std::unordered_map<MtlBase*, bool>& known) {
    if (!base) return false;
    if (base == target) return true;
    if (!known.emplace(base, false).second) return known[base];
    bool found = false;
    if (base->SuperClassID() == MATERIAL_CLASS_ID) {
        Mtl* mtl = static_cast<Mtl*>(base);
        for (int i = 0; !found && i < mtl->NumSubMtls(); ++i) found = Uses(mtl->GetSubMtl(i), target, known);
    }
    for (int i = 0; !found && i < base->NumSubTexmaps(); ++i) found = Uses(base->GetSubTexmap(i), target, known);
    return known[base] = found;
}

static std::vector<Node> UsingNodes(MtlBase* target) {
    std::unordered_map<MtlBase*, bool> known;
    std::vector<Node> out;
    for (INode* node : AllNodes())
        if (Uses(node->GetMtl(), target, known)) out.push_back(Wrap(node));
    return out;
}

// ── Replacement ──────────────────────────────────────────────────

static const char* const kLoop = "would create a reference loop";

// Puts `fresh` in every sub-material slot holding `old` in the tree under
// `parent`; a slot that would loop or that 3ds Max refuses is skipped.
static void ReplaceInTree(Mtl* parent, Mtl* old, Mtl* fresh, std::set<Mtl*>& visited, nb::list& slots,
                          nb::list& skipped) {
    if (!parent || !visited.insert(parent).second) return;
    for (int i = 0; i < parent->NumSubMtls(); ++i) {
        Mtl* sub = parent->GetSubMtl(i);
        if (sub != old) {
            ReplaceInTree(sub, old, fresh, visited, slots, skipped);
            continue;
        }
        const char* reason = nullptr;
        if (fresh->TestForLoop(FOREVER, parent) != REF_SUCCEED) {
            reason = kLoop;
        } else {
            parent->SetSubMtl(i, fresh);
            if (parent->GetSubMtl(i) != fresh) reason = "refused by 3ds Max";
        }
        if (reason) skipped.append(nb::make_tuple(Wrap(parent), i, reason));
        else slots.append(nb::make_tuple(Wrap(parent), i));
    }
}

static nb::dict ReplaceWith(const MaterialRef& oldRef, const MaterialRef& freshRef, bool subMaterials) {
    Mtl* old = ResolveMaterial(oldRef);
    Mtl* fresh = ResolveMaterial(freshRef);
    if (old == fresh) throw BadParam("a material cannot replace itself; pass a different one");
    nb::list nodes, slots, skipped;
    const std::vector<INode*> all = AllNodes();
    for (INode* node : all) {
        if (node->GetMtl() != old) continue;
        if (fresh->TestForLoop(FOREVER, node) != REF_SUCCEED) {
            skipped.append(nb::make_tuple(Wrap(node), nb::none(), kLoop));
            continue;
        }
        node->SetMtl(fresh);
        nodes.append(Wrap(node));
    }
    if (subMaterials) {
        // The new material keeps what it holds, the old one included.
        std::set<Mtl*> visited{fresh, old};
        for (INode* node : all) ReplaceInTree(node->GetMtl(), old, fresh, visited, slots, skipped);
    }
    nb::dict out;
    out["nodes"] = nodes;
    out["slots"] = slots;
    out["skipped"] = skipped;
    return out;
}

// ── Network graphs (native:inspect_material_network) ─────────────

static const json& Items(const json& object, const char* key) {
    static const json empty = json::array();
    auto it = object.find(key);
    return it != object.end() && it->type() == json::value_t::array ? *it : empty;
}

static std::string RootId(const json& graph) {
    return graph.value("root", json::object()).value("id", std::string("n0"));
}

// Whether a node's material or a scene material is `mtl` or holds it.
static bool UsedInScene(Mtl* mtl) {
    std::unordered_map<MtlBase*, bool> known;
    for (INode* node : AllNodes())
        if (Uses(node->GetMtl(), mtl, known)) return true;
    MtlBaseLib* scene = GetCOREInterface()->GetSceneMtls();
    for (int i = 0; scene && i < scene->Count(); ++i)
        if (Uses((*scene)[i], mtl, known)) return true;
    return false;
}

// Request fields that make the network handlers resolve exactly `mtl`. They
// look a name up as an object first, so a uniquely named object that carries
// it, or carries it as a sub-material, names it best; else its own name,
// which they search for in the scene's materials only.
static json MaterialQuery(Mtl* mtl, const char* nameKey, const char* subKey) {
    const std::vector<INode*> nodes = AllNodes();
    std::unordered_map<std::wstring, int> uses;
    for (INode* node : nodes) ++uses[node->GetName()];
    auto query = [&](INode* node, int sub) { return json{{nameKey, NodeName(node)}, {subKey, sub}}; };
    for (INode* node : nodes)
        if (node->GetMtl() == mtl && uses[node->GetName()] == 1) return query(node, 0);
    for (INode* node : nodes) {
        Mtl* own = node->GetMtl();
        if (!own || uses[node->GetName()] != 1) continue;
        for (int i = 0; i < own->NumSubMtls(); ++i)
            if (own->GetSubMtl(i) == mtl) return query(node, i + 1);
    }
    if (!UsedInScene(mtl))
        throw NotFound(Label(mtl) + " is not used in the scene, and 3ds Max's graph reader reads only those; "
                       "assign it to a node first");
    return json{{nameKey, NameOf(mtl)}, {subKey, 0}};
}

// The handlers find a material by name; make sure they found this one.
static void RequireRoot(const json& graph, Mtl* mtl) {
    const std::string root = RootId(graph);
    const std::string handle = std::to_string((unsigned long long)Animatable::GetHandleByAnim(mtl));
    for (const json& node : Items(graph, "nodes"))
        if (node.value("id", std::string()) == root && node.value("handle", std::string()) == handle) return;
    throw Ambiguous("another object or material is also named '" + NameOf(mtl) +
                    "', and 3ds Max's graph reader picked that one; give this material a unique name and retry");
}

// A handler graph with the live material or map of each node id. It points
// into the reply, which must outlive it.
struct Graph {
    std::unordered_map<std::string, const json*> nodes;
    std::unordered_map<std::string, MtlBase*> bases;
    std::vector<MtlBase*> order;

    explicit Graph(const json& graph) {
        for (const json& node : Items(graph, "nodes")) {
            const std::string id = node.value("id", std::string());
            nodes[id] = &node;
            AnimHandle handle = 0;
            try { handle = (AnimHandle)std::stoull(node.value("handle", std::string("0"))); } catch (...) {}
            Animatable* anim = Live(handle, MATERIAL_CLASS_ID);
            if (!anim) anim = Live(handle, TEXMAP_CLASS_ID);
            if (!anim) continue;
            bases[id] = static_cast<MtlBase*>(anim);
            order.push_back(static_cast<MtlBase*>(anim));
        }
    }
    explicit Graph(json&&) = delete;

    nb::object Object(const std::string& id) const {
        auto it = bases.find(id);
        return it == bases.end() ? nb::none() : RefToPython(it->second);
    }

    // The first map or material of the graph with this name.
    nb::object Named(const std::string& name) const {
        for (MtlBase* base : order)
            if (NameOf(base) == name) return RefToPython(base);
        return nb::none();
    }

    std::string Describe(const std::string& id) const {
        if (auto it = bases.find(id); it != bases.end()) return Label(it->second);
        auto node = nodes.find(id);
        if (node == nodes.end()) return id;
        return "'" + node->second->value("name", std::string()) + "' " + node->second->value("class", std::string());
    }
};

// The graph as indented lines: "slot: 'Name' Class  path", each child under
// its parent. A node met again is marked "(see above)"; " ..." marks unread
// inputs (raise depth to read them).
static std::vector<std::string> TreeLines(const json& reply, const Graph& graph) {
    std::unordered_map<std::string, std::vector<const json*>> children;
    for (const json& edge : Items(reply, "edges")) children[edge.value("parentId", std::string())].push_back(&edge);
    const json truncated = reply.value("truncated", json::object());
    std::set<std::string> limited;
    for (const json& id : Items(truncated, "depthLimited"))
        if (id.type() == json::value_t::string) limited.insert(id.get<std::string>());
    auto line = [&](const std::string& id) {
        std::string text = graph.Describe(id);
        if (auto node = graph.nodes.find(id); node != graph.nodes.end())
            for (const json& file : Items(*node->second, "files")) {
                text += "  " + file.value("path", std::string());
                if (!file.value("exists", true)) text += " (missing)";
            }
        return limited.count(id) ? text + " ..." : text;
    };
    std::vector<std::string> lines;
    std::set<std::string> shown;
    std::function<void(const std::string&, const std::string&)> walk = [&](const std::string& id, const std::string& indent) {
        for (const json* edge : children[id]) {
            const std::string child = edge->value("nodeId", std::string());
            const std::string slot = indent + edge->value("slot", std::string()) + ": ";
            if (!shown.insert(child).second) {
                lines.push_back(slot + graph.Describe(child) + " (see above)");
                continue;
            }
            lines.push_back(slot + line(child));
            walk(child, indent + "  ");
        }
    };
    const std::string root = RootId(reply);
    shown.insert(root);
    lines.push_back(line(root));
    walk(root, "  ");
    return lines;
}

static nb::dict Network(const MaterialRef& r, int depth, int maxNodes) {
    Mtl* mtl = ResolveMaterial(r);
    if (depth < 0 || depth > 16) throw BadParam("depth must be 0-16");
    if (maxNodes < 1 || maxNodes > 2000) throw BadParam("max_nodes must be 1-2000");
    json args = MaterialQuery(mtl, "name", "sub_material_index");
    args["depth"] = depth;
    args["scope"] = "wired";
    args["include_values"] = false;
    args["verify_files"] = true;
    args["max_nodes"] = maxNodes;
    const json reply = NativeJson("native:inspect_material_network", args);
    RequireRoot(reply, mtl);
    const Graph graph(reply);

    nb::list links, files, issues;
    for (const json& edge : Items(reply, "edges"))
        links.append(nb::make_tuple(graph.Object(edge.value("parentId", std::string())), edge.value("slot", std::string()),
                                    graph.Object(edge.value("nodeId", std::string()))));
    for (const json& node : Items(reply, "nodes"))
        for (const json& file : Items(node, "files")) {
            nb::dict entry;
            entry["map"] = graph.Object(node.value("id", std::string()));
            entry["param"] = file.value("param", std::string());
            entry["path"] = file.value("path", std::string());
            if (file.contains("exists")) entry["exists"] = file.value("exists", false);
            if (file.contains("udim")) entry["udim"] = file.value("udim", 0);
            files.append(entry);
        }
    for (const char* key : {"issues", "warnings"})
        for (const json& issue : Items(reply, key)) {
            nb::dict entry;
            entry["code"] = issue.value("code", std::string());
            entry["message"] = issue.value("message", std::string());
            entry["map"] = graph.Object(issue.value("nodeId", std::string()));
            issues.append(entry);
        }

    nb::dict out;
    out["tree"] = TreeLines(reply, graph);
    out["links"] = links;
    out["files"] = files;
    out["issues"] = issues;
    const bool complete = reply.value("complete", false);
    out["complete"] = complete;
    if (!complete) {
        const json truncated = reply.value("truncated", json::object());
        nb::list limited;
        for (const json& id : Items(truncated, "depthLimited"))
            if (id.type() == json::value_t::string) limited.append(graph.Object(id.get<std::string>()));
        nb::dict info;
        info["nodes_omitted"] = truncated.value("nodesOmitted", 0);
        info["edges_omitted"] = truncated.value("edgesOmitted", 0);
        info["depth_limited"] = limited;
        out["truncated"] = info;
    }
    return out;
}

// ── Replication (native:replicate_material) ──────────────────────

static bool HasRemap(const std::string& folder, nb::handle pathMap) {
    return !folder.empty() || (!pathMap.is_none() && nb::len(pathMap) > 0);
}

// Fields shared by the preflight and the run: the source, the mode, where the
// textures go (a folder and/or {old path or file name: new path}), no targets.
static json ReplicateArgs(Mtl* mtl, const char* mode, const std::string& folder, nb::handle pathMap, bool allowMissing) {
    if (!pathMap.is_none() && !nb::isinstance<nb::dict>(pathMap))
        throw BadParam("path_map must be a dict {old path or file name: new path}");
    std::error_code ec;
    if (!folder.empty() && !std::filesystem::is_directory(std::filesystem::path(Utf8ToWide(folder)), ec))
        throw BadParam("texture_folder does not exist: " + folder);
    json args = MaterialQuery(mtl, "source", "source_sub_material_index");
    args["mode"] = mode;
    args["texture_folder"] = folder;
    args["path_map"] = pathMap.is_none() ? json::object() : FromPython(pathMap);
    args["allow_missing"] = allowMissing;
    args["assign"] = false;
    args["targets"] = json::array();
    return args;
}

// The handler's plan for `args`, which changes nothing.
static json ReplicatePlan(Mtl* mtl, json args) {
    args["preview"] = true;
    args["verify"] = true;
    json plan = NativeJson("native:replicate_material", args);
    RequireRoot(plan.value("graph", json::object()), mtl);
    return plan;
}

static void RequirePlan(const json& plan, Mtl* mtl) {
    std::vector<std::string> errors;
    for (const json& error : Items(plan, "errors"))
        if (error.type() == json::value_t::string) errors.push_back(error.get<std::string>());
    if (!errors.empty())
        throw BadParam(Label(mtl) + " cannot be replicated as asked: " + Join(errors, 5) +
                       ". Fix them; allow_missing=True accepts missing texture files");
}

// The run after a clean preflight. The handler then skips its own check of
// the result (verify), which the preflight already did.
static json RunReplicate(json args) {
    args["preview"] = false;
    args["verify"] = false;
    args["extend_udim_tiles"] = true;
    return NativeJson("native:replicate_material", args);
}

static MaterialRef Replicate(const MaterialRef& r, const std::string& folder, PathMapArg given, const std::string& name,
                             NodesOrNone nodes, bool allowMissing) {
    const nb::handle pathMap = given.value;
    Mtl* mtl = ResolveMaterial(r);
    const std::vector<INode*> targets = nodes.is_none() ? std::vector<INode*>() : NodeList(nodes.value, "replicate");
    json args = ReplicateArgs(mtl, HasRemap(folder, pathMap) ? "clone_and_remap" : "clone", folder, pathMap, allowMissing);
    // The handler assigns by name; its plan refuses a name several objects share.
    if (!targets.empty()) {
        args["assign"] = true;
        for (INode* node : targets) args["targets"].push_back(NodeName(node));
    }
    if (!name.empty()) args["material_name"] = name;
    RequirePlan(ReplicatePlan(mtl, args), mtl);

    MtlBaseLib* scene = GetCOREInterface()->GetSceneMtls();
    std::set<MtlBase*> before;
    for (int i = 0; scene && i < scene->Count(); ++i) before.insert((*scene)[i]);
    const json result = RunReplicate(args);
    if (!targets.empty() && targets.front()->GetMtl() && targets.front()->GetMtl() != mtl)
        return Wrap(targets.front()->GetMtl());
    // Unassigned, the copy is kept among the scene materials.
    const std::wstring copyName = Utf8ToWide(result.value("materialName", std::string()));
    for (int i = 0; scene && i < scene->Count(); ++i) {
        MtlBase* entry = (*scene)[i];
        if (entry && entry->SuperClassID() == MATERIAL_CLASS_ID && !before.count(entry) && copyName == entry->GetName().data())
            return Wrap(static_cast<Mtl*>(entry));
    }
    throw MaxError("3ds Max replicated " + Label(mtl) + " but did not report the copy");
}

// One dict per texture file that moves: {map, param, old, new, exists} planned,
// {map, param, old, new} or {map, param, tiles} done.
using RemappedFiles = nb::typed<nb::list, nb::typed<nb::dict, std::string, RemapField>>;

static RemappedFiles RemapFiles(const MaterialRef& r, const std::string& folder, PathMapArg given, bool allowMissing,
                                bool preview) {
    const nb::handle pathMap = given.value;
    Mtl* mtl = ResolveMaterial(r);
    if (!HasRemap(folder, pathMap)) throw BadParam("remap_files needs texture_folder or path_map");
    json args = ReplicateArgs(mtl, "remap_in_place", folder, pathMap, allowMissing);
    const json plan = ReplicatePlan(mtl, args);
    const json planGraph = plan.value("graph", json::object());
    const Graph graph(planGraph);
    nb::list out;
    if (preview) {
        for (const json& file : Items(plan, "plannedFiles")) {
            if (!file.value("remapped", false)) continue;
            nb::dict entry;
            entry["map"] = graph.Object(file.value("nodeId", std::string()));
            entry["param"] = file.value("param", std::string());
            entry["old"] = file.value("oldPath", std::string());
            entry["new"] = file.value("newPath", std::string());
            entry["exists"] = file.value("exists", false);
            out.append(entry);
        }
        return out;
    }
    RequirePlan(plan, mtl);
    args["confirm"] = true;
    const json result = RunReplicate(args);
    // The handler names each changed map; UDIM tile lists report their tiles.
    for (const json& change : Items(result, "remapped")) {
        nb::dict entry;
        entry["map"] = graph.Named(change.value("node", std::string()));
        entry["param"] = change.value("param", std::string());
        if (change.contains("tileCount")) {
            entry["tiles"] = change.value("tileCount", 0);
        } else {
            entry["old"] = change.value("oldPath", std::string());
            entry["new"] = change.value("newPath", std::string());
        }
        out.append(entry);
    }
    return out;
}

// ── Scene and library ────────────────────────────────────────────

std::optional<MaterialRef> NodeMaterial(const Node& n) {
    Mtl* mtl = Resolve(n)->GetMtl();
    if (!mtl) return std::nullopt;
    return Wrap(mtl);
}

void SetNodeMaterial(const Node& n, std::optional<MaterialRef> material) {
    INode* node = Resolve(n);
    node->SetMtl(material ? ResolveMaterial(*material) : nullptr);
}

static std::vector<MaterialRef> SceneMaterials(const std::optional<std::string>& pattern) {
    RequireMain();
    std::vector<MaterialRef> out;
    std::set<Mtl*> seen;
    for (INode* node : AllNodes()) {
        Mtl* mtl = node->GetMtl();
        if (mtl && seen.insert(mtl).second && (!pattern || HandlerHelpers::WildcardMatch(NameOf(mtl), *pattern)))
            out.push_back(Wrap(mtl));
    }
    return out;
}

struct MaterialCollection {};
struct TexmapCollection {};

// Every map below a material or map: its own slots, and for a material those of
// its sub-materials. `seen` keeps a map shared by several slots to one entry.
static void CollectTexmaps(MtlBase* base, std::set<MtlBase*>& seen, std::vector<TexmapRef>& out) {
    if (!base || !seen.insert(base).second) return;
    if (base->SuperClassID() == TEXMAP_CLASS_ID) out.push_back(Wrap(static_cast<Texmap*>(base)));
    for (int i = 0; i < base->NumSubTexmaps(); ++i) CollectTexmaps(base->GetSubTexmap(i), seen, out);
    if (base->SuperClassID() == MATERIAL_CLASS_ID) {
        Mtl* mtl = static_cast<Mtl*>(base);
        for (int i = 0; i < mtl->NumSubMtls(); ++i) CollectTexmaps(mtl->GetSubMtl(i), seen, out);
    }
}

static std::vector<TexmapRef> SceneTexmaps(const std::optional<std::string>& pattern) {
    RequireMain();
    std::set<MtlBase*> seen;
    std::vector<TexmapRef> all;
    for (const MaterialRef& material : SceneMaterials(std::nullopt)) CollectTexmaps(ResolveMaterial(material), seen, all);
    CollectTexmaps(GetCOREInterface()->GetEnvironmentMap(), seen, all);
    if (!pattern) return all;
    std::vector<TexmapRef> out;
    for (const TexmapRef& map : all)
        if (HandlerHelpers::WildcardMatch(NameOf(ResolveTexmap(map)), *pattern)) out.push_back(map);
    return out;
}

static nb::typed<nb::list, MaterialOrMap> LibraryMaterials() {
    RequireMain();
    nb::list out;
    MtlBaseLib& library = GetCOREInterface()->GetMaterialLibrary();
    for (int i = 0; i < library.Count(); ++i) out.append(RefToPython(library[i]));
    return out;
}

static nb::typed<nb::list, std::optional<MaterialOrMap>> MeditMaterials() {
    RequireMain();
    nb::list out;
    for (int slot = 0; slot < 24; ++slot) out.append(RefToPython(GetCOREInterface()->GetMtlSlot(slot)));
    return out;
}

static LibraryBackups BackupLibrary(const std::string& source, const std::string& folder, const std::string& path,
                                    const std::string& prefix) {
    if (source != "all" && source != "current" && source != "medit" && source != "combined")
        throw BadParam("source must be 'all', 'current', 'medit' or 'combined', not '" + source + "'");
    if (!path.empty() && source == "all")
        throw BadParam("path names one file; pass source='current', 'medit' or 'combined' with it");
    const json args = {{"source", source}, {"backup_dir", folder}, {"file_path", path}, {"prefix", prefix}};
    return LibraryBackups{ToPython(NativeJson("native:backup_material_library", args).value("saved", json::array()))};
}

// ── Binding ──────────────────────────────────────────────────────

void BindMaterials(nb::module_& m) {
    nb::class_<ObjectRef>(m, "Object",
        "Any other object a parameter holds, with its own parameters: a map's coords and output, a curve control, ... "
        "tex.params.coords.params.describe().")
        .def_prop_ro("handle", [](const ObjectRef& r) { return (unsigned long long)r.handle; },
            "The object's animatable handle. In pymxs: rt.getAnimByHandle(x.handle).")
        .def_prop_ro("class_name", [](const ObjectRef& r) { return HandlerHelpers::MaxScriptVisibleClassName(ResolveObject(r)); },
            "MAXScript class: StandardUVGen, StandardXYZGen, StandardTextureOutput, ...")
        .def_prop_ro("params", [](const ObjectRef& r) { ResolveObject(r); return Params{0, r.handle}; },
            "The object's parameters.")
        .def("__repr__", [](const ObjectRef& r) {
            RequireMain();
            Animatable* anim = Animatable::GetAnimByHandle(r.handle);
            if (!anim || anim->TestAFlag(A_IS_DELETED)) return "<Object #" + std::to_string(r.handle) + " deleted>";
            return "<Object " + HandlerHelpers::MaxScriptVisibleClassName(anim) + ">";
        })
        .def("__eq__", [](const ObjectRef& a, nb::handle b) {
            return nb::isinstance<ObjectRef>(b) && a.handle == nb::cast<ObjectRef>(b).handle;
        }, nb::arg("other").none())
        .def("__hash__", [](const ObjectRef& r) { return (Py_hash_t)r.handle; });

    nb::class_<TexmapRef>(m, "Texmap", "A texture map: tex.params.filename = 'C:/tex/wood.png'; mat.params.base_color_map = tex.")
        .def_prop_ro("handle", [](const TexmapRef& r) { return (unsigned long long)r.tex; },
            "The map's animatable handle. In pymxs: rt.getAnimByHandle(tex.handle).")
        .def_prop_rw("name",
            [](const TexmapRef& r) { return NameOf(ResolveTexmap(r)); },
            [](const TexmapRef& r, const std::string& name) { ResolveTexmap(r)->SetName(Utf8ToWide(name).c_str()); },
            "The map's name.")
        .def_prop_ro("class_name", [](const TexmapRef& r) { return HandlerHelpers::MaxScriptVisibleClassName(ResolveTexmap(r)); },
            "MAXScript class: Bitmaptexture, Noise, OSLMap, ...")
        .def_prop_ro("params", [](const TexmapRef& r) { ResolveTexmap(r); return Params{0, r.tex}; }, "The map's parameters.")
        .def_prop_ro("maps", [](const TexmapRef& r) -> nb::typed<nb::dict, std::string, MapSlot> { return Maps(ResolveTexmap(r)); },
            "Sub-map slots by parameter name: {'map1': <Texmap>, 'mask': None}; set them through params.")
        .def_prop_ro("nodes", [](const TexmapRef& r) { return UsingNodes(ResolveTexmap(r)); },
            "Scene nodes whose material uses this map.")
        .def("__repr__", [](const TexmapRef& r) { return Repr("Texmap", r.tex, TEXMAP_CLASS_ID); })
        .def("__eq__", [](const TexmapRef& a, nb::handle b) {
            return nb::isinstance<TexmapRef>(b) && a.tex == nb::cast<TexmapRef>(b).tex;
        }, nb::arg("other").none())
        .def("__hash__", [](const TexmapRef& r) { return (Py_hash_t)r.tex; });

    nb::class_<MaterialRef>(m, "Material", "A material: mat.params.base_color = (1, 0, 0); node.material = mat.")
        .def_prop_ro("handle", [](const MaterialRef& r) { return (unsigned long long)r.mtl; },
            "The material's animatable handle. In pymxs: rt.getAnimByHandle(mat.handle).")
        .def_prop_rw("name",
            [](const MaterialRef& r) { return NameOf(ResolveMaterial(r)); },
            [](const MaterialRef& r, const std::string& name) { ResolveMaterial(r)->SetName(Utf8ToWide(name).c_str()); },
            "The material's name.")
        .def_prop_ro("class_name", [](const MaterialRef& r) { return HandlerHelpers::MaxScriptVisibleClassName(ResolveMaterial(r)); },
            "MAXScript class: PhysicalMaterial, Multimaterial, ...")
        .def_prop_ro("params", [](const MaterialRef& r) { ResolveMaterial(r); return Params{0, r.mtl}; },
            "The material's parameters.")
        .def_prop_ro("maps", [](const MaterialRef& r) -> nb::typed<nb::dict, std::string, MapSlot> { return Maps(ResolveMaterial(r)); },
            "Map slots by parameter name: {'base_color_map': <Texmap>, 'bump_map': None, ...}; set them through params.")
        .def_prop_ro("sub_materials", &SubMaterials,
            "Sub-materials, None where empty; for a Multi/Sub-Object entry i is material ID i+1.")
        .def("set_sub_material", &SetSubMaterial, "index"_a, "material"_a,
            "Put a material (None empties) in sub-material index, as sub_materials counts: multi.set_sub_material(0, brick) sets ID 1.")
        .def_prop_ro("nodes", [](const MaterialRef& r) { return UsingNodes(ResolveMaterial(r)); },
            "Scene nodes whose material is this one or contains it.")
        .def("network", &Network, "depth"_a = 6, "max_nodes"_a = 80,
            "The map graph: tree is lines to print (print('\\n'.join(net['tree']))), links [(parent, slot, child)], files "
            "[{'map', 'param', 'path', 'exists'}], and truncated says what was left out when complete is False. "
            "Keys: {'tree', 'links', 'files', 'issues', 'complete', 'truncated'?}.")
        .def("replace_with", &ReplaceWith, "new"_a, "sub_materials"_a = true,
            "Put new wherever this material is: on nodes and in sub-material slots. Keys: {'nodes', 'slots', 'skipped'}.")
        .def("replicate", &Replicate, "texture_folder"_a = "", "path_map"_a.none() = nb::none(), "name"_a = "",
            "nodes"_a.none() = nb::none(), "allow_missing"_a = false,
            "Copy the material, its texture files repointed to texture_folder or path_map {old path or file name: new path}, "
            "onto nodes, and return the copy: mat.replicate('D:/tex/oak', name='Oak', nodes=['Table']).")
        .def("remap_files", &RemapFiles, "texture_folder"_a = "", "path_map"_a.none() = nb::none(), "allow_missing"_a = false,
            "preview"_a = false,
            "Repoint texture files in place, by file name in texture_folder or by path_map {old path or file name: new path}; "
            "returns one {'map', 'param', 'old', 'new'} per changed file, {'map', 'param', 'tiles'} for a UDIM tile list. "
            "preview=True only plans, and adds 'exists' to each.")
        .def("__repr__", [](const MaterialRef& r) { return Repr("Material", r.mtl, MATERIAL_CLASS_ID); })
        .def("__eq__", [](const MaterialRef& a, nb::handle b) {
            return nb::isinstance<MaterialRef>(b) && a.mtl == nb::cast<MaterialRef>(b).mtl;
        }, nb::arg("other").none())
        .def("__hash__", [](const MaterialRef& r) { return (Py_hash_t)r.mtl; });

    BindCollection<MaterialCollection, MaterialRef, std::string>(m, "materials", "Materials",
        "The materials assigned to scene nodes. materials['Brick'] is one by name, materials('Brick*') lists by "
        "wildcard, and materials.new('PhysicalMaterial', 'Red', base_color=(0.8, 0.1, 0.1)) creates one; a new "
        "material is in the collection once a node uses it (node.material = mat).",
        [] { return SceneMaterials(std::nullopt); },
        [](const std::string& name) {
            return FindByName(SceneMaterials(std::nullopt), name, "material",
                              [](const MaterialRef& r) { return NameOf(ResolveMaterial(r)); });
        })
        .def("__call__", [](const MaterialCollection&, const std::optional<std::string>& pattern) {
            return SceneMaterials(pattern);
        }, "pattern"_a = nb::none(), "Materials assigned to scene nodes, or those a wildcard name matches: materials('Brick*').")
        .def("new", [](const MaterialCollection&, const std::string& cls, const std::string& name, nb::kwargs params) {
            return CreateMaterial(cls, name, params);
        }, "cls"_a = "PhysicalMaterial", "name"_a = "", "params"_a,
           "Create a material with parameters: materials.new('PhysicalMaterial', 'Red', base_color=(0.8, 0.1, 0.1)).");

    BindCollection<TexmapCollection, TexmapRef, std::string>(m, "texmaps", "Texmaps",
        "The texture maps the scene uses: every map the scene materials reach, through sub-materials and maps of "
        "maps, and the environment map. texmaps['Wood'] is one by name, texmaps('*noise*') lists by wildcard, and "
        "texmaps.new('Bitmaptexture', 'Wood', filename='C:/tex/wood.png') creates one; a new map is in the "
        "collection once something in the scene uses it.",
        [] { return SceneTexmaps(std::nullopt); },
        [](const std::string& name) {
            return FindByName(SceneTexmaps(std::nullopt), name, "texture map",
                              [](const TexmapRef& r) { return NameOf(ResolveTexmap(r)); });
        })
        .def("__call__", [](const TexmapCollection&, const std::optional<std::string>& pattern) {
            return SceneTexmaps(pattern);
        }, "pattern"_a = nb::none(), "Texture maps the scene uses, or those a wildcard name matches: texmaps('*noise*').")
        .def("new", [](const TexmapCollection&, const std::string& cls, const std::string& name, nb::kwargs params) {
            return CreateTexmap(cls, name, params);
        }, "cls"_a, "name"_a = "", "params"_a,
           "Create a texture map with parameters: texmaps.new('Bitmaptexture', 'Wood', filename='C:/tex/wood.png').");
    m.def("osl", &CreateOsl, "code"_a, "name"_a = "", "params"_a,
          "Compile OSL code into an OSLMap and set its shader parameters; name defaults to the shader's.");
    m.def("material_classes", [](const std::optional<std::string>& pattern) { return MatchingClasses(MATERIAL_CLASS_ID, pattern); },
          "pattern"_a = nb::none(), "Material class names materials.new() takes: material_classes('*PBR*').");
    m.def("texmap_classes", [](const std::optional<std::string>& pattern) { return MatchingClasses(TEXMAP_CLASS_ID, pattern); },
          "pattern"_a = nb::none(), "Texture map class names texmaps.new() takes: texmap_classes('*bitmap*').");
    m.def("material_library", &LibraryMaterials, "The current material library (currentMaterialLibrary): Materials and Texmaps.");
    m.def("medit_materials", &MeditMaterials, "The 24 Compact Material Editor slots (meditMaterials), None where empty.");
    m.def("backup_material_library", &BackupLibrary, "source"_a = "all", "folder"_a = "", "path"_a = "", "prefix"_a = "",
          "Save library, editor slots and both combined ('all') to timestamped .mat files; returns one {'source', 'path', "
          "'count', 'saved', 'skipped', 'error'} per file, skipped with the reason in error when that library is empty.");
}

} // namespace MX_ABI_TAG
} // namespace mx
