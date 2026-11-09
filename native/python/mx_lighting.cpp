// Lights, render jobs and render effects.
//
// Lights are read and edited through the verified provider of their renderer
// (photometric lights under Scanline, V-Ray, Octane, Corona, FStorm). Every enum
// value written or decoded comes from a provider table below, each taken from
// the vendor's own scripts or UI data; any other value reads as unknown, never
// as a guess. Creation runs through the bridge's plugin_patch (one undo step,
// typed readback, rollback); edits write the parameters directly, at the
// current time like node.params. Semantic values are raw parameter values,
// the form the provider tables were verified in; info() extras read like
// node.params (degrees, percentages 0-100).
//
// mx.render() queues a production render that starts once the script that
// queued it returns (or at job.wait()). A done file outside Max records the
// outcome, so render_automations(action='status') sees it as well.
#include "mx_common.h"

#include "mcp_bridge/node_ref.h"

#include <lslights.h>
#include <sfx.h>
#include <psapi.h>

#include <chrono>
#include <filesystem>
#include <fstream>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <random>
#include <sstream>
#include <thread>

namespace mx {
inline namespace MX_ABI_TAG {

using json = nlohmann::json;
namespace fs = std::filesystem;

// ── Providers ────────────────────────────────────────────────────

enum class Family { None, Photometric, VRay, Octane, Corona, FStorm };

enum class Role {
    Unknown,
    PhotoPoint, PhotoRect, PhotoDisk, PhotoSphere, PhotoCylinder,
    VRay,
    Octane, OctaneEnv,
    Corona, CoronaSun, CoronaMoon, CoronaSky, CoronaBitmap, CoronaEnvMap,
    FStorm, FStormSun,
};

static const Class_ID kVRayLight(1012233633u, 1607860959u);
static const Class_ID kVRayBitmap(1734939723u, 46203261u);
static const Class_ID kOctaneLight(592523983u, 1640440069u);
static const Class_ID kOctaneEmission(249824500u, 396179973u);
static const Class_ID kOctaneEnv(1197826109u, 1274825817u);
static const Class_ID kOctaneImage(1865767904u, 1645758520u);
static const Class_ID kCoronaRenderer(1655201228u, 1379677700u);
static const Class_ID kCoronaLight(1110459877u, 692869241u);
static const Class_ID kCoronaSun(2084191360u, 164583279u);
static const Class_ID kCoronaMoon(1510691909u, 443094578u);
static const Class_ID kCoronaSky(1498904930u, 1286306497u);
static const Class_ID kCoronaBitmap(2881116036u, 1699234372u);
static const Class_ID kFStormRenderer(33030992u, 1363098752u);
static const Class_ID kFStormLight(605978406u, 1370574548u);
static const Class_ID kFStormSun(807800799u, 784941243u);

struct Choice { const char* name; int value; };
using Choices = std::vector<Choice>;

// Chaos VRay-VRayLightLister.mcr: its Type and Units dropdowns show type + 1
// and normalizeColor + 1.
static const Choices kVRayTypes = {{"rectangle", 0}, {"dome", 1}, {"sphere", 2}, {"mesh", 3}, {"disk", 4}};
static const Choices kVRayUnits = {{"renderer", 0}, {"lm", 1}, {"cd/m2", 2}, {"W", 3}, {"W/m2/sr", 4}};
static const Choices kVRayColor = {{"rgb", 0}, {"kelvin", 1}};
// V-Ray 7 update 3 VRayBitmap combo item data.
static const Choices kVRayMapping = {{"angular", 0}, {"cubic", 1}, {"spherical", 2}, {"mirrored_ball", 3}, {"max_standard", 4}};
// Octane 2026.3 Type combo item data.
static const Choices kOctaneShapes = {{"rectangle", 0}, {"disk", 1}, {"sphere", 3}, {"cylinder", 4}};
// Chaos coronaConverter.ms shipped with Corona 15: units L3354, colorMode L3379,
// shape L3410-3546, CoronaSun colorMode L3744-3746, CoronaBitmap mapping L45-46.
static const Choices kCoronaShapes = {{"sphere", 0}, {"rectangle", 1}, {"disk", 2}, {"cylinder", 3}};
static const Choices kCoronaUnits = {{"renderer", 0}, {"lm", 1}, {"cd", 2}, {"lx", 3}};
static const Choices kCoronaColor = {{"rgb", 0}, {"kelvin", 1}, {"texmap", 2}};
static const Choices kCoronaSunColor = {{"rgb", 0}, {"kelvin", 1}, {"realistic", 2}};
static const Choices kCoronaMapping = {{"spherical", 0}, {"screen", 1}};
// FStorm 2.0.0Z descriptors on Max 2027.2; the sun's model 0 is the legacy RGB sun.
static const Choices kFStormShapes = {{"rectangle", 0}, {"disk", 1}, {"sphere", 2}};
static const Choices kFStormColor = {{"rgb", 0}, {"kelvin", 1}};
static const Choices kFStormSunModel = {{"rgb", 0}, {"physical", 1}};

static std::optional<int> ValueOf(const Choices& choices, const std::string& name) {
    for (const Choice& c : choices)
        if (name == c.name) return c.value;
    return std::nullopt;
}

static std::string NameOf(const Choices& choices, const json& value) {
    if (!value.is_number_integer()) return "";
    for (const Choice& c : choices)
        if (value.get<int>() == c.value) return c.name;
    return "";
}

struct Provider {
    Family family;
    const char* name;
    std::vector<std::string> kinds, shapes, units, colors, bodies;
    const char* environment;  // how environment lighting binds, or null
    std::vector<std::string> notes;
};

static const Provider& ProviderOf(Family family) {
    static const std::vector<Provider> providers = {
        {Family::Photometric, "photometric", {"area", "point"}, {"rectangle", "disk", "sphere", "cylinder"}, {"cd"},
         {"kelvin", "rgb"}, {}, nullptr,
         {"Intensity is in candelas: the photometric intensity parameter always stores cd."}},
        {Family::VRay, "vray", {"area", "environment"}, {"rectangle", "disk", "sphere"}, {"renderer", "lm", "cd/m2"},
         {"kelvin", "rgb"}, {}, "dome_node",
         {"A rectangle's width is V-Ray's Length (local X), its height V-Ray's Width (local Y).",
          "environment_light makes a spherical VRayLight dome around an HDRI or an existing map."}},
        {Family::Octane, "octane", {"area", "environment"}, {"rectangle", "disk", "sphere", "cylinder"}, {"renderer"},
         {"kelvin"}, {}, "scene_environment_map",
         {"Analytic lights take Kelvin color and renderer power, which live on the light's Black Body emission map."}},
        {Family::Corona, "corona", {"area", "environment", "directional"}, {"rectangle", "disk", "sphere", "cylinder"},
         {"renderer", "lm", "cd"}, {"kelvin", "rgb"}, {"sun", "moon"}, "scene_environment_map",
         {"Corona lights always cast shadows; cast_shadows=False is refused.",
          "environment_light binds the map itself at intensity 1 and needs Corona's environment set to "
          "'3ds Max settings'; single-map and LightMix overrides are left alone.",
          "Directional lights are CoronaSun (body='sun') or CoronaMoon (body='moon', RGB filter only); their "
          "intensity is Corona's multiplier. Sky linking, size multiplier and moon phase keep plugin defaults."}},
        {Family::FStorm, "fstorm", {"area", "directional"}, {"rectangle", "disk", "sphere"}, {"renderer"},
         {"kelvin", "rgb"}, {"sun"}, nullptr,
         {"Area lights take RGB or 500-24000 K; power is native renderer power per area and must be > 0.",
          "Suns are placed by solar={'hour', 'month', 'latitude', 'north_direction'}, never by orientation; their "
          "color is the physical model or a legacy RGB, never Kelvin.",
          "FStormSunLight is sun light only: no sky is added and the environment is left alone.",
          "Other FStorm switches (visible, gi_visible, double_sided, affect_diffuse, affect_glossy) are in "
          "light.params."}},
    };
    for (const Provider& p : providers)
        if (p.family == family) return p;
    throw MaxError("no lighting provider");
}

static const char* FamilyName(Family family) {
    return family == Family::None ? nullptr : ProviderOf(family).name;
}

static nb::object FamilyText(Family family) {
    const char* name = FamilyName(family);
    return name ? nb::cast(name) : nb::none();
}

static Family RendererFamily(const Class_ID& id) {
    if (id == Class_ID(1, 0)) return Family::Photometric;  // Scanline
    if (id == Class_ID(1941615238u, 2012806412u) || id == Class_ID(1770671000u, 1323107829u)) return Family::VRay;
    if (id == Class_ID(2717442453u, 1319335807u)) return Family::Octane;
    if (id == kCoronaRenderer) return Family::Corona;
    if (id == kFStormRenderer) return Family::FStorm;
    return Family::None;
}

static Family ProductionFamily() {
    Renderer* renderer = GetCOREInterface()->GetRenderer(RS_Production);
    return renderer ? RendererFamily(renderer->ClassID()) : Family::None;
}

static Role RoleOf(Animatable* anim) {
    if (!anim) return Role::Unknown;
    const Class_ID id = anim->ClassID();
    if (anim->SuperClassID() == TEXMAP_CLASS_ID) {
        if (id == kOctaneEnv) return Role::OctaneEnv;
        if (id == kCoronaSky) return Role::CoronaSky;
        if (id == kCoronaBitmap) return Role::CoronaBitmap;
        return Role::Unknown;
    }
    if (anim->SuperClassID() != LIGHT_CLASS_ID) return Role::Unknown;
    if (id == LS_POINT_LIGHT_ID) return Role::PhotoPoint;
    if (id == LS_AREA_LIGHT_ID) return Role::PhotoRect;
    if (id == LS_DISC_LIGHT_ID) return Role::PhotoDisk;
    if (id == LS_SPHERE_LIGHT_ID) return Role::PhotoSphere;
    if (id == LS_CYLINDER_LIGHT_ID) return Role::PhotoCylinder;
    if (id == kVRayLight) return Role::VRay;
    if (id == kOctaneLight) return Role::Octane;
    if (id == kCoronaLight) return Role::Corona;
    if (id == kCoronaSun) return Role::CoronaSun;
    if (id == kCoronaMoon) return Role::CoronaMoon;
    if (id == kFStormLight) return Role::FStorm;
    if (id == kFStormSun) return Role::FStormSun;
    return Role::Unknown;
}

static Family FamilyOf(Role role) {
    switch (role) {
    case Role::PhotoPoint: case Role::PhotoRect: case Role::PhotoDisk: case Role::PhotoSphere: case Role::PhotoCylinder:
        return Family::Photometric;
    case Role::VRay: return Family::VRay;
    case Role::Octane: case Role::OctaneEnv: return Family::Octane;
    case Role::Corona: case Role::CoronaSun: case Role::CoronaMoon: case Role::CoronaSky: case Role::CoronaBitmap:
    case Role::CoronaEnvMap:
        return Family::Corona;
    case Role::FStorm: case Role::FStormSun: return Family::FStorm;
    default: return Family::None;
    }
}

static bool IsEnvironmentMap(Role role) {
    return role == Role::OctaneEnv || role == Role::CoronaSky || role == Role::CoronaBitmap || role == Role::CoronaEnvMap;
}

static std::string Cls(Animatable* anim) { return HandlerHelpers::MaxScriptVisibleClassName(anim); }

static std::string Text(double value) {
    std::ostringstream out;
    out << value;
    return out.str();
}

static std::string Quoted(const std::vector<std::string>& names) {
    std::vector<std::string> quoted;
    for (const std::string& name : names) quoted.push_back("'" + name + "'");
    return Join(quoted);
}

static bool Has(const std::vector<std::string>& names, const std::string& name) {
    return std::find(names.begin(), names.end(), name) != names.end();
}

// ── Raw parameters by internal name ──────────────────────────────
// The provider tables name parameters by their exact internal names; a name
// declared by two different parameters is refused rather than guessed.

static bool IsFloat(int type) {
    return type == TYPE_FLOAT || type == TYPE_WORLD || type == TYPE_ANGLE || type == TYPE_PCNT_FRAC ||
           type == TYPE_COLOR_CHANNEL;
}
static bool IsInt(int type) {
    return type == TYPE_INT || type == TYPE_INDEX || type == TYPE_RADIOBTN_INDEX || type == TYPE_ENUM;
}
static bool IsBool(int type) { return type == TYPE_BOOL || type == TYPE_bool; }
static bool IsRef(int type) {
    return type == TYPE_TEXMAP || type == TYPE_MTL || type == TYPE_INODE || type == TYPE_REFTARG;
}
static size_t Components(int type) {
    switch (type) {
    case TYPE_POINT2: return 2;
    case TYPE_POINT3: case TYPE_RGBA: return 3;
    case TYPE_POINT4: case TYPE_FRGBA: return 4;
    default: return 0;
    }
}

[[noreturn]] static void Unverified(const std::string& what, const std::string& problem) {
    throw MaxError(what + " " + problem + ": this plugin version is not verified; use light.params");
}

static std::optional<ParamRef> Lookup(Animatable* owner, const char* name) {
    const std::wstring wanted = Utf8ToWide(name);
    std::optional<ParamRef> found;
    ForEachParam(owner, [&](IParamBlock2* block, const ParamDef& def) {
        if (wcscmp(def.int_name, wanted.c_str()) != 0) return;
        if (found && (found->block->ID() != block->ID() || found->def->ID != def.ID))
            Unverified(Cls(owner) + "." + name, "is declared twice");
        if (!found) found = ParamRef{block, &def};
    });
    return found;
}

static ParamRef Need(Animatable* owner, const char* name) {
    if (auto ref = Lookup(owner, name)) return *ref;
    Unverified(Cls(owner), std::string("has no '") + name + "' parameter");
}

static json ReadOne(const ParamRef& ref, TimeValue t, int index) {
    IParamBlock2* pb = ref.block;
    const ParamID id = ref.def->ID;
    const int type = BaseType(ref.def->type);
    Interval valid = FOREVER;
    if (IsFloat(type)) { float v = 0; pb->GetValue(id, t, v, valid, index); return v; }
    if (IsInt(type)) { int v = 0; pb->GetValue(id, t, v, valid, index); return v; }
    if (IsBool(type)) { int v = 0; pb->GetValue(id, t, v, valid, index); return v != 0; }
    switch (type) {
    case TYPE_RGBA: { Color c; pb->GetValue(id, t, c, valid, index); return json::array({c.r, c.g, c.b}); }
    case TYPE_FRGBA: { AColor c; pb->GetValue(id, t, c, valid, index); return json::array({c.r, c.g, c.b, c.a}); }
    case TYPE_POINT3: { Point3 p; pb->GetValue(id, t, p, valid, index); return json::array({p.x, p.y, p.z}); }
    case TYPE_POINT2: { Point2 p; pb->GetValue(id, t, p, valid, index); return json::array({p.x, p.y}); }
    case TYPE_STRING: case TYPE_FILENAME: {
        const MCHAR* s = nullptr;
        pb->GetValue(id, t, s, valid, index);
        return WideToUtf8(s);
    }
    case TYPE_TEXMAP: {
        Texmap* map = nullptr;
        pb->GetValue(id, t, map, valid, index);
        return map ? json((unsigned long long)Animatable::GetHandleByAnim(map)) : json();
    }
    case TYPE_REFTARG: {
        ReferenceTarget* target = nullptr;
        pb->GetValue(id, t, target, valid, index);
        return target ? json((unsigned long long)Animatable::GetHandleByAnim(target)) : json();
    }
    default: return json();
    }
}

static json ReadRaw(const ParamRef& ref, TimeValue t) {
    if (!(ref.def->type & TYPE_TAB)) return ReadOne(ref, t, 0);
    json items = json::array();
    for (int i = 0; i < ref.block->Count(ref.def->ID); ++i) items.push_back(ReadOne(ref, t, i));
    return items;
}

static json Get(Animatable* owner, const char* name) {
    const auto ref = Lookup(owner, name);
    return ref ? ReadRaw(*ref, Now()) : json();
}

static json Must(Animatable* owner, const char* name) { return ReadRaw(Need(owner, name), Now()); }

static double Number(const json& value, const std::string& what) {
    if (value.is_boolean()) return value.get<bool>() ? 1.0 : 0.0;
    if (!value.is_number()) Unverified(what, "is not a number");
    return value.get<double>();
}

static bool Flag(const json& value) { return value.is_boolean() ? value.get<bool>() : value.is_number() && value != 0; }

static Texmap* MapIn(Animatable* owner, const char* name) {
    const json handle = Get(owner, name);
    if (!handle.is_number()) return nullptr;
    Animatable* anim = Animatable::GetAnimByHandle((AnimHandle)handle.get<unsigned long long>());
    return anim && anim->SuperClassID() == TEXMAP_CLASS_ID ? static_cast<Texmap*>(anim) : nullptr;
}

// The value as this parameter takes it (a 3-part color gains alpha for a
// 4-part one), after the read-only, type and range checks plugin_patch makes.
static json Fit(const ParamDef& def, json value, const std::string& what) {
    if (def.flags & P_READ_ONLY) throw BadParam(what + " is read-only");
    const int type = BaseType(def.type);
    if (type == TYPE_FRGBA && (value.is_array)() && value.size() == 3) value.push_back(1.0);
    bool fits = false;
    if (value.is_boolean()) fits = IsBool(type);
    else if (value.is_number_integer()) fits = IsInt(type);
    else if (value.is_number_float()) fits = IsFloat(type);
    else if ((value.is_string)()) fits = type == TYPE_STRING || type == TYPE_FILENAME;
    else if (value.is_object() || value.is_null()) fits = IsRef(type);
    else if ((value.is_array)()) fits = (def.type & TYPE_TAB) ? IsFloat(type) : Components(type) == value.size();
    if (!fits) Unverified(what, "is " + WithArticle(TypeName(type)) + " parameter, not the type its provider writes");
    if ((def.flags & P_HAS_RANGE) && value.is_number()) {
        const double lo = IsFloat(type) ? def.range_low.f : def.range_low.i;
        const double hi = IsFloat(type) ? def.range_high.f : def.range_high.i;
        const double v = value.get<double>();
        if (v < lo || v > hi) throw BadParam(what + " = " + Text(v) + " is outside its range " + Text(lo) + " to " + Text(hi));
    }
    return value;
}

static void WriteRaw(const ParamRef& ref, const json& v, TimeValue t, const std::string& what) {
    IParamBlock2* pb = ref.block;
    const ParamID id = ref.def->ID;
    const int type = BaseType(ref.def->type);
    auto f = [&](size_t i) { return v[i].get<float>(); };
    BOOL ok = TRUE;
    if (ref.def->type & TYPE_TAB) {
        if ((int)v.size() != pb->Count(id))
            throw BadParam(what + " holds " + std::to_string(pb->Count(id)) + " values, not " + std::to_string(v.size()));
        for (size_t i = 0; ok && i < v.size(); ++i) ok = pb->SetValue(id, t, f(i), (int)i);
    } else if (IsFloat(type)) ok = pb->SetValue(id, t, v.get<float>());
    else if (IsInt(type)) ok = pb->SetValue(id, t, v.get<int>());
    else if (IsBool(type)) ok = pb->SetValue(id, t, v.get<bool>() ? 1 : 0);
    else if (type == TYPE_RGBA) ok = pb->SetValue(id, t, Color(f(0), f(1), f(2)));
    else if (type == TYPE_FRGBA) ok = pb->SetValue(id, t, AColor(f(0), f(1), f(2), f(3)));
    else if (type == TYPE_POINT3) ok = pb->SetValue(id, t, Point3(f(0), f(1), f(2)));
    else if (type == TYPE_POINT2) ok = pb->SetValue(id, t, Point2(f(0), f(1)));
    else Unverified(what, "is " + WithArticle(TypeName(type)) + " parameter, which light edits do not write");
    if (!ok) throw BadParam("3ds Max refused " + what + " = " + v.dump());
}

struct Assign {
    Animatable* owner;
    const char* param;
    json value;
};

// Checks every change first, so a refused value leaves the light as it was;
// then writes them at the current time.
static void Apply(const std::vector<Assign>& changes) {
    struct Checked { ParamRef ref; json value; std::string what; };
    std::vector<Checked> checked;
    for (const Assign& a : changes) {
        const ParamRef ref = Need(a.owner, a.param);
        const std::string what = Cls(a.owner) + "." + a.param;
        checked.push_back({ref, Fit(*ref.def, a.value, what), what});
    }
    const TimeValue t = Now();
    for (const Checked& c : checked) WriteRaw(c.ref, c.value, t, c.what);
}

// ── Lights ───────────────────────────────────────────────────────

// A light node, or an environment map that lights the scene (no node).
struct Light {
    AnimHandle node = 0;
    AnimHandle map = 0;
};

struct LightView {
    INode* node = nullptr;
    Animatable* owner = nullptr;  // the base object, or the environment map
    Role role = Role::Unknown;
};

static LightView ResolveLight(const Light& l) {
    RequireMain();
    LightView v;
    if (l.node) {
        v.node = Resolve(Node{l.node});
        v.owner = BaseObject(v.node);
        if (!v.owner || v.owner->SuperClassID() != LIGHT_CLASS_ID)
            throw NotFound("'" + NodeName(v.node) + "' is no longer a light");
    } else {
        v.owner = Animatable::GetAnimByHandle(l.map);
        if (!v.owner || v.owner->SuperClassID() != TEXMAP_CLASS_ID)
            throw NotFound("environment map #" + std::to_string(l.map) + " was deleted");
    }
    v.role = RoleOf(v.owner);
    // Corona lights the scene from whatever map sits in the environment slot.
    if (v.role == Role::Unknown && !l.node && v.owner == GetCOREInterface()->GetEnvironmentMap() &&
        ProductionFamily() == Family::Corona)
        v.role = Role::CoronaEnvMap;
    return v;
}

static const Provider& Supported(const LightView& v) {
    if (v.role == Role::Unknown)
        throw BadParam(Cls(v.owner) + " has no verified lighting provider; edit it through light.params");
    return ProviderOf(FamilyOf(v.role));
}

static Animatable* Emission(const LightView& v) {
    Texmap* map = MapIn(v.owner, "emission");
    if (!map || map->ClassID() != kOctaneEmission)
        throw BadParam("this Octane light's emission is not a Black Body emission map; edit its emission graph "
                       "through light.params");
    return map;
}

struct Form { std::string kind, shape, body; };

static Form AreaShape(const Choices& shapes, const json& value) {
    const std::string shape = NameOf(shapes, value);
    return shape.empty() ? Form{} : Form{"area", shape, ""};
}

static Form FormOf(const LightView& v) {
    switch (v.role) {
    case Role::PhotoPoint: return {"point", "", ""};
    case Role::PhotoRect: return {"area", "rectangle", ""};
    case Role::PhotoDisk: return {"area", "disk", ""};
    case Role::PhotoSphere: return {"area", "sphere", ""};
    case Role::PhotoCylinder: return {"area", "cylinder", ""};
    case Role::VRay: {
        const std::string type = NameOf(kVRayTypes, Must(v.owner, "type"));
        if (type == "dome") return {"environment", "", ""};
        return type.empty() ? Form{} : Form{"area", type, ""};
    }
    case Role::Octane: return AreaShape(kOctaneShapes, Must(v.owner, "analyticLightType"));
    case Role::Corona: return AreaShape(kCoronaShapes, Must(v.owner, "shape"));
    case Role::FStorm: return AreaShape(kFStormShapes, Must(v.owner, "shape"));
    case Role::CoronaSun: case Role::FStormSun: return {"directional", "", "sun"};
    case Role::CoronaMoon: return {"directional", "", "moon"};
    default: return IsEnvironmentMap(v.role) ? Form{"environment", "", ""} : Form{};
    }
}

static const char* EnableParam(Role role) {
    switch (role) {
    case Role::Octane: case Role::FStorm: case Role::FStormSun: return "enabled";
    default: return IsEnvironmentMap(role) ? nullptr : "on";
    }
}

static bool EnabledOf(const LightView& v) {
    Supported(v);
    if (const char* param = EnableParam(v.role)) return Flag(Must(v.owner, param));
    Interface* ip = GetCOREInterface();
    return ip->GetEnvironmentMap() == v.owner && ip->GetUseEnvironmentMap();
}

static std::optional<bool> ShadowsOf(const LightView& v) {
    switch (v.role) {
    case Role::Corona: case Role::CoronaSun: case Role::CoronaMoon: case Role::FStormSun: return true;
    case Role::FStorm: return Flag(Must(v.owner, "cast_shadows"));
    case Role::Octane: return Flag(Must(Emission(v), "castShadows"));
    default: break;
    }
    Supported(v);
    if (IsEnvironmentMap(v.role)) return std::nullopt;
    return Flag(Must(v.owner, "castShadows"));
}

struct Output {
    double value = 0;
    std::string unit;  // empty: a unit value outside the provider table
};

static Output OutputOf(const LightView& v) {
    auto read = [&](Animatable* owner, const char* param, std::string unit) {
        return Output{Number(Must(owner, param), Cls(owner) + "." + param), std::move(unit)};
    };
    switch (v.role) {
    case Role::PhotoPoint: case Role::PhotoRect: case Role::PhotoDisk: case Role::PhotoSphere: case Role::PhotoCylinder:
        return read(v.owner, "intensity", "cd");
    case Role::VRay: return read(v.owner, "multiplier", NameOf(kVRayUnits, Must(v.owner, "normalizeColor")));
    case Role::Octane: return read(Emission(v), "power", "renderer");
    case Role::OctaneEnv: return read(v.owner, "power", "renderer");
    case Role::Corona: return read(v.owner, "intensity", NameOf(kCoronaUnits, Must(v.owner, "intensityUnits")));
    case Role::CoronaSun: case Role::CoronaMoon: return read(v.owner, "intensity", "renderer");
    case Role::CoronaSky: return read(v.owner, "intensityMultiplier", "renderer");
    case Role::CoronaBitmap: case Role::CoronaEnvMap: return {1.0, "renderer"};  // the slot has no multiplier
    case Role::FStorm: case Role::FStormSun: return read(v.owner, "power", "renderer");
    default: Supported(v); return {};
    }
}

struct Tint {
    std::string mode;  // empty: a mode value outside the provider table
    json rgb, kelvin;
};

static Tint Pick(const std::string& mode, Animatable* owner, const char* rgb, const char* kelvin) {
    Tint t{mode, json(), json()};
    if (mode == "rgb") t.rgb = Must(owner, rgb);
    if (mode == "kelvin") t.kelvin = Must(owner, kelvin);
    return t;
}

static Tint TintOf(const LightView& v) {
    Animatable* o = v.owner;
    switch (v.role) {
    case Role::PhotoPoint: case Role::PhotoRect: case Role::PhotoDisk: case Role::PhotoSphere: case Role::PhotoCylinder:
        return Pick(Flag(Must(o, "useKelvin")) ? "kelvin" : "rgb", o, "rgb", "kelvin");
    case Role::VRay: return Pick(NameOf(kVRayColor, Must(o, "color_mode")), o, "color", "color_temperature");
    case Role::Octane: return Pick("kelvin", Emission(v), "", "temperature");
    case Role::Corona: return Pick(NameOf(kCoronaColor, Must(o, "colorMode")), o, "color", "blackbodyTemp");
    case Role::CoronaSun: return Pick(NameOf(kCoronaSunColor, Must(o, "colorMode")), o, "colorDirect", "blackbodyTemperature");
    case Role::CoronaMoon: return Pick("rgb", o, "colorFilter", "");
    case Role::FStorm: return Pick(NameOf(kFStormColor, Must(o, "color_type")), o, "color", "temperature");
    case Role::FStormSun: return Pick(NameOf(kFStormSunModel, Must(o, "model")), o, "sun_color", "");
    default: Supported(v); return {};  // environment maps take their color from the image
    }
}

// One size of an area shape: value = raw * scale, from a scalar parameter
// (index -1), one element of a 2-vector, or both elements (index 2).
struct Dim {
    const char* dim;
    const char* param;
    int index = -1;
    double scale = 1.0;
};

static std::vector<Dim> DimsOf(Role role, const std::string& shape) {
    const bool rect = shape == "rectangle", round = shape == "disk" || shape == "sphere", tube = shape == "cylinder";
    switch (role) {
    case Role::PhotoRect: return {{"width", "light_width"}, {"height", "light_length"}};
    case Role::PhotoDisk: case Role::PhotoSphere: return {{"radius", "light_radius"}};
    case Role::PhotoCylinder: return {{"radius", "light_radius"}, {"length", "light_length"}};
    case Role::VRay:
        if (rect) return {{"width", "sizeLength"}, {"height", "sizeWidth"}};
        if (round) return {{"radius", "size0"}};
        return {};
    case Role::Octane:
        if (rect) return {{"width", "quadAnalyticLightSize", 0}, {"height", "quadAnalyticLightSize", 1}};
        if (shape == "disk") return {{"radius", "diskAnalyticLightSize", 2, 0.5}};  // two diameters
        if (shape == "sphere") return {{"radius", "sphereAnalyticLightRadius"}};
        if (tube) return {{"radius", "tubeAnalyticLightCapRadius"}, {"length", "tubeAnalyticLightLength"}};
        return {};
    case Role::Corona:
        if (rect) return {{"width", "width"}, {"height", "height"}};
        if (round) return {{"radius", "width"}};
        if (tube) return {{"radius", "width"}, {"length", "height"}};
        return {};
    case Role::FStorm:
        if (rect) return {{"width", "size_x", -1, 2.0}, {"height", "size_y", -1, 2.0}};  // half extents
        if (round) return {{"radius", "size_x"}};
        return {};
    default: return {};
    }
}

static std::optional<double> DimValue(Animatable* owner, const Dim& d) {
    const json raw = Must(owner, d.param);
    const std::string what = Cls(owner) + "." + d.param;
    if (d.index < 0) return Number(raw, what) * d.scale;
    if (!(raw.is_array)() || raw.size() != 2) Unverified(what, "is not a pair");
    if (d.index == 2 && raw[0] != raw[1]) return std::nullopt;  // an ellipse has no single radius
    return Number(raw[d.index == 2 ? 0 : d.index], what) * d.scale;
}

// Raw writes for the given sizes, merged per parameter. A pair keeps its other
// element from `current`, or starts from zeros on a new light.
static std::vector<std::pair<const char*, json>> SizeWrites(const std::vector<Dim>& dims,
                                                            const std::map<std::string, double>& sizes,
                                                            const std::function<json(const char*)>& current) {
    std::vector<std::pair<const char*, json>> writes;
    for (const Dim& d : dims) {
        const auto given = sizes.find(d.dim);
        if (given == sizes.end()) continue;
        const double raw = given->second / d.scale;
        if (d.index < 0) {
            writes.push_back({d.param, raw});
            continue;
        }
        auto slot = std::find_if(writes.begin(), writes.end(), [&](const auto& w) { return std::strcmp(w.first, d.param) == 0; });
        if (slot == writes.end()) {
            json pair = current ? current(d.param) : json();
            if (!(pair.is_array)() || pair.size() != 2) pair = json::array({0.0, 0.0});
            writes.push_back({d.param, pair});
            slot = writes.end() - 1;
        }
        for (int i = 0; i < 2; ++i)
            if (d.index == 2 || d.index == i) slot->second[i] = raw;
    }
    return writes;
}

// ── Light readback ───────────────────────────────────────────────

static nb::object Maybe(const json& value) { return value.is_null() ? nb::none() : ToPython(value); }

static nb::object Rgb(const json& value) {
    if (!(value.is_array)() || value.size() < 3 || !value[0].is_number() || !value[1].is_number() || !value[2].is_number())
        return nb::none();
    return nb::make_tuple(value[0].get<double>(), value[1].get<double>(), value[2].get<double>());
}

static nb::object MaybeText(const std::string& text) { return text.empty() ? nb::none() : nb::cast(text); }

struct Extra {
    const char* key;
    const char* param;
    bool emission = false;  // read from an Octane light's emission map
};

static std::vector<Extra> ExtrasOf(Role role) {
    switch (role) {
    case Role::FStorm:
        return {{"targeted", "targeted"}, {"visible", "visible"}, {"gi_visible", "gi_visible"},
                {"double_sided", "double_sided"}, {"affect_diffuse", "affect_diffuse"},
                {"affect_glossy", "affect_glossy"}, {"directional", "directional"},
                {"directional_angle", "directional_angle"}, {"ies_file", "ies"}, {"ies_enabled", "ies_enabled"}};
    case Role::FStormSun:
        return {{"targeted", "targeted"}, {"visible", "visible"}, {"affect_diffuse", "affect_diffuse"},
                {"affect_glossy", "affect_glossy"}, {"sun_size", "size"}, {"hour", "hour"}, {"month", "month"},
                {"latitude", "latitude"}, {"north_direction", "north_direction"}};
    case Role::VRay:
        return {{"targeted", "targeted"}, {"texture_on", "texmap_on"}, {"double_sided", "doubleSided"},
                {"invisible", "invisible"}, {"dome_spherical", "dome_spherical"}, {"dome_finite", "dome_finite"}};
    case Role::PhotoPoint: case Role::PhotoRect: case Role::PhotoDisk: case Role::PhotoSphere: case Role::PhotoCylinder:
        return {{"multiplier_on", "useMultiplier"}, {"multiplier", "multiplier"}};
    case Role::Octane:
        return {{"normalize", "normalize", true}, {"surface_brightness", "surfaceBrightness", true}};
    case Role::Corona:
        return {{"targeted", "targeted"}, {"two_sided", "twosidedEmission"}, {"visible_directly", "visibleDirectly"},
                {"directionality", "directionality"}};
    case Role::CoronaSun: return {{"targeted", "targeted"}, {"size_multiplier", "sizeMultiplier"}};
    case Role::CoronaMoon: return {{"targeted", "targeted"}, {"size_multiplier", "sizeMultiplier"}, {"phase", "phase"}};
    case Role::CoronaSky:
        return {{"sky_model", "skyModel"}, {"turbidity", "turbidity"}, {"clouds", "cloudsEnable"}};
    case Role::CoronaBitmap: return {{"file", "filename"}, {"gamma", "gamma"}};
    default: return {};
    }
}

// Extras read as node.params shows them (degrees, percentages 0-100).
static void AddExtras(nb::dict& out, Animatable* owner, const std::vector<Extra>& extras, Animatable* emission) {
    for (const Extra& e : extras) {
        Animatable* from = e.emission ? emission : owner;
        const auto ref = from ? Lookup(from, e.param) : std::nullopt;
        out[e.key] = ref ? GetParam(*ref, Now()) : nb::none();
    }
}

static nb::dict SourceInfo(Texmap* map) {
    nb::dict out;
    out["class_name"] = Cls(map);
    out["handle"] = (unsigned long long)Animatable::GetHandleByAnim(map);
    std::vector<Extra> extras;
    if (map->ClassID() == kVRayBitmap) {
        extras = {{"file", "HDRIMapName"}, {"gamma", "gamma"}, {"horizontal_rotation", "horizontalRotation"}};
        out["mapping"] = MaybeText(NameOf(kVRayMapping, Get(map, "mapType")));
    } else if (map->ClassID() == kOctaneImage) {
        extras = {{"file", "filename"}, {"gamma", "gamma"}, {"color_space", "colorSpace"}};
    } else if (map->ClassID() == kCoronaBitmap) {
        extras = ExtrasOf(Role::CoronaBitmap);
        out["mapping"] = MaybeText(NameOf(kCoronaMapping, Get(map, "enviroMapping")));
    }
    AddExtras(out, map, extras, nullptr);
    return out;
}

static Texmap* SourceOf(const LightView& v) {
    if (v.role == Role::VRay) return MapIn(v.owner, "texmap");
    if (v.role == Role::OctaneEnv) return MapIn(v.owner, "texture_tex");
    if (v.role == Role::CoronaBitmap || v.role == Role::CoronaEnvMap) return static_cast<Texmap*>(v.owner);
    return nullptr;
}

// Each part is read on its own: one unreadable parameter is reported in
// 'unreadable' instead of hiding the rest.
static nb::dict LightInfo(const Light& l) {
    const LightView v = ResolveLight(l);
    nb::dict out;
    nb::list unreadable;
    auto part = [&](const char* name, const std::function<void()>& read) {
        try {
            read();
        } catch (const MaxError& e) {
            unreadable.append(nb::make_tuple(name, e.what()));
        }
    };
    out["provider"] = FamilyText(FamilyOf(v.role));
    out["class_name"] = Cls(v.owner);
    out["node"] = v.node ? nb::cast(Wrap(v.node)) : nb::none();
    if (v.role == Role::Unknown) return out;
    Form form;
    part("kind", [&] {
        form = FormOf(v);
        out["kind"] = MaybeText(form.kind);
        out["shape"] = MaybeText(form.shape);
        out["body"] = MaybeText(form.body);
    });
    part("enabled", [&] { out["enabled"] = EnabledOf(v); });
    part("cast_shadows", [&] {
        const auto shadows = ShadowsOf(v);
        out["cast_shadows"] = shadows ? nb::cast(*shadows) : nb::none();
    });
    part("intensity", [&] {
        const Output o = OutputOf(v);
        out["intensity"] = o.value;
        out["unit"] = MaybeText(o.unit);
    });
    part("color", [&] {
        const Tint t = TintOf(v);
        out["color_mode"] = MaybeText(t.mode);
        out["color"] = Rgb(t.rgb);
        out["kelvin"] = Maybe(t.kelvin);
    });
    for (const Dim& d : DimsOf(v.role, form.shape)) {
        part(d.dim, [&] {
            const auto value = DimValue(v.owner, d);
            out[d.dim] = value ? nb::cast(*value) : nb::none();
            if (!value) out["diameters"] = ToPython(Must(v.owner, d.param));
        });
    }
    part("extras", [&] { AddExtras(out, v.owner, ExtrasOf(v.role), v.role == Role::Octane ? Emission(v) : nullptr); });
    part("source", [&] {
        if (Texmap* source = SourceOf(v)) out["source"] = SourceInfo(source);
    });
    out["unreadable"] = unreadable;
    return out;
}

static std::string LightRepr(const Light& l) {
    LightView v;
    try {
        v = ResolveLight(l);
    } catch (const NotFound&) {
        return "<Light #" + std::to_string(l.node ? l.node : l.map) + " deleted>";
    }
    std::string out = "<Light ";
    if (v.node) out += "'" + NodeName(v.node) + "' ";
    out += Cls(v.owner);
    try {
        if (v.role != Role::Unknown) {
            const Form form = FormOf(v);
            for (const std::string& part : {form.kind, form.shape})
                if (!part.empty()) out += " " + part;
            const Output o = OutputOf(v);
            out += " " + Text(o.value) + " " + (o.unit.empty() ? "?" : o.unit);
        }
    } catch (const MaxError&) {
        out += " ?";
    }
    return out + ">";
}

// ── Light edits ──────────────────────────────────────────────────

static void RequireUnit(const std::string& unit, const std::vector<std::string>& units, const std::string& what) {
    if (!Has(units, unit)) throw BadParam(what + " takes unit " + Quoted(units) + ", not '" + unit + "'");
}

// An Octane emission value driven by a texture (its <name>VT slot) is not a
// plain parameter any more.
static void RequireUntextured(Animatable* emission, const char* param) {
    const std::string slot = std::string(param) + "VT";
    if (Lookup(emission, slot.c_str()) && MapIn(emission, slot.c_str()))
        throw BadParam(std::string("Octane ") + param + " is driven by a texture (" + slot + "); edit that map instead");
}

static void SetEnabled(const Light& l, bool on) {
    const LightView v = ResolveLight(l);
    Supported(v);
    const char* param = EnableParam(v.role);
    if (!param)
        throw BadParam("an environment map has no on/off switch; clear Rendering > Environment, or bind another "
                       "map with mx.environment_light(..., replace=True)");
    Apply({{v.owner, param, on}});
}

static void SetShadows(const Light& l, bool on) {
    const LightView v = ResolveLight(l);
    const Provider& p = Supported(v);
    switch (v.role) {
    case Role::Corona: case Role::CoronaSun: case Role::CoronaMoon: case Role::FStormSun:
        if (!on) throw BadParam(std::string(p.name) + " " + Cls(v.owner) + " always casts shadows; it has no shadow switch");
        return;
    case Role::FStorm: return Apply({{v.owner, "cast_shadows", on}});
    case Role::Octane: {
        Animatable* emission = Emission(v);
        RequireUntextured(emission, "castShadows");
        return Apply({{emission, "castShadows", on}});
    }
    default:
        if (IsEnvironmentMap(v.role)) throw BadParam("an environment map has no shadow switch");
        return Apply({{v.owner, "castShadows", on}});
    }
}

static std::vector<Assign> OutputWrites(const LightView& v, double value, const std::string& unit) {
    Animatable* o = v.owner;
    const std::string what = Cls(o) + " intensity";
    switch (v.role) {
    case Role::PhotoPoint: case Role::PhotoRect: case Role::PhotoDisk: case Role::PhotoSphere: case Role::PhotoCylinder:
        RequireUnit(unit, {"cd"}, what);
        return {{o, "useMultiplier", false}, {o, "intensityType", 1}, {o, "intensity", value}};
    case Role::VRay:
        RequireUnit(unit, FormOf(v).kind == "environment" ? std::vector<std::string>{"renderer"} : ProviderOf(Family::VRay).units, what);
        return {{o, "normalizeColor", *ValueOf(kVRayUnits, unit)}, {o, "multiplier", value}};
    case Role::Octane: {
        RequireUnit(unit, {"renderer"}, what);
        Animatable* emission = Emission(v);
        RequireUntextured(emission, "power");
        return {{emission, "power", value}};
    }
    case Role::OctaneEnv: RequireUnit(unit, {"renderer"}, what); return {{o, "power", value}};
    case Role::Corona:
        RequireUnit(unit, ProviderOf(Family::Corona).units, what);
        return {{o, "intensityUnits", *ValueOf(kCoronaUnits, unit)}, {o, "intensity", value}};
    case Role::CoronaSun: case Role::CoronaMoon: RequireUnit(unit, {"renderer"}, what); return {{o, "intensity", value}};
    case Role::CoronaSky: RequireUnit(unit, {"renderer"}, what); return {{o, "intensityMultiplier", value}};
    case Role::CoronaBitmap: case Role::CoronaEnvMap:
        throw BadParam("the Corona environment slot has no multiplier; brighten the map itself (a CoronaSky's "
                       "intensityMultiplier, or a CoronaColorCorrect)");
    case Role::FStorm: case Role::FStormSun:
        RequireUnit(unit, {"renderer"}, what);
        if (value <= 0) throw BadParam("FStorm power must be > 0; set light.enabled = False to turn it off");
        if (v.role == Role::FStormSun && (value < 0.001 || value > 100000))
            throw BadParam("FStorm sun power must be 0.001-100000");
        return {{o, "power", value}};
    default: Supported(v); return {};
    }
}

static std::pair<double, std::string> ParseOutput(nb::handle value) {
    const char* usage = "intensity takes (value, unit): light.intensity = (1500, 'lm'); mx.light_capabilities() lists the units";
    if (!(nb::isinstance<nb::tuple>(value) || nb::isinstance<nb::list>(value)) || nb::len(value) != 2) throw BadParam(usage);
    double amount = 0;
    std::string unit;
    try {
        amount = nb::cast<double>(value[0]);
        unit = nb::cast<std::string>(value[1]);
    } catch (const nb::cast_error&) {
        throw BadParam(usage);
    }
    if (!std::isfinite(amount) || amount < 0) throw BadParam("intensity must be a finite number >= 0");
    return {amount, unit};
}

static void SetIntensity(const Light& l, nb::handle value) {
    const auto [amount, unit] = ParseOutput(value);
    Apply(OutputWrites(ResolveLight(l), amount, unit));
}

static void RequireKelvin(double kelvin) {
    if (!std::isfinite(kelvin) || kelvin < 500 || kelvin > 30000) throw BadParam("kelvin must be 500-30000");
}

static json RgbJson(const Point3& c) { return json::array({c.x, c.y, c.z}); }

static Point3 ParseRgb(nb::handle value) {
    const Point3 c = ToPoint3(value, "color");
    if (c.x < 0 || c.y < 0 || c.z < 0) throw BadParam("color components must be >= 0 (scene-linear, rendering space)");
    return c;
}

static void RefuseEnvironmentColor(const LightView& v) {
    Supported(v);
    if (IsEnvironmentMap(v.role) || FormOf(v).kind == "environment")
        throw BadParam("an environment light takes its color from its map");
}

static void SetKelvin(const Light& l, double kelvin) {
    RequireKelvin(kelvin);
    const LightView v = ResolveLight(l);
    RefuseEnvironmentColor(v);
    Animatable* o = v.owner;
    switch (v.role) {
    case Role::FStorm:
        if (kelvin > 24000) throw BadParam("FStorm light temperature is limited to 24000 K");
        return Apply({{o, "color_type", *ValueOf(kFStormColor, "kelvin")}, {o, "temperature", kelvin}});
    case Role::FStormSun: throw BadParam("an FStorm sun has no Kelvin mode; set light.color (legacy RGB) or light.color_mode = 'physical'");
    case Role::VRay: return Apply({{o, "color_mode", *ValueOf(kVRayColor, "kelvin")}, {o, "color_temperature", kelvin}});
    case Role::CoronaSun: return Apply({{o, "colorMode", *ValueOf(kCoronaSunColor, "kelvin")}, {o, "blackbodyTemperature", kelvin}});
    case Role::CoronaMoon: throw BadParam("a CoronaMoon has an RGB color filter only; set light.color");
    case Role::Corona: return Apply({{o, "colorMode", *ValueOf(kCoronaColor, "kelvin")}, {o, "blackbodyTemp", kelvin}});
    case Role::Octane: {
        Animatable* emission = Emission(v);
        RequireUntextured(emission, "temperature");
        return Apply({{emission, "temperature", kelvin}});
    }
    default: return Apply({{o, "useKelvin", true}, {o, "kelvin", kelvin}});  // photometric
    }
}

static void SetColor(const Light& l, nb::handle value) {
    const json rgb = RgbJson(ParseRgb(value));
    const LightView v = ResolveLight(l);
    RefuseEnvironmentColor(v);
    Animatable* o = v.owner;
    switch (v.role) {
    case Role::FStorm: return Apply({{o, "color_type", *ValueOf(kFStormColor, "rgb")}, {o, "color", rgb}});
    case Role::FStormSun: return Apply({{o, "model", *ValueOf(kFStormSunModel, "rgb")}, {o, "sun_color", rgb}});
    case Role::VRay: return Apply({{o, "color_mode", *ValueOf(kVRayColor, "rgb")}, {o, "color", rgb}});
    case Role::CoronaSun: return Apply({{o, "colorMode", *ValueOf(kCoronaSunColor, "rgb")}, {o, "colorDirect", rgb}});
    case Role::CoronaMoon: return Apply({{o, "colorFilter", rgb}});
    case Role::Corona: return Apply({{o, "colorMode", *ValueOf(kCoronaColor, "rgb")}, {o, "color", rgb}});
    case Role::Octane: throw BadParam("Octane analytic lights take Kelvin color only; set light.kelvin");
    default: return Apply({{o, "useKelvin", false}, {o, "rgb", rgb}});  // photometric
    }
}

static void SetColorMode(const Light& l, const std::string& mode) {
    const LightView v = ResolveLight(l);
    Supported(v);
    if (mode == "realistic" && v.role == Role::CoronaSun)
        return Apply({{v.owner, "colorMode", *ValueOf(kCoronaSunColor, "realistic")}});
    if (mode == "physical" && v.role == Role::FStormSun)
        return Apply({{v.owner, "model", *ValueOf(kFStormSunModel, "physical")}});
    if (TintOf(v).mode == mode) return;
    throw BadParam("set light.kelvin or light.color to switch to '" + mode + "'; color_mode itself only selects "
                   "'realistic' (CoronaSun) or 'physical' (FStorm sun)");
}

static nb::object GetDim(const Light& l, const std::string& dim) {
    const LightView v = ResolveLight(l);
    Supported(v);
    for (const Dim& d : DimsOf(v.role, FormOf(v).shape)) {
        if (dim != d.dim) continue;
        const auto value = DimValue(v.owner, d);
        return value ? nb::cast(*value) : nb::none();
    }
    return nb::none();
}

static void SetDim(const Light& l, const std::string& dim, double value) {
    if (!std::isfinite(value) || value <= 0) throw BadParam(dim + " must be > 0 (scene units)");
    const LightView v = ResolveLight(l);
    Supported(v);
    const Form form = FormOf(v);
    const std::vector<Dim> dims = DimsOf(v.role, form.shape);
    std::vector<std::string> names;
    for (const Dim& d : dims) names.push_back(d.dim);
    if (!Has(names, dim)) {
        const std::string light = form.shape.empty() ? (form.kind.empty() ? "this" : "a " + form.kind) : "a " + form.shape;
        throw BadParam(light + " light has " + (names.empty() ? "no size" : Quoted(names)) + ", not " + dim);
    }
    std::vector<Assign> changes;
    for (auto& [param, raw] : SizeWrites(dims, {{dim, value}}, [&](const char* p) { return Must(v.owner, p); }))
        changes.push_back({v.owner, param, raw});
    Apply(changes);
}

// ── Light creation ───────────────────────────────────────────────

static const std::map<std::string, std::vector<std::string>> kShapeDims = {
    {"rectangle", {"width", "height"}}, {"disk", {"radius"}}, {"sphere", {"radius"}}, {"cylinder", {"radius", "length"}}};

struct Spec {
    std::string kind, name, shape, body, unit;
    double intensity = 0;
    std::map<std::string, double> sizes;
    std::optional<Point3> pos, aim, direction, up;
    std::optional<double> kelvin;
    std::optional<Point3> rgb;
    bool enabled = true, shadows = true;
    std::vector<std::pair<std::string, double>> solar;
};

static void CheckSizes(const Spec& s) {
    std::vector<std::string> given;
    for (const auto& [dim, value] : s.sizes) {
        if (!std::isfinite(value) || value <= 0) throw BadParam(dim + " must be > 0 (scene units)");
        given.push_back(dim);
    }
    std::vector<std::string> needed = kShapeDims.at(s.shape);
    std::sort(needed.begin(), needed.end());
    if (given != needed) throw BadParam("a " + s.shape + " light takes " + Quoted(kShapeDims.at(s.shape)) + " (scene units)");
}

static void CheckSpec(const Spec& s, const Provider& p) {
    if (s.kind == "environment")
        throw BadParam("environment lighting has its own call: mx.environment_light(intensity=1, unit='renderer', hdri='C:/sky.exr')");
    if (!Has(p.kinds, s.kind)) throw BadParam(std::string(p.name) + " has no verified '" + s.kind + "' light; kinds: " + Quoted(p.kinds));
    RequireUnit(s.unit, p.units, std::string(p.name) + " intensity");
    if (!std::isfinite(s.intensity) || s.intensity < 0) throw BadParam("intensity must be a finite number >= 0");
    if (s.kelvin && s.rgb) throw BadParam("pass kelvin or color, not both");
    if (s.kelvin) RequireKelvin(*s.kelvin);
    if (s.aim && s.direction) throw BadParam("pass aim_at or direction, not both");
    const bool oriented = s.aim || s.direction;
    if (s.up && !oriented) throw BadParam("up needs aim_at or direction");
    if (!s.body.empty() && s.kind != "directional") throw BadParam("only directional lights take a body");
    if (!s.solar.empty() && !(p.family == Family::FStorm && s.kind == "directional"))
        throw BadParam("solar places FStorm suns only");
    if (s.kind == "directional") {
        if (!s.shape.empty() || !s.sizes.empty()) throw BadParam("a directional light has no shape or size");
        if (!Has(p.bodies, s.body.empty() ? "sun" : s.body))
            throw BadParam(std::string(p.name) + " directional lights take body " + Quoted(p.bodies));
        if (p.family == Family::FStorm) {
            if (oriented) throw BadParam("an FStorm sun is placed by solar=..., not by aim_at or direction");
            if (s.solar.empty()) throw BadParam("an FStorm sun needs solar={'hour': 14, 'month': 6, 'latitude': 41}");
        } else if (!oriented) {
            throw BadParam("a directional light needs aim_at or direction");
        }
    } else if (s.kind == "point") {
        if (!s.shape.empty() || !s.sizes.empty() || oriented) throw BadParam("a point light has no shape, size or orientation");
    } else {
        if (!Has(p.shapes, s.shape))
            throw BadParam(std::string(p.name) + " area lights take shape " + Quoted(p.shapes) +
                           (s.shape.empty() ? "" : ", not '" + s.shape + "'"));
        CheckSizes(s);
        if (s.shape != "sphere" && !oriented)
            throw BadParam("a " + s.shape + " light emits down its local -Z: pass aim_at or direction");
    }
}

static Point3 Unit(const Point3& v, const char* problem) {
    const float length = v.Length();
    if (length < 1e-9f) throw BadParam(problem);
    return v / length;
}

// Rows x, y, z, position. A light emits down its local -Z; without an up hint
// the roll follows world Z (world Y when the light points straight up or down).
static json WorldMatrix(const Spec& s) {
    const Point3 p = s.pos.value_or(Point3(0, 0, 0));
    auto row = [](const Point3& v) { return json::array({v.x, v.y, v.z}); };
    if (!s.aim && !s.direction)
        return json::array({row(Point3(1.0f, 0.0f, 0.0f)), row(Point3(0.0f, 1.0f, 0.0f)), row(Point3(0.0f, 0.0f, 1.0f)), row(p)});
    const Point3 z = -Unit(s.direction ? *s.direction : *s.aim - p, "the light direction is zero: aim_at equals pos");
    const Point3 up = s.up ? *s.up : (std::abs(z.z) > 0.999f ? Point3(0, 1, 0) : Point3(0, 0, 1));
    const Point3 x = Unit(CrossProd(up, z), "up is parallel to the light direction");
    const Point3 y = Unit(CrossProd(z, x), "up is parallel to the light direction");
    return json::array({row(x), row(y), row(z), row(p)});
}

static json ClassRefJson(SClass_ID super, const Class_ID& id) {
    return {{"superclass_id", (unsigned long long)super}, {"class_id", json::array({id.PartA(), id.PartB()})}};
}

// A plugin_patch plan: created resources and their typed parameter values.
struct Plan {
    json creates = json::array();
    json edits = json::array();
    json environment;
    std::map<std::string, ClassDesc2*> classes;

    void Create(const std::string& id, SClass_ID super, const Class_ID& cls, const std::string& name = "",
                const json& matrix = json()) {
        ClassEntry* entry = ClassDirectory::GetInstance().FindClassEntry(super, cls);
        auto* cd = entry ? dynamic_cast<ClassDesc2*>(entry->FullCD()) : nullptr;
        if (!cd)
            throw NotFound("plugin class (" + std::to_string(cls.PartA()) + ", " + std::to_string(cls.PartB()) +
                           ") is not installed in this 3ds Max; install or update the renderer");
        json item = {{"id", id}, {"class_ref", ClassRefJson(super, cls)}};
        if (super == LIGHT_CLASS_ID) {
            MSTR unique = name.empty() ? MSTR(cd->ClassName()) : MSTR(Utf8ToWide(name).c_str());
            if (name.empty()) GetCOREInterface()->MakeNameUnique(unique);
            item["name"] = WideToUtf8(unique.data());
        }
        if (!matrix.is_null()) item["matrix"] = matrix;
        creates.push_back(item);
        classes[id] = cd;
    }

    void Set(const std::string& id, const char* param, json value) {
        ClassDesc2* cd = classes.at(id);
        const std::string what = WideToUtf8(cd->ClassName()) + "." + param;
        const std::wstring wanted = Utf8ToWide(param);
        std::optional<std::pair<BlockID, const ParamDef*>> found;
        for (int b = 0; b < cd->NumParamBlockDescs(); ++b) {
            ParamBlockDesc2* desc = cd->GetParamBlockDesc(b);
            for (int i = 0; desc && i < desc->count; ++i) {
                const ParamDef& def = desc->GetParamDef(desc->IndextoID(i));
                if (!def.int_name || wcscmp(def.int_name, wanted.c_str()) != 0) continue;
                if (found && (found->first != desc->ID || found->second->ID != def.ID)) Unverified(what, "is declared twice");
                if (!found) found = std::make_pair(desc->ID, &def);
            }
        }
        if (!found) Unverified(WideToUtf8(cd->ClassName()), std::string("has no '") + param + "' parameter");
        edits.push_back({{"owner_ref", {{"created", id}}},
                         {"property_ref", {{"route", "pb2"}, {"block_id", found->first}, {"param_id", found->second->ID}}},
                         {"value", Fit(*found->second, std::move(value), what)}});
    }

    void SetSizes(const std::string& id, Role role, const Spec& s) {
        for (auto& [param, raw] : SizeWrites(DimsOf(role, s.shape), s.sizes, nullptr)) Set(id, param, raw);
    }

    void Bind(const json& owner, bool replace) { environment = {{"owner_ref", owner}, {"replace_existing", replace}}; }

    json Payload(const json& context) const {
        json payload = {{"version", 1}, {"expected_context", context.at("context_token")}, {"creates", creates}, {"edits", edits}};
        if (!environment.is_null()) payload["environment"] = environment;
        return payload;
    }
};

static json Created(const char* id) { return {{"created", id}}; }

// The resolved color: Kelvin, else RGB, else 6500 K where a default applies.
static std::optional<double> KelvinOrDefault(const Spec& s) {
    if (s.rgb) return std::nullopt;
    return s.kelvin.value_or(6500.0);
}

static void SetColorPair(Plan& plan, const char* id, const Spec& s, const char* kelvinParam, const char* rgbParam) {
    if (const auto kelvin = KelvinOrDefault(s)) plan.Set(id, kelvinParam, *kelvin);
    else plan.Set(id, rgbParam, RgbJson(*s.rgb));
}

static void CompilePhotometric(Plan& plan, const Spec& s) {
    static const std::map<std::string, std::pair<Role, Class_ID>> classes = {
        {"point", {Role::PhotoPoint, LS_POINT_LIGHT_ID}}, {"rectangle", {Role::PhotoRect, LS_AREA_LIGHT_ID}},
        {"disk", {Role::PhotoDisk, LS_DISC_LIGHT_ID}}, {"sphere", {Role::PhotoSphere, LS_SPHERE_LIGHT_ID}},
        {"cylinder", {Role::PhotoCylinder, LS_CYLINDER_LIGHT_ID}}};
    const auto& [role, cls] = classes.at(s.kind == "point" ? "point" : s.shape);
    plan.Create("light", LIGHT_CLASS_ID, cls, s.name, WorldMatrix(s));
    plan.Set("light", "on", s.enabled);
    plan.Set("light", "castShadows", s.shadows);
    plan.Set("light", "useMultiplier", false);
    plan.Set("light", "intensityType", 1);  // candelas
    plan.Set("light", "intensity", s.intensity);
    plan.Set("light", "useKelvin", KelvinOrDefault(s).has_value());
    SetColorPair(plan, "light", s, "kelvin", "rgb");
    plan.SetSizes("light", role, s);
}

static void CompileVRay(Plan& plan, const Spec& s) {
    plan.Create("light", LIGHT_CLASS_ID, kVRayLight, s.name, WorldMatrix(s));
    plan.Set("light", "type", *ValueOf(kVRayTypes, s.shape));
    plan.Set("light", "on", s.enabled);
    plan.Set("light", "targeted", false);
    plan.Set("light", "castShadows", s.shadows);
    plan.Set("light", "normalizeColor", *ValueOf(kVRayUnits, s.unit));
    plan.Set("light", "multiplier", s.intensity);
    plan.Set("light", "color_mode", *ValueOf(kVRayColor, KelvinOrDefault(s) ? "kelvin" : "rgb"));
    plan.Set("light", "texmap_on", false);
    plan.Set("light", "doubleSided", false);
    plan.Set("light", "invisible", false);
    SetColorPair(plan, "light", s, "color_temperature", "color");
    plan.SetSizes("light", Role::VRay, s);
}

static void CompileOctane(Plan& plan, const Spec& s) {
    if (s.rgb) throw BadParam("Octane analytic lights take kelvin, not color");
    plan.Create("light", LIGHT_CLASS_ID, kOctaneLight, s.name, WorldMatrix(s));
    plan.Create("emission", TEXMAP_CLASS_ID, kOctaneEmission);
    plan.Set("light", "analyticLightType", *ValueOf(kOctaneShapes, s.shape));
    plan.Set("light", "enabled", s.enabled);
    plan.Set("light", "target_mode", false);
    plan.Set("light", "emission", Created("emission"));
    plan.Set("light", "normalize", true);
    plan.Set("emission", "power", s.intensity);
    plan.Set("emission", "temperature", *KelvinOrDefault(s));
    plan.Set("emission", "normalize", true);
    plan.Set("emission", "surfaceBrightness", false);
    plan.Set("emission", "keepInstancePower", true);
    plan.Set("emission", "castShadows", s.shadows);
    plan.Set("emission", "illumination", true);
    plan.Set("emission", "doubleSided", false);
    plan.Set("emission", "efficiency or texture_input_type", 0);
    plan.Set("emission", "efficiency or texture_value", 0.025);
    plan.SetSizes("light", Role::Octane, s);
}

static void CompileCorona(Plan& plan, const Spec& s) {
    if (!s.shadows) throw BadParam("Corona lights always cast shadows; there is no per-light shadow switch");
    if (s.kind == "directional") {
        RequireUnit(s.unit, {"renderer"}, "Corona sun and moon intensity");
        const bool moon = s.body == "moon";
        plan.Create("light", LIGHT_CLASS_ID, moon ? kCoronaMoon : kCoronaSun, s.name, WorldMatrix(s));
        plan.Set("light", "on", s.enabled);
        plan.Set("light", "targeted", false);
        plan.Set("light", "intensity", s.intensity);
        if (moon) {
            if (s.kelvin) throw BadParam("a CoronaMoon has an RGB color filter only; pass color");
            if (s.rgb) plan.Set("light", "colorFilter", RgbJson(*s.rgb));
            return;
        }
        // Without a color the sun keeps Corona's realistic color.
        plan.Set("light", "colorMode", *ValueOf(kCoronaSunColor, s.kelvin ? "kelvin" : s.rgb ? "rgb" : "realistic"));
        if (s.kelvin) plan.Set("light", "blackbodyTemperature", *s.kelvin);
        if (s.rgb) plan.Set("light", "colorDirect", RgbJson(*s.rgb));
        return;
    }
    plan.Create("light", LIGHT_CLASS_ID, kCoronaLight, s.name, WorldMatrix(s));
    plan.Set("light", "shape", *ValueOf(kCoronaShapes, s.shape));
    plan.Set("light", "on", s.enabled);
    plan.Set("light", "targeted", false);
    plan.Set("light", "intensityUnits", *ValueOf(kCoronaUnits, s.unit));
    plan.Set("light", "intensity", s.intensity);
    plan.Set("light", "colorMode", *ValueOf(kCoronaColor, KelvinOrDefault(s) ? "kelvin" : "rgb"));
    plan.Set("light", "twosidedEmission", false);
    SetColorPair(plan, "light", s, "blackbodyTemp", "color");
    plan.SetSizes("light", Role::Corona, s);
}

static void CompileFStorm(Plan& plan, const Spec& s) {
    const bool sun = s.kind == "directional";
    if (s.intensity <= 0) throw BadParam("FStorm power must be > 0; pass enabled=False for a light that is off");
    if (sun && (s.intensity < 0.001 || s.intensity > 100000)) throw BadParam("FStorm sun power must be 0.001-100000");
    if (sun && !s.shadows) throw BadParam("an FStorm sun has no shadow switch");
    if (sun && s.kelvin) throw BadParam("an FStorm sun has no Kelvin mode; pass color (legacy RGB) or no color (physical)");
    if (!sun && s.kelvin && *s.kelvin > 24000) throw BadParam("FStorm light temperature is limited to 24000 K");
    plan.Create("light", LIGHT_CLASS_ID, sun ? kFStormSun : kFStormLight, s.name, WorldMatrix(s));
    plan.Set("light", "targeted", false);
    plan.Set("light", "enabled", s.enabled);
    plan.Set("light", "power", s.intensity);
    if (sun) {
        plan.Set("light", "model", *ValueOf(kFStormSunModel, s.rgb ? "rgb" : "physical"));
        for (const auto& [key, value] : s.solar) plan.Set("light", key.c_str(), value);
        if (s.rgb) plan.Set("light", "sun_color", RgbJson(*s.rgb));
        return;
    }
    plan.Set("light", "shape", *ValueOf(kFStormShapes, s.shape));
    plan.Set("light", "cast_shadows", s.shadows);
    plan.Set("light", "directional", false);
    plan.Set("light", "ies_enabled", false);
    plan.SetSizes("light", Role::FStorm, s);
    plan.Set("light", "color_type", *ValueOf(kFStormColor, KelvinOrDefault(s) ? "kelvin" : "rgb"));
    SetColorPair(plan, "light", s, "temperature", "color");
}

struct Chosen {
    json identity;
    Family family = Family::None;
};

static Family IdentityFamily(const json& identity) {
    const json& id = identity.at("class_id");
    return RendererFamily(Class_ID(id.at(0).get<ULONG>(), id.at(1).get<ULONG>()));
}

// The production renderer, or the installed one named by its name, label or
// provider name. nullopt: none is assigned.
static std::optional<Chosen> ChooseRenderer(const json& context, const std::optional<std::string>& requested) {
    if (!requested) {
        const json& current = context.at("renderer");
        if (current.is_null()) return std::nullopt;
        return Chosen{current, IdentityFamily(current)};
    }
    const std::string wanted = Lower(*requested);
    std::vector<json> matches;
    std::vector<std::string> names;
    for (const json& r : context.at("renderers")) {
        names.push_back(r.value("name", ""));
        if (Lower(r.value("name", "")) == wanted || Lower(r.value("label", "")) == wanted) matches.push_back(r);
    }
    if (matches.empty())
        for (const json& r : context.at("renderers"))
            if (const char* family = FamilyName(IdentityFamily(r)); family && wanted == family) matches.push_back(r);
    if (matches.size() != 1)
        throw BadParam(std::string(matches.empty() ? "no installed renderer is" : "several renderers are") + " named '" +
                       *requested + "'." + Suggest(*requested, names) + " Renderers: " + Join(names));
    return Chosen{matches.front(), IdentityFamily(matches.front())};
}

static const Provider& RequireProvider(const json& context, const std::optional<std::string>& renderer) {
    const auto chosen = ChooseRenderer(context, renderer);
    if (!chosen) throw BadParam("no production renderer is assigned; pick one in Render Setup or pass renderer=");
    if (chosen->family == Family::None)
        throw BadParam("no verified lighting provider for " + chosen->identity.value("label", std::string("this renderer")) +
                       "; create its lights with mx.create(cls, ...) and set node.params");
    return ProviderOf(chosen->family);
}

// Runs the plan in one bridge transaction, without keys whatever the animate mode.
static json Patch(const Plan& plan, const json& context) {
    NoAnimate noAnimate;
    return NativeJson("native:plugin_patch", plan.Payload(context));
}

// The plan's resource "light": its node, or the map when it has none.
static Light Commit(const Plan& plan, const json& context) {
    const json applied = Patch(plan, context);
    for (const json& resource : applied.value("resources", json::array())) {
        if (resource.value("id", "") != "light") continue;
        if (resource.contains("node_ref")) return Light{(AnimHandle)NodeRefs::ParseHandle(resource.at("node_ref").at("handle")), 0};
        return Light{0, (AnimHandle)NodeRefs::ParseHandle(resource.at("owner_ref").at("handle"))};
    }
    throw MaxError("plugin_patch reported no created light; inspect the scene before retrying");
}

static Light CreateLight(Spec s, const std::optional<std::string>& renderer) {
    RequireMain();
    const json context = NativeJson("native:lighting_context", json::object());
    const Provider& p = RequireProvider(context, renderer);
    CheckSpec(s, p);
    Plan plan;
    switch (p.family) {
    case Family::Photometric: CompilePhotometric(plan, s); break;
    case Family::VRay: CompileVRay(plan, s); break;
    case Family::Octane: CompileOctane(plan, s); break;
    case Family::Corona: CompileCorona(plan, s); break;
    default: CompileFStorm(plan, s); break;
    }
    return Commit(plan, context);
}

static std::vector<std::pair<std::string, double>> ParseSolar(nb::handle value) {
    if (value.is_none()) return {};
    const char* usage = "solar takes a dict of hour (0-24), month (1-12), latitude (-90-90) and north_direction: "
                        "solar={'hour': 14, 'month': 6, 'latitude': 41}";
    if (!nb::isinstance<nb::dict>(value)) throw BadParam(usage);
    struct Range { const char* key; double lo, hi; };
    static const Range ranges[] = {{"hour", 0, 24}, {"month", 1, 12}, {"latitude", -90, 90}, {"north_direction", -1e9, 1e9}};
    std::vector<std::pair<std::string, double>> out;
    for (auto [key, item] : nb::borrow<nb::dict>(value)) {
        const std::string name = nb::cast<std::string>(nb::str(key));
        const Range* range = std::find_if(std::begin(ranges), std::end(ranges), [&](const Range& r) { return name == r.key; });
        double number = 0;
        try {
            number = nb::cast<double>(item);
        } catch (const nb::cast_error&) {
            throw BadParam(usage);
        }
        if (range == std::end(ranges) || !std::isfinite(number) || number < range->lo || number > range->hi) throw BadParam(usage);
        out.push_back({name, number});
    }
    if (out.empty()) throw BadParam(usage);
    return out;
}

static std::optional<Point3> OptionalPoint(nb::handle value, const char* what) {
    if (value.is_none()) return std::nullopt;
    return ToPoint3(value, what);
}

static Light LightCall(const std::string& kind, double intensity, const std::string& unit, const std::string& name,
                       const std::optional<std::string>& shape, std::optional<double> width, std::optional<double> height,
                       std::optional<double> radius, std::optional<double> length, nb::handle pos, nb::handle aim_at,
                       nb::handle direction, nb::handle up, std::optional<double> kelvin, nb::handle color, bool enabled,
                       bool cast_shadows, const std::optional<std::string>& body, nb::handle solar,
                       const std::optional<std::string>& renderer) {
    Spec s;
    s.kind = kind;
    s.intensity = intensity;
    s.unit = unit;
    s.name = name;
    s.shape = shape.value_or("");
    s.body = body.value_or("");
    const std::pair<const char*, std::optional<double>> sizes[] = {
        {"width", width}, {"height", height}, {"radius", radius}, {"length", length}};
    for (const auto& [dim, value] : sizes)
        if (value) s.sizes[dim] = *value;
    s.pos = OptionalPoint(pos, "pos");
    s.aim = OptionalPoint(aim_at, "aim_at");
    s.direction = OptionalPoint(direction, "direction");
    s.up = OptionalPoint(up, "up");
    s.kelvin = kelvin;
    if (!color.is_none()) s.rgb = ParseRgb(color);
    s.enabled = enabled;
    s.shadows = cast_shadows;
    s.solar = ParseSolar(solar);
    return CreateLight(std::move(s), renderer);
}

// ── Environment lights ───────────────────────────────────────────

static Texmap* ExistingMap(nb::handle map) {
    nb::object handle = nb::borrow(map);
    if (!nb::isinstance<nb::int_>(map) && nb::hasattr(map, "handle")) handle = map.attr("handle");
    AnimHandle h = 0;
    try {
        h = (AnimHandle)nb::cast<unsigned long long>(handle);
    } catch (const nb::cast_error&) {
        throw BadParam("map takes a texture map's anim handle (an int)");
    }
    Animatable* anim = Animatable::GetAnimByHandle(h);
    if (!anim || anim->SuperClassID() != TEXMAP_CLASS_ID) throw BadParam("#" + std::to_string(h) + " is not a texture map");
    return static_cast<Texmap*>(anim);
}

static json OwnerRef(const json& context, Animatable* anim) {
    return {{"session", context.at("session")}, {"handle", std::to_string((unsigned long long)Animatable::GetHandleByAnim(anim))},
            {"class_ref", ClassRefJson(anim->SuperClassID(), anim->ClassID())}};
}

static std::string HdriPath(const std::string& path) {
    std::error_code ec;
    const fs::path full = fs::absolute(fs::path(Utf8ToWide(path)), ec);
    if (ec || !fs::is_regular_file(full, ec)) throw BadParam("hdri file not found: " + path);
    const std::string ext = Lower(WideToUtf8(full.extension().c_str()));
    if (ext != ".exr" && ext != ".hdr")
        throw BadParam("hdri takes a scene-linear .exr or .hdr; bind another encoding as an existing map");
    return WideToUtf8(full.c_str());
}

// Corona ignores the 3ds Max environment slot unless Render Setup's scene
// environment is set to 3ds Max settings (bg.source = 0).
static void RequireCoronaMaxEnvironment() {
    Renderer* renderer = GetCOREInterface()->GetRenderer(RS_Production);
    if (!renderer || renderer->ClassID() != kCoronaRenderer)
        throw BadParam("binding a Corona environment needs Corona as the production renderer");
    for (int b = 0; b < renderer->NumParamBlocks(); ++b) {
        IParamBlock2* block = renderer->GetParamBlock(b);
        ParamBlockDesc2* desc = block ? block->GetDesc() : nullptr;
        if (!desc || block->ID() != 0 || desc->IDtoIndex(1057) < 0) continue;
        if (wcscmp(desc->GetParamDef(1057).int_name, L"bg.source") != 0) break;
        if (ReadOne(ParamRef{block, &desc->GetParamDef(1057)}, Now(), 0) == 0) return;
        break;
    }
    throw BadParam("Corona takes its environment from its own override: set Render Setup > Scene > Environment to "
                   "'3ds Max settings' first (single-map and LightMix overrides are left alone)");
}

struct EnvSource {
    Texmap* existing = nullptr;
    std::string hdri;
    double rotation = 0;
    std::optional<std::string> colorSpace;
};

static json VRayImage(Plan& plan, const EnvSource& src) {
    if (src.colorSpace) throw BadParam("V-Ray input primaries have no verified binding; set the map's color space on an existing map");
    plan.Create("image", TEXMAP_CLASS_ID, kVRayBitmap);
    plan.Set("image", "HDRIMapName", src.hdri);
    plan.Set("image", "mapType", *ValueOf(kVRayMapping, "spherical"));
    plan.Set("image", "color_space", 0);  // linear samples: no input transfer curve
    plan.Set("image", "gamma", 1.0);
    plan.Set("image", "horizontalRotation", src.rotation * PI / 180.0);  // stored in radians
    return Created("image");
}

static void CompileVRayDome(Plan& plan, double intensity, const json& image) {
    plan.Create("light", LIGHT_CLASS_ID, kVRayLight);
    plan.Set("light", "type", *ValueOf(kVRayTypes, "dome"));
    plan.Set("light", "on", true);
    plan.Set("light", "targeted", false);
    plan.Set("light", "normalizeColor", *ValueOf(kVRayUnits, "renderer"));
    plan.Set("light", "multiplier", intensity);
    plan.Set("light", "color_mode", *ValueOf(kVRayColor, "rgb"));
    plan.Set("light", "color", json::array({1.0, 1.0, 1.0}));
    plan.Set("light", "texmap", image);
    plan.Set("light", "texmap_on", true);
    plan.Set("light", "dome_spherical", true);
    plan.Set("light", "dome_finite", false);
    plan.Set("light", "castShadows", true);
}

static void CompileOctaneEnv(Plan& plan, double intensity, const EnvSource& src, const json& context, bool replace) {
    json image;
    if (src.existing) {
        image = OwnerRef(context, src.existing);
    } else {
        plan.Create("image", TEXMAP_CLASS_ID, kOctaneImage);
        plan.Set("image", "filename", src.hdri);
        plan.Set("image", "gamma", 1.0);
        if (src.colorSpace) plan.Set("image", "colorSpace", *src.colorSpace);
        image = Created("image");
    }
    plan.Create("light", TEXMAP_CLASS_ID, kOctaneEnv);
    plan.Set("light", "texture_input_type", 3);  // texture
    plan.Set("light", "texture_tex", image);
    plan.Set("light", "power", intensity);
    if (src.rotation != 0) plan.Set("light", "rotation", json::array({src.rotation / 360.0, 0.0}));
    plan.Bind(Created("light"), replace);
}

static void CompileCoronaEnv(Plan& plan, const EnvSource& src) {
    if (src.rotation != 0) throw BadParam("Corona HDRI rotation has no verified binding; rotate the CoronaBitmap after creating it");
    if (src.colorSpace) throw BadParam("Corona input primaries have no verified binding; set them on an existing map");
    plan.Create("light", TEXMAP_CLASS_ID, kCoronaBitmap);
    plan.Set("light", "filename", src.hdri);
    plan.Set("light", "enviroMapping", *ValueOf(kCoronaMapping, "spherical"));
    plan.Set("light", "gamma", 1.0);  // linear samples: no input transfer curve
}

static Light EnvironmentLight(double intensity, const std::string& unit, const std::optional<std::string>& hdri,
                              nb::handle map, double rotation, const std::optional<std::string>& colorSpace, bool replace,
                              const std::optional<std::string>& renderer) {
    RequireMain();
    const json context = NativeJson("native:lighting_context", json::object());
    const Provider& p = RequireProvider(context, renderer);
    if (!p.environment)
        throw BadParam(std::string(p.name) + " has no verified environment light" +
                       (p.family == Family::Photometric ? "; a background map alone does not light a Scanline scene" : ""));
    RequireUnit(unit, {"renderer"}, "environment intensity");
    if (!std::isfinite(intensity) || intensity < 0) throw BadParam("intensity must be a finite number >= 0");
    if (hdri.has_value() == !map.is_none()) throw BadParam("pass hdri='C:/sky.exr' or map=<texture map handle>, one of them");
    EnvSource src{map.is_none() ? nullptr : ExistingMap(map), hdri ? HdriPath(*hdri) : "", rotation, colorSpace};
    if (src.existing && (rotation != 0 || colorSpace))
        throw BadParam("set an existing map's rotation and color space on the map itself; it may be shared");
    Interface* ip = GetCOREInterface();
    const bool slot = p.family != Family::VRay;
    if (slot && src.existing && ip->GetEnvironmentMap() == src.existing && ip->GetUseEnvironmentMap() && p.family == Family::Corona)
        return Light{0, Animatable::GetHandleByAnim(src.existing)};  // already the environment
    if (slot && ip->GetEnvironmentMap() && !replace)
        throw BadParam("the scene already has an environment map ('" + WideToUtf8(ip->GetEnvironmentMap()->GetName().data()) +
                       "'); pass replace=True to rebind the slot");
    Plan plan;
    if (p.family == Family::VRay) {
        CompileVRayDome(plan, intensity, src.existing ? OwnerRef(context, src.existing) : VRayImage(plan, src));
    } else if (p.family == Family::Octane) {
        CompileOctaneEnv(plan, intensity, src, context, replace);
    } else {
        RequireCoronaMaxEnvironment();
        if (intensity != 1)
            throw BadParam("the Corona environment slot has no multiplier: bind at intensity=1, then brighten the map "
                           "itself (a CoronaSky's intensityMultiplier, or a CoronaColorCorrect)");
        if (src.existing) {
            plan.Bind(OwnerRef(context, src.existing), replace);
            Patch(plan, context);
            return Light{0, Animatable::GetHandleByAnim(src.existing)};
        }
        CompileCoronaEnv(plan, src);
        plan.Bind(Created("light"), replace);
    }
    return Commit(plan, context);
}

// ── Capabilities and listing ─────────────────────────────────────

static nb::list StringList(const std::vector<std::string>& items) {
    nb::list out;
    for (const std::string& item : items) out.append(nb::str(item.c_str()));
    return out;
}

static nb::dict Capabilities(const std::optional<std::string>& renderer) {
    RequireMain();
    const json context = NativeJson("native:lighting_context", json::object());
    nb::dict out;
    nb::list renderers;
    for (const json& r : context.at("renderers")) {
        nb::dict entry;
        entry["name"] = r.value("name", "");
        entry["label"] = r.value("label", "");
        entry["provider"] = FamilyText(IdentityFamily(r));
        renderers.append(entry);
    }
    out["renderers"] = renderers;
    out["meters_per_unit"] = ToPython(context.at("meters_per_unit"));
    out["color_management"] = ToPython(context.at("color_management"));
    const auto chosen = ChooseRenderer(context, renderer);
    out["renderer"] = chosen ? nb::cast(chosen->identity.value("name", "")) : nb::none();
    if (!chosen || chosen->family == Family::None) {
        out["supported"] = false;
        out["provider"] = nb::none();
        out["reason"] = chosen ? "no verified lighting provider for " + chosen->identity.value("label", std::string("this renderer")) +
                                     "; create its lights with mx.create(cls, ...) and set node.params"
                               : std::string("no production renderer is assigned");
        return out;
    }
    const Provider& p = ProviderOf(chosen->family);
    out["supported"] = true;
    out["provider"] = p.name;
    out["kinds"] = StringList(p.kinds);
    out["shapes"] = StringList(p.shapes);
    out["units"] = StringList(p.units);
    out["colors"] = StringList(p.colors);
    out["bodies"] = StringList(p.bodies);
    out["environment"] = p.environment ? nb::cast(p.environment) : nb::none();
    std::vector<std::string> notes = p.notes;
    notes.push_back("Distances are scene units (meters_per_unit converts); colors are scene-linear in the rendering "
                    "space; nothing here changes exposure.");
    out["notes"] = StringList(notes);
    return out;
}

static std::vector<Light> SceneLights() {
    RequireMain();
    std::vector<Light> out;
    for (INode* node : AllNodes()) {
        Object* base = BaseObject(node);
        if (base && base->SuperClassID() == LIGHT_CLASS_ID) out.push_back(Light{Animatable::GetHandleByAnim(node), 0});
    }
    if (Texmap* env = GetCOREInterface()->GetEnvironmentMap()) {
        const Light l{0, Animatable::GetHandleByAnim(env)};
        if (ResolveLight(l).role != Role::Unknown) out.push_back(l);
    }
    return out;
}

static std::optional<Light> NodeLight(const Node& n) {
    Object* base = BaseObject(Resolve(n));
    if (!base || base->SuperClassID() != LIGHT_CLASS_ID) return std::nullopt;
    return Light{n.handle, 0};
}

// ── Render jobs ──────────────────────────────────────────────────

using BridgeCall = const char* (*)(const char* request, size_t length, size_t* responseLength);

// The bridge's in-process entry point. NativeJson refuses to run off the main
// thread, and render workers call the bridge from their own thread.
static BridgeCall BridgeEntry() {
    std::vector<HMODULE> modules(512);
    DWORD needed = 0;
    HANDLE process = GetCurrentProcess();
    while (K32EnumProcessModules(process, modules.data(), DWORD(modules.size() * sizeof(HMODULE)), &needed) &&
           needed > modules.size() * sizeof(HMODULE))
        modules.resize(needed / sizeof(HMODULE));
    modules.resize(needed / sizeof(HMODULE));
    for (HMODULE module : modules)
        if (auto call = reinterpret_cast<BridgeCall>(GetProcAddress(module, "mcp_bridge_call"))) return call;
    return nullptr;
}

enum class Phase { Queued, Inline, Posted, Done, Failed, Cancelled };

struct RenderState {
    std::mutex mutex;
    std::string id, output;
    fs::path signal;
    int width = 0, height = 0;
    Phase phase = Phase::Queued;
    double seconds = 0;
    std::string error;
};

struct RenderJob { std::string id; };

static std::vector<std::shared_ptr<RenderState>>& Jobs() {  // main thread only
    static std::vector<std::shared_ptr<RenderState>> jobs;
    return jobs;
}

static std::shared_ptr<RenderState> FindJob(const RenderJob& j) {
    RequireMain();
    for (const auto& job : Jobs())
        if (job->id == j.id) return job;
    throw NotFound("render job '" + j.id + "' is not from this 3ds Max session");
}

static const char* StatusName(Phase phase) {
    switch (phase) {
    case Phase::Queued: return "queued";
    case Phase::Inline: case Phase::Posted: return "rendering";
    case Phase::Done: return "done";
    case Phase::Failed: return "failed";
    default: return "cancelled";
    }
}

static fs::path SignalDir() {
    wchar_t buffer[MAX_PATH];
    const DWORD n = GetEnvironmentVariableW(L"LOCALAPPDATA", buffer, MAX_PATH);
    if (n && n < MAX_PATH) return fs::path(buffer) / "3dsmax-mcp" / "render_signals";
    const DWORD home = GetEnvironmentVariableW(L"USERPROFILE", buffer, MAX_PATH);
    return fs::path(home && home < MAX_PATH ? buffer : L".") / "AppData" / "Local" / "3dsmax-mcp" / "render_signals";
}

// The done file render_automations(action='status', job_id=...) reads: written
// to a temporary name, then renamed, so a reader never sees half of it.
static void WriteSignal(const RenderState& job) {
    json doc = {{"job_id", job.id}, {"status", job.phase == Phase::Done ? "complete" : StatusName(job.phase)},
                {"complete", job.phase == Phase::Done}, {"output", job.output}, {"width", job.width},
                {"height", job.height}, {"seconds", job.seconds}, {"source", "_mx"}};
    if (!job.error.empty()) doc["error"] = job.error;
    std::error_code ec;
    fs::create_directories(job.signal.parent_path(), ec);
    fs::path temp = job.signal;
    temp += L".tmp";
    {
        std::ofstream file(temp, std::ios::binary | std::ios::trunc);
        if (!file) return;
        file << doc.dump();
    }
    MoveFileExW(temp.c_str(), job.signal.c_str(), MOVEFILE_REPLACE_EXISTING);
}

static double SecondsSince(std::chrono::steady_clock::time_point start) {
    return std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
}

// Settles a job that is still in `from` (another path may have settled it).
static void Finish(RenderState& job, std::initializer_list<Phase> from, const std::string& error, double seconds) {
    std::lock_guard<std::mutex> lock(job.mutex);
    if (std::find(from.begin(), from.end(), job.phase) == from.end()) return;
    job.phase = error.empty() ? Phase::Done : Phase::Failed;
    job.error = error;
    job.seconds = seconds;
    WriteSignal(job);
}

static json RenderArgs(const RenderState& job) {
    return {{"width", job.width}, {"height", job.height}, {"output_path", job.output}};
}

static std::string ErrorText(const json& error) {
    json payload = error;
    if ((error.is_string)()) {
        payload = json::parse(error.get<std::string>(), nullptr, false);
        if (!payload.is_object()) return error.get<std::string>();
    }
    if (payload.is_object() && payload.contains("message") && (payload["message"].is_string)())
        return payload["message"].get<std::string>();
    return error.dump();
}

// One request as a pipe client sends it. Max drops a request that waited past
// its executor's timeout without starting (the main thread stayed busy); that
// one is sent again, since it never ran.
static void SendUntilStarted(BridgeCall call, const std::string& type, const json& args, const std::string& tag) {
    for (int attempt = 1;; ++attempt) {
        const json request = {{"command", type == "maxscript" ? args.value("code", std::string()) : args.dump()},
                              {"type", type}, {"requestId", tag + "-" + std::to_string(attempt)}, {"protocolVersion", 2}};
        const std::string text = request.dump();
        size_t length = 0;
        const char* raw = call(text.c_str(), text.size(), &length);
        const json response = json::parse(raw ? std::string(raw, length) : std::string(), nullptr, false);
        if (!response.is_object()) throw std::runtime_error(type + ": unreadable reply from the bridge");
        if (response.value("success", false)) return;
        const std::string error = ErrorText(response.value("error", json()));
        if (error.find("before starting") == std::string::npos) throw std::runtime_error(error);
    }
}

static void RenderWhenFree(std::shared_ptr<RenderState> job, BridgeCall call) {
    const std::string tag = "mx-render-" + job->id;
    try {
        // A no-op queued on Max's main thread behind the script that called
        // mx.render(): once it ran, that script has returned.
        SendUntilStarted(call, "maxscript", {{"code", "ok"}}, tag + "-after");
        {
            std::lock_guard<std::mutex> lock(job->mutex);
            if (job->phase != Phase::Queued) return;  // wait() rendered it, or cancel() dropped it
            job->phase = Phase::Posted;
        }
        const auto start = std::chrono::steady_clock::now();
        SendUntilStarted(call, "native:render_scene", RenderArgs(*job), tag);
        Finish(*job, {Phase::Posted}, "", SecondsSince(start));
    } catch (const std::exception& e) {
        Finish(*job, {Phase::Queued, Phase::Posted}, e.what(), 0);
    }
}

static std::string NewJobId() {
    static std::mt19937_64 random{std::random_device{}()};
    static const char* hex = "0123456789abcdef";
    std::string id;
    for (int i = 0; i < 12; ++i) id += hex[random() & 15];
    return id;
}

static std::string OutputPath(const std::string& output) {
    if (output.empty()) return "";
    std::error_code ec;
    const fs::path full = fs::absolute(fs::path(Utf8ToWide(output)), ec);
    if (ec || !fs::is_directory(full.parent_path(), ec))
        throw BadParam("the output folder of '" + output + "' does not exist; create it first");
    if (full.extension().empty()) throw BadParam("output needs an image extension: .png, .exr, .jpg, .tif");
    return WideToUtf8(full.c_str());
}

static RenderJob QueueRender(std::optional<int> width, std::optional<int> height, const std::string& output) {
    RequireMain();
    Interface* ip = GetCOREInterface();
    if (GetCOREInterface8()->IsRenderActive()) throw MaxError("a render is running; wait for it before queuing another");
    if (!ip->GetRenderer(RS_Production)) throw MaxError("no production renderer is assigned; pick one in Render Setup");
    auto job = std::make_shared<RenderState>();
    job->width = width.value_or(ip->GetRendWidth());
    job->height = height.value_or(ip->GetRendHeight());
    if (job->width < 1 || job->height < 1 || job->width > 32768 || job->height > 32768)
        throw BadParam("width and height must be 1-32768 pixels");
    job->output = OutputPath(output);
    const BridgeCall call = BridgeEntry();
    if (!call) throw MaxError("the MCP bridge plugin is not loaded in this 3ds Max");
    job->id = NewJobId();
    job->signal = SignalDir() / (job->id + ".done.json");
    auto& jobs = Jobs();
    // Keep the last 64 jobs, dropping the oldest settled ones first.
    for (auto it = jobs.begin(); jobs.size() >= 64 && it != jobs.end();) {
        Phase phase;
        {
            std::lock_guard<std::mutex> lock((*it)->mutex);
            phase = (*it)->phase;
        }
        const bool settled = phase == Phase::Done || phase == Phase::Failed || phase == Phase::Cancelled;
        it = settled ? jobs.erase(it) : it + 1;
    }
    jobs.push_back(job);
    std::thread(RenderWhenFree, job, call).detach();
    return RenderJob{job->id};
}

static nb::dict ResultDict(const RenderState& job) {
    nb::dict out;
    out["status"] = StatusName(job.phase);
    out["output"] = job.output.empty() ? nb::none() : nb::cast(job.output);
    out["width"] = job.width;
    out["height"] = job.height;
    out["seconds"] = job.seconds;
    return out;
}

static void RenderInline(RenderState& job) {
    std::string error;
    const auto start = std::chrono::steady_clock::now();
    {
        nb::gil_scoped_release release;  // other Python threads run on during the render
        try {
            NativeJson("native:render_scene", RenderArgs(job));
        } catch (const std::exception& e) {
            error = e.what();
        }
    }
    Finish(job, {Phase::Inline}, error, SecondsSince(start));
}

static nb::dict WaitJob(const RenderJob& j) {
    const auto job = FindJob(j);
    Phase phase;
    {
        std::lock_guard<std::mutex> lock(job->mutex);
        phase = job->phase;
        if (phase == Phase::Queued) job->phase = Phase::Inline;
    }
    if (phase == Phase::Queued) RenderInline(*job);
    else if (phase == Phase::Inline || phase == Phase::Posted)
        throw MaxError("render '" + j.id + "' is handed to Max's main thread and runs after this script returns; read "
                       "job.status from a later script, or render_automations(action='status', job_id='" + j.id +
                       "') from outside Max");
    std::lock_guard<std::mutex> lock(job->mutex);
    if (job->phase == Phase::Failed) throw MaxError("render '" + j.id + "' failed: " + job->error);
    if (job->phase == Phase::Cancelled) throw MaxError("render '" + j.id + "' was cancelled before it started");
    return ResultDict(*job);
}

static bool CancelJob(const RenderJob& j) {
    const auto job = FindJob(j);
    std::lock_guard<std::mutex> lock(job->mutex);
    if (job->phase != Phase::Queued) return false;
    job->phase = Phase::Cancelled;
    WriteSignal(*job);
    return true;
}

template <typename F>
static auto WithJob(const RenderJob& j, F&& read) {
    const auto job = FindJob(j);
    std::lock_guard<std::mutex> lock(job->mutex);
    return read(*job);
}

static std::string JobRepr(const RenderJob& j) {
    try {
        return WithJob(j, [](const RenderState& s) {
            return "<RenderJob " + s.id + " " + StatusName(s.phase) + " " + std::to_string(s.width) + "x" +
                   std::to_string(s.height) + (s.output.empty() ? "" : " -> " + s.output) + ">";
        });
    } catch (const NotFound&) {
        return "<RenderJob " + j.id + " unknown>";
    }
}

// ── Render effects ───────────────────────────────────────────────

struct EffectRef { AnimHandle handle = 0; };

struct Placed {
    SpecialFX* fx = nullptr;
    bool atmospheric = false;
    int index = 0;  // 0-based in its list
};

static std::optional<Placed> Place(AnimHandle handle) {
    Interface* ip = GetCOREInterface();
    for (int i = 0; i < ip->NumAtmospheric(); ++i)
        if (Atmospheric* a = ip->GetAtmospheric(i); a && Animatable::GetHandleByAnim(a) == handle) return Placed{a, true, i};
    for (int i = 0; i < ip->NumEffects(); ++i)
        if (Effect* e = ip->GetEffect(i); e && Animatable::GetHandleByAnim(e) == handle) return Placed{e, false, i};
    return std::nullopt;
}

static Placed ResolveEffect(const EffectRef& r) {
    RequireMain();
    if (auto placed = Place(r.handle)) return *placed;
    throw NotFound("effect #" + std::to_string(r.handle) + " was deleted");
}

// The instance's own name (SpecialFX::name, MAXScript .name), else the
// plug-in's name: GetName() names the plug-in, not this effect.
static std::string EffectName(SpecialFX* fx) {
    std::string name = WideToUtf8(fx->name.data());
    if (name.empty()) name = WideToUtf8(fx->GetName(false).data());
    return name.empty() ? Cls(fx) : name;
}

static std::vector<EffectRef> SceneEffects(const std::optional<std::string>& kind) {
    RequireMain();
    if (kind && *kind != "atmospheric" && *kind != "render") throw BadParam("kind must be 'atmospheric' or 'render'");
    Interface* ip = GetCOREInterface();
    std::vector<EffectRef> out;
    if (!kind || *kind == "atmospheric")
        for (int i = 0; i < ip->NumAtmospheric(); ++i)
            if (Atmospheric* a = ip->GetAtmospheric(i)) out.push_back(EffectRef{Animatable::GetHandleByAnim(a)});
    if (!kind || *kind == "render")
        for (int i = 0; i < ip->NumEffects(); ++i)
            if (Effect* e = ip->GetEffect(i)) out.push_back(EffectRef{Animatable::GetHandleByAnim(e)});
    return out;
}

static void SetEffectActive(const EffectRef& r, bool on) {
    const Placed p = ResolveEffect(r);
    // setActive is MAXScript's switch; the bridge's handler runs it by index.
    NativeJson("native:toggle_effect",
               {{"index", p.index + 1}, {"effect_type", p.atmospheric ? "atmospheric" : "render"}, {"active", on}});
    if ((ResolveEffect(r).fx->Active(Now()) != 0) != on)
        throw MaxError("3ds Max kept '" + EffectName(p.fx) + "' " + (on ? "inactive" : "active"));
}

static void DeleteEffect(const EffectRef& r) {
    const Placed p = ResolveEffect(r);
    if (p.atmospheric) GetCOREInterface()->DeleteAtmosphere(p.index);
    else GetCOREInterface()->DeleteEffect(p.index);
}

static std::string EffectRepr(const EffectRef& r) {
    const auto p = Place(r.handle);
    if (!p) return "<Effect #" + std::to_string(r.handle) + " deleted>";
    return "<Effect '" + EffectName(p->fx) + "' " + Cls(p->fx) + (p->atmospheric ? " atmospheric" : " render") +
           (p->fx->Active(Now()) ? " active" : " inactive") + ">";
}

// ── Binding ──────────────────────────────────────────────────────

static void BindLights(nb::module_& m) {
    auto light = nb::class_<Light>(m, "Light",
        "A light through its renderer's verified provider: light.intensity = (1500, 'lm'), light.kelvin = 4500. "
        "Lists: mx.lights(); a node's: node.light.")
        .def_prop_ro("node", [](const Light& l) -> std::optional<Node> {
            const LightView v = ResolveLight(l);
            if (!v.node) return std::nullopt;
            return Wrap(v.node);
        }, "The light's node, or None for an environment map.")
        .def_prop_ro("params", [](const Light& l) {
            ResolveLight(l);
            return l.node ? Params{l.node, 0} : Params{0, l.map};
        }, "Every parameter of the light (or environment map), for what the semantic properties do not cover.")
        .def_prop_ro("provider", [](const Light& l) { return FamilyText(FamilyOf(ResolveLight(l).role)); },
            "'photometric', 'vray', 'octane', 'corona' or 'fstorm'; None when no verified provider decodes this class.")
        .def_prop_ro("kind", [](const Light& l) { return MaybeText(FormOf(ResolveLight(l)).kind); },
            "'area', 'point', 'directional' or 'environment'; None when undecoded.")
        .def_prop_ro("shape", [](const Light& l) { return MaybeText(FormOf(ResolveLight(l)).shape); },
            "Area shape: 'rectangle', 'disk', 'sphere', 'cylinder' (V-Ray also 'mesh'); None otherwise.")
        .def_prop_rw("enabled", [](const Light& l) { return EnabledOf(ResolveLight(l)); }, &SetEnabled,
            "Whether the light is on (for an environment map: whether it is the active environment).")
        .def_prop_rw("cast_shadows", [](const Light& l) { return ShadowsOf(ResolveLight(l)); }, &SetShadows,
            "Shadow casting; Corona lights and FStorm suns always cast shadows.")
        .def_prop_rw("intensity", [](const Light& l) {
            const Output o = OutputOf(ResolveLight(l));
            return nb::make_tuple(o.value, MaybeText(o.unit));
        }, &SetIntensity,
            "(value, unit), set with an explicit unit: light.intensity = (1500, 'lm'). Units per renderer: mx.light_capabilities().")
        .def_prop_rw("color", [](const Light& l) { return Rgb(TintOf(ResolveLight(l)).rgb); }, &SetColor,
            "RGB color (0-1, scene-linear) when color_mode is 'rgb', else None; setting it switches to RGB.")
        .def_prop_rw("kelvin", [](const Light& l) { return Maybe(TintOf(ResolveLight(l)).kelvin); }, &SetKelvin,
            "Color temperature (500-30000 K) when color_mode is 'kelvin', else None; setting it switches to Kelvin.")
        .def_prop_rw("color_mode", [](const Light& l) { return MaybeText(TintOf(ResolveLight(l)).mode); }, &SetColorMode,
            "'rgb', 'kelvin', 'texmap', 'realistic' (CoronaSun) or 'physical' (FStorm sun); set only 'realistic' or 'physical'.")
        .def("info", &LightInfo, "Everything decoded about the light, with provider extras and any unreadable parts, as a dict.")
        .def("__repr__", &LightRepr)
        .def("__eq__", [](const Light& a, nb::handle b) {
            return nb::isinstance<Light>(b) && a.node == nb::cast<Light>(b).node && a.map == nb::cast<Light>(b).map;
        }, nb::arg("other").none())
        .def("__hash__", [](const Light& l) { return (Py_hash_t)(l.node ? l.node : l.map); });
    for (const char* dim : {"width", "height", "radius", "length"}) {
        light.def_prop_rw(dim, [dim](const Light& l) { return GetDim(l, dim); },
            [dim](const Light& l, double value) { SetDim(l, dim, value); },
            "Area size in scene units (rectangle width/height are full extents, disk/sphere/cylinder radius, "
            "cylinder length); None when the shape has no such size.");
    }

    AddProperty<Node>("light", &NodeLight, "The node's Light (lights only), or None: node.light.intensity.");

    m.def("light", &LightCall, "kind"_a, nb::kw_only(), "intensity"_a, "unit"_a, "name"_a = "", "shape"_a = nb::none(),
          "width"_a = nb::none(), "height"_a = nb::none(), "radius"_a = nb::none(), "length"_a = nb::none(),
          "pos"_a = nb::none(), "aim_at"_a = nb::none(), "direction"_a = nb::none(), "up"_a = nb::none(),
          "kelvin"_a = nb::none(), "color"_a = nb::none(), "enabled"_a = true, "cast_shadows"_a = true,
          "body"_a = nb::none(), "solar"_a = nb::none(), "renderer"_a = nb::none(),
          "Create a light for the renderer's provider in one undo step: light('area', intensity=1500, unit='lm', "
          "shape='rectangle', width=120, height=60, pos=(0, -200, 250), aim_at=(0, 0, 50), kelvin=4500). "
          "Kinds, shapes and units: light_capabilities(); default color 6500 K.");
    m.def("environment_light", &EnvironmentLight, nb::kw_only(), "intensity"_a, "unit"_a, "hdri"_a = nb::none(),
          "map"_a = nb::none(), "rotation"_a = 0.0, "color_space"_a = nb::none(), "replace"_a = false,
          "renderer"_a = nb::none(),
          "Light the scene from an HDRI (.exr/.hdr) or an existing map handle: environment_light(intensity=1, "
          "unit='renderer', hdri='C:/sky.exr'). replace=True rebinds an occupied environment slot.");
    m.def("light_capabilities", &Capabilities, "renderer"_a = nb::none(),
          "What the current (or named) renderer's lighting provider supports: kinds, shapes, units, colors, notes.");
    m.def("lights", &SceneLights, "Every light node, plus the environment map when a provider decodes it.");
}

static void BindRender(nb::module_& m) {
    nb::class_<RenderJob>(m, "RenderJob",
        "A production render queued by mx.render(): job.wait() renders it now; else it starts when the script returns.")
        .def_prop_ro("id", [](const RenderJob& j) { return j.id; })
        .def_prop_ro("status", [](const RenderJob& j) { return WithJob(j, [](const RenderState& s) { return std::string(StatusName(s.phase)); }); },
            "'queued', 'rendering', 'done', 'failed' or 'cancelled'.")
        .def_prop_ro("done", [](const RenderJob& j) {
            return WithJob(j, [](const RenderState& s) { return s.phase == Phase::Done || s.phase == Phase::Failed || s.phase == Phase::Cancelled; });
        }, "True once the job ended (done, failed or cancelled).")
        .def_prop_ro("result", [](const RenderJob& j) {
            return WithJob(j, [](const RenderState& s) { return s.phase == Phase::Done ? nb::object(ResultDict(s)) : nb::none(); });
        }, "{'status', 'output', 'width', 'height', 'seconds'} once done, else None.")
        .def_prop_ro("error", [](const RenderJob& j) {
            return WithJob(j, [](const RenderState& s) { return MaybeText(s.error); });
        }, "Why the render failed, else None.")
        .def_prop_ro("signal_path", [](const RenderJob& j) {
            return WithJob(j, [](const RenderState& s) { return WideToUtf8(s.signal.c_str()); });
        }, "Done file written when the job ends; render_automations(action='status', job_id=job.id) reads it outside Max.")
        .def("wait", &WaitJob,
            "Render now if still queued and return the result; raises if it failed, was cancelled, or already went to Max.")
        .def("cancel", &CancelJob, "Drop the job if it has not started; returns whether it was dropped.")
        .def("__repr__", &JobRepr)
        .def("__eq__", [](const RenderJob& a, nb::handle b) { return nb::isinstance<RenderJob>(b) && a.id == nb::cast<RenderJob>(b).id; }, nb::arg("other").none())
        .def("__hash__", [](const RenderJob& j) { return (Py_hash_t)std::hash<std::string>{}(j.id); });

    m.def("render", &QueueRender, "width"_a = nb::none(), "height"_a = nb::none(), "output"_a = "",
          "Queue a production render of the active view at the time slider's frame; it starts when this script returns, "
          "or now with .wait(): mx.render(output='C:/r/a.png').wait(). Size defaults to Render Setup's.");
    m.def("render_jobs", [] {
        RequireMain();
        std::vector<RenderJob> out;
        for (const auto& job : Jobs()) out.push_back(RenderJob{job->id});
        return out;
    }, "Render jobs queued in this Max session, oldest first.");
}

static void BindEffects(nb::module_& m) {
    nb::class_<EffectRef>(m, "Effect", "An atmospheric or render effect: fx.active = False; fx.params.density = 20.")
        .def_prop_ro("name", [](const EffectRef& r) { return EffectName(ResolveEffect(r).fx); },
            "Name in the Environment or Effects list.")
        .def_prop_ro("class_name", [](const EffectRef& r) { return Cls(ResolveEffect(r).fx); },
            "MAXScript class: VolumeFog, Blur, Lens_Effects, ...")
        .def_prop_ro("kind", [](const EffectRef& r) { return ResolveEffect(r).atmospheric ? "atmospheric" : "render"; },
            "'atmospheric' (Rendering > Environment) or 'render' (Rendering > Effects).")
        .def_prop_ro("index", [](const EffectRef& r) { return ResolveEffect(r).index + 1; }, "1-based position in its list.")
        .def_prop_rw("active", [](const EffectRef& r) { return ResolveEffect(r).fx->Active(Now()) != 0; }, &SetEffectActive,
            "Whether the effect renders.")
        .def_prop_ro("nodes", [](const EffectRef& r) {
            SpecialFX* fx = ResolveEffect(r).fx;
            std::vector<Node> out;
            for (int i = 0; i < fx->NumGizmos(); ++i)
                if (INode* node = fx->GetGizmo(i); InScene(node)) out.push_back(Wrap(node));
            return out;
        }, "Gizmo nodes the effect uses (atmospheric apparatus); empty for most render effects.")
        .def_prop_ro("params", [](const EffectRef& r) { ResolveEffect(r); return Params{0, r.handle}; },
            "The effect's parameters.")
        .def("delete", &DeleteEffect, "Remove the effect from the scene.")
        .def("__repr__", &EffectRepr)
        .def("__eq__", [](const EffectRef& a, nb::handle b) { return nb::isinstance<EffectRef>(b) && a.handle == nb::cast<EffectRef>(b).handle; }, nb::arg("other").none())
        .def("__hash__", [](const EffectRef& r) { return (Py_hash_t)r.handle; });

    m.def("effects", &SceneEffects, "kind"_a = nb::none(),
          "Atmospherics then render effects, or one kind: effects('atmospheric').");
}

void BindLighting(nb::module_& m) {
    BindLights(m);
    BindRender(m);
    BindEffects(m);
}

} // namespace MX_ABI_TAG
} // namespace mx
