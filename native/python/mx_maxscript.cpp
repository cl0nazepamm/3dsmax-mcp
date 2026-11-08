// MAXScript property access: the fallback for names that are not ParamBlock2
// parameters. It covers what MAXScript adds on top of parameter blocks: legacy
// aliases (Bend's `angle`), old-style parameter blocks, scripted plugins and
// MAXScript-only properties (`center`, `gizmo`). Values are literals; no
// script text is evaluated.
#include "mx_common.h"

#include <maxscript/foundation/colors.h>
#include <maxscript/foundation/3dmath.h>

namespace mx {
inline namespace MX_ABI_TAG {

// MAXScript's own name resolution for a ParamBlock2 parameter, aliases included.
std::optional<ParamRef> MxsFindParam(Animatable* owner, const std::string& name) {
    RequireMain();
    ScopedMaxScriptEvaluationContext context;
    one_value_local(property);
    vl.property = Name::intern(Utf8ToWide(name).c_str());
    for (int b = 0; b < owner->NumParamBlocks(); ++b) {
        IParamBlock2* block = owner->GetParamBlock(b);
        ParamBlockDesc2* desc = block ? block->GetDesc() : nullptr;
        if (!desc) continue;
        int tab = -1;
        if (ParamDef* def = MAXClass::get_pb2_param_def(vl.property, block, desc, tab)) {
            return ParamRef{block, def};
        }
    }
    return std::nullopt;
}

static Value* ToMxs(nb::handle value, const std::string& what) {
    if (value.is_none()) return &undefined;
    if (nb::isinstance<nb::bool_>(value)) return nb::cast<bool>(value) ? &true_value : &false_value;
    if (nb::isinstance<nb::int_>(value)) return Integer::intern(nb::cast<int>(value));
    if (nb::isinstance<nb::float_>(value)) return Float::intern(nb::cast<float>(value));
    if (nb::isinstance<nb::str>(value)) {
        const std::string text = nb::cast<std::string>(value);
        if (text.size() > 1 && text[0] == '#') return Name::intern(Utf8ToWide(text.substr(1)).c_str());
        return new String(Utf8ToWide(text).c_str());
    }
    if (nb::isinstance<Node>(value)) return MAXNode::intern(Resolve(nb::cast<Node>(value)));
    if (nb::isinstance<nb::list>(value) || nb::isinstance<nb::tuple>(value)) {
        const auto xs = Floats(value, 3, 3, what);
        return new Point3Value(Point3(xs[0], xs[1], xs[2]));
    }
    throw BadParam(what + ": cannot pass " + WithArticle(TypeNameOf(value)) + " to 3ds Max");
}

static nb::object FromMxs(Value* v) {
    if (!v || v == &undefined || v == &unsupplied) return nb::none();
    if (v == &true_value) return nb::bool_(true);
    if (v == &false_value) return nb::bool_(false);
    if (is_integer(v)) return nb::int_(v->to_int());
    if (is_float(v) || is_double(v)) return nb::float_(v->to_double());
    if (is_point3(v)) return FromPoint3(v->to_point3());
    if (is_point2(v)) { Point2 p = v->to_point2(); return nb::make_tuple(p.x, p.y); }
    if (is_color(v)) {
        AColor c = v->to_acolor();
        return c.a == 1.0f ? nb::make_tuple(c.r, c.g, c.b) : nb::make_tuple(c.r, c.g, c.b, c.a);
    }
    if (is_string(v) || is_name(v)) return nb::str(WideToUtf8(v->to_string()).c_str());
    if (is_node(v)) {
        INode* node = v->to_node();
        return InScene(node) ? nb::cast(Wrap(node)) : nb::none();
    }
    if (is_array(v)) {
        nb::list items;
        Array* array = static_cast<Array*>(v);
        for (int i = 0; i < array->size; ++i) items.append(FromMxs(array->data[i]));
        return items;
    }
    // Anything else (controllers, gizmos, maps) is shown, not converted.
    StringStream* text = new StringStream(0);
    v->sprin1(text);
    nb::str shown(WideToUtf8(text->to_string()).c_str());
    return shown;
}

static ReferenceTarget* AsRef(Animatable* owner) {
    if (!owner || !owner->IsRefMaker() || !static_cast<ReferenceMaker*>(owner)->IsRefTarget()) return nullptr;
    return static_cast<ReferenceTarget*>(owner);
}

// MAXScript evaluates properties at its own time; carry at() into it.
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
};

std::optional<nb::object> MxsGet(Animatable* owner, const std::string& name, TimeValue t) {
    RequireMain();
    ReferenceTarget* ref = AsRef(owner);
    if (!ref) return std::nullopt;
    ScopedMaxScriptEvaluationContext context;
    MxsTime time(t);
    three_value_locals(wrapper, property, result);
    std::optional<nb::object> out;
    try {
        vl.wrapper = MAXClass::make_wrapper_for(ref);
        vl.property = Name::intern(Utf8ToWide(name).c_str());
        vl.result = vl.wrapper->_get_property(vl.property);
        out = FromMxs(vl.result);
    } catch (...) {
        out.reset();  // not a MAXScript property of this object
    }
    return out;
}

bool MxsSet(Animatable* owner, const std::string& name, nb::handle value, TimeValue t) {
    RequireMain();
    ReferenceTarget* ref = AsRef(owner);
    if (!ref) return false;
    // Unknown names first, so a refused value is not reported as a missing property.
    if (!MxsGet(owner, name, t)) return false;
    ScopedMaxScriptEvaluationContext context;
    MxsTime time(t);
    three_value_locals(wrapper, property, input);
    bool ok = true;
    try {
        vl.input = ToMxs(value, name);
        vl.wrapper = MAXClass::make_wrapper_for(ref);
        vl.property = Name::intern(Utf8ToWide(name).c_str());
        vl.wrapper->_set_property(vl.property, vl.input);
    } catch (const BadParam&) {
        throw;
    } catch (...) {
        ok = false;
    }
    if (!ok) throw BadParam("3ds Max refused " + name + " = " + ReprOf(value));
    return true;
}

std::vector<std::string> MxsPropNames(Animatable* owner) {
    RequireMain();
    std::vector<std::string> names;
    ReferenceTarget* ref = AsRef(owner);
    if (!ref) return names;
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

} // namespace MX_ABI_TAG
} // namespace mx
