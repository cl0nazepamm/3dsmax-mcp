// _mx: 3ds Max for agents, bound straight from the SDK with nanobind.
//
// Every call runs on 3ds Max's main thread, where Max's Python runs; any other
// thread gets WrongThread. Nodes are held by anim handle and looked up again on
// every access, so a deleted node raises NotFound instead of touching freed
// memory. Nothing here opens an undo hold.
//
// Parameter values follow MAXScript: angles in degrees, percentages 0-100,
// times in frames. Colors are 0-1 floats.

// nanobind first: the MAXScript headers pulled in below define function-like
// macros (is_string, ...) that would rewrite names inside nanobind's headers.
#include <nanobind/nanobind.h>
#include <nanobind/stl/optional.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstring>
#include <optional>
#include <string>
#include <vector>

#include "mcp_bridge/handler_helpers.h"
#include "mx_build_tag.h"

namespace nb = nanobind;
using namespace nb::literals;
using HandlerHelpers::Utf8ToWide;
using HandlerHelpers::WideToUtf8;

namespace mx {
inline namespace MX_ABI_TAG {

// ── Errors ───────────────────────────────────────────────────────

struct MaxError : std::runtime_error { using std::runtime_error::runtime_error; };
struct NotFound : MaxError { using MaxError::MaxError; };
struct Ambiguous : MaxError { using MaxError::MaxError; };
struct BadParam : MaxError { using MaxError::MaxError; };
struct UnknownParam : MaxError { using MaxError::MaxError; };
struct WrongThread : MaxError { using MaxError::MaxError; };

PyObject* g_max_error = nullptr;
PyObject* g_not_found = nullptr;
PyObject* g_ambiguous = nullptr;
PyObject* g_bad_param = nullptr;
PyObject* g_unknown_param = nullptr;
PyObject* g_wrong_thread = nullptr;

void TranslateErrors(const std::exception_ptr& error, void*) {
    try {
        std::rethrow_exception(error);
    } catch (const UnknownParam& e) {
        PyErr_SetString(g_unknown_param, e.what());
    } catch (const NotFound& e) {
        PyErr_SetString(g_not_found, e.what());
    } catch (const Ambiguous& e) {
        PyErr_SetString(g_ambiguous, e.what());
    } catch (const BadParam& e) {
        PyErr_SetString(g_bad_param, e.what());
    } catch (const WrongThread& e) {
        PyErr_SetString(g_wrong_thread, e.what());
    } catch (const MaxError& e) {
        PyErr_SetString(g_max_error, e.what());
    }
}

void RequireMain() {
    if (GetCurrentThreadId() != GetCOREInterface15()->GetMainThreadID())
        throw WrongThread("_mx runs only on 3ds Max's main thread");
}

// ── Names and suggestions ────────────────────────────────────────

std::string Lower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return (char)std::tolower(c); });
    return s;
}

size_t EditDistance(const std::string& a, const std::string& b) {
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

// " Did you mean 'x'?" for the closest name within a few edits, else "".
std::string Suggest(const std::string& wanted, const std::vector<std::string>& names) {
    const std::string key = Lower(wanted);
    const std::string* best = nullptr;
    size_t bestDistance = std::max<size_t>(2, key.size() / 3) + 1;
    for (const std::string& name : names) {
        const size_t d = EditDistance(key, Lower(name));
        if (d < bestDistance) { bestDistance = d; best = &name; }
    }
    return best ? " Did you mean '" + *best + "'?" : std::string();
}

std::string Join(const std::vector<std::string>& names, size_t limit = 16) {
    std::string out;
    for (size_t i = 0; i < names.size() && i < limit; ++i) {
        if (i) out += ", ";
        out += names[i];
    }
    if (names.size() > limit) out += ", ... (" + std::to_string(names.size()) + " in all)";
    return out;
}

// "a float", "an int".
std::string WithArticle(const std::string& noun) {
    const bool vowel = !noun.empty() && std::string("aeiou").find(noun[0]) != std::string::npos;
    return (vowel ? "an " : "a ") + noun;
}

std::string TypeNameOf(nb::handle value) {
    return nb::type_name(value.type()).c_str();
}

// ── Time ─────────────────────────────────────────────────────────

std::vector<TimeValue>& TimeStack() {
    static std::vector<TimeValue> stack;
    return stack;
}

TimeValue Now() {
    const auto& stack = TimeStack();
    return stack.empty() ? GetCOREInterface()->GetTime() : stack.back();
}

TimeValue FrameToTicks(double frame) { return (TimeValue)std::lround(frame * GetTicksPerFrame()); }
double TicksToFrame(TimeValue t) { return double(t) / GetTicksPerFrame(); }

struct AtTime { TimeValue time = 0; };
struct AnimateMode { bool on = true; int previous = 0; };

// Creation and other setup must never key, whatever the animate mode.
struct NoAnimate {
    NoAnimate() { SuspendAnimate(); }
    ~NoAnimate() { ResumeAnimate(); }
};

// ── Nodes ────────────────────────────────────────────────────────

struct Node { AnimHandle handle = 0; };

INode* Resolve(const Node& n) {
    RequireMain();
    INode* node = HandlerHelpers::FindNodeByHandle(n.handle);
    // A deleted node can outlive its deletion in the undo buffer; it has no parent.
    if (!node || !node->GetParentNode())
        throw NotFound("node #" + std::to_string(n.handle) + " is no longer in the scene");
    return node;
}

Node Wrap(INode* node) { return Node{Animatable::GetHandleByAnim(node)}; }

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
    return base ? HandlerHelpers::ScriptClassName(base) : std::string("Unknown");
}

std::string NodeName(INode* node) { return WideToUtf8(node->GetName()); }

std::string Repr(const Node& n) {
    INode* node = HandlerHelpers::FindNodeByHandle(n.handle);
    if (!node || !node->GetParentNode()) return "<Node #" + std::to_string(n.handle) + " deleted>";
    return "<Node '" + NodeName(node) + "' #" + std::to_string(n.handle) + " " + ClassOf(node) + ">";
}

Node FindNode(nb::handle key) {
    RequireMain();
    if (nb::isinstance<Node>(key)) {
        Node n = nb::cast<Node>(key);
        Resolve(n);
        return n;
    }
    if (nb::isinstance<nb::int_>(key) && !nb::isinstance<nb::bool_>(key)) {
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

std::vector<Node> Nodes(const std::optional<std::string>& pattern) {
    RequireMain();
    std::vector<Node> out;
    for (INode* node : AllNodes()) {
        if (!pattern || HandlerHelpers::WildcardMatch(NodeName(node), *pattern)) out.push_back(Wrap(node));
    }
    return out;
}

// ── Values ───────────────────────────────────────────────────────

std::vector<float> Floats(nb::handle value, size_t lo, size_t hi, const std::string& what) {
    std::vector<float> xs;
    try {
        xs = nb::cast<std::vector<float>>(value);
    } catch (const nb::cast_error&) {
        throw BadParam(what + " expects " + std::to_string(lo) + " numbers, got " + TypeNameOf(value));
    }
    if (xs.size() < lo || xs.size() > hi)
        throw BadParam(what + " expects " + std::to_string(lo) + " numbers, got " + std::to_string(xs.size()));
    return xs;
}

Point3 ToPoint3(nb::handle value, const std::string& what) {
    const auto xs = Floats(value, 3, 3, what);
    return Point3(xs[0], xs[1], xs[2]);
}

nb::object FromPoint3(const Point3& p) { return nb::make_tuple(p.x, p.y, p.z); }

// ── Parameters (ParamBlock2) ─────────────────────────────────────

struct Params { AnimHandle node = 0; };

Animatable* ParamOwner(const Params& p) {
    INode* node = Resolve(Node{p.node});
    Object* base = BaseObject(node);
    if (!base) throw NotFound("'" + NodeName(node) + "' has no base object");
    return base;
}

struct ParamRef {
    IParamBlock2* block = nullptr;
    const ParamDef* def = nullptr;
};

template <typename F>
void ForEachParam(Animatable* owner, F&& visit) {
    for (int b = 0; b < owner->NumParamBlocks(); ++b) {
        IParamBlock2* block = owner->GetParamBlock(b);
        ParamBlockDesc2* desc = block ? block->GetDesc() : nullptr;
        if (!desc) continue;
        for (int i = 0; i < desc->count; ++i) {
            const ParamDef& def = desc->GetParamDef(desc->IndextoID(i));
            if (def.int_name && *def.int_name) visit(block, def);
        }
    }
}

std::vector<std::string> ParamNames(Animatable* owner) {
    std::vector<std::string> names;
    ForEachParam(owner, [&](IParamBlock2*, const ParamDef& def) { names.push_back(WideToUtf8(def.int_name)); });
    return names;
}

std::optional<ParamRef> FindParam(Animatable* owner, const std::string& name) {
    const std::wstring wanted = Utf8ToWide(name);
    std::optional<ParamRef> found;
    ForEachParam(owner, [&](IParamBlock2* block, const ParamDef& def) {
        if (!found && _wcsicmp(def.int_name, wanted.c_str()) == 0) found = ParamRef{block, &def};
    });
    return found;
}

ParamRef RequireParam(Animatable* owner, const std::string& name) {
    if (auto ref = FindParam(owner, name)) return *ref;
    const std::vector<std::string> names = ParamNames(owner);
    const std::string cls = HandlerHelpers::ScriptClassName(owner);
    if (names.empty())
        throw UnknownParam(cls + " has no ParamBlock2 parameters; use rt (pymxs) for its properties");
    throw UnknownParam(cls + " has no parameter '" + name + "'." + Suggest(name, names) +
                       " Parameters: " + Join(names));
}

int BaseType(int type) { return type & ~TYPE_TAB; }

const char* TypeName(int type) {
    switch (BaseType(type)) {
    case TYPE_FLOAT: return "float";
    case TYPE_WORLD: return "distance";
    case TYPE_ANGLE: return "angle";
    case TYPE_PCNT_FRAC: return "percent";
    case TYPE_COLOR_CHANNEL: return "float";
    case TYPE_INT: return "int";
    case TYPE_INDEX: return "index";
    case TYPE_RADIOBTN_INDEX: return "choice";
    case TYPE_BOOL: return "bool";
    case TYPE_TIMEVALUE: return "frame";
    case TYPE_POINT3: return "point3";
    case TYPE_POINT4: return "point4";
    case TYPE_RGBA: return "color";
    case TYPE_FRGBA: return "color4";
    case TYPE_STRING: return "string";
    case TYPE_FILENAME: return "filename";
    case TYPE_INODE: return "node";
    case TYPE_MTL: return "material";
    case TYPE_TEXMAP: return "texmap";
    case TYPE_REFTARG: return "reference";
    default: return "other";
    }
}

nb::object GetOne(const ParamRef& ref, TimeValue t, int index) {
    IParamBlock2* pb = ref.block;
    const ParamID id = ref.def->ID;
    Interval valid = FOREVER;
    switch (BaseType(ref.def->type)) {
    case TYPE_FLOAT: case TYPE_WORLD: case TYPE_COLOR_CHANNEL: {
        float v = 0; pb->GetValue(id, t, v, valid, index); return nb::float_(v);
    }
    case TYPE_ANGLE: { float v = 0; pb->GetValue(id, t, v, valid, index); return nb::float_(RadToDeg(v)); }
    case TYPE_PCNT_FRAC: { float v = 0; pb->GetValue(id, t, v, valid, index); return nb::float_(v * 100.0); }
    case TYPE_INT: case TYPE_INDEX: case TYPE_RADIOBTN_INDEX: {
        int v = 0; pb->GetValue(id, t, v, valid, index); return nb::int_(v);
    }
    case TYPE_BOOL: { int v = 0; pb->GetValue(id, t, v, valid, index); return nb::bool_(v != 0); }
    case TYPE_TIMEVALUE: { int v = 0; pb->GetValue(id, t, v, valid, index); return nb::float_(TicksToFrame(v)); }
    case TYPE_POINT3: { Point3 v; pb->GetValue(id, t, v, valid, index); return FromPoint3(v); }
    case TYPE_POINT4: { Point4 v; pb->GetValue(id, t, v, valid, index); return nb::make_tuple(v.x, v.y, v.z, v.w); }
    case TYPE_RGBA: { Color v; pb->GetValue(id, t, v, valid, index); return nb::make_tuple(v.r, v.g, v.b); }
    case TYPE_FRGBA: { AColor v; pb->GetValue(id, t, v, valid, index); return nb::make_tuple(v.r, v.g, v.b, v.a); }
    case TYPE_STRING: case TYPE_FILENAME: {
        const MCHAR* v = nullptr; pb->GetValue(id, t, v, valid, index); return nb::str(WideToUtf8(v).c_str());
    }
    case TYPE_INODE: {
        INode* v = nullptr; pb->GetValue(id, t, v, valid, index);
        return v ? nb::cast(Wrap(v)) : nb::none();
    }
    default: return nb::none();
    }
}

nb::object GetParam(const ParamRef& ref, TimeValue t) {
    if (!(ref.def->type & TYPE_TAB)) return GetOne(ref, t, 0);
    nb::list items;
    for (int i = 0; i < ref.block->Count(ref.def->ID); ++i) items.append(GetOne(ref, t, i));
    return items;
}

void SetOne(const ParamRef& ref, TimeValue t, nb::handle value, int index, const std::string& what) {
    IParamBlock2* pb = ref.block;
    const ParamID id = ref.def->ID;
    const int type = BaseType(ref.def->type);
    BOOL ok = FALSE;
    try {
        switch (type) {
        case TYPE_FLOAT: case TYPE_WORLD: case TYPE_COLOR_CHANNEL:
            ok = pb->SetValue(id, t, nb::cast<float>(value), index); break;
        case TYPE_ANGLE: ok = pb->SetValue(id, t, DegToRad(nb::cast<float>(value)), index); break;
        case TYPE_PCNT_FRAC: ok = pb->SetValue(id, t, nb::cast<float>(value) / 100.0f, index); break;
        case TYPE_INT: case TYPE_INDEX: case TYPE_RADIOBTN_INDEX:
            if (nb::isinstance<nb::float_>(value)) throw nb::cast_error();
            ok = pb->SetValue(id, t, nb::cast<int>(value), index); break;
        case TYPE_BOOL: {
            const bool on = nb::isinstance<nb::bool_>(value) ? nb::cast<bool>(value) : nb::cast<int>(value) != 0;
            ok = pb->SetValue(id, t, on ? 1 : 0, index); break;
        }
        case TYPE_TIMEVALUE: ok = pb->SetValue(id, t, FrameToTicks(nb::cast<double>(value)), index); break;
        case TYPE_POINT3: { Point3 p = ToPoint3(value, what); ok = pb->SetValue(id, t, p, index); break; }
        case TYPE_POINT4: {
            const auto xs = Floats(value, 4, 4, what);
            Point4 p(xs[0], xs[1], xs[2], xs[3]); ok = pb->SetValue(id, t, p, index); break;
        }
        case TYPE_RGBA: {
            const auto xs = Floats(value, 3, 4, what);
            Color c(xs[0], xs[1], xs[2]); ok = pb->SetValue(id, t, c, index); break;
        }
        case TYPE_FRGBA: {
            const auto xs = Floats(value, 3, 4, what);
            AColor c(xs[0], xs[1], xs[2], xs.size() > 3 ? xs[3] : 1.0f); ok = pb->SetValue(id, t, c, index); break;
        }
        case TYPE_STRING: case TYPE_FILENAME: {
            const std::wstring text = Utf8ToWide(nb::cast<std::string>(value));
            ok = pb->SetValue(id, t, text.c_str(), index); break;
        }
        case TYPE_INODE: {
            INode* node = value.is_none() ? nullptr : Resolve(nb::cast<Node>(value));
            ok = pb->SetValue(id, t, node, index); break;
        }
        default:
            throw BadParam(what + " is " + WithArticle(TypeName(type)) + " parameter, which _mx cannot set yet; use rt (pymxs)");
        }
    } catch (const nb::cast_error&) {
        throw BadParam(what + " expects " + WithArticle(TypeName(type)) + ", got " + TypeNameOf(value));
    }
    if (!ok) throw BadParam("3ds Max refused " + what + " = " + std::string(nb::repr(value).c_str()));
}

void SetParam(Animatable* owner, const std::string& name, nb::handle value, TimeValue t) {
    const ParamRef ref = RequireParam(owner, name);
    const std::string what = WideToUtf8(ref.def->int_name);
    if (!(ref.def->type & TYPE_TAB)) return SetOne(ref, t, value, 0, what);
    const std::vector<nb::handle> items = [&] {
        try { return nb::cast<std::vector<nb::handle>>(value); }
        catch (const nb::cast_error&) { throw BadParam(what + " is a list parameter; pass a list"); }
    }();
    ref.block->SetCount(ref.def->ID, (int)items.size());
    for (size_t i = 0; i < items.size(); ++i) SetOne(ref, t, items[i], (int)i, what + "[" + std::to_string(i) + "]");
}

std::string ParamsRepr(const Params& p) {
    Animatable* owner = ParamOwner(p);
    std::string out = "<Params " + HandlerHelpers::ScriptClassName(owner);
    const TimeValue t = Now();
    int shown = 0;
    ForEachParam(owner, [&](IParamBlock2* block, const ParamDef& def) {
        if (++shown > 8) return;
        out += " " + WideToUtf8(def.int_name) + "=" + nb::repr(GetParam(ParamRef{block, &def}, t)).c_str();
    });
    if (shown > 8) out += " ...";
    return out + ">";
}

// One line per parameter: "radius: distance = 25.0", "+anim" when animatable.
std::vector<std::string> Describe(const Params& p) {
    Animatable* owner = ParamOwner(p);
    std::vector<std::string> lines;
    const TimeValue t = Now();
    ForEachParam(owner, [&](IParamBlock2* block, const ParamDef& def) {
        std::string line = WideToUtf8(def.int_name) + ": " + TypeName(def.type);
        if (def.type & TYPE_TAB) line += "[]";
        line += " = " + std::string(nb::repr(GetParam(ParamRef{block, &def}, t)).c_str());
        if (def.flags & P_ANIMATABLE) line += " +anim";
        lines.push_back(line);
    });
    return lines;
}

// ── Creation ─────────────────────────────────────────────────────

const SClass_ID kCreatable[] = {GEOMOBJECT_CLASS_ID, SHAPE_CLASS_ID, HELPER_CLASS_ID, LIGHT_CLASS_ID, CAMERA_CLASS_ID};

std::vector<std::string> CreatableClassNames() {
    std::vector<std::string> names;
    auto& dir = DllDir::GetInstance();
    for (int d = 0; d < dir.Count(); ++d) {
        const DllDesc& dll = dir[d];
        for (int c = 0; c < dll.NumberOfClasses(); ++c) {
            ClassDesc* cd = dll[c];
            if (!cd || !cd->IsPublic()) continue;
            if (std::find(std::begin(kCreatable), std::end(kCreatable), cd->SuperClassID()) == std::end(kCreatable)) continue;
            names.push_back(HandlerHelpers::ScriptClassName(cd));
        }
    }
    return names;
}

Node Create(const std::string& cls, const std::string& name, nb::handle pos, nb::kwargs params) {
    RequireMain();
    ClassDesc* cd = nullptr;
    for (SClass_ID super : kCreatable) {
        try {
            cd = HandlerHelpers::FindClassDescByName(cls, super);
        } catch (const std::runtime_error&) {
            throw Ambiguous("class name '" + cls + "' matches several classes");
        }
        if (cd) break;
    }
    if (!cd || !cd->IsPublic())
        throw NotFound("no creatable class '" + cls + "'." + Suggest(cls, CreatableClassNames()));

    Interface* ip = GetCOREInterface();
    Object* object = static_cast<Object*>(ip->CreateInstance(cd->SuperClassID(), cd->ClassID()));
    if (!object) throw MaxError("3ds Max could not create a " + cls);
    INode* node = ip->CreateObjectNode(object);
    if (!node) throw MaxError("3ds Max could not create a node for the " + cls);

    MSTR nodeName = name.empty() ? MSTR(cd->ClassName()) : MSTR(Utf8ToWide(name).c_str());
    if (name.empty()) ip->MakeNameUnique(nodeName);
    node->SetName(nodeName.data());

    NoAnimate noAnimate;
    const TimeValue t = Now();
    try {
        if (!pos.is_none()) {
            Matrix3 tm;
            tm.IdentityMatrix();
            tm.SetTrans(ToPoint3(pos, "pos"));
            node->SetNodeTM(t, tm);
        }
        for (auto [key, value] : params) SetParam(object, nb::cast<std::string>(key), value, t);
    } catch (...) {
        ip->DeleteNode(node, FALSE);  // a failed create leaves nothing behind
        throw;
    }
    return Wrap(node);
}

// ── Module ───────────────────────────────────────────────────────

void Bind(nb::module_& m) {
    m.doc() = "3ds Max for agents: nodes, parameters, creation and time, bound from the SDK.";

    // Python exception types, each also a builtin error where one fits
    // (UnknownParam is an AttributeError, so hasattr() works on Params).
    auto exception = [&](const char* name, nb::handle base, nb::handle builtin, const char* doc) {
        nb::object bases = builtin ? nb::object(nb::make_tuple(base, builtin)) : nb::borrow(base);
        PyObject* type = PyErr_NewExceptionWithDoc(name, doc, bases.ptr(), nullptr);
        if (!type) throw nb::python_error();
        m.attr(std::strchr(name, '.') + 1) = nb::steal(type);
        return type;
    };
    g_max_error = exception("_mx.MaxError", PyExc_Exception, nb::handle(), "Base of every _mx error.");
    g_not_found = exception("_mx.NotFound", g_max_error, PyExc_LookupError, "A node, class or object does not exist.");
    g_ambiguous = exception("_mx.Ambiguous", g_max_error, PyExc_LookupError,
                            "A name matches several nodes or classes; pass a handle.");
    g_bad_param = exception("_mx.BadParam", g_max_error, PyExc_ValueError,
                            "A value has the wrong type or 3ds Max refused it.");
    g_unknown_param = exception("_mx.UnknownParam", g_max_error, PyExc_AttributeError, "No parameter of that name.");
    g_wrong_thread = exception("_mx.WrongThread", g_max_error, nb::handle(), "Called off 3ds Max's main thread.");
    nb::register_exception_translator(TranslateErrors);

    m.attr("BUILD") = MX_BUILD_ID;
    m.attr("MAX_VERSION") = MAX_SDK_VERSION;

    nb::class_<Node>(m, "Node", "A scene node, held by handle.")
        .def_prop_ro("handle", [](const Node& n) { return (unsigned long long)n.handle; })
        .def_prop_rw("name",
            [](const Node& n) { return NodeName(Resolve(n)); },
            [](const Node& n, const std::string& name) { Resolve(n)->SetName(Utf8ToWide(name).c_str()); })
        .def_prop_ro("class_name", [](const Node& n) { return ClassOf(Resolve(n)); },
            "Class of the base object (Sphere, Editable_Poly, ...).")
        .def_prop_rw("pos",
            [](const Node& n) { return FromPoint3(Resolve(n)->GetNodeTM(Now()).GetTrans()); },
            [](const Node& n, nb::handle value) {
                INode* node = Resolve(n);
                const TimeValue t = Now();
                Matrix3 tm = node->GetNodeTM(t);
                tm.SetTrans(ToPoint3(value, "pos"));
                node->SetNodeTM(t, tm);
            },
            "World position of the pivot (x, y, z).")
        .def_prop_ro("params", [](const Node& n) { Resolve(n); return Params{n.handle}; },
            "The base object's parameters: node.params.radius = 10.")
        .def_prop_rw("parent",
            [](const Node& n) -> std::optional<Node> {
                INode* parent = Resolve(n)->GetParentNode();
                if (!parent || parent->IsRootNode()) return std::nullopt;
                return Wrap(parent);
            },
            [](const Node& n, std::optional<Node> parent) {
                INode* node = Resolve(n);
                INode* target = parent ? Resolve(*parent) : GetCOREInterface()->GetRootNode();
                target->AttachChild(node, 1);
            },
            "Parent node or None; reparenting keeps the world position.")
        .def_prop_ro("children", [](const Node& n) {
            INode* node = Resolve(n);
            std::vector<Node> out;
            for (int i = 0; i < node->NumberOfChildren(); ++i) out.push_back(Wrap(node->GetChildNode(i)));
            return out;
        })
        .def_prop_ro("exists", [](const Node& n) {
            RequireMain();
            INode* node = HandlerHelpers::FindNodeByHandle(n.handle);
            return node && node->GetParentNode();
        }, "False once the node is deleted.")
        .def("delete", [](const Node& n) { GetCOREInterface()->DeleteNode(Resolve(n), FALSE); }, "Delete the node.")
        .def("__repr__", &Repr)
        .def("__eq__", [](const Node& a, const Node& b) { return a.handle == b.handle; })
        .def("__hash__", [](const Node& n) { return (Py_hash_t)n.handle; });

    nb::class_<Params>(m, "Params", "ParamBlock2 parameters by name: params.radius, params['radius'].")
        .def("__getattr__", [](const Params& p, const std::string& name) -> nb::object {
            if (name.size() > 1 && name[0] == '_' && name[1] == '_') throw nb::attribute_error(name.c_str());
            return GetParam(RequireParam(ParamOwner(p), name), Now());
        })
        .def("__setattr__", [](const Params& p, const std::string& name, nb::handle value) {
            SetParam(ParamOwner(p), name, value, Now());
        })
        .def("__getitem__", [](const Params& p, const std::string& name) {
            return GetParam(RequireParam(ParamOwner(p), name), Now());
        })
        .def("__setitem__", [](const Params& p, const std::string& name, nb::handle value) {
            SetParam(ParamOwner(p), name, value, Now());
        })
        .def("__contains__", [](const Params& p, const std::string& name) {
            return FindParam(ParamOwner(p), name).has_value();
        })
        .def("keys", [](const Params& p) { return ParamNames(ParamOwner(p)); }, "Parameter names.")
        .def("to_dict", [](const Params& p) {
            nb::dict out;
            const TimeValue t = Now();
            ForEachParam(ParamOwner(p), [&](IParamBlock2* block, const ParamDef& def) {
                out[WideToUtf8(def.int_name).c_str()] = GetParam(ParamRef{block, &def}, t);
            });
            return out;
        }, "Every parameter and its value.")
        .def("describe", &Describe, "One line per parameter: name, type, value, +anim when animatable.")
        .def("__dir__", [](const Params& p) {
            std::vector<std::string> names = ParamNames(ParamOwner(p));
            for (const char* method : {"describe", "keys", "to_dict"}) names.push_back(method);
            return names;
        })
        .def("__repr__", &ParamsRepr);

    nb::class_<AtTime>(m, "AtTime")
        .def("__enter__", [](const AtTime& a) { RequireMain(); TimeStack().push_back(a.time); })
        .def("__exit__", [](const AtTime&, nb::args) { if (!TimeStack().empty()) TimeStack().pop_back(); });

    nb::class_<AnimateMode>(m, "AnimateMode")
        .def("__enter__", [](AnimateMode& a) {
            RequireMain();
            a.previous = Animating();
            if (a.on) AnimateOn(); else AnimateOff();
        })
        .def("__exit__", [](AnimateMode& a, nb::args) { if (a.previous) AnimateOn(); else AnimateOff(); });

    m.def("node", &FindNode, "key"_a, "Node by name, handle or Node; NotFound or Ambiguous otherwise.");
    m.def("nodes", &Nodes, "pattern"_a = nb::none(), "Scene nodes, optionally filtered by a wildcard name pattern.");
    m.def("create", &Create, "cls"_a, "name"_a = "", "pos"_a = nb::none(), "params"_a,
          "Create an object (geometry, shape, helper, light, camera) with parameters: "
          "create('Sphere', 'Ball', pos=(0, 0, 10), radius=10, segs=32).");
    m.def("at", [](double frame) { return AtTime{FrameToTicks(frame)}; }, "frame"_a,
          "with at(frame): reads and edits happen at that frame; the time slider does not move.");
    m.def("animate", [](bool on) { return AnimateMode{on}; }, "on"_a = true,
          "with animate(): edits create keys, like the Auto Key button.");
    m.def("frame", [] { RequireMain(); return TicksToFrame(GetCOREInterface()->GetTime()); }, "Current frame.");
    m.def("set_frame", [](double frame) {
        RequireMain();
        GetCOREInterface()->SetTime(FrameToTicks(frame));
    }, "frame"_a, "Move the time slider.");
    m.def("redraw", [] { RequireMain(); GetCOREInterface()->RedrawViews(GetCOREInterface()->GetTime()); },
          "Redraw the viewports.");
}

} // namespace MX_ABI_TAG
} // namespace mx

NB_MODULE(_mx, m) { mx::Bind(m); }
