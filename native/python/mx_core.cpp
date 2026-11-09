// Names, time, node identity and value conversion shared by every binding.
#include "mx_common.h"

#include <iInstanceMgr.h>

namespace mx {
inline namespace MX_ABI_TAG {

// ── Names and suggestions ────────────────────────────────────────

std::string Lower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return (char)std::tolower(c); });
    return s;
}

std::string Trim(const std::string& s) {
    const char* space = " \t\n\r\f\v";
    const size_t first = s.find_first_not_of(space);
    return first == std::string::npos ? std::string() : s.substr(first, s.find_last_not_of(space) - first + 1);
}

static size_t EditDistance(const std::string& a, const std::string& b) {
    std::vector<size_t> row(b.size() + 1);
    for (size_t j = 0; j <= b.size(); ++j) row[j] = j;
    for (size_t i = 1; i <= a.size(); ++i) {
        size_t diagonal = row[0];
        row[0] = i;
        for (size_t j = 1; j <= b.size(); ++j) {
            const size_t above = row[j];
            row[j] = std::min({row[j] + 1, row[j - 1] + 1, diagonal + (a[i - 1] == b[j - 1] ? 0 : 1)});
            diagonal = above;
        }
    }
    return row[b.size()];
}

std::string Suggest(const std::string& wanted, const std::vector<std::string>& names) {
    const std::string key = Lower(wanted);
    const std::string* best = nullptr;
    size_t bestDistance = std::max<size_t>(2, key.size() / 3) + 1;
    for (const std::string& name : names) {
        const size_t d = EditDistance(key, Lower(name));
        if (d < bestDistance) { bestDistance = d; best = &name; }
    }
    // Else the shortest name that starts with it: "Physical" -> "PhysicalMaterial".
    if (!best && key.size() >= 3) {
        for (const std::string& name : names)
            if (Lower(name).rfind(key, 0) == 0 && (!best || name.size() < best->size())) best = &name;
    }
    // Else the shortest name that contains it: "angle" -> "BendAngle".
    if (!best && key.size() >= 4) {
        for (const std::string& name : names)
            if (Lower(name).find(key) != std::string::npos && (!best || name.size() < best->size())) best = &name;
    }
    return best ? " Did you mean '" + *best + "'?" : std::string();
}

std::string Join(const std::vector<std::string>& names, size_t limit) {
    std::string out;
    for (size_t i = 0; i < names.size() && i < limit; ++i) {
        if (i) out += ", ";
        out += names[i];
    }
    if (names.size() > limit) out += ", ... (" + std::to_string(names.size()) + " in all)";
    return out;
}

std::string WithArticle(const std::string& noun) {
    const bool vowel = !noun.empty() && std::string("aeiou").find(noun[0]) != std::string::npos;
    return (vowel ? "an " : "a ") + noun;
}

std::string TypeNameOf(nb::handle value) { return nb::type_name(value.type()).c_str(); }
std::string ReprOf(nb::handle value) { return nb::repr(value).c_str(); }

// ── Time ─────────────────────────────────────────────────────────

std::vector<TimeValue>& TimeStack() {
    static std::vector<TimeValue> stack;
    return stack;
}

TimeValue Now() {
    const auto& stack = TimeStack();
    return stack.empty() ? GetCOREInterface()->GetTime() : stack.back();
}

// ── Nodes ────────────────────────────────────────────────────────

// A deleted node can outlive its deletion in the undo buffer; it has no parent.
bool InScene(INode* node) { return node && node->GetParentNode(); }

INode* Resolve(const Node& n) {
    RequireMain();
    INode* node = HandlerHelpers::FindNodeByHandle(n.handle);
    if (!InScene(node)) throw NotFound("node #" + std::to_string(n.handle) + " is no longer in the scene");
    return node;
}

std::vector<INode*> AllNodes() {
    std::vector<INode*> all;
    HandlerHelpers::CollectNodes(GetCOREInterface()->GetRootNode(), all);
    return all;
}

Object* BaseObject(INode* node) {
    Object* object = node->GetObjectRef();
    return object ? object->FindBaseObject() : nullptr;
}

std::string ClassOf(INode* node) {
    Object* base = BaseObject(node);
    return base ? HandlerHelpers::MaxScriptVisibleClassName(base) : std::string("Unknown");
}

Node FindNode(nb::handle key) {
    RequireMain();
    if (nb::isinstance<Node>(key)) {
        Node n = nb::cast<Node>(key);
        Resolve(n);
        return n;
    }
    if (IsInteger(key)) {
        Node n{(AnimHandle)nb::cast<unsigned long long>(key)};
        Resolve(n);
        return n;
    }
    if (!nb::isinstance<nb::str>(key))
        throw BadParam("node() takes a name, a handle or a Node, not " + TypeNameOf(key));
    const std::string name = nb::cast<std::string>(key);
    const std::vector<INode*> matches = HandlerHelpers::CollectNodesByName(name);
    if (matches.empty()) {
        std::vector<std::string> names;
        for (INode* node : AllNodes()) names.push_back(NodeName(node));
        throw NotFound("no node named '" + name + "'." + Suggest(name, names));
    }
    if (matches.size() > 1) {
        std::vector<std::string> handles;
        for (INode* node : matches) handles.push_back("#" + std::to_string(Animatable::GetHandleByAnim(node)));
        throw Ambiguous(std::to_string(matches.size()) + " nodes are named '" + name + "' (" + Join(handles) +
                        "); pass the handle instead");
    }
    return Wrap(matches.front());
}

// A Node, a name, a handle, or a list of them.
int InstanceCount(INode* node) {
    INodeTab nodes;
    return int(IInstanceMgr::GetInstanceMgr()->GetInstances(*node, nodes));
}

std::vector<INode*> NodeList(nb::handle nodes, const char* what, bool allow_empty) {
    std::vector<INode*> out;
    if (IsSequence(nodes)) {
        for (nb::handle item : nodes) out.push_back(Resolve(FindNode(item)));
    } else {
        out.push_back(Resolve(FindNode(nodes)));
    }
    if (out.empty() && !allow_empty) throw BadParam(std::string(what) + " needs at least one node");
    return out;
}

// ── Classes ──────────────────────────────────────────────────────

// The registry of every class by superclass; the plugin DLL list can omit
// classes that load later (the Point helper, for one). MAXScript names win
// over UI and internal names. Only the matched plugin is loaded.
ClassDesc* FindClass(const std::string& name, std::initializer_list<SClass_ID> supers) {
    RequireMain();
    const std::wstring wanted = Utf8ToWide(name);
    ScopedMaxScriptEvaluationContext context;
    auto& directory = ClassDirectory::GetInstance();
    for (bool scriptNames : {true, false}) {
        for (SClass_ID super : supers) {
            SubClassList* list = directory.GetClassList(super);
            if (!list) continue;
            for (int i = list->GetFirst(ACC_PUBLIC); i >= 0; i = list->GetNext(ACC_PUBLIC)) {
                ClassEntry& entry = (*list)[i];
                const Class_ID id = entry.ClassID();
                bool matches = false;
                if (scriptNames) {
                    Class_ID lookup = id;
                    MAXClass* cls = MAXClass::lookup_class(&lookup, super, false);
                    matches = cls && cls->name && _wcsicmp(cls->name->to_string(), wanted.c_str()) == 0;
                } else {
                    matches = _wcsicmp(entry.ClassName().data(), wanted.c_str()) == 0 ||
                              _wcsicmp(entry.NonLocalizedClassName().data(), wanted.c_str()) == 0;
                }
                if (!matches) continue;
                ClassDesc* cd = entry.FullCD();
                if (!cd || cd->SuperClassID() != super || cd->ClassID() != id)
                    throw MaxError("3ds Max returned a different class for '" + name + "'");
                return cd;
            }
        }
    }
    return nullptr;
}

std::vector<std::string> ClassNames(std::initializer_list<SClass_ID> supers) {
    std::vector<std::string> names;
    auto& directory = ClassDirectory::GetInstance();
    for (SClass_ID super : supers) {
        SubClassList* list = directory.GetClassList(super);
        if (!list) continue;
        for (int i = list->GetFirst(ACC_PUBLIC); i >= 0; i = list->GetNext(ACC_PUBLIC))
            names.push_back(WideToUtf8((*list)[i].NonLocalizedClassName().data()));
    }
    return names;
}

// ── Values ───────────────────────────────────────────────────────

std::vector<float> Floats(nb::handle value, size_t lo, size_t hi, const std::string& what) {
    std::vector<float> xs;
    const std::string count = lo == hi ? std::to_string(lo) : std::to_string(lo) + "-" + std::to_string(hi);
    try {
        xs = nb::cast<std::vector<float>>(value);
    } catch (const nb::cast_error&) {
        throw BadParam(what + " expects " + count + " numbers, got " + TypeNameOf(value));
    }
    if (xs.size() < lo || xs.size() > hi)
        throw BadParam(what + " expects " + count + " numbers, got " + std::to_string(xs.size()));
    return xs;
}

Point3 ToPoint3(nb::handle value, const std::string& what) {
    const auto xs = Floats(value, 3, 3, what);
    return Point3(xs[0], xs[1], xs[2]);
}

} // namespace MX_ABI_TAG
} // namespace mx
