// Parameters by name as JSON values, for every owner the bridge edits: base
// objects, modifiers, materials, maps, controllers, renderers. One codec serves
// the tools (core handlers) and _mx, so a parameter reads, describes and sets
// the same way everywhere.
//
// A name resolves, in order, to:
//   1. a ParamBlock2 parameter (internal name or MAXScript alias, any case);
//   2. a transform or point subanim (a modifier's gizmo, center, Lathe's axis),
//      read and set through its controller;
//   3. a MAXScript property (old-style parameter blocks, scripted plugins,
//      properties MAXScript adds on top).
//
// Values follow MAXScript units: distances in scene units, angles in degrees,
// percentages 0-100, frames for times, colors as 0-1 floats [r, g, b(, a)] or
// "#rrggbb". Integer parameters with named choices take the choice's name.
// Maps, materials and other references take null, {"handle": n} for an
// existing one, or {"class": "...", "name": "...", "params": {...}} for a new
// one built in place. Transforms are {"position", "rotation" (degrees), "scale"},
// any part optional. Everything runs on Max's main thread; nothing here opens
// an undo hold.
#pragma once

#include "mcp_bridge/handler_helpers.h"

#include <control.h>
#include <decomp.h>
#include <maxscript/compiler/thunks.h>
#include <maxscript/foundation/3dmath.h>
#include <maxscript/foundation/arrays.h>
#include <maxscript/foundation/colors.h>
#include <maxscript/foundation/name.h>
#include <maxscript/foundation/numbers.h>

#include <array>
#include <cctype>
#include <cstdio>
#include <cstring>
#include <limits>

namespace ParamCodec {

using json = nlohmann::json;
using HandlerHelpers::Utf8ToWide;
using HandlerHelpers::WideToUtf8;

// ── Errors ───────────────────────────────────────────────────────
// A bridge-structured error (BAD_PARAM), also catchable by type so _mx can
// raise its own BadParam / UnknownParam with the plain message.

struct Error : std::runtime_error {
    std::string code;
    std::string text;
    bool unknown = false;   // the name is not a parameter of the owner
    Error(const std::string& c, const std::string& t, const json& hint = json(), bool unknownName = false)
        : std::runtime_error(HandlerHelpers::StructuredErrorPayload(c, t, hint)), code(c), text(t), unknown(unknownName) {}
};

[[noreturn]] inline void Fail(const std::string& text, const json& hint = json()) { throw Error("BAD_PARAM", text, hint); }

// MAXScript defines is_array(v), is_string(v) and, before the 2027 SDK,
// is_number(v) as macros; parentheses keep them off nlohmann's methods of the
// same names.
inline bool IsArray(const json& v) { return (v.is_array)(); }
inline bool IsString(const json& v) { return (v.is_string)(); }
inline bool IsNumber(const json& v) { return (v.is_number)(); }

// ── Names ────────────────────────────────────────────────────────

inline std::string Lower(std::string s) {
    for (char& c : s) c = (char)std::tolower((unsigned char)c);
    return s;
}

// Lowercase with spaces, underscores and hyphens dropped: "Base Color" matches base_color.
inline std::string Folded(const std::string& s) {
    std::string out;
    for (char c : s)
        if (c != ' ' && c != '_' && c != '-') out.push_back((char)std::tolower((unsigned char)c));
    return out;
}

inline size_t EditDistance(const std::string& a, const std::string& b) {
    std::vector<size_t> row(b.size() + 1);
    for (size_t j = 0; j <= b.size(); ++j) row[j] = j;
    for (size_t i = 1; i <= a.size(); ++i) {
        size_t previous = row[0];
        row[0] = i;
        for (size_t j = 1; j <= b.size(); ++j) {
            const size_t current = row[j];
            row[j] = std::min({row[j] + 1, row[j - 1] + 1, previous + (a[i - 1] == b[j - 1] ? 0 : 1)});
            previous = current;
        }
    }
    return row[b.size()];
}

// The closest name within a few edits, else one that starts with or contains it.
inline std::string Closest(const std::string& wanted, const std::vector<std::string>& names) {
    const std::string key = Folded(wanted);
    const std::string* best = nullptr;
    size_t bestDistance = std::max<size_t>(2, key.size() / 3) + 1;
    for (const std::string& name : names) {
        const size_t d = EditDistance(key, Folded(name));
        if (d < bestDistance) { bestDistance = d; best = &name; }
    }
    if (!best && key.size() >= 3)
        for (const std::string& name : names)
            if (Folded(name).find(key) != std::string::npos && (!best || name.size() < best->size())) best = &name;
    return best ? *best : std::string();
}

inline std::string JoinNames(const std::vector<std::string>& names, size_t limit = 40) {
    std::string out;
    for (size_t i = 0; i < names.size() && i < limit; ++i) out += (i ? ", " : "") + names[i];
    if (names.size() > limit) out += ", ... (" + std::to_string(names.size()) + " in all)";
    return out;
}

inline std::string OwnerLabel(Animatable* owner) {
    if (!owner) return "this object";
    std::string label = HandlerHelpers::MaxScriptVisibleClassName(owner);
    if (owner->SuperClassID() == MATERIAL_CLASS_ID || owner->SuperClassID() == TEXMAP_CLASS_ID) {
        const std::string name = WideToUtf8(static_cast<MtlBase*>(owner)->GetName().data());
        if (!name.empty()) label += " '" + name + "'";
    } else if (owner->SuperClassID() == OSM_CLASS_ID || owner->SuperClassID() == WSM_CLASS_ID) {
        const std::string name = WideToUtf8(static_cast<Modifier*>(owner)->GetName(false).data());
        if (!name.empty() && name != label) label += " '" + name + "'";
    }
    return label;
}

// ── ParamBlock2 parameters ───────────────────────────────────────

struct Ref {
    IParamBlock2* block = nullptr;
    const ParamDef* def = nullptr;
};

inline int BaseType(int type) { return type & ~TYPE_TAB; }

// The name MAXScript shows: the parameter's alias when it has one (Bend's
// BendAngle is `angle`), else its internal name.
inline std::string Name(IParamBlock2* block, const ParamDef& def) {
    const MCHAR* alias = block ? block->FindParamAlias(def.ID, -1) : nullptr;
    return WideToUtf8(alias && *alias ? alias : def.int_name);
}

template <typename F>
inline void ForEach(Animatable* owner, F&& visit) {
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

inline std::optional<Ref> FindPB2(Animatable* owner, const std::string& name) {
    const std::wstring wanted = Utf8ToWide(name);
    std::optional<Ref> found;
    ForEach(owner, [&](IParamBlock2* block, const ParamDef& def) {
        if (found) return;
        const MCHAR* alias = block->FindParamAlias(def.ID, -1);
        if (_wcsicmp(def.int_name, wanted.c_str()) == 0 || (alias && _wcsicmp(alias, wanted.c_str()) == 0))
            found = Ref{block, &def};
    });
    if (found) return found;
#if MAX_SDK_VERSION >= 2027
    // MAXScript's own resolution (aliases it registers itself); its lookup is public from the 2027 SDK.
    HandlerHelpers::RequireMaxScriptThread();
    ScopedMaxScriptEvaluationContext context;
    one_value_local(property);
    vl.property = Name::intern(wanted.c_str());
    for (int b = 0; b < owner->NumParamBlocks(); ++b) {
        IParamBlock2* block = owner->GetParamBlock(b);
        ParamBlockDesc2* desc = block ? block->GetDesc() : nullptr;
        if (!desc) continue;
        int tab = -1;
        if (ParamDef* def = MAXClass::get_pb2_param_def(vl.property, block, desc, tab)) return Ref{block, def};
    }
#endif
    return std::nullopt;
}

inline const char* TypeName(int type) {
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

// External value = internal * scale: degrees for angles, 0-100 for percentages.
inline double Scale(int type) {
    switch (BaseType(type)) {
    case TYPE_ANGLE: return 180.0 / PI;
    case TYPE_PCNT_FRAC: return 100.0;
    default: return 1.0;
    }
}

inline bool FloatKind(int type) {
    const int t = BaseType(type);
    return t == TYPE_FLOAT || t == TYPE_WORLD || t == TYPE_ANGLE || t == TYPE_PCNT_FRAC || t == TYPE_COLOR_CHANNEL;
}
inline bool IntKind(int type) {
    const int t = BaseType(type);
    return t == TYPE_INT || t == TYPE_INDEX || t == TYPE_RADIOBTN_INDEX;
}

// ── Named choices of integer parameters ──────────────────────────

struct Choice {
    std::string name;
    int value = 0;
};

struct Choices {
    std::vector<Choice> items;
    std::string source;
    bool complete = true;
    std::string evidence;
};

// Integer parameters whose plugins publish no names; the meanings come from the
// providers' own scripts and UI (the renderer presets in maxmcp/assets/renderers
// carry the same evidence for the plugin inspector).
struct ProviderDomain {
    SClass_ID sid;
    ULONG a, b;
    int block, param;
    const char* name;
    std::vector<std::pair<const char*, int>> choices;
    bool complete;
    const char* evidence;
};

inline const std::vector<ProviderDomain>& ProviderDomains() {
    static const char* const vrayLights = "Chaos VRay-VRayLightLister.mcr";
    static const char* const vrayBitmap =
        "V-Ray 7 update 3 VRayBitmap Qt combo item data (mapping, transfer function and RGB primaries)";
    static const char* const coronaLight =
        "Chaos coronaConverter.ms shipped with Corona 15 (units L3354, colorMode L3379, shape L3410-3546)";
    static const std::vector<ProviderDomain> domains = {
        {LIGHT_CLASS_ID, 1012233633u, 1607860959u, 0, 1, "type",
         {{"rectangle", 0}, {"dome", 1}, {"sphere", 2}, {"mesh", 3}, {"disk", 4}}, true, vrayLights},
        {LIGHT_CLASS_ID, 1012233633u, 1607860959u, 0, 12, "normalizeColor",
         {{"renderer", 0}, {"lm", 1}, {"cd/m2", 2}, {"W", 3}, {"W/m2/sr", 4}}, true, vrayLights},
        {LIGHT_CLASS_ID, 1012233633u, 1607860959u, 0, 39, "color_mode", {{"rendering_rgb", 0}, {"kelvin", 1}}, true, vrayLights},
        {LIGHT_CLASS_ID, 592523983u, 1640440069u, 0, 14768, "analyticLightType",
         {{"rectangle", 0}, {"disk", 1}, {"sphere", 3}, {"cylinder", 4}}, true,
         "Octane 2026.3 Type combo item data (Quad=0, Disc=1, Sphere=3, Tube=4)"},
        {TEXMAP_CLASS_ID, 1734939723u, 46203261u, 0, 2, "mapType",
         {{"angular", 0}, {"cubic", 1}, {"spherical", 2}, {"mirrored_ball", 3}, {"max_standard", 4}}, true, vrayBitmap},
        {TEXMAP_CLASS_ID, 1734939723u, 46203261u, 0, 25, "color_space",
         {{"none", 0}, {"inverse_gamma", 1}, {"srgb", 2}, {"from_max", 3}, {"auto", 4}}, true, vrayBitmap},
        {TEXMAP_CLASS_ID, 1734939723u, 46203261u, 3, 41, "rgbColorSpace",
         {{"default", 0}, {"srgb", 1}, {"acescg", 2}, {"raw", 3}}, true, vrayBitmap},
        {LIGHT_CLASS_ID, 1110459877u, 692869241u, 0, 121, "shape",
         {{"sphere", 0}, {"rectangle", 1}, {"disk", 2}, {"cylinder", 3}}, true, coronaLight},
        {LIGHT_CLASS_ID, 1110459877u, 692869241u, 0, 132, "colorMode",
         {{"rendering_rgb", 0}, {"kelvin", 1}, {"texmap", 2}}, true, coronaLight},
        {LIGHT_CLASS_ID, 1110459877u, 692869241u, 0, 136, "intensityUnits",
         {{"renderer", 0}, {"lm", 1}, {"cd", 2}, {"lx", 3}}, true, coronaLight},
        {LIGHT_CLASS_ID, 2084191360u, 164583279u, 0, 107, "colorMode",
         {{"rendering_rgb", 0}, {"kelvin", 1}, {"realistic", 2}}, true,
         "Chaos coronaConverter.ms shipped with Corona 15 (L3744-3746: colorMode 0 = direct colour; PB2 default 2 = realistic)"},
        {TEXMAP_CLASS_ID, 2881116036u, 1699234372u, 0, 102, "enviroMapping", {{"spherical", 0}, {"screen", 1}}, false,
         "Chaos coronaConverter.ms shipped with Corona 15 (L45-46: CORONA_BITMAP_MAPPING_SPHERICAL=0, SCREEN=1)"},
    };
    return domains;
}

inline const ProviderDomain* FindProviderDomain(SClass_ID sid, const Class_ID& cls, int block, int param, const std::string& name) {
    for (const ProviderDomain& d : ProviderDomains())
        if (d.sid == sid && d.a == cls.PartA() && d.b == cls.PartB() && d.block == block && d.param == param && name == d.name)
            return &d;
    return nullptr;
}

// The items of a rollout dialog template (DLGTEMPLATE or DLGTEMPLATEEX).
struct DialogItem {
    int id = 0;
    WORD classOrdinal = 0;   // 0x0080 for a Win32 button, 0 for a named class
    DWORD style = 0;
    std::wstring text;       // without '&' accelerators
};

inline std::vector<DialogItem> DialogItems(HINSTANCE module, int templateId) {
    std::vector<DialogItem> items;
    if (!module || templateId <= 0) return items;
    HRSRC resource = FindResourceW(module, MAKEINTRESOURCEW(templateId), MAKEINTRESOURCEW(5) /* RT_DIALOG */);
    if (!resource) return items;
    HGLOBAL loaded = LoadResource(module, resource);
    const auto* begin = loaded ? static_cast<const BYTE*>(LockResource(loaded)) : nullptr;
    const DWORD size = SizeofResource(module, resource);
    if (!begin || size < 18) return items;
    const BYTE* end = begin + size;
    const BYTE* p = begin;
    struct Truncated {};
    auto word = [&]() -> WORD { if (p + 2 > end) throw Truncated{}; WORD w; std::memcpy(&w, p, 2); p += 2; return w; };
    auto dword = [&]() -> DWORD { if (p + 4 > end) throw Truncated{}; DWORD d; std::memcpy(&d, p, 4); p += 4; return d; };
    auto skip = [&](size_t n) { if (p + n > end) throw Truncated{}; p += n; };
    auto align = [&]() { const size_t offset = (size_t)(p - begin); if (offset % 4) skip(4 - offset % 4); };
    WORD ordinal = 0;
    auto text = [&]() -> std::wstring {   // sz_Or_Ord
        ordinal = 0;
        const WORD first = word();
        if (first == 0x0000) return {};
        if (first == 0xFFFF) { ordinal = word(); return {}; }
        std::wstring s(1, (wchar_t)first);
        for (WORD c = word(); c; c = word()) s.push_back((wchar_t)c);
        return s;
    };
    try {
        WORD version = 0, signature = 0;
        std::memcpy(&version, p, 2);
        std::memcpy(&signature, p + 2, 2);
        const bool extended = version == 1 && signature == 0xFFFF;
        DWORD style = 0;
        WORD count = 0;
        if (extended) {
            skip(4); dword(); dword(); style = dword(); count = word(); skip(8);
        } else {
            style = dword(); dword(); count = word(); skip(8);
        }
        text(); text(); text();   // menu, class, title
        if (style & (DS_SETFONT | DS_SHELLFONT)) {
            word();
            if (extended) { word(); skip(2); }
            text();
        }
        for (WORD i = 0; i < count && i < 2048; ++i) {
            align();
            DialogItem item;
            if (extended) {
                dword(); dword(); item.style = dword(); skip(8); item.id = (int)dword();
            } else {
                item.style = dword(); dword(); skip(8); item.id = (int)word();
            }
            text();
            item.classOrdinal = ordinal;
            for (wchar_t c : text())
                if (c != L'&') item.text.push_back(c);
            skip(word());   // creation data
            items.push_back(std::move(item));
        }
    } catch (const Truncated&) {
        items.clear();   // a malformed template yields no labels rather than wrong ones
    }
    return items;
}

// The rollout templates a parameter's controls live in: the maps it names in a
// multi-rollout block, else the block's own template.
inline std::vector<int> ParamTemplates(ParamBlockDesc2* desc, const ParamDef& def) {
    std::vector<int> templates;
    if ((desc->flags & P_MULTIMAP) && desc->map_specs.Count() > 0) {
        for (int m = 0; m < def.maps.Count(); ++m)
            for (int i = 0; i < desc->map_specs.Count(); ++i)
                if (desc->map_specs[i].map_id == def.maps[m] && desc->map_specs[i].dlg_template)
                    templates.push_back(desc->map_specs[i].dlg_template);
        if (def.maps.Count() == 0 && desc->map_specs[0].dlg_template) templates.push_back(desc->map_specs[0].dlg_template);
    } else if (desc->dlg_template) {
        templates.push_back(desc->dlg_template);
    }
    return templates;
}

// Radio button labels from the parameter's own rollout; every control must be a
// radio button there, else there are no labels. Values follow p_vals.
inline std::optional<Choices> RadioChoices(ParamBlockDesc2* desc, const ParamDef& def) {
    if (def.ctrl_type != TYPE_RADIO || def.ctrl_count <= 1 || !def.ctrl_IDs || !desc || !desc->cd) return std::nullopt;
    HINSTANCE module = desc->cd->HInstance();
    for (int templateId : ParamTemplates(desc, def)) {
        const std::vector<DialogItem> items = DialogItems(module, templateId);
        Choices found;
        found.source = "rollout radio buttons";
        for (int i = 0; i < def.ctrl_count; ++i) {
            const auto item = std::find_if(items.begin(), items.end(), [&](const DialogItem& d) { return d.id == def.ctrl_IDs[i]; });
            const DWORD kind = item == items.end() ? 0 : (item->style & 0x0F);
            const bool radio = item != items.end() && item->classOrdinal == 0x0080 &&
                               (kind == BS_RADIOBUTTON || kind == BS_AUTORADIOBUTTON);
            if (!radio || item->text.empty()) { found.items.clear(); break; }
            std::string label = WideToUtf8(item->text.c_str());
            while (!label.empty() && (label.back() == ':' || label.back() == ' ')) label.pop_back();
            found.items.push_back({label, def.val_bits ? def.val_bits[i] : i});
        }
        if (!found.items.empty()) return found;
    }
    return std::nullopt;
}

// Dropdown items: p_ui TYPE_INTLISTBOX / TYPE_INT_COMBOBOX, <control>, <count>,
// <string resource per item>; the control ID comes first in ctrl_IDs, the item
// strings after it. A list box's value is the item's index; a combo box's
// follows p_vals when given.
inline std::optional<Choices> ListChoices(ParamBlockDesc2* desc, const ParamDef& def) {
    const bool combo = def.ctrl_type == TYPE_INT_COMBOBOX;
    if ((!combo && def.ctrl_type != TYPE_INTLISTBOX) || (def.type & TYPE_TAB) || !def.ctrl_IDs || def.ctrl_count < 2 || !desc || !desc->cd)
        return std::nullopt;
    Choices found;
    found.source = combo ? "parameter combo box items" : "parameter dropdown items";
    for (int i = 1; i < def.ctrl_count; ++i) {
        const MCHAR* label = def.ctrl_IDs[i] > 0 ? desc->GetString(def.ctrl_IDs[i]) : nullptr;
        if (!label || !*label) return std::nullopt;
        found.items.push_back({WideToUtf8(label), combo && def.val_bits ? def.val_bits[i - 1] : i - 1});
    }
    return found;
}

inline std::optional<Choices> ChoicesOf(ClassDesc* cd, ParamBlockDesc2* desc, const ParamDef& def, const std::string& name) {
    if (!IntKind(def.type)) return std::nullopt;
    if (cd && desc) {
        if (const ProviderDomain* d = FindProviderDomain(cd->SuperClassID(), cd->ClassID(), desc->ID, def.ID, name)) {
            Choices out;
            for (const auto& [label, value] : d->choices) out.items.push_back({label, value});
            out.source = "provider reference";
            out.complete = d->complete;
            out.evidence = d->evidence;
            return out;
        }
    }
    if (auto radio = RadioChoices(desc, def)) return radio;
    return ListChoices(desc, def);
}

inline json ChoicesJson(const Choices& c) {
    json items = json::array();
    for (const Choice& item : c.items) items.push_back({{"name", item.name}, {"value", item.value}});
    return items;
}

inline std::optional<int> ChoiceValue(const Choices& c, const std::string& name) {
    const std::string key = Folded(name);
    for (const Choice& item : c.items)
        if (Folded(item.name) == key) return item.value;
    return std::nullopt;
}

inline std::string ChoiceNames(const Choices& c) {
    std::vector<std::string> names;
    for (const Choice& item : c.items) names.push_back(item.name + "=" + std::to_string(item.value));
    return JoinNames(names);
}

// ── Reading values ───────────────────────────────────────────────

inline json RefJson(ReferenceTarget* target) {
    if (!target) return nullptr;
    if (target->SuperClassID() == BASENODE_CLASS_ID) return HandlerHelpers::NodeIdentityJson(static_cast<INode*>(target));
    json out = {{"class", HandlerHelpers::MaxScriptVisibleClassName(target)},
                {"handle", (unsigned long long)Animatable::GetHandleByAnim(target)}};
    if (target->SuperClassID() == MATERIAL_CLASS_ID || target->SuperClassID() == TEXMAP_CLASS_ID)
        out["name"] = WideToUtf8(static_cast<MtlBase*>(target)->GetName().data());
    return out;
}

inline float Clean(float v) { return v + 0.0f; }   // -0.0 reads as 0.0
inline json Point3Json(const Point3& p) { return json::array({Clean(p.x), Clean(p.y), Clean(p.z)}); }

inline json GetOne(const Ref& ref, TimeValue t, int index) {
    IParamBlock2* pb = ref.block;
    const ParamID id = ref.def->ID;
    const int type = BaseType(ref.def->type);
    Interval valid = FOREVER;
    switch (type) {
    case TYPE_FLOAT: case TYPE_WORLD: case TYPE_COLOR_CHANNEL: case TYPE_ANGLE: case TYPE_PCNT_FRAC: {
        float v = 0; pb->GetValue(id, t, v, valid, index); return v * Scale(type);
    }
    case TYPE_INT: case TYPE_INDEX: case TYPE_RADIOBTN_INDEX: { int v = 0; pb->GetValue(id, t, v, valid, index); return v; }
    case TYPE_BOOL: { int v = 0; pb->GetValue(id, t, v, valid, index); return v != 0; }
    case TYPE_TIMEVALUE: { int v = 0; pb->GetValue(id, t, v, valid, index); return double(v) / GetTicksPerFrame(); }
    case TYPE_POINT3: { Point3 v; pb->GetValue(id, t, v, valid, index); return Point3Json(v); }
    case TYPE_POINT4: { Point4 v; pb->GetValue(id, t, v, valid, index); return json::array({v.x, v.y, v.z, v.w}); }
    case TYPE_RGBA: { Color v; pb->GetValue(id, t, v, valid, index); return json::array({v.r, v.g, v.b}); }
    case TYPE_FRGBA: { AColor v; pb->GetValue(id, t, v, valid, index); return json::array({v.r, v.g, v.b, v.a}); }
    case TYPE_STRING: case TYPE_FILENAME: {
        const MCHAR* v = nullptr; pb->GetValue(id, t, v, valid, index); return WideToUtf8(v);
    }
    case TYPE_INODE: { INode* v = nullptr; pb->GetValue(id, t, v, valid, index); return v ? RefJson(v) : json(nullptr); }
    case TYPE_TEXMAP: { Texmap* v = nullptr; pb->GetValue(id, t, v, valid, index); return RefJson(v); }
    case TYPE_MTL: { Mtl* v = nullptr; pb->GetValue(id, t, v, valid, index); return RefJson(v); }
    case TYPE_REFTARG: { ReferenceTarget* v = nullptr; pb->GetValue(id, t, v, valid, index); return RefJson(v); }
    default: return nullptr;
    }
}

inline json GetPB2(const Ref& ref, TimeValue t) {
    if (!(ref.def->type & TYPE_TAB)) return GetOne(ref, t, 0);
    json items = json::array();
    for (int i = 0; i < ref.block->Count(ref.def->ID); ++i) items.push_back(GetOne(ref, t, i));
    return items;
}

// ── Subanim transforms and points ────────────────────────────────
// A modifier's gizmo and center, Lathe's axis: subanims whose controller holds
// a transform or a point, set through that controller so the owner updates.

inline bool SubControlKind(Control* c) {
    if (!c) return false;
    const SClass_ID sid = c->SuperClassID();
    return sid == CTRL_MATRIX3_CLASS_ID || sid == CTRL_POSITION_CLASS_ID || sid == CTRL_POINT3_CLASS_ID;
}

inline Control* FindSubControl(Animatable* owner, const std::string& name) {
    const std::string key = Folded(name);
    for (int i = 0; i < owner->NumSubs(); ++i) {
        Animatable* sub = owner->SubAnim(i);
        if (!sub || !(sub->SuperClassID() == CTRL_MATRIX3_CLASS_ID || sub->SuperClassID() == CTRL_POSITION_CLASS_ID ||
                      sub->SuperClassID() == CTRL_POINT3_CLASS_ID))
            continue;
        if (Folded(WideToUtf8(owner->SubAnimName(i, false).data())) == key) return static_cast<Control*>(sub);
    }
    return nullptr;
}

inline std::vector<std::pair<std::string, Control*>> SubControls(Animatable* owner) {
    std::vector<std::pair<std::string, Control*>> out;
    for (int i = 0; i < owner->NumSubs(); ++i) {
        Animatable* sub = owner->SubAnim(i);
        if (!sub) continue;
        const SClass_ID sid = sub->SuperClassID();
        if (sid != CTRL_MATRIX3_CLASS_ID && sid != CTRL_POSITION_CLASS_ID && sid != CTRL_POINT3_CLASS_ID) continue;
        std::string name = Lower(WideToUtf8(owner->SubAnimName(i, false).data()));
        for (char& c : name) if (c == ' ') c = '_';
        out.push_back({name, static_cast<Control*>(sub)});
    }
    return out;
}

inline Control* EulerAxis(Control* rc, int axis) {
    if (!rc) return nullptr;
    return axis == 0 ? rc->GetXController() : axis == 1 ? rc->GetYController() : rc->GetZController();
}

inline json SubControlGet(Control* c, TimeValue t) {
    Interval valid = FOREVER;
    if (c->SuperClassID() != CTRL_MATRIX3_CLASS_ID) {
        Point3 p(0, 0, 0);
        c->GetValue(t, &p, valid, CTRL_ABSOLUTE);
        return Point3Json(p);
    }
    Point3 pos(0, 0, 0), scale(1, 1, 1);
    float angles[3] = {0, 0, 0};
    Control* pc = c->GetPositionController();
    Control* rc = c->GetRotationController();
    Control* sc = c->GetScaleController();
    if (pc && rc && sc) {
        pc->GetValue(t, &pos, valid, CTRL_ABSOLUTE);
        if (EulerAxis(rc, 0) && EulerAxis(rc, 1) && EulerAxis(rc, 2)) {
            for (int a = 0; a < 3; ++a) EulerAxis(rc, a)->GetValue(t, &angles[a], valid, CTRL_ABSOLUTE);
        } else {
            Quat q;
            rc->GetValue(t, &q, valid, CTRL_ABSOLUTE);
            QuatToEuler(q, angles, EULERTYPE_XYZ);
        }
        ScaleValue s(Point3(1, 1, 1));
        sc->GetValue(t, &s, valid, CTRL_ABSOLUTE);
        scale = s.s;
    } else {
        Matrix3 tm(1);
        c->GetValue(t, &tm, valid, CTRL_RELATIVE);
        AffineParts parts;
        decomp_affine(tm, &parts);
        pos = parts.t;
        QuatToEuler(parts.q, angles, EULERTYPE_XYZ);
        scale = parts.k * (parts.f < 0 ? -1.0f : 1.0f);
    }
    return {{"position", Point3Json(pos)},
            {"rotation", json::array({Clean(RadToDeg(angles[0])), Clean(RadToDeg(angles[1])), Clean(RadToDeg(angles[2]))})},
            {"scale", Point3Json(scale)}};
}

inline bool IsFiniteNumber(const json& v) {
    return IsNumber(v) && !v.is_boolean() && std::isfinite(v.get<double>());
}

inline float Number(const json& v, const std::string& what) {
    if (!IsFiniteNumber(v)) Fail(what + " expects a number, got " + v.dump());
    const double d = v.get<double>();
    if (std::abs(d) > std::numeric_limits<float>::max()) Fail(what + " is out of range: " + v.dump());
    return (float)d;
}

inline std::vector<float> Numbers(const json& v, size_t lo, size_t hi, const std::string& what) {
    if (!IsArray(v) || v.size() < lo || v.size() > hi)
        Fail(what + " expects a list of " + (lo == hi ? std::to_string(lo) : std::to_string(lo) + "-" + std::to_string(hi)) +
             " numbers, got " + v.dump());
    std::vector<float> out;
    for (const json& item : v) out.push_back(Number(item, what));
    return out;
}

inline Point3 PointOf(const json& v, const std::string& what) {
    const auto xs = Numbers(v, 3, 3, what);
    return Point3(xs[0], xs[1], xs[2]);
}

inline void SubControlSet(Control* c, const json& v, TimeValue t, const std::string& what) {
    if (c->SuperClassID() != CTRL_MATRIX3_CLASS_ID) {
        Point3 p = PointOf(v, what);
        c->SetValue(t, &p, 1, CTRL_ABSOLUTE);
        return;
    }
    json parts = v;
    if (IsArray(v) && v.size() == 4) {   // four rows: X, Y, Z axes, then position
        Matrix3 tm;
        for (int r = 0; r < 4; ++r) tm.SetRow(r, PointOf(v[r], what + " row " + std::to_string(r + 1)));
        tm.ValidateFlags();
        AffineParts ap;
        decomp_affine(tm, &ap);
        float angles[3];
        QuatToEuler(ap.q, angles, EULERTYPE_XYZ);
        parts = {{"position", Point3Json(ap.t)},
                 {"rotation", json::array({RadToDeg(angles[0]), RadToDeg(angles[1]), RadToDeg(angles[2])})},
                 {"scale", Point3Json(ap.k)}};
    }
    if (!parts.is_object())
        Fail(what + " is a transform: pass {\"position\": [x, y, z], \"rotation\": [x, y, z] degrees, \"scale\": [x, y, z]}, any part optional");
    for (auto it = parts.begin(); it != parts.end(); ++it)
        if (it.key() != "position" && it.key() != "rotation" && it.key() != "scale")
            Fail(what + " has no transform part '" + it.key() + "'; use position, rotation, scale");
    Control* pc = c->GetPositionController();
    Control* rc = c->GetRotationController();
    Control* sc = c->GetScaleController();
    if (pc && rc && sc) {
        if (parts.contains("position")) { Point3 p = PointOf(parts["position"], what + ".position"); pc->SetValue(t, &p, 1, CTRL_ABSOLUTE); }
        if (parts.contains("rotation")) {
            const Point3 deg = PointOf(parts["rotation"], what + ".rotation");
            if (EulerAxis(rc, 0) && EulerAxis(rc, 1) && EulerAxis(rc, 2)) {
                for (int a = 0; a < 3; ++a) { float r = DegToRad(deg[a]); EulerAxis(rc, a)->SetValue(t, &r, 1, CTRL_ABSOLUTE); }
            } else {
                float angles[3] = {DegToRad(deg.x), DegToRad(deg.y), DegToRad(deg.z)};
                Quat q;
                EulerToQuat(angles, q, EULERTYPE_XYZ);
                rc->SetValue(t, &q, 1, CTRL_ABSOLUTE);
            }
        }
        if (parts.contains("scale")) {
            const json& s = parts["scale"];
            Point3 k = IsFiniteNumber(s) ? Point3(1, 1, 1) * Number(s, what + ".scale") : PointOf(s, what + ".scale");
            ScaleValue value(k);
            sc->SetValue(t, &value, 1, CTRL_ABSOLUTE);
        }
        return;
    }
    // A matrix controller without position/rotation/scale tracks: compose and set the whole transform.
    const json current = SubControlGet(c, t);
    auto part = [&](const char* key) { return parts.contains(key) ? parts[key] : current[key]; };
    const Point3 deg = PointOf(part("rotation"), what + ".rotation");
    float angles[3] = {DegToRad(deg.x), DegToRad(deg.y), DegToRad(deg.z)};
    Quat q;
    EulerToQuat(angles, q, EULERTYPE_XYZ);
    const json& s = part("scale");
    // The inverse of decomp_affine, which SubControlGet reads with: scale, then rotation, then translation.
    Matrix3 rotation;
    q.MakeMatrix(rotation);
    Matrix3 tm = ScaleMatrix(IsFiniteNumber(s) ? Point3(1, 1, 1) * Number(s, what + ".scale") : PointOf(s, what + ".scale")) * rotation;
    tm.SetTrans(PointOf(part("position"), what + ".position"));
    SetXFormPacket packet(tm);
    c->SetValue(t, &packet, 1, CTRL_ABSOLUTE);
}

// ── MAXScript properties ─────────────────────────────────────────

struct MxsAt {
    BOOL usedContext;
    TimeValue previous;
    explicit MxsAt(TimeValue t) : usedContext(thread_local(use_time_context)), previous(thread_local(current_time)) {
        thread_local(use_time_context) = TRUE;
        thread_local(current_time) = t;
    }
    ~MxsAt() {
        thread_local(use_time_context) = usedContext;
        thread_local(current_time) = previous;
    }
    MxsAt(const MxsAt&) = delete;
    MxsAt& operator=(const MxsAt&) = delete;
};

inline ReferenceTarget* AsRefTarget(Animatable* owner) {
    if (!owner || !owner->IsRefMaker() || !static_cast<ReferenceMaker*>(owner)->IsRefTarget()) return nullptr;
    return static_cast<ReferenceTarget*>(owner);
}

inline std::string Shown(Value* v) {
    StringStream* text = new StringStream(0);
    v->sprin1(text);
    return WideToUtf8(text->to_string());
}

inline json FromMxs(Value* v, int depth = 0) {
    if (!v || v == &undefined || v == &unsupplied) return nullptr;
    if (v == &true_value) return true;
    if (v == &false_value) return false;
    if (is_integer(v)) return v->to_int();
    if (is_float(v) || is_double(v)) return v->to_double();
    if (is_point3(v)) return Point3Json(v->to_point3());
    if (is_point2(v)) { const Point2 p = v->to_point2(); return json::array({p.x, p.y}); }
    if (is_color(v)) {
        const AColor c = v->to_acolor();
        return c.a == 1.0f ? json::array({c.r, c.g, c.b}) : json::array({c.r, c.g, c.b, c.a});
    }
    if (is_string(v)) return WideToUtf8(v->to_string());
    if (is_name(v)) return "#" + WideToUtf8(v->to_string());
    if (is_node(v)) return RefJson(v->to_node());
    if (is_matrix3(v)) {
        const Matrix3 m = v->to_matrix3();
        return {{"matrix3", json::array({Point3Json(m.GetRow(0)), Point3Json(m.GetRow(1)), Point3Json(m.GetRow(2)), Point3Json(m.GetRow(3))})}};
    }
    if (is_array(v) && depth < 4) {
        json items = json::array();
        Array* array = static_cast<Array*>(v);
        for (int i = 0; i < array->size && i < 4096; ++i) items.push_back(FromMxs(array->data[i], depth + 1));
        return items;
    }
    if (v->is_kind_of(class_tag(MAXWrapper)) && !is_subAnim(v)) {
        if (ReferenceTarget* target = static_cast<MAXWrapper*>(v)->get_max_object()) return RefJson(target);
    }
    // Anything else is shown, never handed back as if it were a settable value.
    return {{"shown", Shown(v)}};
}

// The MAXScript value for `value`, shaped by the property's current value
// when there is one: a color stays a color, a name stays a name.
inline Value* ToMxs(const json& value, Value* current, const std::string& what) {
    if (value.is_null()) return &undefined;
    if (value.is_boolean()) return value.get<bool>() ? &true_value : &false_value;
    const bool currentColor = current && is_color(current);
    if (IsString(value)) {
        const std::string text = value.get<std::string>();
        if (currentColor && !text.empty() && text[0] == '#') {
            unsigned r = 0, g = 0, b = 0, a = 255;
            const int n = std::sscanf(text.c_str() + 1, "%02x%02x%02x%02x", &r, &g, &b, &a);
            if ((n == 3 && text.size() == 7) || (n == 4 && text.size() == 9))
                return new ColorValue(r / 255.0f, g / 255.0f, b / 255.0f, a / 255.0f);
            Fail(what + " expects a color as [r, g, b] (0-1) or \"#rrggbb\", got " + value.dump());
        }
        if ((current && is_name(current)) || (text.size() > 1 && text[0] == '#'))
            return Name::intern(Utf8ToWide(text[0] == '#' ? text.substr(1) : text).c_str());
        return new String(Utf8ToWide(text).c_str());
    }
    if (value.is_number_integer() && !(current && (is_float(current) || is_double(current))))
        return Integer::intern(value.get<int>());
    if (IsNumber(value)) return Float::intern(Number(value, what));
    if (value.is_object() && value.contains("matrix3")) {
        const json& rows = value["matrix3"];
        if (!IsArray(rows) || rows.size() != 4) Fail(what + ": matrix3 takes four rows of three numbers");
        Matrix3 m;
        for (int r = 0; r < 4; ++r) m.SetRow(r, PointOf(rows[r], what));
        m.ValidateFlags();
        return new Matrix3Value(m);
    }
    if (value.is_object() && (value.contains("handle") || value.contains("name")) && current && is_node(current)) {
        INode* node = nullptr;
        if (value.contains("handle")) node = HandlerHelpers::FindNodeByHandle(value["handle"].get<unsigned long long>());
        else node = HandlerHelpers::FindUniqueNodeByName(value["name"].get<std::string>());
        if (!node) Fail(what + ": no object " + value.dump());
        return MAXNode::intern(node);
    }
    if (IsArray(value)) {
        if (currentColor) {
            const auto c = Numbers(value, 3, 4, what);
            return new ColorValue(c[0], c[1], c[2], c.size() > 3 ? c[3] : 1.0f);
        }
        if (value.size() == 2 && ((current && is_point2(current)) || !current)) {
            const auto xs = Numbers(value, 2, 2, what);
            return new Point2Value(Point2(xs[0], xs[1]));
        }
        if (value.size() == 3) return new Point3Value(PointOf(value, what));
    }
    Fail(what + ": cannot pass " + value.dump() + " to 3ds Max" +
         (current ? " (the property holds " + Shown(current) + ")" : std::string()));
}

// The property's value, or nullopt when the owner has no such MAXScript property.
inline std::optional<json> MxsGet(Animatable* owner, const std::string& name, TimeValue t) {
    HandlerHelpers::RequireMaxScriptThread();
    ReferenceTarget* ref = AsRefTarget(owner);
    if (!ref) return std::nullopt;
    ScopedMaxScriptEvaluationContext context;
    MxsAt time(t);
    three_value_locals(wrapper, property, result);
    std::optional<json> out;
    try {
        vl.wrapper = MAXClass::make_wrapper_for(ref);
        vl.property = Name::intern(Utf8ToWide(name).c_str());
        vl.result = vl.wrapper->_get_property(vl.property);
        out = FromMxs(vl.result);
    } catch (...) {
        out.reset();
    }
    return out;
}

// False when the owner has no such property; a refused value fails.
inline bool MxsSet(Animatable* owner, const std::string& name, const json& value, TimeValue t) {
    HandlerHelpers::RequireMaxScriptThread();
    ReferenceTarget* ref = AsRefTarget(owner);
    if (!ref) return false;
    ScopedMaxScriptEvaluationContext context;
    MxsAt time(t);
    four_value_locals(wrapper, property, current, input);
    try {
        vl.wrapper = MAXClass::make_wrapper_for(ref);
        vl.property = Name::intern(Utf8ToWide(name).c_str());
        vl.current = vl.wrapper->_get_property(vl.property);
    } catch (...) {
        return false;
    }
    vl.input = ToMxs(value, vl.current, name);
    try {
        vl.wrapper->_set_property(vl.property, vl.input);
    } catch (const MAXScriptException& e) {
        StringStream* text = new StringStream(0);
        e.sprin1(text);
        std::string message = WideToUtf8(text->to_string());
        while (!message.empty() && std::isspace((unsigned char)message.back())) message.pop_back();
        if (message.rfind("-- ", 0) == 0) message.erase(0, 3);
        Fail("3ds Max refused " + name + " = " + value.dump() + ": " + message);
    } catch (...) {
        Fail("3ds Max refused " + name + " = " + value.dump());
    }
    return true;
}

inline std::vector<std::string> MxsNames(Animatable* owner) {
    std::vector<std::string> names;
    ReferenceTarget* ref = AsRefTarget(owner);
    if (!ref) return names;
    HandlerHelpers::RequireMaxScriptThread();
    ScopedMaxScriptEvaluationContext context;
    two_value_locals(wrapper, result);
    try {
        vl.wrapper = MAXClass::make_wrapper_for(ref);
        vl.result = vl.wrapper->get_props_vf(nullptr, 0);
        if (vl.result && is_array(vl.result)) {
            Array* array = static_cast<Array*>(vl.result);
            for (int i = 0; i < array->size; ++i) names.push_back(WideToUtf8(array->data[i]->to_string()));
        }
    } catch (...) {
        names.clear();
    }
    return names;
}

// ── Names of an owner ────────────────────────────────────────────

// ParamBlock2 names, then transform/point subanims, then other MAXScript properties.
inline std::vector<std::string> AllNames(Animatable* owner) {
    std::vector<std::string> names;
    auto known = [&](const std::string& n) {
        return std::any_of(names.begin(), names.end(), [&](const std::string& m) { return _stricmp(m.c_str(), n.c_str()) == 0; });
    };
    ForEach(owner, [&](IParamBlock2* block, const ParamDef& def) { names.push_back(Name(block, def)); });
    for (const auto& [name, control] : SubControls(owner)) if (!known(name)) names.push_back(name);
    for (const std::string& name : MxsNames(owner)) if (!known(name)) names.push_back(name);
    return names;
}

[[noreturn]] inline void FailUnknown(Animatable* owner, const std::string& name) {
    const std::vector<std::string> names = AllNames(owner);
    const std::string label = OwnerLabel(owner);
    json hint = {{"parameters", names}};
    if (names.empty()) throw Error("BAD_PARAM", label + " has no parameters", hint, true);
    std::string message = label + " has no parameter '" + name + "'.";
    if (const std::string closest = Closest(name, names); !closest.empty()) {
        message += " Did you mean '" + closest + "'?";
        hint["did_you_mean"] = closest;
    }
    throw Error("BAD_PARAM", message + " Parameters: " + JoinNames(names), hint, true);
}

// ── Setting values ───────────────────────────────────────────────

// A class by its MAXScript global (ai_standard_surface, scripted plugins): some
// plugins register under a different name in the class directory. `supers`
// empty takes any superclass.
inline ClassDesc* ScriptClass(const std::string& name, const std::vector<SClass_ID>& supers) {
    HandlerHelpers::RequireMaxScriptThread();
    ScopedMaxScriptEvaluationContext context;
    Value* value = globals->get(Name::intern(Utf8ToWide(name).c_str()));
    while (value && (is_globalthunk(value) || is_constglobalthunk(value))) value = static_cast<GlobalThunk*>(value)->cell;
    if (!value || !is_maxclass(value)) return nullptr;
    auto* cls = static_cast<MAXClass*>(value);
    if (!supers.empty() && std::find(supers.begin(), supers.end(), cls->sclass_id) == supers.end()) return nullptr;
    ClassDesc* cd = cls->cd ? cls->cd : GetCOREInterface()->GetDllDir().ClassDir().FindClass(cls->sclass_id, cls->class_id);
    return cd && cd->SuperClassID() == cls->sclass_id ? cd : nullptr;
}

inline ClassDesc* ClassFor(const std::string& name, std::initializer_list<SClass_ID> supers, const std::string& what) {
    for (SClass_ID super : supers)
        if (ClassDesc* cd = HandlerHelpers::FindClassDescByName(name, super)) return cd;
    if (ClassDesc* cd = ScriptClass(name, std::vector<SClass_ID>(supers))) return cd;
    Fail(what + ": no class '" + name + "' of the kind this slot takes");
}

inline void Apply(Animatable* owner, const json& params, TimeValue t, json* readback = nullptr, json* warnings = nullptr);

// A map, material or other reference: null, {"handle": n} for an existing one,
// or {"class": ..., "name": ..., "params": {...}} for a new one.
inline ReferenceTarget* RefOf(const json& value, int type, SClass_ID declared, TimeValue t, const std::string& what, json* warnings) {
    if (value.is_null()) return nullptr;
    std::initializer_list<SClass_ID> texmap{TEXMAP_CLASS_ID}, material{MATERIAL_CLASS_ID},
        any{TEXMAP_CLASS_ID, MATERIAL_CLASS_ID, GEOMOBJECT_CLASS_ID, SHAPE_CLASS_ID, OSM_CLASS_ID, CTRL_FLOAT_CLASS_ID};
    const SClass_ID wanted = type == TYPE_TEXMAP ? TEXMAP_CLASS_ID : type == TYPE_MTL ? MATERIAL_CLASS_ID
                           : (declared && declared != REF_TARGET_CLASS_ID ? declared : 0);
    if (value.is_object() && value.contains("handle") && !value.contains("class")) {
        const json& h = value["handle"];
        Animatable* anim = h.is_number_unsigned() || h.is_number_integer()
            ? Animatable::GetAnimByHandle((AnimHandle)h.get<unsigned long long>()) : nullptr;
        ReferenceTarget* target = anim ? AsRefTarget(anim) : nullptr;
        if (!target) Fail(what + ": nothing with handle " + h.dump());
        if (wanted && target->SuperClassID() != wanted)
            Fail(what + ": handle " + h.dump() + " is " + HandlerHelpers::MaxScriptVisibleClassName(target) + ", not the kind this slot takes");
        return target;
    }
    if (!value.is_object() || !value.contains("class") || !IsString(value["class"]))
        Fail(what + " takes null, {\"handle\": n}, {\"class\": \"...\", \"params\": {...}} for a new one, or "
             "{\"params\": {...}} to edit the one it holds; got " + value.dump());
    for (auto it = value.begin(); it != value.end(); ++it)
        if (it.key() != "class" && it.key() != "name" && it.key() != "params")
            Fail(what + ": unknown key '" + it.key() + "'; a new reference takes class, name and params");
    const std::string cls = value["class"].get<std::string>();
    ClassDesc* cd = type == TYPE_TEXMAP ? ClassFor(cls, texmap, what)
                  : type == TYPE_MTL ? ClassFor(cls, material, what)
                  : wanted ? ClassFor(cls, {wanted}, what) : ClassFor(cls, any, what);
    auto* created = static_cast<ReferenceTarget*>(GetCOREInterface()->CreateInstance(cd->SuperClassID(), cd->ClassID()));
    if (!created) Fail(what + ": 3ds Max could not create " + cls);
    try {
        if (value.contains("name") && IsString(value["name"]) &&
            (created->SuperClassID() == TEXMAP_CLASS_ID || created->SuperClassID() == MATERIAL_CLASS_ID))
            static_cast<MtlBase*>(created)->SetName(Utf8ToWide(value["name"].get<std::string>()).c_str());
        if (value.contains("params")) Apply(created, value["params"], t, nullptr, warnings);
    } catch (...) {
        created->MaybeAutoDelete();
        throw;
    }
    return created;
}

inline INode* NodeOf(const json& value, const std::string& what) {
    if (value.is_null()) return nullptr;
    INode* node = nullptr;
    if (value.is_number_unsigned() || value.is_number_integer()) node = HandlerHelpers::FindNodeByHandle(value.get<unsigned long long>());
    else if (IsString(value)) node = HandlerHelpers::FindUniqueNodeByName(value.get<std::string>());
    else if (value.is_object() && value.contains("handle")) node = HandlerHelpers::FindNodeByHandle(value["handle"].get<unsigned long long>());
    else if (value.is_object() && value.contains("name")) node = HandlerHelpers::FindUniqueNodeByName(value["name"].get<std::string>());
    else Fail(what + " takes an object name, handle or null, got " + value.dump());
    if (!node) Fail(what + ": no object " + value.dump());
    return node;
}

inline std::array<float, 4> ColorOf(const json& value, const std::string& what) {
    if (IsString(value)) {
        const std::string text = value.get<std::string>();
        unsigned r = 0, g = 0, b = 0, a = 255;
        const int n = text.size() > 1 && text[0] == '#' ? std::sscanf(text.c_str() + 1, "%02x%02x%02x%02x", &r, &g, &b, &a) : 0;
        if ((n == 3 && text.size() == 7) || (n == 4 && text.size() == 9)) return {r / 255.0f, g / 255.0f, b / 255.0f, a / 255.0f};
        Fail(what + " expects a color as [r, g, b] (0-1 floats) or \"#rrggbb\", got " + value.dump());
    }
    const auto c = Numbers(value, 3, 4, what + " (a color: [r, g, b] 0-1 floats, or \"#rrggbb\")");
    return {c[0], c[1], c[2], c.size() > 3 ? c[3] : 1.0f};
}

inline int IntOf(const json& value, const std::optional<Choices>& choices, const std::string& what) {
    if (IsString(value) && choices) {
        if (auto v = ChoiceValue(*choices, value.get<std::string>())) return *v;
        Fail(what + " has no choice '" + value.get<std::string>() + "'. Choices: " + ChoiceNames(*choices));
    }
    if (IsString(value)) Fail(what + " expects an int; it publishes no named choices, got " + value.dump());
    const float f = Number(value, what);
    if (std::floor(f) != f) Fail(what + " expects a whole number, got " + value.dump());
    return (int)f;
}

inline void Warn(json* warnings, const std::string& text) {
    if (warnings) warnings->push_back(text);
}

inline void CheckRange(const ParamDef& def, double external, const std::string& what, json* warnings) {
    if (!(def.flags & P_HAS_RANGE)) return;
    const int type = BaseType(def.type);
    double lo = 0, hi = 0;
    if (FloatKind(type)) { lo = def.range_low.f * Scale(type); hi = def.range_high.f * Scale(type); }
    else if (IntKind(type)) { lo = def.range_low.i; hi = def.range_high.i; }
    else return;
    if (lo < hi && (external < lo - 1e-6 || external > hi + 1e-6)) {
        char text[160];
        std::snprintf(text, sizeof text, " = %g is outside its declared range [%g, %g]; check the readback", external, lo, hi);
        Warn(warnings, what + text);
    }
}

inline void SetOne(Animatable* owner, const Ref& ref, const json& value, TimeValue t, int index, const std::string& what, json* warnings) {
    IParamBlock2* pb = ref.block;
    const ParamID id = ref.def->ID;
    const int type = BaseType(ref.def->type);
    BOOL ok = FALSE;
    switch (type) {
    case TYPE_FLOAT: case TYPE_WORLD: case TYPE_COLOR_CHANNEL: case TYPE_ANGLE: case TYPE_PCNT_FRAC: {
        const float v = Number(value, what);
        CheckRange(*ref.def, v, what, warnings);
        ok = pb->SetValue(id, t, float(v / Scale(type)), index);
        break;
    }
    case TYPE_INT: case TYPE_INDEX: case TYPE_RADIOBTN_INDEX: {
        ParamBlockDesc2* desc = pb->GetDesc();
        const auto choices = ChoicesOf(desc ? desc->cd : nullptr, desc, *ref.def, Name(pb, *ref.def));
        const int v = IntOf(value, choices, what);
        if (choices && choices->complete && !IsString(value) &&
            std::none_of(choices->items.begin(), choices->items.end(), [&](const Choice& c) { return c.value == v; }))
            Fail(what + " = " + std::to_string(v) + " is not one of its choices: " + ChoiceNames(*choices));
        if (!choices) CheckRange(*ref.def, v, what, warnings);
        ok = pb->SetValue(id, t, v, index);
        break;
    }
    case TYPE_BOOL: {
        if (!value.is_boolean() && !(value.is_number_integer() && (value.get<int>() == 0 || value.get<int>() == 1)))
            Fail(what + " expects true or false, got " + value.dump());
        ok = pb->SetValue(id, t, (value.is_boolean() ? value.get<bool>() : value.get<int>() != 0) ? 1 : 0, index);
        break;
    }
    case TYPE_TIMEVALUE: ok = pb->SetValue(id, t, (int)std::lround(Number(value, what) * GetTicksPerFrame()), index); break;
    case TYPE_POINT3: { Point3 p = PointOf(value, what); ok = pb->SetValue(id, t, p, index); break; }
    case TYPE_POINT4: {
        const auto xs = Numbers(value, 4, 4, what);
        Point4 p(xs[0], xs[1], xs[2], xs[3]);
        ok = pb->SetValue(id, t, p, index);
        break;
    }
    case TYPE_RGBA: case TYPE_FRGBA: {
        const auto c = ColorOf(value, what);
        if (IsArray(value) && std::max({c[0], c[1], c[2]}) > 1.0f &&
            std::all_of(value.begin(), value.end(), [](const json& x) { return x.is_number_integer(); }))
            Warn(warnings, what + " = " + value.dump() + " was set as given; colors are 0-1 floats, so this looks like 0-255 "
                 "(pass [r/255, g/255, b/255] or \"#rrggbb\")");
        if (type == TYPE_RGBA) { Color v(c[0], c[1], c[2]); ok = pb->SetValue(id, t, v, index); }
        else { AColor v(c[0], c[1], c[2], c[3]); ok = pb->SetValue(id, t, v, index); }
        break;
    }
    case TYPE_STRING: case TYPE_FILENAME: {
        if (!IsString(value)) Fail(what + " expects a string, got " + value.dump());
        const std::wstring text = Utf8ToWide(value.get<std::string>());
        ok = pb->SetValue(id, t, text.c_str(), index);
        break;
    }
    case TYPE_INODE: ok = pb->SetValue(id, t, NodeOf(value, what), index); break;
    case TYPE_TEXMAP: case TYPE_MTL: case TYPE_REFTARG: {
        if (value.is_object() && value.contains("params") && !value.contains("class") && !value.contains("handle")) {
            // {"params": {...}} edits what the slot already holds.
            for (auto it = value.begin(); it != value.end(); ++it)
                if (it.key() != "params") Fail(what + ": {\"params\": ...} edits the slot's current " + std::string(TypeName(type)) + "; '" + it.key() + "' does not belong there");
            Interval valid = FOREVER;
            ReferenceTarget* current = nullptr;
            if (type == TYPE_TEXMAP) { Texmap* v = nullptr; pb->GetValue(id, t, v, valid, index); current = v; }
            else if (type == TYPE_MTL) { Mtl* v = nullptr; pb->GetValue(id, t, v, valid, index); current = v; }
            else { ReferenceTarget* v = nullptr; pb->GetValue(id, t, v, valid, index); current = v; }
            if (!current) Fail(what + " is empty; build one with {\"class\": \"...\", \"params\": {...}}");
            Apply(current, value["params"], t, nullptr, warnings);
            return;
        }
        ReferenceTarget* target = RefOf(value, type, ref.def->sclass_ID, t, what, warnings);
        if (target && target->TestForLoop(FOREVER, pb) != REF_SUCCEED) {
            Fail(what + ": " + value.dump() + " already contains this object, so it cannot go in this slot");
        }
        if (type == TYPE_TEXMAP) ok = pb->SetValue(id, t, static_cast<Texmap*>(target), index);
        else if (type == TYPE_MTL) ok = pb->SetValue(id, t, static_cast<Mtl*>(target), index);
        else ok = pb->SetValue(id, t, target, index);
        if (!ok && target && value.is_object() && value.contains("class")) target->MaybeAutoDelete();
        break;
    }
    default:
        Fail(what + " is " + std::string(TypeName(type)) + " parameter this bridge cannot set yet");
    }
    if (!ok) Fail("3ds Max refused " + what + " = " + value.dump());
}

inline void SetPB2(Animatable* owner, const Ref& ref, const json& value, TimeValue t, json* warnings) {
    const std::string what = Name(ref.block, *ref.def);
    if (ref.def->flags & P_READ_ONLY) Fail(what + " is read-only");
    if (!(ref.def->type & TYPE_TAB)) return SetOne(owner, ref, value, t, 0, what, warnings);
    if (!IsArray(value)) Fail(what + " is a list parameter; pass a list");
    // Resizing drops or adds entries; a list of the same length keeps them in place.
    if (ref.block->Count(ref.def->ID) != (int)value.size()) ref.block->SetCount(ref.def->ID, (int)value.size());
    for (size_t i = 0; i < value.size(); ++i) SetOne(owner, ref, value[i], t, (int)i, what + "[" + std::to_string(i) + "]", warnings);
}

// One parameter by name; Error with the owner's parameter names when there is none.
inline void Set(Animatable* owner, const std::string& name, const json& value, TimeValue t, json* warnings = nullptr) {
    if (auto ref = FindPB2(owner, name)) return SetPB2(owner, *ref, value, t, warnings);
    if (Control* c = FindSubControl(owner, name)) return SubControlSet(c, value, t, name);
    if (MxsSet(owner, name, value, t)) return;
    FailUnknown(owner, name);
}

inline json Get(Animatable* owner, const std::string& name, TimeValue t) {
    if (auto ref = FindPB2(owner, name)) return GetPB2(*ref, t);
    if (Control* c = FindSubControl(owner, name)) return SubControlGet(c, t);
    if (auto value = MxsGet(owner, name, t)) return *value;
    FailUnknown(owner, name);
}

// Every name is checked before the first change, so an unknown name changes nothing.
inline void Apply(Animatable* owner, const json& params, TimeValue t, json* readback, json* warnings) {
    if (params.is_null()) return;
    if (!params.is_object()) Fail("params must be an object of {name: value}, got " + params.dump());
    std::vector<std::string> unknown;
    for (auto it = params.begin(); it != params.end(); ++it)
        if (!FindPB2(owner, it.key()) && !FindSubControl(owner, it.key()) && !MxsGet(owner, it.key(), t)) unknown.push_back(it.key());
    if (!unknown.empty()) FailUnknown(owner, unknown.front());
    for (auto it = params.begin(); it != params.end(); ++it) Set(owner, it.key(), it.value(), t, warnings);
    if (auto* ref = AsRefTarget(owner)) ref->NotifyDependents(FOREVER, PART_ALL, REFMSG_CHANGE);
    if (readback) {
        if (!readback->is_object()) *readback = json::object();
        for (auto it = params.begin(); it != params.end(); ++it) (*readback)[it.key()] = Get(owner, it.key(), t);
    }
}

// ── Describing ───────────────────────────────────────────────────

inline json DefaultJson(int type, const PB2Value& v) {
    switch (BaseType(type)) {
    case TYPE_FLOAT: case TYPE_WORLD: case TYPE_ANGLE: case TYPE_PCNT_FRAC: case TYPE_COLOR_CHANNEL: return v.f * Scale(type);
    case TYPE_INT: case TYPE_INDEX: case TYPE_RADIOBTN_INDEX: return v.i;
    case TYPE_BOOL: return v.i != 0;
    case TYPE_TIMEVALUE: return double(v.i) / GetTicksPerFrame();
    case TYPE_POINT3: return v.p ? Point3Json(*v.p) : json(nullptr);
    case TYPE_RGBA: return v.p ? json::array({v.p->x, v.p->y, v.p->z}) : json(nullptr);
    case TYPE_POINT4: case TYPE_FRGBA: return v.p4 ? json::array({v.p4->x, v.p4->y, v.p4->z, v.p4->w}) : json(nullptr);
    case TYPE_STRING: case TYPE_FILENAME: return v.s ? json(WideToUtf8(v.s)) : json(nullptr);
    default: return nullptr;
    }
}

// One ParamBlock2 parameter's description; the live value when a block is given.
inline json DescribeDef(ClassDesc* cd, ParamBlockDesc2* desc, const ParamDef& def, IParamBlock2* block, TimeValue t) {
    const int type = BaseType(def.type);
    const std::string name = block ? Name(block, def) : WideToUtf8(def.int_name);
    json entry = {{"name", name}, {"type", TypeName(type)}, {"source", "paramblock2"}};
    if (def.type & TYPE_TAB) entry["list"] = true;
    if (desc && def.local_name) {
        const MCHAR* label = desc->GetString(def.local_name);
        if (label && *label && Folded(WideToUtf8(label)) != Folded(name)) entry["label"] = WideToUtf8(label);
    }
    if (block) entry["value"] = GetPB2(Ref{block, &def}, t);
    if (def.flags & P_HAS_DEFAULT) {
        const json d = DefaultJson(type, def.def);
        if (!d.is_null()) entry["default"] = d;
    }
    if (def.flags & P_HAS_RANGE) {
        if (FloatKind(type)) entry["range"] = json::array({def.range_low.f * Scale(type), def.range_high.f * Scale(type)});
        else if (IntKind(type)) entry["range"] = json::array({def.range_low.i, def.range_high.i});
    }
    if (auto choices = ChoicesOf(cd, desc, def, name)) {
        entry["choices"] = ChoicesJson(*choices);
        entry["choices_source"] = choices->source;
        if (!choices->complete) entry["choices_complete"] = false;
        if (block && entry["value"].is_number_integer())
            for (const Choice& c : choices->items)
                if (c.value == entry["value"].get<int>()) entry["choice"] = c.name;
    }
    if (type == TYPE_REFTARG && def.sclass_ID && def.sclass_ID != REF_TARGET_CLASS_ID) {
        MAXSuperClass* super = MAXSuperClass::lookup_superclass(def.sclass_ID);
        if (super && super->name) entry["takes"] = WideToUtf8(super->name->to_string());
    }
    if (def.flags & P_ANIMATABLE) entry["animatable"] = true;
    if (def.flags & P_READ_ONLY) entry["read_only"] = true;
    return entry;
}

// The JSON type of a MAXScript property's value.
inline const char* ValueType(const json& v) {
    if (v.is_null()) return "undefined";
    if (v.is_boolean()) return "bool";
    if (v.is_number_integer()) return "int";
    if (IsNumber(v)) return "float";
    if (IsString(v)) return v.get<std::string>().rfind('#', 0) == 0 ? "name" : "string";
    if (IsArray(v)) {
        if (v.size() == 3 && std::all_of(v.begin(), v.end(), [](const json& x) { return IsNumber(x); })) return "point3";
        if (v.size() == 2 && std::all_of(v.begin(), v.end(), [](const json& x) { return IsNumber(x); })) return "point2";
        return "list";
    }
    if (v.is_object()) {
        if (v.contains("matrix3")) return "matrix3";
        if (v.contains("shown")) return "value";
        if (v.contains("layer")) return "node";
        if (v.contains("class")) return "reference";
    }
    return "value";
}

struct DescribeOptions {
    std::string query;    // only names (or labels) containing this text
    size_t offset = 0;
    size_t limit = 400;
};

inline bool Wanted(const DescribeOptions& o, const json& entry) {
    if (o.query.empty()) return true;
    const std::string q = Folded(o.query);
    if (Folded(entry["name"].get<std::string>()).find(q) != std::string::npos) return true;
    return entry.contains("label") && Folded(entry["label"].get<std::string>()).find(q) != std::string::npos;
}

// Every parameter of a live owner: ParamBlock2 parameters with types, values,
// defaults, ranges and choices; transform and point subanims; then the other
// MAXScript properties with the type of their current value.
inline json Describe(Animatable* owner, TimeValue t, const DescribeOptions& o = {}) {
    json entries = json::array();
    std::vector<std::string> seen;
    auto known = [&](const std::string& n) {
        return std::any_of(seen.begin(), seen.end(), [&](const std::string& m) { return _stricmp(m.c_str(), n.c_str()) == 0; });
    };
    ClassDesc* cd = GetCOREInterface()->GetDllDir().ClassDir().FindClass(owner->SuperClassID(), owner->ClassID());
    HandlerHelpers::RequireMaxScriptThread();
    ScopedMaxScriptEvaluationContext context;
    int blocks = 0;
    for (int b = 0; b < owner->NumParamBlocks(); ++b) {
        IParamBlock2* block = owner->GetParamBlock(b);
        ParamBlockDesc2* desc = block ? block->GetDesc() : nullptr;
        if (!desc) continue;
        ++blocks;
        for (int i = 0; i < desc->count; ++i) {
            const ParamDef& def = desc->GetParamDef(desc->IndextoID(i));
            if (!def.int_name || !*def.int_name) continue;
            json entry = DescribeDef(cd, desc, def, block, t);
            seen.push_back(entry["name"].get<std::string>());
            seen.push_back(WideToUtf8(def.int_name));
            if (Wanted(o, entry)) entries.push_back(std::move(entry));
        }
    }
    for (const auto& [name, control] : SubControls(owner)) {
        if (known(name)) continue;
        seen.push_back(name);
        json entry = {{"name", name}, {"type", control->SuperClassID() == CTRL_MATRIX3_CLASS_ID ? "transform" : "point3"},
                      {"source", "subanim"}, {"value", SubControlGet(control, t)}, {"animatable", true},
                      {"controller", WideToUtf8(control->ClassName().data())}};
        if (Wanted(o, entry)) entries.push_back(std::move(entry));
    }
    size_t properties = 0;
    for (const std::string& name : MxsNames(owner)) {
        if (known(name) || FindSubControl(owner, name)) continue;
        seen.push_back(name);
        ++properties;
        const std::optional<json> value = MxsGet(owner, name, t);
        json entry = {{"name", name}, {"type", value ? ValueType(*value) : "value"}, {"source", "maxscript"}};
        if (value) entry["value"] = *value;
        if (Wanted(o, entry)) entries.push_back(std::move(entry));
    }
    const size_t total = entries.size();
    json page = json::array();
    for (size_t i = o.offset; i < total && page.size() < o.limit; ++i) page.push_back(entries[i]);
    json out = {{"class", HandlerHelpers::MaxScriptVisibleClassName(owner)}, {"owner", OwnerLabel(owner)},
                {"handle", (unsigned long long)Animatable::GetHandleByAnim(owner)},
                {"params", page}, {"total", total}};
    if (o.offset + page.size() < total) out["next_offset"] = o.offset + page.size();
    out["coverage"] = {{"paramblock2_blocks", blocks}, {"maxscript_properties", properties},
                       {"units", "distances in scene units, angles in degrees, percentages 0-100, colors 0-1, times in frames"}};
    return out;
}

// Classes it is safe to create without a node, to list properties only an instance shows.
inline bool ProbeableKind(SClass_ID sid) {
    return sid == OSM_CLASS_ID || sid == WSM_CLASS_ID || sid == GEOMOBJECT_CLASS_ID || sid == SHAPE_CLASS_ID ||
           sid == MATERIAL_CLASS_ID || sid == TEXMAP_CLASS_ID || sid == HELPER_CLASS_ID || sid == LIGHT_CLASS_ID ||
           sid == CAMERA_CLASS_ID;
}

// A class's parameters without a scene instance. For the kinds it is safe to
// create without a node, a temporary instance is described (so aliases,
// old-style blocks, gizmos and MAXScript-only properties appear) with its
// values reported as defaults; otherwise the declared ParamBlock2 parameters.
inline json DescribeClass(ClassDesc* cd, TimeValue t, const DescribeOptions& o = {}) {
    HandlerHelpers::RequireMaxScriptThread();
    ScopedMaxScriptEvaluationContext context;
    Animatable* instance = nullptr;
    if (ProbeableKind(cd->SuperClassID()) && cd->IsPublic()) {
        try {
            instance = static_cast<Animatable*>(GetCOREInterface()->CreateInstance(cd->SuperClassID(), cd->ClassID()));
        } catch (...) {
            instance = nullptr;
        }
    }
    json out;
    if (instance) {
        struct Discard {
            Animatable* anim;
            ~Discard() {
                if (auto* ref = AsRefTarget(anim)) ref->MaybeAutoDelete();
                else anim->DeleteThis();
            }
        } discard{instance};
        out = Describe(instance, t, o);
        for (json& entry : out["params"]) {
            if (!entry.contains("value")) continue;
            if (!entry.contains("default")) entry["default"] = entry["value"];
            entry.erase("value");
            entry.erase("choice");
        }
        out.erase("handle");
        out.erase("owner");
        out["instance_probed"] = true;
    } else {
        json entries = json::array();
        for (int b = 0; b < cd->NumParamBlockDescs(); ++b) {
            ParamBlockDesc2* desc = cd->GetParamBlockDesc(b);
            for (int i = 0; desc && i < desc->count; ++i) {
                const ParamDef& def = desc->GetParamDef(desc->IndextoID(i));
                if (!def.int_name || !*def.int_name) continue;
                json entry = DescribeDef(cd, desc, def, nullptr, t);
                if (Wanted(o, entry)) entries.push_back(std::move(entry));
            }
        }
        const size_t total = entries.size();
        json page = json::array();
        for (size_t i = o.offset; i < total && page.size() < o.limit; ++i) page.push_back(entries[i]);
        out = {{"params", page}, {"total", total}, {"instance_probed", false}};
        if (o.offset + page.size() < total) out["next_offset"] = o.offset + page.size();
        out["coverage"] = {{"note", "declared ParamBlock2 parameters only; an instance may add MAXScript properties and subanims"}};
    }
    out["class"] = HandlerHelpers::MaxScriptClassName(cd);
    return out;
}

// ── Owner paths ──────────────────────────────────────────────────
// What a parameter belongs to, as MAXScript would reach it from the node:
//   object (default)            the base object, below every modifier
//   modifiers[Bend]             a modifier by name, then by class; modifiers[1] is the top one
//   material, material[2]       the node's material, a sub-material (1-based) or by name
//   .<name>                     a map/material/reference parameter's value, or a subanim
//                               (material.base_color_map, modifiers[Bend].gizmo, transform.position)
//   [n] after a list            its nth element (1-based)
//   transform                   the node's transform controller; node: the node itself

inline Object* BaseObjectOf(INode* node) {
    Object* object = node->GetObjectRef();
    while (object && object->SuperClassID() == GEN_DERIVOB_CLASS_ID) object = static_cast<IDerivedObject*>(object)->GetObjRef();
    return object;
}

// Object-space modifiers, top of the stack first (as the modifier panel lists them).
inline std::vector<Modifier*> ModifierStack(INode* node) {
    std::vector<Modifier*> mods;
    Object* object = node->GetObjectRef();
    while (object && object->SuperClassID() == GEN_DERIVOB_CLASS_ID) {
        auto* derived = static_cast<IDerivedObject*>(object);
        for (int i = 0; i < derived->NumModifiers(); ++i) mods.push_back(derived->GetModifier(i));
        object = derived->GetObjRef();
    }
    return mods;
}

inline std::string Unquote(std::string key) {
    while (!key.empty() && std::isspace((unsigned char)key.front())) key.erase(key.begin());
    while (!key.empty() && std::isspace((unsigned char)key.back())) key.pop_back();
    if (key.size() >= 2 && (key.front() == '"' || key.front() == '\'') && key.back() == key.front()) key = key.substr(1, key.size() - 2);
    else if (!key.empty() && key.front() == '#') key.erase(key.begin());
    return key;
}

inline bool WholeNumber(const std::string& key, int& out) {
    if (key.empty() || key.find_first_not_of("0123456789") != std::string::npos || key.size() > 6) return false;
    out = std::stoi(key);
    return true;
}

inline Modifier* PickModifier(INode* node, const std::string& key) {
    const std::vector<Modifier*> stack = ModifierStack(node);
    std::vector<std::string> listing;
    for (size_t i = 0; i < stack.size(); ++i)
        listing.push_back(std::to_string(i + 1) + ": " + WideToUtf8(stack[i]->GetName(false).data()) + " (" +
                          HandlerHelpers::MaxScriptVisibleClassName(stack[i]) + ")");
    const std::string nodeName = WideToUtf8(node->GetName());
    if (stack.empty()) Fail("'" + nodeName + "' has no modifiers");
    int index = 0;
    if (WholeNumber(key, index)) {
        if (index < 1 || index > (int)stack.size())
            Fail("'" + nodeName + "' has no modifiers[" + key + "]; its stack, top first: " + JoinNames(listing));
        return stack[index - 1];
    }
    std::vector<Modifier*> byName, byClass;
    for (Modifier* mod : stack) {
        if (_stricmp(WideToUtf8(mod->GetName(false).data()).c_str(), key.c_str()) == 0) byName.push_back(mod);
        else if (_stricmp(HandlerHelpers::MaxScriptVisibleClassName(mod).c_str(), key.c_str()) == 0 ||
                 _wcsicmp(mod->ClassName().data(), Utf8ToWide(key).c_str()) == 0)
            byClass.push_back(mod);
    }
    const std::vector<Modifier*>& found = byName.empty() ? byClass : byName;
    if (found.size() == 1) return found.front();
    if (found.empty()) Fail("'" + nodeName + "' has no modifier named or of class '" + key + "'; its stack, top first: " + JoinNames(listing));
    throw Error("AMBIGUOUS", "'" + nodeName + "' has several modifiers matching '" + key +
                "'; use modifiers[<position>]. Its stack, top first: " + JoinNames(listing));
}

inline Animatable* PickSubMaterial(Mtl* mtl, const std::string& key) {
    int index = 0;
    std::vector<std::string> listing;
    for (int i = 0; i < mtl->NumSubMtls(); ++i) {
        Mtl* sub = mtl->GetSubMtl(i);
        listing.push_back(std::to_string(i + 1) + ": " + (sub ? WideToUtf8(sub->GetName().data()) : std::string("(empty)")));
    }
    if (WholeNumber(key, index)) {
        if (index < 1 || index > mtl->NumSubMtls() || !mtl->GetSubMtl(index - 1))
            Fail(OwnerLabel(mtl) + " has no sub-material [" + key + "]; sub-materials: " + (listing.empty() ? "none" : JoinNames(listing)));
        return mtl->GetSubMtl(index - 1);
    }
    for (int i = 0; i < mtl->NumSubMtls(); ++i)
        if (Mtl* sub = mtl->GetSubMtl(i))
            if (_stricmp(WideToUtf8(sub->GetName().data()).c_str(), key.c_str()) == 0) return sub;
    Fail(OwnerLabel(mtl) + " has no sub-material '" + key + "'; sub-materials: " + (listing.empty() ? "none" : JoinNames(listing)));
}

// The animatable `.name` reaches from owner: a reference parameter's value, else a subanim.
inline Animatable* Child(Animatable* owner, const std::string& name, std::optional<int> element, TimeValue t) {
    if (auto ref = FindPB2(owner, name)) {
        const int type = BaseType(ref->def->type);
        if (type != TYPE_TEXMAP && type != TYPE_MTL && type != TYPE_REFTARG && type != TYPE_INODE)
            Fail(name + " is " + TypeName(type) + " parameter, not a map, material or reference; describe " + OwnerLabel(owner) + " to read it");
        int index = 0;
        if (ref->def->type & TYPE_TAB) {
            const int count = ref->block->Count(ref->def->ID);
            if (!element) Fail(name + " is a list of " + std::to_string(count) + "; add [n] (1-based)");
            if (*element < 1 || *element > count) Fail(name + " has " + std::to_string(count) + " entries; [" + std::to_string(*element) + "] is out of range");
            index = *element - 1;
        } else if (element) {
            Fail(name + " is not a list");
        }
        ReferenceTarget* target = nullptr;
        Interval valid = FOREVER;
        if (type == TYPE_TEXMAP) { Texmap* v = nullptr; ref->block->GetValue(ref->def->ID, t, v, valid, index); target = v; }
        else if (type == TYPE_MTL) { Mtl* v = nullptr; ref->block->GetValue(ref->def->ID, t, v, valid, index); target = v; }
        else if (type == TYPE_INODE) { INode* v = nullptr; ref->block->GetValue(ref->def->ID, t, v, valid, index); target = v; }
        else { ReferenceTarget* v = nullptr; ref->block->GetValue(ref->def->ID, t, v, valid, index); target = v; }
        if (!target)
            Fail(OwnerLabel(owner) + "." + name + " is empty; set it first, e.g. {\"" + name + "\": {\"class\": \"Bitmaptexture\", \"params\": {...}}}");
        return target;
    }
    if (element && owner->SuperClassID() == MATERIAL_CLASS_ID) Fail("use material[n] for a sub-material");
    const std::string key = Folded(name);
    std::vector<std::string> subs;
    for (int i = 0; i < owner->NumSubs(); ++i) {
        const std::string subName = WideToUtf8(owner->SubAnimName(i, false).data());
        Animatable* sub = owner->SubAnim(i);
        if (!sub) continue;
        subs.push_back(subName);
        if (Folded(subName) == key) return sub;
    }
    std::vector<std::string> refs;
    ForEach(owner, [&](IParamBlock2* block, const ParamDef& def) {
        const int type = BaseType(def.type);
        if (type == TYPE_TEXMAP || type == TYPE_MTL || type == TYPE_REFTARG) refs.push_back(Name(block, def));
    });
    std::string message = OwnerLabel(owner) + " has no map, material or subanim '" + name + "'.";
    std::vector<std::string> all = refs;
    all.insert(all.end(), subs.begin(), subs.end());
    if (const std::string closest = Closest(name, all); !closest.empty()) message += " Did you mean '" + closest + "'?";
    if (!refs.empty()) message += " Reference parameters: " + JoinNames(refs) + ".";
    if (!subs.empty()) message += " Subanims: " + JoinNames(subs) + ".";
    Fail(message);
}

inline Animatable* ResolvePath(INode* node, const std::string& rawPath, TimeValue t) {
    std::string path = rawPath;
    while (!path.empty() && std::isspace((unsigned char)path.front())) path.erase(path.begin());
    while (!path.empty() && std::isspace((unsigned char)path.back())) path.pop_back();
    if (!path.empty() && path[0] == '$') {   // "$Box001.modifiers[1]": the node is given separately
        const size_t dot = path.find('.');
        path = dot == std::string::npos ? std::string() : path.substr(dot + 1);
    }
    size_t i = 0;
    auto ident = [&]() {
        const size_t start = i;
        while (i < path.size() && path[i] != '.' && path[i] != '[') ++i;
        std::string s = path.substr(start, i - start);
        while (!s.empty() && std::isspace((unsigned char)s.back())) s.pop_back();
        while (!s.empty() && std::isspace((unsigned char)s.front())) s.erase(s.begin());
        return s;
    };
    auto bracket = [&]() -> std::optional<std::string> {
        if (i >= path.size() || path[i] != '[') return std::nullopt;
        const size_t close = path.find(']', i);
        if (close == std::string::npos) Fail("path '" + rawPath + "' has an unclosed '['");
        std::string key = Unquote(path.substr(i + 1, close - i - 1));
        i = close + 1;
        return key;
    };
    const std::string root = Folded(ident());
    Animatable* owner = nullptr;
    if (root.empty() || root == "object" || root == "baseobject" || root == "base") {
        owner = BaseObjectOf(node);
        if (!owner) Fail("'" + WideToUtf8(node->GetName()) + "' has no base object");
        if (auto key = bracket()) Fail("object takes no [" + *key + "]");
    } else if (root == "modifiers" || root == "modifier" || root == "mod") {
        auto key = bracket();
        if (!key) Fail("modifiers needs a modifier: modifiers[<name, class or 1-based position>]");
        owner = PickModifier(node, *key);
    } else if (root == "material" || root == "mtl") {
        Mtl* mtl = node->GetMtl();
        if (!mtl) Fail("'" + WideToUtf8(node->GetName()) + "' has no material");
        owner = mtl;
        while (auto key = bracket()) owner = PickSubMaterial(static_cast<Mtl*>(owner), *key);
    } else if (root == "transform" || root == "controller") {
        owner = node->GetTMController();
        if (!owner) Fail("'" + WideToUtf8(node->GetName()) + "' has no transform controller");
    } else if (root == "node") {
        owner = node;
    } else {
        Fail("path '" + rawPath + "' must start with object, modifiers[...], material, transform or node");
    }
    while (i < path.size()) {
        if (path[i] == '[') {
            auto key = bracket();
            if (owner->SuperClassID() == MATERIAL_CLASS_ID) { owner = PickSubMaterial(static_cast<Mtl*>(owner), *key); continue; }
            Fail("path '" + rawPath + "': [" + *key + "] follows a list or material only");
        }
        if (path[i] != '.') Fail("path '" + rawPath + "' is malformed closest '" + path.substr(i) + "'");
        ++i;
        const std::string name = ident();
        if (name.empty()) Fail("path '" + rawPath + "' has an empty segment");
        std::optional<int> element;
        if (auto key = bracket()) {
            int n = 0;
            if (!WholeNumber(*key, n)) Fail("path '" + rawPath + "': [" + *key + "] must be a 1-based number");
            element = n;
        }
        owner = Child(owner, name, element, t);
    }
    return owner;
}

// "radius: distance = 25.0 (default 25.0) [0.0, 1e+30] {x=0, y=1} +anim", for _mx's describe().
inline std::string Line(const json& entry) {
    auto shown = [](const json& v) {
        if (v.is_number_float()) {
            char text[40];
            std::snprintf(text, sizeof text, "%.6g", v.get<double>());
            std::string s = text;
            if (s.find_first_of(".e") == std::string::npos && s.find("inf") == std::string::npos && s.find("nan") == std::string::npos) s += ".0";
            return s;
        }
        return v.dump();
    };
    std::string line = entry["name"].get<std::string>() + ": " + entry["type"].get<std::string>();
    if (entry.value("list", false)) line += "[]";
    if (entry.contains("value")) line += " = " + shown(entry["value"]);
    else if (entry.contains("default")) line += " = " + shown(entry["default"]);
    if (entry.contains("choice")) line += " (" + entry["choice"].get<std::string>() + ")";
    if (entry.contains("range")) line += " [" + shown(entry["range"][0]) + ", " + shown(entry["range"][1]) + "]";
    if (entry.contains("choices")) {
        std::string items;
        for (const json& c : entry["choices"]) items += (items.empty() ? "" : ", ") + c["name"].get<std::string>() + "=" + std::to_string(c["value"].get<int>());
        line += " {" + items + "}";
    }
    if (entry.contains("label")) line += " \"" + entry["label"].get<std::string>() + "\"";
    if (entry.value("animatable", false)) line += " +anim";
    if (entry.value("read_only", false)) line += " read-only";
    if (entry["source"] != "paramblock2") line += " (" + entry["source"].get<std::string>() + ")";
    return line;
}

} // namespace ParamCodec
