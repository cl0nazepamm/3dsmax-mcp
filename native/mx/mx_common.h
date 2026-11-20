// Shared core of _mx: errors, time, node identity, value conversion and the
// ParamBlock2 parameter model used by nodes, modifiers and materials.
//
// Every call runs on 3ds Max's main thread, where Max's Python runs; any other
// thread gets WrongThread. Scene objects are held by anim handle and looked up
// again on every access, so a deleted object raises NotFound instead of
// touching freed memory. Nothing here opens an undo hold.
//
// Values follow MAXScript: angles in degrees, percentages 0-100, times in
// frames. Colors are 0-1 floats.
//
// Conventions every name follows (maxmcp/mx/_reference.py lints the ones a
// walk over the loaded module can see; the reference is not written while it
// reports a problem):
// - Every class, function, property and method has a description.
// - A wrapper of a plugin object (Node, Modifier, Material, Texmap, Controller,
//   Effect, and Object for any other kind) offers the object protocol: handle,
//   exists, class_name, class_info, params, interfaces, interface(name),
//   references(depth). A class binds what is its own; mx_plugins.cpp binds the
//   rest once for all of them.
// - What a parameter returns, it takes back: params[name] = params[name] is
//   never an error for a parameter that can be set.
// - A name has one shape: state and related objects are properties (nodes,
//   params, modifiers), work is a method, on every class that has the name.
// - What the scene or 3ds Max has many of is a collection (mx_collection.h):
//   nodes, materials, layers, classes, ... Lookup is c[key], listing is
//   c(pattern, ...), creation is c.new(...), deletion is c.remove(x) or the
//   item's own delete(). No function of the module does a collection's work.
// - A node is the same thing inside 3ds Max and outside it: what mx.Node
//   offers (maxmcp/mx/_node.py), Node offers under the same name and meaning.
// - Positions in a Python sequence count from 0 (node.splines[i], knots,
//   sub_materials). A number 3ds Max itself shows or MAXScript takes is an ID
//   and keeps Max's value, from 1: vertex, edge and face IDs, material IDs,
//   steps in a MAXScript path ('modifiers[1]', 'pos[2]').
// - Annotations say what a value is. `object` and a bare dict, list or tuple
//   result are not allowed, except for the few values the lint names as
//   dynamic (DYNAMIC): what a bridge handler, a published function or a
//   macroscript returns. A report dictionary names its keys: "Keys: {...}".
#pragma once

// nanobind first: the MAXScript headers pulled in below define function-like
// macros (is_string, ...) that would rewrite names inside nanobind's headers.
#include <nanobind/nanobind.h>
#include <nanobind/stl/optional.h>
#include <nanobind/stl/pair.h>
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
#include "mcp_bridge/modifier_helpers.h"
#include <istdplug.h>
#include "mx_build_tag.h"

namespace nb = nanobind;
using namespace nb::literals;

namespace mx {
// A new tag per dev build gives every build its own C++ types, so a rebuilt
// module can load beside an older one in the same Max session.
inline namespace MX_ABI_TAG {

using HandlerHelpers::Utf8ToWide;
using HandlerHelpers::WideToUtf8;

// ── Errors ───────────────────────────────────────────────────────
// Translated to the Python types of the same names (mx_module.cpp).

struct MaxError : std::runtime_error { using std::runtime_error::runtime_error; };
struct NotFound : MaxError { using MaxError::MaxError; };
struct Ambiguous : MaxError { using MaxError::MaxError; };
struct BadParam : MaxError { using MaxError::MaxError; };
struct UnknownParam : MaxError { using MaxError::MaxError; };
struct WrongThread : MaxError { using MaxError::MaxError; };

inline void RequireMain() {
    if (GetCurrentThreadId() != GetCOREInterface15()->GetMainThreadID())
        throw WrongThread("_mx runs only on 3ds Max's main thread");
}

// Free an instance that was created and then not used. Inside a script's undo
// step records may point at it already (3ds Max records the references a
// constructor makes), and DeleteThis would leave them dangling for a rollback
// to run into. MaybeAutoDelete frees it the same way when nothing is recorded,
// and defers the delete into the step while 3ds Max is holding.
inline void FreeUnused(Animatable* fresh) {
    if (!fresh) return;
    if (fresh->IsRefMaker() && static_cast<ReferenceMaker*>(fresh)->IsRefTarget())
        static_cast<ReferenceTarget*>(fresh)->MaybeAutoDelete();
    else
        fresh->DeleteThis();
}

// ── Names and suggestions ────────────────────────────────────────

std::string Lower(std::string s);
std::string Trim(const std::string& s);
// " Did you mean 'x'?" for the closest name within a few edits, else "".
std::string Suggest(const std::string& wanted, const std::vector<std::string>& names);
std::string Join(const std::vector<std::string>& names, size_t limit = 16);
// "a float", "an int".
std::string WithArticle(const std::string& noun);
std::string TypeNameOf(nb::handle value);
std::string ReprOf(nb::handle value);

// ── Time ─────────────────────────────────────────────────────────

std::vector<TimeValue>& TimeStack();
// The frame reads and edits happen at: the innermost at(), else the time slider.
TimeValue Now();
inline TimeValue FrameToTicks(double frame) { return (TimeValue)std::lround(frame * GetTicksPerFrame()); }
inline double TicksToFrame(TimeValue t) { return double(t) / GetTicksPerFrame(); }

// Setup that must never key, whatever the animate mode.
struct NoAnimate {
    NoAnimate() { SuspendAnimate(); }
    ~NoAnimate() { ResumeAnimate(); }
    NoAnimate(const NoAnimate&) = delete;
    NoAnimate& operator=(const NoAnimate&) = delete;
};

// Edits that must key, whatever the animate mode.
struct ForceAnimate {
    int previous;
    ForceAnimate() : previous(Animating()) { AnimateOn(); }
    ~ForceAnimate() { if (!previous) AnimateOff(); }
    ForceAnimate(const ForceAnimate&) = delete;
    ForceAnimate& operator=(const ForceAnimate&) = delete;
};

// ── Nodes ────────────────────────────────────────────────────────

struct Node { AnimHandle handle = 0; };

// The live INode, or NotFound once it left the scene.
INode* Resolve(const Node& n);
inline Node Wrap(INode* node) { return Node{Animatable::GetHandleByAnim(node)}; }
bool InScene(INode* node);
std::vector<INode*> AllNodes();
Object* BaseObject(INode* node);
std::string ClassOf(INode* node);
inline std::string NodeName(INode* node) { return WideToUtf8(node->GetName()); }
// 'name', for messages.
inline std::string Quoted(INode* node) { return "'" + NodeName(node) + "'"; }
Node FindNode(nb::handle key);
std::vector<INode*> NodeList(nb::handle nodes, const char* what, bool allow_empty = false);
// Nodes matching every filter (mx_scene.cpp): what nodes(...) lists.
std::vector<Node> FindNodes(const std::optional<std::string>& pattern, const std::optional<std::string>& cls,
                            const std::optional<std::string>& layer, bool rootsOnly,
                            const std::optional<std::string>& uses, nb::kwargs props);
// Nodes sharing this node's object, itself included.
int InstanceCount(INode* node);

// ── Values ───────────────────────────────────────────────────────

// An int or float; bools are not numbers here.
inline bool IsNumber(nb::handle v) {
    return (nb::isinstance<nb::int_>(v) || nb::isinstance<nb::float_>(v)) && !nb::isinstance<nb::bool_>(v);
}
inline bool IsInteger(nb::handle v) { return nb::isinstance<nb::int_>(v) && !nb::isinstance<nb::bool_>(v); }
inline bool IsSequence(nb::handle v) { return nb::isinstance<nb::list>(v) || nb::isinstance<nb::tuple>(v); }

std::vector<float> Floats(nb::handle value, size_t lo, size_t hi, const std::string& what);
Point3 ToPoint3(nb::handle value, const std::string& what);

// ── Value types (mx_values.cpp) ──────────────────────────────────
// Point3, Box, Matrix and Color are Python classes of _mx: tuples with names
// and a little vector math. A value still unpacks, compares and passes
// wherever a plain (x, y, z) does, and any (x, y, z) passes where one is asked
// for. A binding names the type it gives by returning one of the wrappers
// below, and the type it takes with an Arg; ToPoint3 and friends then convert
// arg.value under the argument's own name, so a wrong value is BadParam.

nb::object MakePoint3(const Point3& p);
// At double precision, for coordinates that never were a Point3.
nb::object MakePoint3(double x, double y, double z);
nb::object MakeBox(const Box3& box);
nb::object MakeMatrix(const Matrix3& tm);
nb::object MakeColor(float r, float g, float b);
inline nb::object FromPoint3(const Point3& p) { return MakePoint3(p); }
// Four rows of (x, y, z): the X, Y and Z axes, then the position.
Matrix3 ToMatrix3(nb::handle value, const std::string& what);

struct AsPoint { Point3 p; };
struct AsBox { Box3 box; };
struct AsMatrix { Matrix3 tm; };
struct AsColor { float r = 0, g = 0, b = 0; };
// A Point3 that already is a Python object (MakePoint3), named for the signature.
struct PointObject { nb::object value; };
struct PointArg { nb::handle value; };
struct MatrixArg { nb::handle value; };
struct ColorArg { nb::handle value; };
// One number for every axis, or (x, y, z).
struct ScaleArg { nb::handle value; };
// A node, or its name or handle. These convert to the handle the lookups take.
struct NodeArg {
    nb::handle value;
    operator nb::handle() const { return value; }
};
// A node or its name, or a list of them; NodesOrNone where None means "the default".
struct NodesArg {
    nb::handle value;
    operator nb::handle() const { return value; }
    bool is_none() const { return value.is_none(); }
};
struct NodesOrNone : NodesArg {};

// ── Parameters (ParamBlock2) ─────────────────────────────────────

// Parameters of a node's base object (owner 0) or of the animatable with anim
// handle `owner` (a modifier or material). `kind` names it in errors.
struct Params {
    AnimHandle node = 0;
    AnimHandle owner = 0;
};

Animatable* ParamOwner(const Params& p);

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

inline int BaseType(int type) { return type & ~TYPE_TAB; }
const char* TypeName(int type);
std::string ParamName(IParamBlock2* block, const ParamDef& def);
std::vector<std::string> ParamNames(Animatable* owner);
std::optional<ParamRef> FindParam(Animatable* owner, const std::string& name);
ParamRef RequireParam(Animatable* owner, const std::string& name);
nb::object GetParam(const ParamRef& ref, TimeValue t);
void SetParam(Animatable* owner, const std::string& name, nb::handle value, TimeValue t);
std::vector<std::string> AllNames(Animatable* owner);
nb::object GetValue(Animatable* owner, const std::string& name, TimeValue t);

// MAXScript property access (mx_maxscript.cpp), the fallback for names that
// are not ParamBlock2 parameters.
std::optional<ParamRef> MxsFindParam(Animatable* owner, const std::string& name);
std::optional<nb::object> MxsGet(Animatable* owner, const std::string& name, TimeValue t);
bool MxsSet(Animatable* owner, const std::string& name, nb::handle value, TimeValue t);
std::vector<std::string> MxsPropNames(Animatable* owner);

// External value = internal * scale: degrees for angles, 0-100 for percentages.
double ParamScale(int type);

// ── Generated MAXScript (mx_maxscript.cpp) ───────────────────────
// Where an operation exists only in MAXScript, its source is built here.
// Values enter it only through these literals.

// A string literal; NUL characters are refused.
std::string MxsString(const std::string& text);
// %.9g whatever the C locale; negatives in parentheses, so `f (-2)` stays one argument.
std::string MxsNumber(double v);
std::string MxsPoint(const Point3& p);
// (getAnimByHandle N)
std::string MxsAnim(AnimHandle handle);
inline std::string MxsAnim(Animatable* anim) { return MxsAnim(Animatable::GetHandleByAnim(anim)); }
// MAXScript source defining `fn __mxQuote s`: s as a JSON string literal, for
// scripts that reply with JSON text.
extern const char* const kMxsQuote;

// Runs source through the bridge's maxscript handler; its result as text.
std::string RunMaxScript(const std::string& code);
// Calls the MAXScript global function `name`; the caller keeps `args` alive.
// A MAXScript error becomes MaxError.
Value* MxsCall(const MCHAR* name, Value** args, int count);
// A value's printed form, and an error's message without its leading "-- ".
std::string MxsShown(Value* v);
std::string MxsErrorText(const MAXScriptException& e);

// MAXScript evaluates at its own time; this carries at() into it.
struct MxsTime {
    BOOL usedContext;
    TimeValue previous;
    explicit MxsTime(TimeValue t) : usedContext(thread_local(use_time_context)), previous(thread_local(current_time)) {
        thread_local(use_time_context) = TRUE;
        thread_local(current_time) = t;
    }
    ~MxsTime() {
        thread_local(use_time_context) = usedContext;
        thread_local(current_time) = previous;
    }
    MxsTime(const MxsTime&) = delete;
    MxsTime& operator=(const MxsTime&) = delete;
};

// ── Tracks ───────────────────────────────────────────────────────

// An animation track: a node transform track ("pos", "pos.z", "rot.y",
// "scale") or a parameter of a Params owner. Resolved on every access.
struct Track {
    AnimHandle node = 0;
    AnimHandle owner = 0;   // Params owner for parameter tracks (0 = base object)
    std::string path;       // transform path, or the parameter name
    bool param = false;
};

// ── Modifiers and materials ──────────────────────────────────────

struct ModifierRef {
    AnimHandle node = 0;
    AnimHandle mod = 0;
};

struct MaterialRef { AnimHandle mtl = 0; };
struct TexmapRef { AnimHandle tex = 0; };
// Any other reference target a parameter holds (a map's coords and output, a
// curve control, ...): the object protocol and its params, nothing of its own.
struct ObjectRef { AnimHandle handle = 0; };

// A reference target as Python: Material, Texmap, Node, None for null, else
// an Object (mx_materials.cpp).
nb::object RefToPython(ReferenceTarget* target);
// The Material, Texmap, Node or Object `value` holds, nullptr for None. `super`
// restricts the kind (0: any of them); anything else is BadParam naming `what`.
ReferenceTarget* PythonToRef(nb::handle value, SClass_ID super, const std::string& what);

// A target and its references, depth (0-8) levels down, as nested dicts
// (mx_scene.cpp): 'seen' marks a target listed above, and nodes other than
// the root are listed, not expanded.
nb::dict ReferenceTree(ReferenceTarget* root, int depth);

// A public class by MAXScript or UI name within the superclasses, in order.
ClassDesc* FindClass(const std::string& name, std::initializer_list<SClass_ID> supers);
// Public class names of the superclasses, for suggestions.
std::vector<std::string> ClassNames(std::initializer_list<SClass_ID> supers);
std::vector<ModifierRef> NodeModifiers(const Node& n);
ModifierRef AddModifier(const Node& n, const std::string& cls, const std::string& name, nb::kwargs params);
std::optional<MaterialRef> NodeMaterial(const Node& n);
void SetNodeMaterial(const Node& n, std::optional<MaterialRef> material);

// ── Bridge handlers (mx_native.cpp) ──────────────────────────────

// Run a bridge handler in-process ("native:scene_patch", "maxscript", ...)
// and return its parsed reply. A handler error becomes the matching _mx
// error. Heavy operations reuse their existing handler this way.
nlohmann::json NativeJson(const std::string& type, const nlohmann::json& args);
// A run_python script's undo step is open (the bridge's script_hold).
bool ScriptStepActive();
// Around a scene reset or fetch: closes a run_python script's undo step
// (committing what the script did so far) and opens a new one after, since
// replacing the scene inside an open undo step is unsafe. Outside a script,
// any open undo operation refuses with MaxError naming `call`.
struct SceneReplace {
    explicit SceneReplace(const char* call);
    ~SceneReplace();
    SceneReplace(const SceneReplace&) = delete;
    SceneReplace& operator=(const SceneReplace&) = delete;
    bool broke = false;
};
nb::object ToPython(const nlohmann::json& value);
// dict/list/tuple/str/number/bool/None, and Node as its handle.
nlohmann::json FromPython(nb::handle value);

// ── Extending bound classes ──────────────────────────────────────
// A domain file adds methods and properties to classes bound elsewhere (Node,
// Params, Track, ...) without editing that file. Call after the class is bound.

// The class is named by its Python type here, for classes whose C++ type
// another file keeps to itself; the function then takes `nb::handle self`.
template <typename F, typename... Extra>
void AddMethodTo(nb::handle cls, const char* name, F&& f, const Extra&... extra) {
    nb::setattr(cls, name, nb::cpp_function(std::forward<F>(f), nb::is_method(), nb::scope(cls), nb::name(name), extra...));
}

template <typename Cls, typename F, typename... Extra>
void AddMethod(const char* name, F&& f, const Extra&... extra) {
    AddMethodTo(nb::type<Cls>(), name, std::forward<F>(f), extra...);
}

// A property is a Python property over hidden accessor methods
// (_get_<name>, _set_<name>): nanobind accessor functions crash when a
// property object calls them directly, bound methods do not.
nb::object MakeProperty(nb::handle cls, const char* name, bool settable, const char* doc);

template <typename G>
void AddPropertyTo(nb::handle cls, const char* name, G&& getter, const char* doc) {
    AddMethodTo(cls, ("_get_" + std::string(name)).c_str(), std::forward<G>(getter));
    nb::setattr(cls, name, MakeProperty(cls, name, false, doc));
}

template <typename Cls, typename G>
void AddProperty(const char* name, G&& getter, const char* doc) {
    AddPropertyTo(nb::type<Cls>(), name, std::forward<G>(getter), doc);
}

template <typename Cls, typename G, typename S>
void AddProperty(const char* name, G&& getter, S&& setter, const char* doc) {
    AddMethod<Cls>(("_get_" + std::string(name)).c_str(), std::forward<G>(getter));
    AddMethod<Cls>(("_set_" + std::string(name)).c_str(), std::forward<S>(setter));
    nb::setattr(nb::type<Cls>(), name, MakeProperty(nb::type<Cls>(), name, true, doc));
}

// ── Bindings ─────────────────────────────────────────────────────

void BindValues(nb::module_& m);       // mx_values.cpp
void BindParams(nb::module_& m);       // mx_params.cpp
void BindNodes(nb::module_& m);        // mx_nodes.cpp
void BindModifiers(nb::module_& m);    // mx_modifiers.cpp
void BindMaterials(nb::module_& m);    // mx_materials.cpp
void BindTracks(nb::module_& m);       // mx_tracks.cpp
void BindControllers(nb::module_& m);  // mx_controllers.cpp
void BindNative(nb::module_& m);       // mx_native.cpp
void BindScene(nb::module_& m);        // mx_scene.cpp
void BindOrganize(nb::module_& m);     // mx_organize.cpp
void BindViewport(nb::module_& m);     // mx_viewport.cpp
void BindFiles(nb::module_& m);        // mx_files.cpp
void BindMesh(nb::module_& m);         // mx_mesh.cpp
void BindSolids(nb::module_& m);       // mx_solids.cpp
void BindCurves(nb::module_& m);       // mx_curves.cpp
void BindRendering(nb::module_& m);    // mx_render.cpp
void BindPlugins(nb::module_& m);      // mx_plugins.cpp

} // namespace MX_ABI_TAG
} // namespace mx

// How the value wrappers cross into Python, and what a signature calls them.
namespace nanobind::detail {

#define MX_VALUE_CASTER(Type, Text, Make)                                                        \
    template <> struct type_caster<mx::Type> {                                                   \
        NB_TYPE_CASTER(mx::Type, const_name(Text))                                               \
        bool from_python(handle, uint32_t, cleanup_list*) noexcept { return false; }             \
        static handle from_cpp(const mx::Type& v, rv_policy, cleanup_list*) noexcept {           \
            try {                                                                                \
                return (Make).release();                                                         \
            } catch (python_error& error) {                                                      \
                error.restore();                                                                 \
                return handle();                                                                 \
            }                                                                                    \
        }                                                                                        \
    };

// An Arg takes any Python value; the binding converts it and names the argument.
#define MX_ARG_CASTER(Type, Text)                                                                \
    template <> struct type_caster<mx::Type> {                                                   \
        NB_TYPE_CASTER(mx::Type, const_name(Text))                                               \
        bool from_python(handle src, uint32_t, cleanup_list*) noexcept {                         \
            value.value = src;                                                                   \
            return true;                                                                         \
        }                                                                                        \
        static handle from_cpp(const mx::Type& v, rv_policy, cleanup_list*) noexcept {           \
            return handle(v.value).inc_ref();                                                    \
        }                                                                                        \
    };

// A Result is a Python object the binding already built; only its name is new.
#define MX_RESULT_CASTER(Type, Text)                                                             \
    template <> struct type_caster<mx::Type> {                                                   \
        NB_TYPE_CASTER(mx::Type, const_name(Text))                                               \
        bool from_python(handle, uint32_t, cleanup_list*) noexcept { return false; }             \
        static handle from_cpp(const mx::Type& v, rv_policy, cleanup_list*) noexcept {           \
            return handle(v.value).inc_ref();                                                    \
        }                                                                                        \
    };

MX_VALUE_CASTER(AsPoint, "_mx.Point3", mx::MakePoint3(v.p))
MX_VALUE_CASTER(AsBox, "_mx.Box", mx::MakeBox(v.box))
MX_VALUE_CASTER(AsMatrix, "_mx.Matrix", mx::MakeMatrix(v.tm))
MX_VALUE_CASTER(AsColor, "_mx.Color", mx::MakeColor(v.r, v.g, v.b))
MX_RESULT_CASTER(PointObject, "_mx.Point3")
MX_ARG_CASTER(PointArg, "_mx.Point3")
MX_ARG_CASTER(MatrixArg, "_mx.Matrix")
MX_ARG_CASTER(ColorArg, "_mx.Color")
MX_ARG_CASTER(ScaleArg, "_mx.Point3 | float")
MX_ARG_CASTER(NodeArg, "_mx.Node | str | int")
MX_ARG_CASTER(NodesArg, "_mx.Node | str | collections.abc.Sequence[_mx.Node | str]")
// The OrNone types go on arguments declared with .none(), which adds "| None" to the signature itself.
MX_ARG_CASTER(NodesOrNone, "_mx.Node | str | collections.abc.Sequence[_mx.Node | str]")

} // namespace nanobind::detail

// A binding file names a type of its own with these, at global scope before its
// namespace: MX_ARG_TYPE(KeyValue, "float | _mx.Point3") declares mx::KeyValue,
// an argument that holds any Python value in .value (and converts to nb::handle);
// MX_RESULT_TYPE declares one that returns the nb::object in .value. The text is
// what the signature shows, so it must say what the value really is.
#define MX_ARG_TYPE(Type, Text)                                                                  \
    namespace mx { inline namespace MX_ABI_TAG {                                                 \
    struct Type {                                                                                \
        nb::handle value;                                                                        \
        operator nb::handle() const { return value; }                                            \
        bool is_none() const { return value.is_none(); }                                         \
    };                                                                                           \
    } }                                                                                          \
    namespace nanobind::detail { MX_ARG_CASTER(Type, Text) }

#define MX_RESULT_TYPE(Type, Text)                                                               \
    namespace mx { inline namespace MX_ABI_TAG {                                                 \
    struct Type { nb::object value; };                                                           \
    } }                                                                                          \
    namespace nanobind::detail { MX_RESULT_CASTER(Type, Text) }

// Shared by several files: a point or a box that may be left out. Declare the
// argument with .none(): an explicit None then reaches the binding, and the
// signature gets its "| None" from that.
MX_ARG_TYPE(PointOrNone, "_mx.Point3")
MX_ARG_TYPE(BoxOrNone, "_mx.Box")
