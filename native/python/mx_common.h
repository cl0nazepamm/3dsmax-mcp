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

// ── Names and suggestions ────────────────────────────────────────

std::string Lower(std::string s);
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
Node FindNode(nb::handle key);
std::vector<INode*> NodeList(nb::handle nodes, const char* what, bool allow_empty = false);

// ── Values ───────────────────────────────────────────────────────

std::vector<float> Floats(nb::handle value, size_t lo, size_t hi, const std::string& what);
Point3 ToPoint3(nb::handle value, const std::string& what);
inline nb::object FromPoint3(const Point3& p) { return nb::make_tuple(p.x, p.y, p.z); }

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

// A reference target as Python: Material, Texmap, Node, None for null, else
// its class name in angle brackets (mx_materials.cpp).
nb::object RefToPython(ReferenceTarget* target);
// The Material, Texmap or Node `value` holds, nullptr for None. `super`
// restricts the kind (0: any of them); anything else is BadParam naming `what`.
ReferenceTarget* PythonToRef(nb::handle value, SClass_ID super, const std::string& what);

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
nb::object ToPython(const nlohmann::json& value);
// dict/list/tuple/str/number/bool/None, and Node as its handle.
nlohmann::json FromPython(nb::handle value);

// ── Extending bound classes ──────────────────────────────────────
// A domain file adds methods and properties to classes bound elsewhere (Node,
// Params, Track, ...) without editing that file. Call after the class is bound.

template <typename Cls, typename F, typename... Extra>
void AddMethod(const char* name, F&& f, const Extra&... extra) {
    nb::handle cls = nb::type<Cls>();
    nb::setattr(cls, name, nb::cpp_function(std::forward<F>(f), nb::is_method(), nb::scope(cls), nb::name(name), extra...));
}

// A property is a Python property over hidden accessor methods
// (_get_<name>, _set_<name>): nanobind accessor functions crash when a
// property object calls them directly, bound methods do not.
nb::object MakeProperty(nb::handle cls, const char* name, bool settable, const char* doc);

template <typename Cls, typename G>
void AddProperty(const char* name, G&& getter, const char* doc) {
    AddMethod<Cls>(("_get_" + std::string(name)).c_str(), std::forward<G>(getter));
    nb::setattr(nb::type<Cls>(), name, MakeProperty(nb::type<Cls>(), name, false, doc));
}

template <typename Cls, typename G, typename S>
void AddProperty(const char* name, G&& getter, S&& setter, const char* doc) {
    AddMethod<Cls>(("_get_" + std::string(name)).c_str(), std::forward<G>(getter));
    AddMethod<Cls>(("_set_" + std::string(name)).c_str(), std::forward<S>(setter));
    nb::setattr(nb::type<Cls>(), name, MakeProperty(nb::type<Cls>(), name, true, doc));
}

// ── Bindings ─────────────────────────────────────────────────────

void BindParams(nb::module_& m);
void BindNodes(nb::module_& m);
void BindTracks(nb::module_& m);
void BindAnimation(nb::module_& m);
void BindModifiers(nb::module_& m);
void BindMaterials(nb::module_& m);
void BindNative(nb::module_& m);
void BindOrganize(nb::module_& m);
void BindViewport(nb::module_& m);
void BindScene(nb::module_& m);
void BindFiles(nb::module_& m);
void BindMesh(nb::module_& m);
void BindLighting(nb::module_& m);
void BindSolids(nb::module_& m);
void BindCurves(nb::module_& m);
void BindPlugins(nb::module_& m);

} // namespace MX_ABI_TAG
} // namespace mx
