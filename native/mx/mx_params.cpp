// Parameters by name, for base objects, modifiers, materials and maps:
// ParamBlock2 parameters, transform and point subanims (gizmo, center, Lathe's
// axis), then MAXScript properties. Descriptions, named choices and subanim
// values come from the shared parameter codec (param_codec.h), the model the
// bridge's tools use; reads and writes of plain values stay native Python here.
#include "mx_common.h"

// What a ParamBlock2 parameter holds (GetOne): a number (angles in degrees,
// percentages 0-100, times in frames), an int, a bool, a string, a Point3, a
// color as (r, g, b), a point4 or color4 as four floats, a Node, Material,
// Texmap or Object, and None for an empty slot or a type that is not read. A
// list parameter holds a list of them (GetParam).
#define MX_PARAM_HELD                                                                                    \
    "float | int | bool | str | _mx.Point3 | tuple[float, float, float] | "                             \
    "tuple[float, float, float, float] | _mx.Node | _mx.Material | _mx.Texmap | _mx.Object | None"
MX_RESULT_TYPE(ParamHeld, MX_PARAM_HELD " | list[" MX_PARAM_HELD "]")
#undef MX_PARAM_HELD

#include "mcp_bridge/param_codec.h"

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

// A codec call with its errors as _mx's own.
template <typename F>
static auto Codec(F&& f) -> decltype(f()) {
    try {
        return f();
    } catch (const ParamCodec::Error& error) {
        if (error.unknown) throw UnknownParam(error.text);
        if (error.code == "AMBIGUOUS") throw Ambiguous(error.text);
        throw BadParam(error.text);
    }
}

// Parameter names, then transform/point subanims, then the MAXScript properties that are not parameters.
std::vector<std::string> AllNames(Animatable* owner) {
    return Codec([&] { return ParamCodec::AllNames(owner); });
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

// A parameter, else a transform/point subanim, else a MAXScript property, else UnknownParam.
nb::object GetValue(Animatable* owner, const std::string& name, TimeValue t) {
    if (auto ref = FindParam(owner, name)) return GetParam(*ref, t);
    if (Control* control = ParamCodec::FindSubControl(owner, name))
        return ToPython(Codec([&] { return ParamCodec::SubControlGet(control, t); }));
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
    if (target && nb::isinstance<ObjectRef>(value)) {
        // An Object is of no kind _mx knows, and a slot does not say which class
        // it takes: it fits only where an object of its own class already is.
        ReferenceTarget* current = nullptr;
        Interval valid = FOREVER;
        pb->GetValue(id, t, current, valid, index);
        if (!current || current->ClassID() != target->ClassID() || current->SuperClassID() != target->SuperClassID())
            throw BadParam(what + " takes an Object of the class it holds" +
                           (current ? " (" + HandlerHelpers::MaxScriptVisibleClassName(current) + ")" : std::string(", and it is empty")) +
                           ", not " + ReprOf(value));
    }
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
            if (nb::isinstance<nb::str>(value)) {   // a named choice: BendAxis = "x"
                ParamBlockDesc2* desc = pb->GetDesc();
                const auto choices = ParamCodec::ChoicesOf(desc ? desc->cd : nullptr, desc, *ref.def, what);
                const std::string label = nb::cast<std::string>(value);
                const auto chosen = choices ? ParamCodec::ChoiceValue(*choices, label) : std::nullopt;
                if (!chosen)
                    throw BadParam(choices ? what + " has no choice '" + label + "'. Choices: " + ParamCodec::ChoiceNames(*choices)
                                           : what + " expects an int; it publishes no named choices");
                ok = pb->SetValue(id, t, *chosen, index); break;
            }
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
            throw BadParam(what + " is read-only here: its type is one _mx neither reads nor sets (params.info() "
                           "marks it read_only); use rt (pymxs)");
        }
    } catch (const nb::cast_error&) {
        throw BadParam(what + " expects " + WithArticle(TypeName(type)) + ", got " + TypeNameOf(value));
    }
    if (!ok) throw BadParam("3ds Max refused " + what + " = " + ReprOf(value));
}

void SetParam(Animatable* owner, const std::string& name, nb::handle value, TimeValue t) {
    const std::optional<ParamRef> found = FindParam(owner, name);
    if (!found) {
        if (Control* control = ParamCodec::FindSubControl(owner, name)) {
            const nlohmann::json input = FromPython(value);
            Codec([&] { ParamCodec::SubControlSet(control, input, t, name); return 0; });
            if (auto* target = ParamCodec::AsRefTarget(owner)) target->NotifyDependents(FOREVER, PART_ALL, REFMSG_CHANGE);
            return;
        }
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

// The codec's description of every parameter, subanim and MAXScript property.
static nlohmann::json Described(const Params& p, const std::string& query) {
    Animatable* owner = ParamOwner(p);
    ParamCodec::DescribeOptions options;
    options.query = query;
    options.limit = 100000;
    return Codec([&] { return ParamCodec::Describe(owner, Now(), options); });
}

// One line per parameter: "radius: distance = 25.0 [0.0, 1e+30] +anim", choices as {x=0, y=1}.
static std::vector<std::string> Describe(const Params& p, const std::string& query) {
    std::vector<std::string> lines;
    const nlohmann::json described = Described(p, query);   // kept alive for the loop
    for (const nlohmann::json& entry : described["params"]) lines.push_back(ParamCodec::Line(entry));
    return lines;
}

void BindParams(nb::module_& m) {
    nb::class_<Params>(m, "Params",
        "Parameters by name: p.radius, p['radius'] = 10. Angles in degrees, percentages 0-100, colors 0-1, "
        "named choices by name (p.BendAxis = 'x'), gizmos as {'position', 'rotation', 'scale'}. "
        "p.describe() lists them with ranges and choices.")
        .def("__getattr__", [](const Params& p, const std::string& name) -> nb::object {
            if (name.size() > 1 && name[0] == '_' && name[1] == '_') throw nb::attribute_error(name.c_str());
            return GetValue(ParamOwner(p), name, Now());
        })
        // None is a value here: it empties a map, material or node slot.
        .def("__setattr__", [](const Params& p, const std::string& name, nb::handle value) {
            SetParam(ParamOwner(p), name, value, Now());
        }, "name"_a, nb::arg("value").none())
        .def("__getitem__", [](const Params& p, const std::string& name) {
            return GetValue(ParamOwner(p), name, Now());
        })
        .def("__setitem__", [](const Params& p, const std::string& name, nb::handle value) {
            SetParam(ParamOwner(p), name, value, Now());
        }, "name"_a, nb::arg("value").none())
        .def("__contains__", [](const Params& p, const std::string& name) {
            Animatable* owner = ParamOwner(p);
            return FindParam(owner, name).has_value() || ParamCodec::FindSubControl(owner, name) != nullptr ||
                   MxsGet(owner, name, Now()).has_value();
        })
        .def("keys", [](const Params& p) { return AllNames(ParamOwner(p)); },
            "Parameter names, then transform/point subanims (gizmo, center), then MAXScript properties that are not parameters.")
        .def("to_dict", [](const Params& p) -> nb::typed<nb::dict, std::string, ParamHeld> {
            nb::dict out;
            const TimeValue t = Now();
            ForEachParam(ParamOwner(p), [&](IParamBlock2* block, const ParamDef& def) {
                out[ParamName(block, def).c_str()] = GetParam(ParamRef{block, &def}, t);
            });
            return out;
        }, "Every ParamBlock2 parameter and its value, by name; a list parameter gives a list.")
        .def("update", [](const Params& p, nb::kwargs values) {
            Animatable* owner = ParamOwner(p);
            const TimeValue t = Now();
            for (auto [key, value] : values) SetParam(owner, nb::cast<std::string>(key), value, t);
        }, "values"_a, "Set several parameters: p.update(radius=10, segs=32).")
        .def("describe", &Describe, "query"_a = "",
            "One line per parameter: name, type, value, range, {named choices}, \"UI label\", +anim; "
            "subanims (gizmo, center) and MAXScript properties after. query filters by name or label.")
        .def("info", [](const Params& p, const std::string& query) -> nb::typed<nb::list, nb::dict> {
                // The codec's "params" is an array of objects.
                return nb::borrow<nb::list>(ToPython(Described(p, query)["params"]));
            }, "query"_a = "",
            "describe() as a list of dicts. Each has name, type and source (paramblock2, subanim or maxscript), and "
            "where they apply value, default, range, choices (with choices_source, and choice for the current one), "
            "label, list, takes, animatable, read_only and controller.")
        .def("track", [](const Params& p, const std::string& name) {
            const ParamRef ref = RequireParam(ParamOwner(p), name);
            return Track{p.node, p.owner, ParamName(ref.block, *ref.def), true};
        }, "name"_a, "The parameter's animation track.")
        .def("__dir__", [](const Params& p) {
            std::vector<std::string> names = AllNames(ParamOwner(p));
            for (const char* method : {"describe", "info", "keys", "to_dict", "track", "update"}) names.push_back(method);
            return names;
        })
        .def("__repr__", &ParamsRepr);
}

} // namespace MX_ABI_TAG
} // namespace mx
