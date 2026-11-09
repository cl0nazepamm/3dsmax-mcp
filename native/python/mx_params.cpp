// ParamBlock2 parameters by name, for base objects, modifiers and materials.
#include "mx_common.h"

namespace mx {
inline namespace MX_ABI_TAG {

Animatable* ParamOwner(const Params& p) {
    RequireMain();
    if (p.owner) {
        Animatable* owner = Animatable::GetAnimByHandle(p.owner);
        if (!owner) throw NotFound("this object (#" + std::to_string(p.owner) + ") was deleted");
        return owner;
    }
    INode* node = Resolve(Node{p.node});
    Object* base = BaseObject(node);
    if (!base) throw NotFound("'" + NodeName(node) + "' has no base object");
    return base;
}

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

double ParamScale(int type) {
    switch (BaseType(type)) {
    case TYPE_ANGLE: return 180.0 / PI;
    case TYPE_PCNT_FRAC: return 100.0;
    default: return 1.0;
    }
}

// The name MAXScript shows: a parameter's alias when it has one (plugins
// converted from old parameter blocks keep their MAXScript names that way:
// Bend's BendAngle is `angle`), else its internal name.
std::string ParamName(IParamBlock2* block, const ParamDef& def) {
    const MCHAR* alias = block->FindParamAlias(def.ID, -1);
    return WideToUtf8(alias && *alias ? alias : def.int_name);
}

std::vector<std::string> ParamNames(Animatable* owner) {
    std::vector<std::string> names;
    ForEachParam(owner, [&](IParamBlock2* block, const ParamDef& def) { names.push_back(ParamName(block, def)); });
    return names;
}

// Parameter names plus the MAXScript properties that are not parameters.
std::vector<std::string> AllNames(Animatable* owner) {
    std::vector<std::string> names = ParamNames(owner);
    for (const std::string& extra : MxsPropNames(owner)) {
        const bool known = std::any_of(names.begin(), names.end(),
            [&](const std::string& name) { return _stricmp(name.c_str(), extra.c_str()) == 0; });
        if (!known) names.push_back(extra);
    }
    return names;
}

// By MAXScript name or internal name, ignoring case.
std::optional<ParamRef> FindParam(Animatable* owner, const std::string& name) {
    const std::wstring wanted = Utf8ToWide(name);
    std::optional<ParamRef> found;
    ForEachParam(owner, [&](IParamBlock2* block, const ParamDef& def) {
        if (found) return;
        const MCHAR* alias = block->FindParamAlias(def.ID, -1);
        if (_wcsicmp(def.int_name, wanted.c_str()) == 0 || (alias && _wcsicmp(alias, wanted.c_str()) == 0))
            found = ParamRef{block, &def};
    });
    return found ? found : MxsFindParam(owner, name);
}

[[noreturn]] static void ThrowUnknown(Animatable* owner, const std::string& name) {
    const std::vector<std::string> names = AllNames(owner);
    const std::string cls = HandlerHelpers::MaxScriptVisibleClassName(owner);
    if (names.empty()) throw UnknownParam(cls + " has no parameters");
    throw UnknownParam(cls + " has no parameter '" + name + "'." + Suggest(name, names) +
                       " Parameters: " + Join(names));
}

ParamRef RequireParam(Animatable* owner, const std::string& name) {
    if (auto ref = FindParam(owner, name)) return *ref;
    if (MxsGet(owner, name, Now()))
        throw BadParam("'" + name + "' is a MAXScript property, not a parameter, so it has no track here");
    ThrowUnknown(owner, name);
}

// A parameter, else a MAXScript property, else UnknownParam.
nb::object GetValue(Animatable* owner, const std::string& name, TimeValue t) {
    if (auto ref = FindParam(owner, name)) return GetParam(*ref, t);
    if (auto value = MxsGet(owner, name, t)) return *value;
    ThrowUnknown(owner, name);
}

// ── Reading ──────────────────────────────────────────────────────

static nb::object GetOne(const ParamRef& ref, TimeValue t, int index) {
    IParamBlock2* pb = ref.block;
    const ParamID id = ref.def->ID;
    const int type = BaseType(ref.def->type);
    Interval valid = FOREVER;
    switch (type) {
    case TYPE_FLOAT: case TYPE_WORLD: case TYPE_COLOR_CHANNEL: case TYPE_ANGLE: case TYPE_PCNT_FRAC: {
        float v = 0; pb->GetValue(id, t, v, valid, index); return nb::float_(v * ParamScale(type));
    }
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
        return InScene(v) ? nb::cast(Wrap(v)) : nb::none();
    }
    case TYPE_TEXMAP: { Texmap* v = nullptr; pb->GetValue(id, t, v, valid, index); return RefToPython(v); }
    case TYPE_MTL: { Mtl* v = nullptr; pb->GetValue(id, t, v, valid, index); return RefToPython(v); }
    case TYPE_REFTARG: { ReferenceTarget* v = nullptr; pb->GetValue(id, t, v, valid, index); return RefToPython(v); }
    default: return nb::none();
    }
}

nb::object GetParam(const ParamRef& ref, TimeValue t) {
    if (!(ref.def->type & TYPE_TAB)) return GetOne(ref, t, 0);
    nb::list items;
    for (int i = 0; i < ref.block->Count(ref.def->ID); ++i) items.append(GetOne(ref, t, i));
    return items;
}

// ── Writing ──────────────────────────────────────────────────────

// A material, map or other reference, by object. A reference parameter that
// names a superclass takes only that kind; a value that already uses this
// block (a map put inside itself) would close a reference loop.
static BOOL SetReference(const ParamRef& ref, TimeValue t, nb::handle value, int index, const std::string& what) {
    IParamBlock2* pb = ref.block;
    const ParamID id = ref.def->ID;
    const int type = BaseType(ref.def->type);
    SClass_ID super = type == TYPE_TEXMAP ? TEXMAP_CLASS_ID : type == TYPE_MTL ? MATERIAL_CLASS_ID : ref.def->sclass_ID;
    if (super == REF_TARGET_CLASS_ID) super = 0;
    ReferenceTarget* target = PythonToRef(value, super, what);
    if (target && target->TestForLoop(FOREVER, pb) != REF_SUCCEED)
        throw BadParam(what + ": " + ReprOf(value) + " already contains this object, so it cannot go in this slot");
    if (type == TYPE_TEXMAP) return pb->SetValue(id, t, static_cast<Texmap*>(target), index);
    if (type == TYPE_MTL) return pb->SetValue(id, t, static_cast<Mtl*>(target), index);
    return pb->SetValue(id, t, target, index);
}

static void SetOne(const ParamRef& ref, TimeValue t, nb::handle value, int index, const std::string& what) {
    IParamBlock2* pb = ref.block;
    const ParamID id = ref.def->ID;
    const int type = BaseType(ref.def->type);
    BOOL ok = FALSE;
    try {
        switch (type) {
        case TYPE_FLOAT: case TYPE_WORLD: case TYPE_COLOR_CHANNEL: case TYPE_ANGLE: case TYPE_PCNT_FRAC:
            if (nb::isinstance<nb::bool_>(value)) throw nb::cast_error();
            ok = pb->SetValue(id, t, float(nb::cast<double>(value) / ParamScale(type)), index); break;
        case TYPE_INT: case TYPE_INDEX: case TYPE_RADIOBTN_INDEX:
            if (nb::isinstance<nb::float_>(value) || nb::isinstance<nb::bool_>(value)) throw nb::cast_error();
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
            INode* node = value.is_none() ? nullptr : Resolve(FindNode(value));
            ok = pb->SetValue(id, t, node, index); break;
        }
        case TYPE_TEXMAP: case TYPE_MTL: case TYPE_REFTARG: ok = SetReference(ref, t, value, index, what); break;
        default:
            throw BadParam(what + " is " + WithArticle(TypeName(type)) +
                           " parameter, which _mx cannot set yet; use rt (pymxs)");
        }
    } catch (const nb::cast_error&) {
        throw BadParam(what + " expects " + WithArticle(TypeName(type)) + ", got " + TypeNameOf(value));
    }
    if (!ok) throw BadParam("3ds Max refused " + what + " = " + ReprOf(value));
}

void SetParam(Animatable* owner, const std::string& name, nb::handle value, TimeValue t) {
    const std::optional<ParamRef> found = FindParam(owner, name);
    if (!found) {
        if (MxsSet(owner, name, value, t)) return;
        ThrowUnknown(owner, name);
    }
    const ParamRef ref = *found;
    const std::string what = ParamName(ref.block, *ref.def);
    if (!(ref.def->type & TYPE_TAB)) return SetOne(ref, t, value, 0, what);
    if (!nb::isinstance<nb::list>(value) && !nb::isinstance<nb::tuple>(value))
        throw BadParam(what + " is a list parameter; pass a list");
    std::vector<nb::handle> items;
    for (nb::handle item : value) items.push_back(item);
    // Resizing drops or adds entries; a list of the same length keeps them in place.
    if (ref.block->Count(ref.def->ID) != (int)items.size()) ref.block->SetCount(ref.def->ID, (int)items.size());
    for (size_t i = 0; i < items.size(); ++i) SetOne(ref, t, items[i], (int)i, what + "[" + std::to_string(i) + "]");
}

// ── Binding ──────────────────────────────────────────────────────

static std::string ParamsRepr(const Params& p) {
    Animatable* owner = ParamOwner(p);
    std::string out = "<Params " + HandlerHelpers::MaxScriptVisibleClassName(owner);
    const TimeValue t = Now();
    int shown = 0;
    ForEachParam(owner, [&](IParamBlock2* block, const ParamDef& def) {
        if (++shown > 8) return;
        out += " " + ParamName(block, def) + "=" + ReprOf(GetParam(ParamRef{block, &def}, t));
    });
    if (shown > 8) out += " ...";
    return out + ">";
}

// One line per parameter: "radius: distance = 25.0 +anim".
static std::vector<std::string> Describe(const Params& p) {
    Animatable* owner = ParamOwner(p);
    std::vector<std::string> lines;
    const TimeValue t = Now();
    ForEachParam(owner, [&](IParamBlock2* block, const ParamDef& def) {
        std::string line = ParamName(block, def) + ": " + TypeName(def.type);
        if (def.type & TYPE_TAB) line += "[]";
        line += " = " + ReprOf(GetParam(ParamRef{block, &def}, t));
        if (def.flags & P_ANIMATABLE) line += " +anim";
        lines.push_back(line);
    });
    return lines;
}

void BindParams(nb::module_& m) {
    nb::class_<Params>(m, "Params",
        "Parameters by name: p.radius, p['radius'] = 10. Angles in degrees, percentages 0-100, colors 0-1.")
        .def("__getattr__", [](const Params& p, const std::string& name) -> nb::object {
            if (name.size() > 1 && name[0] == '_' && name[1] == '_') throw nb::attribute_error(name.c_str());
            return GetValue(ParamOwner(p), name, Now());
        })
        .def("__setattr__", [](const Params& p, const std::string& name, nb::handle value) {
            SetParam(ParamOwner(p), name, value, Now());
        })
        .def("__getitem__", [](const Params& p, const std::string& name) {
            return GetValue(ParamOwner(p), name, Now());
        })
        .def("__setitem__", [](const Params& p, const std::string& name, nb::handle value) {
            SetParam(ParamOwner(p), name, value, Now());
        })
        .def("__contains__", [](const Params& p, const std::string& name) {
            Animatable* owner = ParamOwner(p);
            return FindParam(owner, name).has_value() || MxsGet(owner, name, Now()).has_value();
        })
        .def("keys", [](const Params& p) { return AllNames(ParamOwner(p)); },
            "Parameter names, then MAXScript properties that are not parameters.")
        .def("to_dict", [](const Params& p) {
            nb::dict out;
            const TimeValue t = Now();
            ForEachParam(ParamOwner(p), [&](IParamBlock2* block, const ParamDef& def) {
                out[ParamName(block, def).c_str()] = GetParam(ParamRef{block, &def}, t);
            });
            return out;
        }, "Every parameter and its value.")
        .def("update", [](const Params& p, nb::kwargs values) {
            Animatable* owner = ParamOwner(p);
            const TimeValue t = Now();
            for (auto [key, value] : values) SetParam(owner, nb::cast<std::string>(key), value, t);
        }, "values"_a, "Set several parameters: p.update(radius=10, segs=32).")
        .def("describe", &Describe, "One line per parameter: name, type, value, +anim when animatable.")
        .def("track", [](const Params& p, const std::string& name) {
            const ParamRef ref = RequireParam(ParamOwner(p), name);
            return Track{p.node, p.owner, ParamName(ref.block, *ref.def), true};
        }, "name"_a, "The parameter's animation track.")
        .def("__dir__", [](const Params& p) {
            std::vector<std::string> names = AllNames(ParamOwner(p));
            for (const char* method : {"describe", "keys", "to_dict", "track", "update"}) names.push_back(method);
            return names;
        })
        .def("__repr__", &ParamsRepr);
}

} // namespace MX_ABI_TAG
} // namespace mx
