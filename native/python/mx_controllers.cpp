// Controllers on tracks: assigning and layering them, constraint targets,
// parameter wiring, and script controllers checked off the track before they
// replace anything.
#include "mx_common.h"
#include "mx_tracks.h"

#include <iparamwire.h>
#include <maxscript/foundation/3dmath.h>
#include <maxscript/foundation/numbers.h>
#include <maxscript/maxwrapper/scriptcontroller.h>

#include <cstdio>
#include <unordered_set>

namespace mx {
inline namespace MX_ABI_TAG {

using json = nlohmann::json;

struct ControllerRef { AnimHandle ctrl = 0; };

static Control* ResolveController(const ControllerRef& ref) {
    RequireMain();
    Animatable* anim = Animatable::GetAnimByHandle(ref.ctrl);
    if (!IsController(anim)) throw NotFound("controller #" + std::to_string(ref.ctrl) + " was deleted");
    return static_cast<Control*>(anim->GetInterface(I_CONTROL));
}

static ControllerRef RefOf(Control* c) { return ControllerRef{Animatable::GetHandleByAnim(c)}; }

static const char* KindName(SClass_ID super) {
    switch (super) {
    case CTRL_FLOAT_CLASS_ID: return "float";
    case CTRL_POINT3_CLASS_ID: return "point3";
    case CTRL_POINT4_CLASS_ID: return "point4";
    case CTRL_POSITION_CLASS_ID: return "position";
    case CTRL_ROTATION_CLASS_ID: return "rotation";
    case CTRL_SCALE_CLASS_ID: return "scale";
    case CTRL_MATRIX3_CLASS_ID: return "transform";
    default: return "other";
    }
}

static Control* NewController(SClass_ID super, Class_ID id, const std::string& what) {
    auto* created = static_cast<Animatable*>(GetCOREInterface()->CreateInstance(super, id));
    if (!created) throw MaxError("3ds Max could not create " + WithArticle(what));
    if (created->SuperClassID() != super || created->ClassID() != id || !IsController(created)) {
        created->DeleteThis();
        throw MaxError("3ds Max created a different class than " + what);
    }
    return static_cast<Control*>(created->GetInterface(I_CONTROL));
}

static void Notify(Control* c) { c->NotifyDependents(FOREVER, PART_ALL, REFMSG_CHANGE); }

// MAXScript errors raised inside SDK calls, as BadParam.
template <typename F>
static auto Guarded(const std::string& what, F&& f) -> decltype(f()) {
    try {
        return f();
    } catch (const MAXScriptException& e) {
        throw BadParam(what + ": " + MxsErrorText(e));
    }
}

static std::string FrameText(TimeValue t) { return ReprOf(nb::float_(TicksToFrame(t))); }

static nb::dict Dict(std::initializer_list<std::pair<const char*, nb::object>> items) {
    nb::dict out;
    for (const auto& [key, value] : items) out[key] = value;
    return out;
}

// ── Assigning controllers ────────────────────────────────────────

static Class_ID ListClass(SClass_ID super) {
    switch (super) {
    case CTRL_FLOAT_CLASS_ID: return Class_ID(FLOATLIST_CONTROL_CLASS_ID, 0);
    case CTRL_POINT3_CLASS_ID: return Class_ID(POINT3LIST_CONTROL_CLASS_ID, 0);
    case CTRL_POSITION_CLASS_ID: return Class_ID(POSLIST_CONTROL_CLASS_ID, 0);
    case CTRL_ROTATION_CLASS_ID: return Class_ID(ROTLIST_CONTROL_CLASS_ID, 0);
    case CTRL_SCALE_CLASS_ID: return Class_ID(SCALELIST_CONTROL_CLASS_ID, 0);
    case CTRL_MATRIX3_CLASS_ID: return Class_ID(TMLIST_CONTROL_CLASS_ID, 0);
    default: return Class_ID(0, 0);
    }
}

static IListControl* ListOf(Control* c) { return c ? GetIListControlInterface(c) : nullptr; }

// A list controller's "Available" slot, where a new layer goes.
static int AvailableSlot(Control* list) {
    for (int i = 0; i < list->NumSubs(); ++i)
        if (_wcsicmp(list->SubAnimName(i, false).data(), L"Available") == 0) return i;
    return ListOf(list)->GetListCount();
}

static Control* NewFromClass(const std::string& cls, SClass_ID super) {
    // Within the track's superclass: Noise_Float and Noise_Position share a
    // UI name, and only one fits the track.
    ClassDesc* cd = FindClass(cls, {super});
    if (!cd)
        throw NotFound("no " + std::string(KindName(super)) + " controller class '" + cls + "'." +
                       Suggest(cls, ClassNames({super})));
    return NewController(super, cd->ClassID(), cls);
}

static void SetParams(Control* c, nb::kwargs params) {
    NoAnimate noAnimate;
    const TimeValue t = Now();
    for (auto [key, value] : params) SetParam(c, nb::cast<std::string>(key), value, t);
}

static void AppendLayer(const ResolvedTrack& r, Control* list, Control* layer) {
    if (!list->AssignController(layer, AvailableSlot(list))) throw MaxError(r.label + ": the list controller refused the layer");
    Notify(list);
}

// Put a layer on the track's list controller. Without one, the track gets a
// new list whose first layer is the current animation (for a parameter that
// is not animated, a default controller holding its value).
static void AddLayer(const ResolvedTrack& r, Control* layer) {
    if (ListOf(r.ctrl)) return AppendLayer(r, r.ctrl, layer);
    const SClass_ID super = TrackSuper(r);
    if (ListClass(super) == Class_ID(0, 0)) throw BadParam(r.label + ": " + KindName(super) + " tracks have no list controller");
    Control* list = NewController(super, ListClass(super), "list controller");
    KeepAlive staged(list);
    Control* base = r.ctrl ? r.ctrl : super == CTRL_POINT3_CLASS_ID ? NewDefaultPoint3Controller() : NewDefaultFloatController();
    KeepAlive made(r.ctrl ? nullptr : base);
    if (!r.ctrl) PutController(r, base);  // takes the parameter's value
    try {
        AppendLayer(r, list, base);
        PutController(r, list);
        AppendLayer(r, list, layer);
    } catch (...) {
        PutController(r, r.ctrl);
        throw;
    }
}

static ControllerRef Assign(const Track& tr, nb::handle cls, bool layer, nb::kwargs params) {
    RequireMain();
    HoldSuspend noUndo;  // see AddTarget
    const ResolvedTrack r = ResolveTrack(tr);
    const SClass_ID super = TrackSuper(r);
    Control* fresh = nullptr;
    const bool shared = nb::isinstance<ControllerRef>(cls);
    if (shared) {
        fresh = ResolveController(nb::cast<ControllerRef>(cls));
        if (fresh->SuperClassID() != super)
            throw BadParam(r.label + " takes a " + KindName(super) + " controller, not " + WithArticle(KindName(fresh->SuperClassID())));
    } else if (nb::isinstance<nb::str>(cls)) {
        fresh = NewFromClass(nb::cast<std::string>(cls), super);
    } else {
        throw BadParam("assign() takes a controller class name or a Controller, not " + TypeNameOf(cls));
    }
    KeepAlive staged(shared ? nullptr : fresh);  // freed below unless it lands on the track
    KeepAlive old(r.ctrl);
    // A replacement starts from the current animation, as in Track View.
    if (!shared && !layer && r.ctrl) fresh->Copy(r.ctrl);
    SetParams(fresh, params);
    if (layer) {
        AddLayer(r, fresh);
    } else {
        PutController(r, fresh);
        if (ResolveTrack(tr).ctrl != fresh) {
            PutController(r, r.ctrl);
            throw MaxError(r.label + ": 3ds Max did not keep the " + CtrlClass(fresh));
        }
    }
    return RefOf(fresh);
}

// ── Constraint targets ───────────────────────────────────────────

// Position, orientation, look-at and path constraints share these calls
// without sharing a base class.
template <typename I>
static bool WeightedTargets(I* c, nb::list& out) {
    if (!c) return false;
    for (int i = 0; i < c->GetNumTargets(); ++i) {
        INode* node = c->GetNode(i);
        out.append(nb::make_tuple(InScene(node) ? nb::cast(Wrap(node)) : nb::none(), c->GetTargetWeight(i)));
    }
    return true;
}

static bool Weighted(Control* c) {
    return GetIPosConstInterface(c) || GetIOrientConstInterface(c) || GetILookAtConstInterface(c) || GetIPathConstInterface(c);
}

static BOOL AppendWeighted(Control* c, INode* node, float weight) {
    if (auto* pos = GetIPosConstInterface(c)) return pos->AppendTarget(node, weight);
    if (auto* orient = GetIOrientConstInterface(c)) return orient->AppendTarget(node, weight);
    if (auto* lookAt = GetILookAtConstInterface(c)) return lookAt->AppendTarget(node, weight);
    return (GetIPathConstInterface(c))->AppendTarget(node, weight);
}

static ILinkCtrl* LinkOf(Control* c) { return static_cast<ILinkCtrl*>(c->GetInterface(LINK_CONSTRAINT_INTERFACE)); }
static IAttachCtrl* AttachOf(Control* c) { return static_cast<IAttachCtrl*>(c->GetInterface(I_ATTACHCTRL)); }
static IScriptCtrl* ScriptOf(Control* c) { return c ? static_cast<IScriptCtrl*>(c->GetInterface(IID_SCRIPT_CONTROL)) : nullptr; }
static bool IsExpression(Control* c) {
    const std::string cls = CtrlClass(c);
    return cls.size() > 11 && Lower(cls).compare(cls.size() - 11, 11, "_expression") == 0;
}

static nb::list Targets(Control* c) {
    nb::list out;
    if (WeightedTargets(GetIPosConstInterface(c), out) || WeightedTargets(GetIOrientConstInterface(c), out) ||
        WeightedTargets(GetILookAtConstInterface(c), out) || WeightedTargets(GetIPathConstInterface(c), out))
        return out;
    if (ILinkCtrl* link = LinkOf(c)) {
        for (int i = 0; i < link->GetNumTargets(); ++i) {
            INode* node = link->GetNode(i);
            out.append(nb::make_tuple(InScene(node) ? nb::cast(Wrap(node)) : nb::none(), link->GetFrameNumber(i)));
        }
    } else if (IAttachCtrl* attach = AttachOf(c)) {
        if (INode* node = attach->GetObject(); InScene(node)) out.append(Wrap(node));
    }
    return out;
}

// ── Script controller variables ──────────────────────────────────

static nb::object FromMxsConstant(Value* v) {
    if (v == &true_value || v == &false_value) return nb::bool_(v == &true_value);
    if (is_integer(v)) return nb::int_(v->to_int());
    if (is_float(v) || is_double(v)) return nb::float_(v->to_double());
    if (is_point3(v)) return FromPoint3(v->to_point3());
    if (is_string(v)) return nb::str(WideToUtf8(v->to_string()).c_str());
    return nb::str(MxsShown(v).c_str());
}

// The name of a variable type ("constant", "target", "node", "object"),
// read from the interface's own enumeration.
static std::string VarKind(IScriptCtrl* sc, Value* which) {
    const int code = sc->GetVarType(which);
    FPInterfaceDesc* desc = sc->GetDesc();
    for (int e = 0; desc && e < desc->enumerations.Count(); ++e) {
        FPEnum* values = desc->enumerations[e];
        if (!values || values->ID != IScriptCtrl::enumValueType) continue;
        for (int i = 0; i < values->enumeration.Count(); ++i)
            if (values->enumeration[i].code == code) return Lower(WideToUtf8(values->enumeration[i].name));
    }
    return "unknown";
}

struct ScriptVar {
    std::string name, kind, shown;
    unsigned long long identity = 0;  // the referenced node, controller or track owner
    double offset = 0;
    nb::object value;
};

// User variables; the first four (T, S, F, NT) are the controller's own.
static std::vector<ScriptVar> ScriptVars(IScriptCtrl* sc) {
    std::vector<ScriptVar> vars;
    ScopedMaxScriptEvaluationContext context;
    two_value_locals(which, item);
    const int count = sc->NumVariables();
    for (int i = 5; i <= count; ++i) {
        ScriptVar v;
        vl.which = Integer::intern(i);
        try {
            v.name = WideToUtf8(sc->GetVarName(i - 1).data());
            v.kind = VarKind(sc, vl.which);
            v.offset = TicksToFrame(sc->GetOffset(vl.which));
            if (v.kind == "node") {
                INode* node = sc->GetNode(vl.which);
                v.identity = node ? Animatable::GetHandleByAnim(node) : 0;
                v.value = InScene(node) ? nb::cast(Wrap(node)) : nb::none();
                v.shown = node ? NodeName(node) : "";
            } else if (v.kind == "object") {
                ReferenceTarget* object = sc->GetObject(vl.which);
                v.identity = object ? Animatable::GetHandleByAnim(object) : 0;
                v.value = IsController(object) ? nb::cast(RefOf(static_cast<Control*>(object->GetInterface(I_CONTROL))))
                                               : nb::none();
                v.shown = object ? CtrlClass(object) : "";
            } else if (v.kind == "target") {
                vl.item = sc->GetTarget(vl.which, FALSE);
                v.shown = MxsShown(vl.item);
                v.value = nb::str(v.shown.c_str());
                vl.item = sc->GetTarget(vl.which, TRUE);
                if (ReferenceTarget* owner = vl.item ? vl.item->to_reftarg() : nullptr) v.identity = Animatable::GetHandleByAnim(owner);
            } else {
                vl.item = sc->GetVariable(vl.which, FALSE, 0);
                v.shown = MxsShown(vl.item);
                v.value = FromMxsConstant(vl.item);
            }
        } catch (const MAXScriptException&) {
            v.shown = "?";
        }
        if (!v.value.is_valid()) v.value = nb::none();
        vars.push_back(std::move(v));
    }
    return vars;
}

static nb::list VarsList(const std::vector<ScriptVar>& vars) {
    nb::list out;
    for (const ScriptVar& v : vars)
        out.append(Dict({{"name", nb::str(v.name.c_str())}, {"kind", nb::str(v.kind.c_str())}, {"value", v.value},
                         {"offset", nb::float_(v.offset)}}));
    return out;
}

// A binding: a script input by name, from a Python value.
struct Binding {
    enum Kind { Constant, NodeVar, Target, Object } kind = Constant;
    std::string name;
    nb::object constant;
    INode* node = nullptr;
    ReferenceTarget* parent = nullptr;  // target: the track's slot
    int sub = -1;
    double offset = 0;
    Control* object = nullptr;
};

static bool IsIdentifier(const std::string& name) {
    if (name.empty() || std::isdigit((unsigned char)name[0])) return false;
    return std::all_of(name.begin(), name.end(), [](unsigned char c) { return std::isalnum(c) || c == '_'; });
}

static bool IsNumbers(nb::handle v, size_t count) {
    if (!IsSequence(v)) return false;
    if (nb::len(v) != count) return false;
    for (nb::handle item : v)
        if (!IsNumber(item)) return false;
    return true;
}

// driven: the node the script drives (0 for a material track), which an
// input cannot reference: the controller would depend on itself.
static Binding ParseBinding(const std::string& name, nb::handle value, AnimHandle driven) {
    const std::string key = Lower(name);
    if (!IsIdentifier(name) || key == "t" || key == "s" || key == "f" || key == "nt" || key == "this" || key.rfind("__mcp", 0) == 0)
        throw BadParam("'" + name + "' cannot name a script input; use an identifier other than T, S, F, NT and this");
    Binding b;
    b.name = name;
    nb::handle track = value;
    if (nb::isinstance<nb::tuple>(value) || nb::isinstance<nb::list>(value)) {
        std::vector<nb::handle> items;
        for (nb::handle item : value) items.push_back(item);
        if (items.size() == 2 && nb::isinstance<Track>(items[0])) {
            if (!IsNumber(items[1])) throw BadParam(name + ": a (track, offset) input takes the offset in frames");
            track = items[0];
            b.offset = nb::cast<double>(items[1]);
        }
    }
    if (nb::isinstance<Track>(track)) {
        const Track tr = nb::cast<Track>(track);
        if (driven && tr.node == driven && !tr.param)
            throw BadParam(name + ": a track of the driven node itself would make a loop; read it through a separate driver node");
        const ResolvedTrack r = ResolveTrack(tr);
        if (!r.parent || r.sub < 0) throw BadParam(name + ": " + r.label + " cannot be a script input");
        b.kind = Binding::Target;
        b.parent = r.parent;
        b.sub = r.sub;
    } else if (nb::isinstance<Node>(value)) {
        b.kind = Binding::NodeVar;
        b.node = Resolve(nb::cast<Node>(value));
        if (driven && nb::cast<Node>(value).handle == driven)
            throw BadParam(name + ": the driven node cannot be its own input; use a separate driver node");
    } else if (nb::isinstance<ControllerRef>(value)) {
        b.kind = Binding::Object;
        b.object = ResolveController(nb::cast<ControllerRef>(value));
    } else if (nb::isinstance<nb::bool_>(value) || IsNumber(value) || nb::isinstance<nb::str>(value) ||
               IsNumbers(value, 3) || IsNumbers(value, 4)) {
        b.constant = nb::borrow(value);
    } else {
        throw BadParam(name + ": an input is a Node, a Track, (Track, offset_frames), a Controller, a number, a bool, "
                       "a string, (x, y, z) or a quat (x, y, z, w); got " + TypeNameOf(value));
    }
    return b;
}

static std::vector<Binding> ParseBindings(nb::handle bindings, AnimHandle driven) {
    if (!nb::isinstance<nb::dict>(bindings)) throw BadParam("bindings is a dict of input name to value");
    std::vector<Binding> out;
    std::unordered_set<std::string> seen;
    for (auto [key, value] : nb::borrow<nb::dict>(bindings)) {
        const std::string name = nb::cast<std::string>(nb::str(key));
        if (!seen.insert(Lower(name)).second) throw BadParam("two inputs are named '" + name + "' (names ignore case)");
        out.push_back(ParseBinding(name, value, driven));
    }
    if (out.size() > 64) throw BadParam("a script takes at most 64 inputs");
    return out;
}

static Value* ConstantValue(nb::handle v) {
    if (nb::isinstance<nb::bool_>(v)) return nb::cast<bool>(v) ? &true_value : &false_value;
    if (nb::isinstance<nb::int_>(v)) return Integer::intern(nb::cast<int>(v));
    if (nb::isinstance<nb::float_>(v)) return Float::intern(nb::cast<float>(v));
    if (nb::isinstance<nb::str>(v)) return new String(Utf8ToWide(nb::cast<std::string>(v)).c_str());
    const auto xs = Floats(v, 3, 4, "constant");
    if (xs.size() == 3) return new Point3Value(Point3(xs[0], xs[1], xs[2]));
    return new QuatValue(Quat(xs[0], xs[1], xs[2], xs[3]));
}

static void AddBinding(IScriptCtrl* sc, const Binding& b) {
    ScopedMaxScriptEvaluationContext context;
    one_value_local(item);
    const MSTR name(Utf8ToWide(b.name).c_str());
    const BOOL ok = Guarded("input '" + b.name + "'", [&] {
        switch (b.kind) {
        case Binding::NodeVar: return sc->AddNode(name, b.node);
        case Binding::Target:
            vl.item = MAXSubAnim::intern(b.parent, b.sub);
            return sc->AddTarget(name, vl.item, CheckedTicks(b.offset, "offset"), &undefined);  // as MAXScript passes it
        case Binding::Object:
            vl.item = MAXClass::make_wrapper_for(b.object);
            return sc->AddObject(name, vl.item);
        default: {
            vl.item = ConstantValue(b.constant);
            FPValue constant;
            vl.item->to_fpvalue(constant);
            return sc->AddConstant(name, constant);
        }
        }
    });
    if (!ok) throw BadParam("3ds Max refused the input '" + b.name + "' (a duplicate name, or a reference loop)");
}

static void ReplaceBindings(IScriptCtrl* sc, const std::vector<Binding>& bindings) {
    {
        ScopedMaxScriptEvaluationContext context;
        one_value_local(which);
        for (int i = sc->NumVariables(); i >= 5; --i) {
            vl.which = Integer::intern(i);
            Guarded("removing a script input", [&] { return sc->DeleteVariable(vl.which); });
        }
    }
    for (const Binding& b : bindings) AddBinding(sc, b);
}

// ── Expression controllers (MAXScript's IExprCtrl) ───────────────

static std::string ExpressionText(Control* c) {
    const json text = NativeJson("maxscript", {{"code", std::string("(\n") + kMxsQuote + "__mxQuote (" + MxsAnim(c) +
                                                           ".GetExpression())\n)"}});
    return (text.is_string)() ? text.get<std::string>() : text.dump();
}

static void SetExpressionText(Control* c, const std::string& expression) {
    NativeJson("maxscript", {{"code", "(local c = " + MxsAnim(c) + "\nif not (c.SetExpression " + MxsString(expression) +
                                          ") do throw \"3ds Max rejected the expression\"\nc.Update()\ntrue)"}});
}

static nb::list ExpressionVars(Control* c) {
    const json rows = NativeJson("maxscript", {{"code", std::string("(\n") + kMxsQuote + "local c = " + MxsAnim(c) + R"MXS(
local out = ""
for i = 1 to c.NumScalars() do (
    local n = c.GetScalarName i
    if findItem #("T", "S", "F", "NT") n == 0 do
        out += (if out == "" then "" else ",") + "[" + __mxQuote n + "," + __mxQuote ((c.GetScalarType i) as string) + "]"
)
for i = 1 to c.NumVectors() do
    out += (if out == "" then "" else ",") + "[" + __mxQuote (c.GetVectorName i) + "," + __mxQuote ((c.GetVectorType i) as string) + "]"
"[" + out + "]"
))MXS"}});
    // MAXScript names the kinds in camel case: scalarTarget -> scalar_target.
    auto snake = [](const std::string& name) {
        std::string out;
        for (char ch : name) {
            if (std::isupper((unsigned char)ch)) out += '_';
            out += (char)std::tolower((unsigned char)ch);
        }
        return out;
    };
    nb::list out;
    for (const json& row : rows)
        if ((row.is_array)() && row.size() == 2)
            out.append(Dict({{"name", nb::str(row[0].get<std::string>().c_str())},
                             {"kind", nb::str(snake(row[1].get<std::string>()).c_str())}}));
    return out;
}

static void AddExpressionInput(Control* c, const std::string& name, nb::handle target) {
    if (!IsIdentifier(name)) throw BadParam("'" + name + "' cannot name an expression input; use an identifier");
    std::string call;
    if (nb::isinstance<Track>(target)) {
        const ResolvedTrack r = ResolveTrack(nb::cast<Track>(target));
        const bool scalar = r.kind == TrackKind::Float;
        if (!scalar && r.kind != TrackKind::Point3) throw BadParam(r.label + ": expression inputs are float or point3 tracks");
        const std::string source = r.ctrl ? MxsAnim(r.ctrl) : "(getSubAnim " + MxsAnim(r.parent) + " " + std::to_string(r.sub + 1) + ")";
        call = std::string(scalar ? "AddScalarTarget " : "AddVectorTarget ") + MxsString(name) + " " + source;
    } else if (nb::isinstance<Node>(target)) {
        call = "AddVectorNode " + MxsString(name) + " " + MxsAnim(Resolve(nb::cast<Node>(target)));
    } else if (IsNumber(target)) {
        call = "AddScalarConstant " + MxsString(name) + " " + ReprOf(nb::float_(nb::cast<double>(target)));
    } else if (IsNumbers(target, 3)) {
        const Point3 p = ToPoint3(target, name);
        call = "AddVectorConstant " + MxsString(name) + " [" + ReprOf(nb::float_(p.x)) + "," + ReprOf(nb::float_(p.y)) + "," +
               ReprOf(nb::float_(p.z)) + "]";
    } else {
        throw BadParam("an expression input is a Track, a Node, a number or (x, y, z), not " + TypeNameOf(target));
    }
    NativeJson("maxscript", {{"code", "(if not (" + MxsAnim(c) + "." + call + ") do throw \"3ds Max refused the input '" +
                                          HandlerHelpers::JsonEscape(name) + "'\"\ntrue)"}});
}

// ── Controller properties ────────────────────────────────────────

static nb::object GetExpression(const ControllerRef& ref) {
    Control* c = ResolveController(ref);
    if (IScriptCtrl* sc = ScriptOf(c)) return nb::str(WideToUtf8(sc->GetExpression().data()).c_str());
    if (IsExpression(c)) return nb::str(ExpressionText(c).c_str());
    return nb::none();
}

static void SetExpression(const ControllerRef& ref, const std::string& expression) {
    Control* c = ResolveController(ref);
    if (IScriptCtrl* sc = ScriptOf(c)) {
        const MSTR text(Utf8ToWide(expression).c_str());
        if (!Guarded("expression", [&] { return sc->SetExpression(text); })) throw BadParam("3ds Max rejected the expression");
        Notify(c);
        return;
    }
    if (IsExpression(c)) return SetExpressionText(c, expression);
    throw BadParam(CtrlClass(c) + " has no expression; script and expression controllers do");
}

static void AddTarget(const ControllerRef& ref, nb::handle target, std::optional<double> weight,
                      std::optional<double> frame, const std::optional<std::string>& name) {
    // _mx keeps no undo records. Controllers open their own holds (a constraint's
    // AppendTarget does), and a record they leave keeps a parameter block alive
    // after _mx frees its controller: deleting a target then crashed Max.
    HoldSuspend noUndo;
    Control* c = ResolveController(ref);
    const std::string cls = CtrlClass(c);
    if (IScriptCtrl* sc = ScriptOf(c)) {
        if (weight || frame) throw BadParam(cls + " inputs take name=, not weight= or frame=");
        if (!name) throw BadParam("a script input needs name=: add_target(mx.node('Hip'), name='hip')");
        AddBinding(sc, ParseBinding(*name, target, 0));
        return Notify(c);
    }
    if (IsExpression(c)) {
        if (weight || frame) throw BadParam(cls + " inputs take name=, not weight= or frame=");
        if (!name) throw BadParam("an expression input needs name=: add_target(track, name='h')");
        return AddExpressionInput(c, *name, target);
    }
    if (name) throw BadParam(cls + " targets take no name");
    INode* node = Resolve(FindNode(target));
    BOOL added = FALSE;
    if (Weighted(c)) {
        if (frame) throw BadParam(cls + " targets take weight=, not frame=");
        if (weight && (!std::isfinite(*weight) || *weight < 0)) throw BadParam("weight must be a number from 0 up");
        added = AppendWeighted(c, node, float(weight.value_or(50.0)));
    } else if (ILinkCtrl* link = LinkOf(c)) {
        if (weight) throw BadParam(cls + " targets take frame=, not weight=");
        added = link->AddTarget(node, (int)std::lround(frame ? *frame : TicksToFrame(Now())));
    } else if (IAttachCtrl* attach = AttachOf(c)) {
        if (weight || frame) throw BadParam(cls + " takes the node alone");
        added = attach->SetObject(node);
    } else {
        throw BadParam(cls + " takes no targets; constraints, script and expression controllers do");
    }
    if (!added) throw BadParam(cls + " refused '" + NodeName(node) + "' as a target");
    Notify(c);
}

static nb::object Variables(const ControllerRef& ref) {
    Control* c = ResolveController(ref);
    if (IScriptCtrl* sc = ScriptOf(c)) return VarsList(ScriptVars(sc));
    if (IsExpression(c)) return ExpressionVars(c);
    return nb::list();
}

static std::vector<ControllerRef> Layers(const ControllerRef& ref) {
    std::vector<ControllerRef> out;
    if (IListControl* list = ListOf(ResolveController(ref)))
        for (int i = 0; i < list->GetListCount(); ++i)
            if (Control* layer = list->GetSubCtrl(i)) out.push_back(RefOf(layer));
    return out;
}

static std::string ControllerRepr(const ControllerRef& ref) {
    Animatable* anim = Animatable::GetAnimByHandle(ref.ctrl);
    if (!IsController(anim)) return "<Controller #" + std::to_string(ref.ctrl) + " deleted>";
    const int keys = anim->NumKeys();
    return "<Controller " + CtrlClass(anim) + (keys > 0 ? ", " + std::to_string(keys) + " keys>" : ">");
}

// ── Script controllers, checked before they replace anything ─────

static std::string Fnv(const std::string& text) {
    unsigned long long hash = 1469598103934665603ULL;
    for (unsigned char c : text) {
        hash ^= c;
        hash *= 1099511628211ULL;
    }
    char out[17];
    std::snprintf(out, sizeof out, "%016llx", hash);
    return out;
}

// Changes when the track's controller, script, inputs or out-of-range types
// change, or its static value when it has no controller.
static std::string StateToken(const Track& tr, const ResolvedTrack& r) {
    json state = json::array({(unsigned long long)tr.node, (unsigned long long)tr.owner, tr.path});
    if (!r.ctrl) {
        state.push_back(ReprOf(ValueAt(r, Now())));
    } else {
        state.push_back((unsigned long long)Animatable::GetHandleByAnim(r.ctrl));
        state.push_back(CtrlClass(r.ctrl));
        state.push_back(r.ctrl->GetORT(ORT_BEFORE));
        state.push_back(r.ctrl->GetORT(ORT_AFTER));
        if (IScriptCtrl* sc = ScriptOf(r.ctrl)) {
            state.push_back(WideToUtf8(sc->GetExpression().data()));
            state.push_back(sc->GetThrowOnError());
            for (const ScriptVar& v : ScriptVars(sc)) state.push_back(json::array({v.name, v.kind, v.shown, v.identity, v.offset}));
        }
    }
    return Fnv(state.dump(-1, ' ', false, json::error_handler_t::replace));
}

// The script controller kind for the track: float, position, rotation, scale
// or point3, as MAXScript names them.
static const char* ScriptKind(const ResolvedTrack& r, Class_ID* id = nullptr) {
    struct Entry { SClass_ID super; Class_ID id; const char* kind; };
    static const Entry entries[] = {
        {CTRL_FLOAT_CLASS_ID, FLOAT_SCRIPT_CONTROL_CLASS_ID, "float"},
        {CTRL_POSITION_CLASS_ID, POSITION_SCRIPT_CONTROL_CLASS_ID, "position"},
        {CTRL_ROTATION_CLASS_ID, ROTATION_SCRIPT_CONTROL_CLASS_ID, "rotation"},
        {CTRL_SCALE_CLASS_ID, SCALE_SCRIPT_CONTROL_CLASS_ID, "scale"},
        {CTRL_POINT3_CLASS_ID, POINT3_SCRIPT_CONTROL_CLASS_ID, "point3"},
    };
    const SClass_ID super = TrackSuper(r);
    for (const Entry& e : entries)
        if (e.super == super) {
            if (id) *id = e.id;
            return e.kind;
        }
    throw BadParam(r.label + " is a " + KindName(super) + " track; script controllers go on float, point3, position, "
                   "rotation and scale tracks");
}

// A copy of the track's script controller, or a new one of the track's kind.
static Control* Stage(const ResolvedTrack& r) {
    if (ScriptOf(r.ctrl)) {
        ReferenceTarget* copy = CloneRefHierarchy(r.ctrl);
        if (!IsController(copy)) throw MaxError(r.label + ": 3ds Max could not copy the " + CtrlClass(r.ctrl));
        return static_cast<Control*>(copy->GetInterface(I_CONTROL));
    }
    Class_ID id;
    const char* kind = ScriptKind(r, &id);
    return NewController(TrackSuper(r), id, std::string(kind) + "_script");
}

// Compile the source as a function of the controller's inputs and run it at
// each time, so syntax, runtime and output-type errors keep MAXScript's
// detail; then set it on the controller and sample the controller itself.
const char* const kEvaluate = R"MXS(
fn __mcpNum v = (
    local f = v as float
    if bit.isNAN f or not (bit.isFinite f) do throw "the script returned a value that is not a finite number"
    formattedPrint f format:".9g"
)
fn __mcpValue v kind = (
    case kind of (
        "float": (
            if classOf v != Float and classOf v != Integer do throw ("expected a number, got " + ((classOf v) as string))
            __mcpNum v
        )
        "rotation": (
            if classOf v != Quat do throw ("expected a quat, got " + ((classOf v) as string))
            "[" + __mcpNum v.x + "," + __mcpNum v.y + "," + __mcpNum v.z + "," + __mcpNum v.w + "]"
        )
        default: (
            if classOf v != Point3 do throw ("expected a point3, got " + ((classOf v) as string))
            "[" + __mcpNum v.x + "," + __mcpNum v.y + "," + __mcpNum v.z + "]"
        )
    )
)
local stage = "compile", index = 0
try (
    local names = "T S F NT this"
    local call = "__mcpProbe (c.GetVarValue 1) (c.GetVarValue 2) (c.GetVarValue 3) (c.GetVarValue 4) c"
    for i = 5 to c.NumVariables() do (
        names += " " + c.GetName i
        call += " (c.GetVarValue " + (i as string) + ")"
    )
    local probe = execute ("(fn __mcpProbe " + names + " = (\n" + source + "\n))")
    local run = execute ("(fn __mcpRun __mcpProbe c = (" + call + "))")
    stage = "evaluate"
    for i = 1 to times.count do at time times[i] (
        index = i
        __mcpValue (run probe c) kind
    )
    stage = "controller"
    index = 0
    c.throwOnError = true
    if not (c.SetExpression source) do throw "3ds Max rejected the expression"
    local out = ""
    for i = 1 to times.count do at time times[i] (
        index = i
        c.Update()
        out += (if i > 1 then "," else "") + __mcpValue c.value kind
    )
    c.throwOnError = false
    "{\"samples\":[" + out + "]}"
) catch (
    try (c.throwOnError = false) catch ()
    "{\"stage\":" + __mxQuote stage + ",\"index\":" + (index as string) + ",\"message\":" + __mxQuote (getCurrentException()) + "}"
)
)MXS";

using Samples = std::vector<std::pair<double, nb::object>>;

static nb::object SampleValue(const json& v, const char* kind, double scale) {
    if (!std::strcmp(kind, "float")) return nb::float_(v.get<double>() * scale);
    if (!std::strcmp(kind, "rotation")) {
        const Quat q(v[0].get<float>(), v[1].get<float>(), v[2].get<float>(), v[3].get<float>());
        float angles[3] = {0, 0, 0};
        QuatToEuler(q, angles, EULERTYPE_XYZ);
        return nb::make_tuple(RadToDeg(angles[0]), RadToDeg(angles[1]), RadToDeg(angles[2]));
    }
    return nb::make_tuple(v[0].get<double>(), v[1].get<double>(), v[2].get<double>());
}

static Samples Evaluate(Control* candidate, const std::string& source, const char* kind,
                        const std::vector<TimeValue>& times, double scale) {
    std::string list;
    for (TimeValue t : times) list += (list.empty() ? "" : ", ") + FrameText(t);
    const std::string code = std::string("(\n") + kMxsQuote + "local c = " + MxsAnim(candidate) + "\nlocal times = #(" + list +
                             ")\nlocal kind = \"" + kind + "\"\nlocal source = " + MxsString(source) + "\n" + kEvaluate + ")";
    const json reply = NativeJson("maxscript", {{"code", code}});
    if (!reply.is_object()) throw MaxError("the script check returned no result");
    if (reply.contains("stage")) {
        const std::string stage = reply.value("stage", "");
        const int index = reply.value("index", 0);
        const std::string message = reply.value("message", "");
        const std::string at = index > 0 && index <= (int)times.size() ? " at frame " + FrameText(times[index - 1]) : "";
        if (stage == "compile") throw BadParam("the script does not compile: " + message);
        if (stage == "evaluate") throw BadParam("the script fails" + at + ": " + message);
        throw MaxError("the script controller fails" + at + ": " + message);
    }
    Samples samples;
    const json values = reply.value("samples", json::array());
    for (size_t i = 0; i < values.size() && i < times.size(); ++i)
        samples.emplace_back(TicksToFrame(times[i]), SampleValue(values[i], kind, scale));
    return samples;
}

static std::vector<TimeValue> SampleTimes(const std::optional<std::vector<double>>& frames) {
    if (!frames) return {Now()};
    if (frames->empty() || frames->size() > 64) throw BadParam("frames takes 1-64 frames");
    std::vector<TimeValue> times;
    for (double f : *frames) times.push_back(CheckedTicks(f, "frames"));
    return times;
}

static void CheckSource(const std::string& source) {
    if (source.find_first_not_of(" \t\r\n") == std::string::npos) throw BadParam("the script is empty");
    if (source.size() > 65536) throw BadParam("the script is over 65536 characters");
}

static nb::list SamplesList(const Samples& samples) {
    nb::list out;
    for (const auto& [frame, value] : samples) out.append(nb::make_tuple(frame, value));
    return out;
}

static nb::dict Report(const Track& tr, const ResolvedTrack& r) {
    IScriptCtrl* sc = ScriptOf(r.ctrl);
    nb::dict out;
    out["track"] = tr;
    out["controller"] = r.ctrl ? nb::object(nb::str(CtrlClass(r.ctrl).c_str())) : nb::none();
    out["expected_controller"] = StateToken(tr, r);
    out["script"] = sc ? nb::object(nb::str(WideToUtf8(sc->GetExpression().data()).c_str())) : nb::none();
    out["variables"] = sc ? VarsList(ScriptVars(sc)) : nb::list();
    out["throw_on_error"] = sc ? nb::object(nb::bool_(sc->GetThrowOnError())) : nb::none();
    return out;
}

static void CheckExpected(const Track& tr, const ResolvedTrack& r, const std::optional<std::string>& expected) {
    if (expected && *expected != StateToken(tr, r))
        throw MaxError(r.label + " changed since it was inspected; call inspect_script() for a fresh expected_controller");
}

static nb::dict InspectScript(const Track& tr, const std::optional<std::vector<double>>& frames) {
    RequireMain();
    const ResolvedTrack r = ResolveTrack(tr);
    nb::dict out = Report(tr, r);
    if (!frames) return out;
    IScriptCtrl* sc = ScriptOf(r.ctrl);
    if (!sc) throw BadParam(r.label + " has no script controller to sample");
    Control* candidate = Stage(r);
    KeepAlive staged(candidate);
    ScriptOf(candidate)->SetThrowOnError(false);
    out["samples"] = SamplesList(Evaluate(candidate, WideToUtf8(sc->GetExpression().data()), ScriptKind(r), SampleTimes(frames), r.scale));
    return out;
}

// The staged controller with the new inputs (bindings None keeps the current ones).
static Control* StageWith(const Track& tr, const ResolvedTrack& r, nb::handle bindings) {
    std::optional<std::vector<Binding>> inputs;
    if (!bindings.is_none()) inputs = ParseBindings(bindings, tr.param ? 0 : tr.node);
    Control* candidate = Stage(r);
    IScriptCtrl* sc = ScriptOf(candidate);
    sc->SetThrowOnError(false);  // no error dialogs while inputs change
    try {
        if (inputs) ReplaceBindings(sc, *inputs);
    } catch (...) {
        candidate->MaybeAutoDelete();
        throw;
    }
    return candidate;
}

static nb::dict ValidateScript(const Track& tr, const std::string& source, nb::handle bindings,
                               const std::optional<std::vector<double>>& frames) {
    RequireMain();
    CheckSource(source);
    const ResolvedTrack r = ResolveTrack(tr);
    const char* kind = ScriptKind(r);
    const std::vector<TimeValue> times = SampleTimes(frames);
    Control* candidate = StageWith(tr, r, bindings);
    KeepAlive staged(candidate);
    nb::dict out;
    out["expected_controller"] = StateToken(tr, r);
    out["output_type"] = kind;
    out["samples"] = SamplesList(Evaluate(candidate, source, kind, times, r.scale));
    out["applied"] = false;
    return out;
}

// Put the old controller back (or the old static value) after a failed apply.
static void Restore(const ResolvedTrack& r, nb::handle value) {
    PutController(r, r.ctrl);
    if (!r.ctrl && r.ref) {
        NoAnimate noAnimate;
        SetParam(r.owner, r.name, value, Now());
    }
}

static nb::dict ApplyScript(const Track& tr, const std::string& source, const std::string& expected, nb::handle bindings,
                            const std::optional<std::vector<double>>& frames) {
    RequireMain();
    if (expected.empty()) throw BadParam("apply_script needs expected: the expected_controller from inspect_script() or validate_script()");
    CheckSource(source);
    const ResolvedTrack r = ResolveTrack(tr);
    CheckExpected(tr, r, expected);
    const char* kind = ScriptKind(r);
    const std::vector<TimeValue> times = SampleTimes(frames);
    Control* candidate = StageWith(tr, r, bindings);
    KeepAlive staged(candidate);
    Evaluate(candidate, source, kind, times, r.scale);
    // The slot will reference the controller: refuse a loop through its inputs.
    if (candidate->TestForLoop(FOREVER, r.parent) != REF_SUCCEED)
        throw BadParam(r.label + ": the inputs depend on this track (a reference loop); drive it from a separate node");
    const nb::object before = r.ctrl ? nb::none() : ValueAt(r, Now());
    KeepAlive old(r.ctrl);
    PutController(r, candidate);
    Samples samples;
    try {
        if (ResolveTrack(tr).ctrl != candidate) throw MaxError(r.label + ": 3ds Max did not keep the script controller");
        samples = Evaluate(candidate, source, kind, times, r.scale);
    } catch (const std::exception& e) {
        try {
            Restore(r, before);
        } catch (const std::exception& again) {
            throw MaxError(std::string(e.what()) + "; restoring the old controller failed too: " + again.what());
        }
        throw;
    }
    // A later failing frame shows 3ds Max's script error dialog instead of
    // throwing into whatever evaluates the scene.
    ScriptOf(candidate)->SetThrowOnError(false);
    nb::dict out = Report(tr, ResolveTrack(tr));
    out["samples"] = SamplesList(samples);
    out["applied"] = true;
    return out;
}

// ── Wiring ───────────────────────────────────────────────────────

static IParamWireMgr* WireManager() {
    IParamWireMgr* wires = GetParamWireMgr();
    if (!wires) throw MaxError("3ds Max's parameter wiring is not available");
    return wires;
}

static ControllerRef WireTrack(const Track& tr, const Track& driver, const std::string& expression,
                               const std::optional<std::string>& reverse) {
    RequireMain();
    HoldSuspend noUndo;  // see AddTarget
    const ResolvedTrack driven = ResolveTrack(tr), from = ResolveTrack(driver);
    for (const ResolvedTrack* t : {&driven, &from})
        if (!t->parent || t->sub < 0) throw BadParam(t->label + " cannot be wired");
    if (driven.parent == from.parent && driven.sub == from.sub) throw BadParam("a track cannot be wired to itself");
    if (expression.empty()) throw BadParam("wire() needs an expression of the driver, named by its sub-anim: 'Radius * 2'");
    const std::wstring expr = Utf8ToWide(expression);
    const bool ok = reverse
        ? WireManager()->Connect2Way(driven.parent, driven.sub, from.parent, from.sub, expr.c_str(), Utf8ToWide(*reverse).c_str())
        : WireManager()->Connect(from.parent, from.sub, driven.parent, driven.sub, expr.c_str());
    if (!ok) throw BadParam("3ds Max refused to wire " + driven.label + " to " + from.label + " (mismatched types or a loop)");
    const ResolvedTrack after = ResolveTrack(tr);
    if (!after.ctrl) throw MaxError(driven.label + ": 3ds Max made no wire controller");
    return RefOf(after.ctrl);
}

static void Unwire(const Track& tr) {
    RequireMain();
    HoldSuspend noUndo;  // see AddTarget
    const ResolvedTrack r = ResolveTrack(tr);
    IBaseWireControl* wire = r.ctrl ? GetWireControlInterface(r.ctrl) : nullptr;
    if (!wire) throw BadParam(r.label + " is not wired");
    // Max's Disconnect leaves the track at the driver's raw value, not the
    // expression's result; the value it shows now is written back below.
    const TimeValue t = Now();
    Interval valid = FOREVER;
    float before = 0;
    Point3 beforeP(0, 0, 0);
    const TrackKind kind = KindOf(r.ctrl);
    if (kind == TrackKind::Float) r.ctrl->GetValue(t, &before, valid, CTRL_ABSOLUTE);
    else if (kind == TrackKind::Point3) r.ctrl->GetValue(t, &beforeP, valid, CTRL_ABSOLUTE);
    bool ok = false;
    if (wire->is_two_way()) {
        Control* other = wire->get_num_wires() > 0 ? wire->get_co_controller(0) : nullptr;
        ok = other && WireManager()->Disconnect2Way(r.ctrl, other);
    } else if (wire->is_driven()) {
        ok = WireManager()->Disconnect(r.ctrl);
    } else {
        throw BadParam(r.label + " drives other tracks but is not driven; unwire() the driven track");
    }
    if (!ok) throw MaxError("3ds Max refused to unwire " + r.label);
    const ResolvedTrack after = ResolveTrack(tr);
    if (after.ctrl && KindOf(after.ctrl) == kind && (kind == TrackKind::Float || kind == TrackKind::Point3)) {
        NoAnimate noAnimate;
        if (kind == TrackKind::Float) after.ctrl->SetValue(t, &before, 1, CTRL_ABSOLUTE);
        else after.ctrl->SetValue(t, &beforeP, 1, CTRL_ABSOLUTE);
    }
}

// The nearest node depending on target: the owner of a parameter's block.
static INode* OwnerNode(Animatable* target) {
    if (!target || !target->IsRefMaker() || !static_cast<ReferenceMaker*>(target)->IsRefTarget()) return nullptr;
    std::vector<ReferenceTarget*> frontier{static_cast<ReferenceTarget*>(target)};
    std::unordered_set<ReferenceMaker*> seen;
    for (int depth = 0; depth < 8 && !frontier.empty(); ++depth) {
        std::vector<ReferenceTarget*> next;
        for (ReferenceTarget* t : frontier) {
            DependentIterator it(t);
            while (ReferenceMaker* maker = it.Next()) {
                if (!seen.insert(maker).second) continue;
                if (maker->SuperClassID() == BASENODE_CLASS_ID) return static_cast<INode*>(maker);
                if (maker->IsRefTarget()) next.push_back(static_cast<ReferenceTarget*>(maker));
            }
        }
        frontier = std::move(next);
    }
    return nullptr;
}

static std::vector<nb::dict> NodeWires(const Node& n, int depth) {
    std::vector<nb::dict> out;
    // Wire controllers are Float_Wire, Position_Wire, ...: the filter keeps the walk's list short.
    for (const Track& tr : NodeTracks(n, std::string("_wire"), depth)) {
        const ResolvedTrack r = ResolveTrack(tr);
        IBaseWireControl* wire = r.ctrl ? GetWireControlInterface(r.ctrl) : nullptr;
        if (!wire) continue;
        nb::list links;
        for (int i = 0; i < wire->get_num_wires(); ++i) {
            Animatable* parent = wire->get_wire_parent(i);
            const int sub = wire->get_wire_subnum(i);
            INode* owner = OwnerNode(parent);
            const MCHAR* text = wire->get_expr_text(i);
            const std::string param =
                parent && sub >= 0 && sub < parent->NumSubs() ? WideToUtf8(parent->SubAnimName(sub, false).data()) : "";
            links.append(Dict({{"param", nb::str(param.c_str())}, {"node", InScene(owner) ? nb::cast(Wrap(owner)) : nb::none()},
                               {"expression", nb::str(text ? WideToUtf8(text).c_str() : "")}}));
        }
        out.push_back(Dict({{"track", nb::cast(tr)}, {"controller", nb::str(CtrlClass(r.ctrl).c_str())},
                            {"driven", nb::bool_(wire->is_driven())}, {"driver", nb::bool_(wire->is_driver())},
                            {"two_way", nb::bool_(wire->is_two_way())}, {"links", links}}));
    }
    return out;
}

// ── Binding ──────────────────────────────────────────────────────

void BindControllers(nb::module_& m) {
    nb::class_<ControllerRef>(m, "Controller",
        "A track's controller: track.controller. c.params holds its parameters (Noise frequency, constraint relative, ...).")
        .def_prop_ro("class_name", [](const ControllerRef& c) { return CtrlClass(ResolveController(c)); },
            "MAXScript class name: Bezier_Float, Float_Script, Position_Constraint, ...")
        .def_prop_ro("kind", [](const ControllerRef& c) { return KindName(ResolveController(c)->SuperClassID()); },
            "float, point3, point4, position, rotation, scale or transform.")
        .def_prop_ro("params", [](const ControllerRef& c) { ResolveController(c); return Params{0, c.ctrl}; },
            "The controller's parameters and MAXScript properties.")
        .def_prop_rw("expression", &GetExpression, &SetExpression,
            "Script or expression text, None for other controllers. Setting it is unchecked; "
            "track.apply_script() validates first.")
        .def_prop_ro("variables", &Variables,
            "Script inputs [{name, kind, value, offset}] (offset in frames), or expression inputs [{name, kind}].")
        .def_prop_ro("targets", [](const ControllerRef& c) { return Targets(ResolveController(c)); },
            "Constraint targets: [(node, weight)], [(node, frame)] for a link constraint, [node] for attachment.")
        .def("add_target", &AddTarget, "target"_a, "weight"_a = nb::none(), "frame"_a = nb::none(), "name"_a = nb::none(),
            "Add a target: a node with weight= (position, orientation, look-at, path constraints), with frame= (link), "
            "the attachment node, or a named input of a script (node, track, controller, constant) or expression "
            "(track, node, number, point3): c.add_target(mx.node('Ball'), weight=50).")
        .def_prop_ro("layers", &Layers, "A list controller's layers, first to last; [] for other controllers.")
        .def("__repr__", &ControllerRepr)
        .def("__eq__", [](const ControllerRef& a, nb::handle b) {
            return nb::isinstance<ControllerRef>(b) && a.ctrl == nb::cast<ControllerRef>(b).ctrl;
        }, nb::arg("other").none())
        .def("__hash__", [](const ControllerRef& c) { return (Py_hash_t)c.ctrl; });

    AddProperty<Track>("controller", [](const Track& tr) -> std::optional<ControllerRef> {
        RequireMain();
        const ResolvedTrack r = ResolveTrack(tr);
        if (!r.ctrl) return std::nullopt;
        return RefOf(r.ctrl);
    }, "The track's Controller, or None for a parameter that is not animated.");
    AddMethod<Track>("assign", &Assign, "cls"_a, "layer"_a = false, "params"_a,
        "Give the track a new controller of class cls (a name within the track's kind, or a Controller to share) "
        "and set its parameters; layer=True adds it on top of a list controller. Returns it: "
        "track.assign('Noise_Float', frequency=0.2).");
    AddMethod<Track>("wire", &WireTrack, "driver"_a, "expression"_a, "reverse"_a = nb::none(),
        "Drive this track from driver (a Track) through expression, which names the driver by its sub-anim name: "
        "angle.wire(radius, 'Radius * 2'). reverse makes it two-way (the driver follows this track). Returns the wire "
        "controller.");
    AddMethod<Track>("unwire", &Unwire, "Disconnect this driven track's wire; it keeps a plain controller holding its current value.");
    AddMethod<Track>("inspect_script", &InspectScript, "frames"_a = nb::none(),
        "The track's script controller: {track, controller, expected_controller, script, variables, throw_on_error}, "
        "and samples [(frame, value)] when frames is given.");
    AddMethod<Track>("validate_script", &ValidateScript, "source"_a, "bindings"_a = nb::none(), "frames"_a = nb::none(),
        "Check a script on a copy of the controller, off the track, at frames (default the current one): "
        "{expected_controller, output_type, samples, applied=False}. bindings maps input names to a Node, Track, "
        "(Track, offset_frames), Controller or constant; None keeps the current inputs. The script returns internal "
        "units (radians, 0-1 for percentages); samples read like track.value().");
    AddMethod<Track>("apply_script", &ApplyScript, "source"_a, "expected"_a, "bindings"_a = nb::none(),
        "frames"_a = nb::none(),
        "Validate, then replace the controller with the script controller, sampling it in place; restores the old "
        "controller on failure. expected is expected_controller from inspect_script() or validate_script().");
    AddMethod<Node>("wires", &NodeWires, "depth"_a = 8,
        "The node's wired tracks: [{track, controller, driven, driver, two_way, links: [{param, node, expression}]}].");
}

} // namespace MX_ABI_TAG
} // namespace mx
