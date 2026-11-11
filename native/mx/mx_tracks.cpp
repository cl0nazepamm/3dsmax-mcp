// Time and animation: at()/animate() contexts, the timeline, tracks, keys and
// key edits (delete, move, scale, resample, bake, match, loop, out-of-range).
#include "mx_common.h"
#include "mx_tracks.h"

// What the animation members give and take, named for their signatures. Each
// text is read off the code that makes or uses the value.

// A controller's value (ControlValue): a float; a Point3 for a point3, position
// or scale track and, as Euler XYZ degrees, for a rotation track; the four
// Point3 rows of a transform track; None for a kind that is not read (point4).
MX_RESULT_TYPE(ControllerValue, "float | _mx.Point3 | tuple[_mx.Point3, _mx.Point3, _mx.Point3, _mx.Point3] | None")
// A track's value (ValueAt): its controller's, or the parameter's own while the
// parameter is not animated: an int or bool parameter then gives an int or
// bool, and a list parameter a list.
MX_RESULT_TYPE(TrackValue,
               "float | int | bool | _mx.Point3 | tuple[_mx.Point3, _mx.Point3, _mx.Point3, _mx.Point3] | "
               "list[float | int | bool | _mx.Point3] | None")
// What a key takes (SetKey): a number on a float track; (x, y, z) on a point3,
// position or scale track, Euler degrees on a rotation track; on a parameter
// track what the parameter takes (SetParam): a number, an int, a bool, a named
// choice, (x, y, z) or a color, and a list of them for a list parameter.
MX_ARG_TYPE(KeyValueArg, "float | int | bool | str | _mx.Point3 | list[float | int | bool | str | _mx.Point3]")
// One out-of-range type for both sides, or (before, after) where None keeps a side.
MX_ARG_TYPE(OrtArg, "str | tuple[str | None, str | None]")

#include <decomp.h>

#include <set>
#include <unordered_map>
#include <unordered_set>

namespace mx {
inline namespace MX_ABI_TAG {

// ── Time contexts ────────────────────────────────────────────────

struct AtTime { TimeValue time = 0; };
struct AnimateMode { bool on = true; int previous = 0; };

TimeValue CheckedTicks(double frame, const char* what) {
    const double ticks = frame * GetTicksPerFrame();
    if (!std::isfinite(ticks) || std::fabs(ticks) > 2.0e9)
        throw BadParam(std::string(what) + " is outside 3ds Max's time range");
    return (TimeValue)std::llround(ticks);
}

// ── Resolving tracks ─────────────────────────────────────────────
// A node track is a transform path ("pos", "rot.x", "transform", ...), a
// sub-anim path from the node ("[#Transform][#Position]"), or a transform
// path followed by sub-anim steps ("pos[2]" is a list controller's 2nd layer).

static std::string TrackLabel(const Track& tr) {
    std::string where = "#" + std::to_string(tr.node);
    if (INode* node = HandlerHelpers::FindNodeByHandle(tr.node); InScene(node)) where = "'" + NodeName(node) + "'";
    else if (tr.owner) where = "#" + std::to_string(tr.owner);
    return where + " " + tr.path;
}

bool IsController(Animatable* anim) { return anim && anim->GetInterface(I_CONTROL) != nullptr; }

static Control* AsController(Animatable* anim) { return static_cast<Control*>(anim->GetInterface(I_CONTROL)); }

TrackKind KindOf(Control* c) {
    switch (c->SuperClassID()) {
    case CTRL_FLOAT_CLASS_ID: return TrackKind::Float;
    case CTRL_POINT3_CLASS_ID: case CTRL_POSITION_CLASS_ID: return TrackKind::Point3;
    case CTRL_SCALE_CLASS_ID: return TrackKind::Scale;
    case CTRL_ROTATION_CLASS_ID: return TrackKind::Rotation;
    case CTRL_MATRIX3_CLASS_ID: return TrackKind::Transform;
    default: return TrackKind::Other;
    }
}

static bool KeyableType(int type, TrackKind& kind) {
    switch (BaseType(type)) {
    case TYPE_POINT3: case TYPE_RGBA: kind = TrackKind::Point3; return true;
    case TYPE_FLOAT: case TYPE_WORLD: case TYPE_ANGLE: case TYPE_PCNT_FRAC: case TYPE_COLOR_CHANNEL:
    case TYPE_INT: case TYPE_INDEX: case TYPE_BOOL: kind = TrackKind::Float; return true;
    default: return false;
    }
}

// A ParamBlock2 parameter as the track; the caller sets r.owner.
static void SetParamTrack(ResolvedTrack& r, const ParamRef& ref) {
    r.ref = ref;
    r.name = ParamName(ref.block, *ref.def);
    r.scale = ParamScale(ref.def->type);
    if (!KeyableType(ref.def->type, r.kind))
        throw BadParam(r.name + " is " + WithArticle(TypeName(ref.def->type)) + " parameter, which has no keys");
    if (!(ref.def->flags & P_ANIMATABLE)) throw BadParam(r.name + " is not animatable");
    r.ctrl = ref.block->GetControllerByID(ref.def->ID);
    r.parent = ref.block;
    r.sub = ref.block->GetAnimNum(ref.def->ID);
}

static int SubIndex(Animatable* parent, Animatable* child) {
    for (int i = 0; i < parent->NumSubs(); ++i)
        if (parent->SubAnim(i) == child) return i;
    return -1;
}

static void ResolveTransform(ResolvedTrack& r, INode* node, const std::string& base) {
    Control* tm = node->GetTMController();
    if (!tm) throw MaxError("'" + NodeName(node) + "' has no transform controller");
    const std::string path = Lower(base);
    auto axis = [&](Control* c, char a) -> Control* {
        if (!c) throw BadParam(r.label + ": the transform has no separate " + std::string(1, a) + " track");
        Control* sub = a == 'x' ? c->GetXController() : a == 'y' ? c->GetYController() : c->GetZController();
        if (!sub) throw BadParam(r.label + ": " + HandlerHelpers::MaxScriptVisibleClassName(c) + " has no separate " +
                                 std::string(1, a) + " track; use '" + path.substr(0, 3) + "'");
        r.parent = c;
        return sub;
    };
    r.parent = tm;
    if (path == "transform") {
        r.ctrl = tm; r.kind = TrackKind::Transform; r.parent = node;
    } else if (path == "pos" || path == "position") {
        r.ctrl = tm->GetPositionController(); r.kind = TrackKind::Point3;
    } else if (path.size() == 5 && path.rfind("pos.", 0) == 0 && std::strchr("xyz", path[4])) {
        r.ctrl = axis(tm->GetPositionController(), path[4]);
    } else if (path == "rot" || path == "rotation") {
        r.ctrl = tm->GetRotationController(); r.kind = TrackKind::Rotation;
    } else if (path.size() == 5 && path.rfind("rot.", 0) == 0 && std::strchr("xyz", path[4])) {
        r.ctrl = axis(tm->GetRotationController(), path[4]); r.scale = 180.0 / PI;
    } else if (path == "scale") {
        r.ctrl = tm->GetScaleController(); r.kind = TrackKind::Scale;
    } else {
        throw BadParam("unknown track '" + base + "'; use 'pos', 'pos.x'..'pos.z', 'rot', 'rot.x'..'rot.z', 'scale', "
                       "'transform', a sub-anim path like '[#Transform][#Position]', or params.track(name)");
    }
    if (!r.ctrl)
        throw MaxError(r.label + " has no controller; the transform is a " + HandlerHelpers::MaxScriptVisibleClassName(tm));
    r.sub = SubIndex(r.parent, r.ctrl);
}

static std::string StepKey(std::string name) {
    name = Lower(name);
    std::replace(name.begin(), name.end(), ' ', '_');
    return name;
}

static std::string SubName(Animatable* parent, int i, bool localized) {
    return WideToUtf8(parent->SubAnimName(i, localized).data());
}

// The sub-anim a step names: "#Name" or "Name" (any case, spaces or
// underscores), or a 1-based index.
static int FindSub(Animatable* parent, std::string step, const std::string& label) {
    const int count = parent->NumSubs();
    if (!step.empty() && std::all_of(step.begin(), step.end(), [](unsigned char c) { return std::isdigit(c); })) {
        const int index = std::atoi(step.c_str()) - 1;
        if (index >= 0 && index < count) return index;
        throw NotFound(label + ": no sub-anim [" + step + "]; there are " + std::to_string(count));
    }
    if (!step.empty() && step[0] == '#') step.erase(0, 1);
    const std::string key = StepKey(step);
    for (bool localized : {false, true})
        for (int i = 0; i < count; ++i)
            if (StepKey(SubName(parent, i, localized)) == key) return i;
    std::vector<std::string> names;
    for (int i = 0; i < count; ++i) names.push_back(SubName(parent, i, false));
    throw NotFound(label + ": no sub-anim '" + step + "'." + Suggest(step, names) +
                   (names.empty() ? " It has no sub-anims." : " Sub-anims: " + Join(names)));
}

static std::vector<std::string> SplitSteps(const std::string& steps, const std::string& label) {
    std::vector<std::string> out;
    size_t i = 0;
    while (i < steps.size()) {
        const size_t close = steps.find(']', i);
        if (steps[i] != '[' || close == std::string::npos)
            throw BadParam(label + ": a sub-anim path is a run of [#name] or [index] steps");
        out.push_back(steps.substr(i + 1, close - i - 1));
        i = close + 1;
    }
    return out;
}

static bool IsParamSlot(Animatable* parent, int sub, ParamRef* found = nullptr) {
    if (parent->SuperClassID() != PARAMETER_BLOCK2_CLASS_ID) return false;
    auto* block = static_cast<IParamBlock2*>(parent);
    int tab = 0;
    const int index = block->AnimNumToParamNum(sub, tab);
    if (index < 0 || tab != 0) return false;
    const ParamDef& def = block->GetParamDef(block->IndextoID(index));
    TrackKind kind;
    if (!KeyableType(def.type, kind)) return false;
    if (found) *found = ParamRef{block, &def};
    return true;
}

static void SetSlotTrack(ResolvedTrack& r, Animatable* parent, int sub) {
    if (!parent->IsRefMaker() || !static_cast<ReferenceMaker*>(parent)->IsRefTarget())
        throw BadParam(r.label + " is not a track 3ds Max can reach");
    r.parent = static_cast<ReferenceTarget*>(parent);
    r.sub = sub;
    r.ref.reset();
    r.scale = 1.0;
    ParamRef ref;
    if (IsParamSlot(parent, sub, &ref)) {
        r.owner = ref.block->GetOwner();
        SetParamTrack(r, ref);
        return;
    }
    Animatable* child = parent->SubAnim(sub);
    if (!IsController(child))
        throw BadParam(r.label + (child ? " is " + WithArticle(HandlerHelpers::MaxScriptVisibleClassName(child)) +
                                              ", not an animation track"
                                        : " is empty"));
    r.ctrl = AsController(child);
    r.kind = KindOf(r.ctrl);
    // Euler axes hold radians; read and write them in degrees.
    if (r.kind == TrackKind::Float && parent->SuperClassID() == CTRL_ROTATION_CLASS_ID) r.scale = 180.0 / PI;
}

static void WalkSteps(ResolvedTrack& r, Animatable* root, const std::string& steps) {
    if (!root) throw BadParam(r.label + ": the track has no controller to step into");
    Animatable* parent = root;
    const std::vector<std::string> tokens = SplitSteps(steps, r.label);
    for (size_t i = 0; i < tokens.size(); ++i) {
        const int sub = FindSub(parent, tokens[i], r.label);
        if (i + 1 == tokens.size()) return SetSlotTrack(r, parent, sub);
        Animatable* child = parent->SubAnim(sub);
        if (!child) throw NotFound(r.label + ": [" + tokens[i] + "] is empty");
        parent = child;
    }
}

ResolvedTrack ResolveTrack(const Track& tr) {
    ResolvedTrack r;
    r.label = TrackLabel(tr);
    if (tr.param) {
        r.owner = ParamOwner(Params{tr.node, tr.owner});
        SetParamTrack(r, RequireParam(r.owner, tr.path));
        return r;
    }
    INode* node = Resolve(Node{tr.node});
    const size_t bracket = tr.path.find('[');
    const std::string base = tr.path.substr(0, bracket);
    if (!base.empty()) ResolveTransform(r, node, base);
    if (bracket != std::string::npos) WalkSteps(r, base.empty() ? static_cast<Animatable*>(node) : r.ctrl, tr.path.substr(bracket));
    return r;
}

Track MakeTransformTrack(const Node& n, const std::string& path) {
    Track tr{n.handle, 0, path, false};
    ResolveTrack(tr);  // reject a bad path now, not at first use
    return tr;
}

SClass_ID TrackSuper(const ResolvedTrack& r) {
    if (r.ctrl) return r.ctrl->SuperClassID();
    return r.kind == TrackKind::Point3 ? CTRL_POINT3_CLASS_ID : CTRL_FLOAT_CLASS_ID;
}

void PutController(const ResolvedTrack& r, Control* c) {
    if (!r.parent || r.sub < 0) throw BadParam(r.label + " has no slot for a controller");
    // A track locked against reassignment in the UI stays locked here too.
    if (c && !r.parent->CanAssignController(r.sub)) throw BadParam(r.label + " does not accept a new controller");
    BOOL ok = TRUE;
    if (r.ref) {
        if (c) r.ref->block->SetControllerByID(r.ref->def->ID, 0, c, TRUE);
        else r.ref->block->RemoveControllerByIndex(r.ref->block->IDtoIndex(r.ref->def->ID), 0);
    } else if (r.parent->SuperClassID() == BASENODE_CLASS_ID) {
        ok = static_cast<INode*>(r.parent)->SetTMController(c);
    } else {
        ok = r.parent->AssignController(c, r.sub);
    }
    if (!ok) throw MaxError(r.label + ": 3ds Max refused the controller");
    r.parent->NotifyDependents(FOREVER, PART_ALL, REFMSG_CONTROLREF_CHANGE);
}

// ── Values ───────────────────────────────────────────────────────

static nb::object ControlValue(Control* c, TrackKind kind, double scale, TimeValue t) {
    Interval valid = FOREVER;
    switch (kind) {
    case TrackKind::Float: { float v = 0; c->GetValue(t, &v, valid, CTRL_ABSOLUTE); return nb::float_(v * scale); }
    case TrackKind::Point3: { Point3 v(0, 0, 0); c->GetValue(t, &v, valid, CTRL_ABSOLUTE); return FromPoint3(v); }
    case TrackKind::Scale: {
        ScaleValue v(Point3(1, 1, 1)); c->GetValue(t, &v, valid, CTRL_ABSOLUTE); return FromPoint3(v.s);
    }
    case TrackKind::Rotation: {
        Quat q; q.Identity(); c->GetValue(t, &q, valid, CTRL_ABSOLUTE);
        float angles[3] = {0, 0, 0};
        QuatToEuler(q, angles, EULERTYPE_XYZ);
        return MakePoint3(RadToDeg(angles[0]), RadToDeg(angles[1]), RadToDeg(angles[2]));
    }
    case TrackKind::Transform: {
        Matrix3 tm; tm.IdentityMatrix(); c->GetValue(t, &tm, valid, CTRL_RELATIVE);
        return nb::make_tuple(FromPoint3(tm.GetRow(0)), FromPoint3(tm.GetRow(1)), FromPoint3(tm.GetRow(2)),
                              FromPoint3(tm.GetRow(3)));
    }
    default: return nb::none();
    }
}

nb::object ValueAt(const ResolvedTrack& r, TimeValue t) {
    if (r.ctrl || !r.ref) return ControlValue(r.ctrl, r.kind, r.scale, t);
    // Not animated: the parameter's own value. A color comes from there as a
    // plain (r, g, b); a track gives it as the Point3 it is once animated.
    nb::object value = GetParam(*r.ref, t);
    if (BaseType(r.ref->def->type) != TYPE_RGBA) return value;
    if (!(r.ref->def->type & TYPE_TAB)) return MakePoint3(ToPoint3(value, r.name));
    nb::list colors;
    for (nb::handle color : value) colors.append(MakePoint3(ToPoint3(color, r.name)));
    return colors;
}

// A controller value in its internal units, for copying between times.
struct Sample {
    float f = 0;
    Point3 p = Point3(0, 0, 0);
    ScaleValue s = ScaleValue(Point3(1, 1, 1));
    Quat q;
    Matrix3 m;
};

static Sample Read(Control* c, TrackKind kind, TimeValue t) {
    Sample v;
    v.q.Identity();
    v.m.IdentityMatrix();
    Interval valid = FOREVER;
    switch (kind) {
    case TrackKind::Float: c->GetValue(t, &v.f, valid, CTRL_ABSOLUTE); break;
    case TrackKind::Point3: c->GetValue(t, &v.p, valid, CTRL_ABSOLUTE); break;
    case TrackKind::Scale: c->GetValue(t, &v.s, valid, CTRL_ABSOLUTE); break;
    case TrackKind::Rotation: c->GetValue(t, &v.q, valid, CTRL_ABSOLUTE); break;
    case TrackKind::Transform: c->GetValue(t, &v.m, valid, CTRL_RELATIVE); break;
    default: throw BadParam("a " + HandlerHelpers::MaxScriptVisibleClassName(c) + " value cannot be sampled");
    }
    return v;
}

// A transform is written through its position, rotation and scale parts.
static void Write(Control* c, TrackKind kind, TimeValue t, Sample v) {
    switch (kind) {
    case TrackKind::Float: c->SetValue(t, &v.f, 1, CTRL_ABSOLUTE); break;
    case TrackKind::Point3: c->SetValue(t, &v.p, 1, CTRL_ABSOLUTE); break;
    case TrackKind::Scale: c->SetValue(t, &v.s, 1, CTRL_ABSOLUTE); break;
    case TrackKind::Rotation: c->SetValue(t, &v.q, 1, CTRL_ABSOLUTE); break;
    case TrackKind::Transform: {
        AffineParts parts;
        decomp_affine(v.m, &parts);
        ScaleValue scale(parts.k * parts.f, parts.u);
        if (Control* pc = c->GetPositionController()) pc->SetValue(t, &parts.t, 1, CTRL_ABSOLUTE);
        if (Control* rc = c->GetRotationController()) rc->SetValue(t, &parts.q, 1, CTRL_ABSOLUTE);
        if (Control* sc = c->GetScaleController()) sc->SetValue(t, &scale, 1, CTRL_ABSOLUTE);
        break;
    }
    default: throw BadParam("a " + HandlerHelpers::MaxScriptVisibleClassName(c) + " value cannot be written");
    }
}

// The controllers that hold the keys: axes of XYZ controllers, the parts of a
// position/rotation/scale transform.
static void Leaves(Control* c, std::vector<Control*>& out) {
    if (!c) return;
    Control* axes[3] = {c->GetXController(), c->GetYController(), c->GetZController()};
    if (axes[0] || axes[1] || axes[2]) {
        for (Control* sub : axes) Leaves(sub, out);
        return;
    }
    if (c->SuperClassID() == CTRL_MATRIX3_CLASS_ID) {
        Control* parts[3] = {c->GetPositionController(), c->GetRotationController(), c->GetScaleController()};
        if (parts[0] || parts[1] || parts[2]) {
            for (Control* part : parts) Leaves(part, out);
            return;
        }
    }
    out.push_back(c);
}

static std::vector<Control*> Leaves(Control* c) {
    std::vector<Control*> out;
    Leaves(c, out);
    return out;
}

static bool Keyable(Control* leaf) { return GetKeyControlInterface(leaf) && leaf->IsKeyable(); }

// Keys on the leaves, or BadParam naming the procedural controller in the way.
static std::vector<Control*> KeyedLeaves(const ResolvedTrack& r) {
    if (!r.ctrl) throw BadParam(r.label + " is not animated; key it first");
    std::vector<Control*> leaves = Leaves(r.ctrl);
    for (Control* leaf : leaves)
        if (!Keyable(leaf)) throw BadParam(r.label + " is driven by " + CtrlClass(leaf) + ", which has no keys; bake() it first");
    return leaves;
}

static void KeyAt(Control* leaf, TimeValue t) {
    if (!leaf->IsKeyAtTime(t, 0)) leaf->AddNewKey(t, ADDKEY_INTERP);
}

static void KeyAt(const std::vector<Control*>& leaves, TimeValue t) {
    for (Control* leaf : leaves) KeyAt(leaf, t);
}

static void Changed(const std::vector<Control*>& controllers) {
    for (Control* c : controllers) c->NotifyDependents(FOREVER, PART_ALL, REFMSG_CHANGE);
}

// ── Keys ─────────────────────────────────────────────────────────

const char* const kTangents[] = {"smooth", "linear", "step", "fast", "slow", "custom", "auto"};

static int TangentType(const std::optional<std::string>& name) {
    if (!name) return -1;
    for (int i = 0; i < 7; ++i)
        if (Lower(*name) == kTangents[i]) return i;
    throw BadParam("unknown tangent '" + *name + "'; use smooth, linear, step, fast, slow, auto or custom");
}

static std::string TangentName(DWORD flags, bool in) {
    const int type = in ? GetInTanType(flags) : GetOutTanType(flags);
    return type >= 0 && type < 7 ? kTangents[type] : "other";
}

struct Key {
    double frame = 0;
    nb::object value;
    std::string in_tangent;
    std::string out_tangent;
};

static bool IsClass(Control* c, ULONG id) { return c->ClassID() == Class_ID(id, 0); }

static std::vector<Key> Keys(const ResolvedTrack& r) {
    std::vector<Key> out;
    if (!r.ctrl) return out;
    IKeyControl* kc = GetKeyControlInterface(r.ctrl);
    if (kc && IsClass(r.ctrl, HYBRIDINTERP_FLOAT_CLASS_ID)) {
        for (int i = 0; i < kc->GetNumKeys(); ++i) {
            IBezFloatKey k;
            kc->GetKey(i, &k);
            out.push_back({TicksToFrame(k.time), nb::float_(k.val * r.scale), TangentName(k.flags, true), TangentName(k.flags, false)});
        }
        return out;
    }
    if (kc && IsClass(r.ctrl, HYBRIDINTERP_SCALE_CLASS_ID)) {
        for (int i = 0; i < kc->GetNumKeys(); ++i) {
            IBezScaleKey k;
            kc->GetKey(i, &k);
            out.push_back({TicksToFrame(k.time), FromPoint3(k.val.s), TangentName(k.flags, true), TangentName(k.flags, false)});
        }
        return out;
    }
    // Composite and other controllers: key times and values, no tangents.
    const int count = r.ctrl->NumKeys();
    for (int i = 0; count > 0 && i < count; ++i) {
        const TimeValue t = r.ctrl->GetKeyTime(i);
        out.push_back({TicksToFrame(t), ValueAt(r, t), "", ""});
    }
    return out;
}

// Which keys an edit applies to: exact frames, a start..end range, or both.
struct KeyRange {
    std::vector<TimeValue> times;
    std::optional<TimeValue> start, end;
    bool Bounded() const { return !times.empty() || start || end; }
    bool Contains(TimeValue t) const {
        if (!times.empty() && std::find(times.begin(), times.end(), t) == times.end()) return false;
        return (!start || t >= *start) && (!end || t <= *end);
    }
};

static KeyRange MakeRange(const std::optional<std::vector<double>>& frames, std::optional<double> start,
                          std::optional<double> end) {
    KeyRange range;
    if (frames) {
        if (frames->empty()) throw BadParam("frames is empty; pass None for every key");
        for (double f : *frames) range.times.push_back(CheckedTicks(f, "frames"));
    }
    if (start) range.start = CheckedTicks(*start, "start");
    if (end) range.end = CheckedTicks(*end, "end");
    if (start && end && *range.start > *range.end) throw BadParam("start must not be after end");
    return range;
}

// The keys of each leaf in the range, by index.
struct LeafKeys {
    Control* leaf = nullptr;
    std::vector<int> picked;
};

static std::vector<LeafKeys> PickKeys(const ResolvedTrack& r, const KeyRange& range) {
    std::vector<LeafKeys> out;
    for (Control* leaf : Leaves(r.ctrl)) {
        LeafKeys keys{leaf, {}};
        for (int i = 0; i < leaf->NumKeys(); ++i)
            if (range.Contains(leaf->GetKeyTime(i))) keys.picked.push_back(i);
        if (!keys.picked.empty()) out.push_back(std::move(keys));
    }
    return out;
}

// Edits report keys as the track shows them: one per frame, across the axes.
static int FrameCount(const std::vector<LeafKeys>& picks) {
    std::set<TimeValue> frames;
    for (const LeafKeys& keys : picks)
        for (int i : keys.picked) frames.insert(keys.leaf->GetKeyTime(i));
    return (int)frames.size();
}

static std::vector<Control*> LeavesOf(const std::vector<LeafKeys>& picks) {
    std::vector<Control*> out;
    for (const LeafKeys& keys : picks) out.push_back(keys.leaf);
    return out;
}

static int SetTangents(const ResolvedTrack& r, const KeyRange& range, int in, int out) {
    if (in < 0 && out < 0) return 0;
    std::vector<LeafKeys> picks;
    for (const LeafKeys& keys : PickKeys(r, range))
        if (IsClass(keys.leaf, HYBRIDINTERP_FLOAT_CLASS_ID) || IsClass(keys.leaf, HYBRIDINTERP_SCALE_CLASS_ID))
            picks.push_back(keys);
    if (picks.empty()) throw BadParam(r.label + ": no Bezier keys there to set tangents on");
    for (const LeafKeys& keys : picks) {
        IKeyControl* kc = GetKeyControlInterface(keys.leaf);
        const bool isFloat = IsClass(keys.leaf, HYBRIDINTERP_FLOAT_CLASS_ID);
        for (int i : keys.picked) {
            IBezFloatKey fk;
            IBezScaleKey sk;
            IKey* key = isFloat ? static_cast<IKey*>(&fk) : static_cast<IKey*>(&sk);
            kc->GetKey(i, key);
            if (in >= 0) SetInTanType(key->flags, in);
            if (out >= 0) SetOutTanType(key->flags, out);
            kc->SetKey(i, key);
        }
    }
    Changed(LeavesOf(picks));
    return FrameCount(picks);
}

static void SetKey(const Track& tr, double frame, KeyValueArg given, const std::optional<std::string>& tangent,
                   const std::optional<std::string>& inTangent, const std::optional<std::string>& outTangent) {
    RequireMain();
    const nb::handle value = given.value;
    const TimeValue t = CheckedTicks(frame, "frame");
    const int in = TangentType(inTangent ? inTangent : tangent);
    const int out = TangentType(outTangent ? outTangent : tangent);
    ResolvedTrack r = ResolveTrack(tr);
    if (r.kind == TrackKind::Transform || r.kind == TrackKind::Other)
        throw BadParam(r.label + " takes no keys here; key 'pos', 'rot' and 'scale' instead");
    // A parameter that was never animated has no controller, and setting an
    // unchanged value would not make one: give it the default controller.
    if (!r.ctrl && r.ref) {
        Control* fresh = r.kind == TrackKind::Point3 ? NewDefaultPoint3Controller() : NewDefaultFloatController();
        if (!fresh) throw MaxError(r.label + ": 3ds Max could not create a controller");
        r.ref->block->SetControllerByID(r.ref->def->ID, 0, fresh, TRUE);
        r = ResolveTrack(tr);
    }
    {
        ForceAnimate animate;
        // No value keys the current one. A parameter's comes from the parameter
        // itself, in its own type: an int parameter refuses its controller's float.
        nb::object v = !value.is_none() ? nb::borrow(value) : r.ref ? GetParam(*r.ref, t) : ValueAt(r, t);
        if (r.ref) {
            SetParam(r.owner, r.name, v, t);
        } else if (r.kind == TrackKind::Float) {
            float f = float(nb::cast<double>(v) / r.scale);
            r.ctrl->SetValue(t, &f, 1, CTRL_ABSOLUTE);
        } else if (r.kind == TrackKind::Point3) {
            Point3 p = ToPoint3(v, tr.path);
            r.ctrl->SetValue(t, &p, 1, CTRL_ABSOLUTE);
        } else if (r.kind == TrackKind::Rotation) {
            const auto deg = Floats(v, 3, 3, tr.path);
            float angles[3] = {DegToRad(deg[0]), DegToRad(deg[1]), DegToRad(deg[2])};
            Quat q;
            EulerToQuat(angles, q, EULERTYPE_XYZ);
            r.ctrl->SetValue(t, &q, 1, CTRL_ABSOLUTE);
        } else {
            ScaleValue s(ToPoint3(v, tr.path));
            r.ctrl->SetValue(t, &s, 1, CTRL_ABSOLUTE);
        }
    }
    r = ResolveTrack(tr);  // a parameter gets its controller on its first key
    if (!r.ctrl) throw MaxError(r.label + ": 3ds Max did not create a key");
    // Setting an unchanged value records no key; add it explicitly.
    KeyAt(Leaves(r.ctrl), t);
    KeyRange at;
    at.times.push_back(t);
    SetTangents(r, at, in, out);
}

// ── Key edits ────────────────────────────────────────────────────

static int DeleteKeys(const Track& tr, const std::optional<std::vector<double>>& frames, std::optional<double> start,
                      std::optional<double> end) {
    const KeyRange range = MakeRange(frames, start, end);
    if (!range.Bounded()) throw BadParam("delete_keys needs frames or start/end; clear() deletes every key");
    const std::vector<LeafKeys> picks = PickKeys(ResolveTrack(tr), range);
    const int count = FrameCount(picks);
    for (const LeafKeys& keys : picks)
        for (auto it = keys.picked.rbegin(); it != keys.picked.rend(); ++it) keys.leaf->DeleteKeyByIndex(*it);
    Changed(LeavesOf(picks));
    return count;
}

// Move the picked keys to map(time). The whole plan is checked first, so a
// retime that would put two keys on one tick changes nothing.
template <typename Map>
static int Retime(const ResolvedTrack& r, const std::vector<LeafKeys>& picks, Map map, const char* what) {
    std::vector<std::vector<TimeValue>> plans;
    for (const LeafKeys& keys : picks) {
        if (!GetKeyControlInterface(keys.leaf))
            throw BadParam(r.label + ": " + CtrlClass(keys.leaf) + " keys cannot be retimed");
        std::set<TimeValue> kept, landed;
        for (int i = 0; i < keys.leaf->NumKeys(); ++i)
            if (std::find(keys.picked.begin(), keys.picked.end(), i) == keys.picked.end()) kept.insert(keys.leaf->GetKeyTime(i));
        std::vector<TimeValue> plan;
        for (int i : keys.picked) {
            const TimeValue t = map(keys.leaf->GetKeyTime(i));
            const std::string at = ReprOf(nb::float_(TicksToFrame(t)));
            if (!landed.insert(t).second) throw BadParam(r.label + ": " + what + " would put two keys on frame " + at);
            if (kept.count(t)) throw BadParam(r.label + ": " + what + " would land on the unselected key at frame " + at);
            plan.push_back(t);
        }
        plans.push_back(std::move(plan));
    }
    const int count = FrameCount(picks);
    for (size_t j = 0; j < picks.size(); ++j) {
        IKeyControl* kc = GetKeyControlInterface(picks[j].leaf);
        AnyKey key(kc->GetKeySize());
        for (size_t k = 0; k < picks[j].picked.size(); ++k) {
            kc->GetKey(picks[j].picked[k], key);
            static_cast<IKey*>(key)->time = plans[j][k];
            kc->SetKey(picks[j].picked[k], key);
        }
        kc->SortKeys();
    }
    Changed(LeavesOf(picks));
    return count;
}

static int MoveKeys(const Track& tr, double offset, const std::optional<std::vector<double>>& frames,
                    std::optional<double> start, std::optional<double> end) {
    const TimeValue shift = CheckedTicks(offset, "offset");
    if (shift == 0) throw BadParam("offset is under one tick");
    const ResolvedTrack r = ResolveTrack(tr);
    return Retime(r, PickKeys(r, MakeRange(frames, start, end)),
                  [&](TimeValue t) { return CheckedTicks(TicksToFrame(t) + offset, "the moved keys"); }, "the move");
}

static int ScaleKeys(const Track& tr, double factor, std::optional<double> pivot,
                     const std::optional<std::vector<double>>& frames, std::optional<double> start,
                     std::optional<double> end) {
    if (!std::isfinite(factor) || factor <= 0) throw BadParam("factor must be a positive number");
    const ResolvedTrack r = ResolveTrack(tr);
    const std::vector<LeafKeys> picks = PickKeys(r, MakeRange(frames, start, end));
    TimeValue origin = 0;
    if (pivot) origin = CheckedTicks(*pivot, "pivot");
    else if (start) origin = CheckedTicks(*start, "start");
    else if (!picks.empty()) {
        origin = picks[0].leaf->GetKeyTime(picks[0].picked[0]);
        for (const LeafKeys& keys : picks) origin = std::min(origin, keys.leaf->GetKeyTime(keys.picked[0]));
    }
    const double from = TicksToFrame(origin);
    return Retime(r, picks, [&](TimeValue t) { return CheckedTicks(from + (TicksToFrame(t) - from) * factor, "the scaled keys"); },
                  "the scale");
}

static std::vector<TimeValue> SampleTimes(std::optional<double> start, std::optional<double> end, double step) {
    const Interval range = GetCOREInterface()->GetAnimRange();
    const TimeValue first = start ? CheckedTicks(*start, "start") : range.Start();
    const TimeValue last = end ? CheckedTicks(*end, "end") : range.End();
    if (last < first) throw BadParam("end must not be before start");
    if (!std::isfinite(step) || step <= 0) throw BadParam("step must be a positive number of frames");
    const TimeValue dt = CheckedTicks(step, "step");
    if (dt <= 0) throw BadParam("step is under one tick");
    if ((long long)(last - first) / dt > 100000) throw BadParam("more than 100000 samples; raise step or narrow start/end");
    std::vector<TimeValue> times;
    for (long long t = first; t < last; t += dt) times.push_back((TimeValue)t);
    times.push_back(last);
    return times;
}

// Key every leaf at the times with its own sampled value; replace drops the
// leaf's other keys in the window first.
static int KeyLeaves(const std::vector<Control*>& leaves, const std::vector<TimeValue>& times, bool replace) {
    std::vector<std::vector<Sample>> samples;
    for (Control* leaf : leaves) {
        std::vector<Sample> values;
        for (TimeValue t : times) values.push_back(Read(leaf, KindOf(leaf), t));
        samples.push_back(std::move(values));
    }
    ForceAnimate animate;
    for (size_t j = 0; j < leaves.size(); ++j) {
        Control* leaf = leaves[j];
        for (int i = leaf->NumKeys() - 1; replace && i >= 0; --i) {
            const TimeValue t = leaf->GetKeyTime(i);
            if (t >= times.front() && t <= times.back()) leaf->DeleteKeyByIndex(i);
        }
        for (size_t k = 0; k < times.size(); ++k) {
            KeyAt(leaf, times[k]);
            Write(leaf, KindOf(leaf), times[k], samples[j][k]);
        }
    }
    Changed(leaves);
    return (int)times.size();
}

static Control* NewKeyable(const ResolvedTrack& r) {
    switch (TrackSuper(r)) {
    case CTRL_FLOAT_CLASS_ID: return NewDefaultFloatController();
    case CTRL_POINT3_CLASS_ID:
        return r.ref && BaseType(r.ref->def->type) == TYPE_RGBA ? NewDefaultColorController() : NewDefaultPoint3Controller();
    case CTRL_POSITION_CLASS_ID: return NewDefaultPositionController();
    case CTRL_ROTATION_CLASS_ID: return NewDefaultRotationController();
    case CTRL_SCALE_CLASS_ID: return NewDefaultScaleController();
    case CTRL_MATRIX3_CLASS_ID: return NewDefaultMatrix3Controller();
    default: return nullptr;
    }
}

// A node's transform relative to its parent. Read from the nodes, not the
// controller: a link or look-at controller may ignore the parent matrix.
static Sample LocalTM(INode* node, TimeValue t) {
    Sample v;
    v.m = node->GetNodeTM(t);
    if (INode* parent = node->GetParentNode(); parent && !parent->IsRootNode()) v.m = v.m * Inverse(parent->GetNodeTM(t));
    return v;
}

// A procedural track (script, constraint, wire, noise, list...) becomes the
// default keyable controller holding its sampled values.
static int BakeProcedural(const ResolvedTrack& r, const std::vector<TimeValue>& times) {
    INode* node = r.parent->SuperClassID() == BASENODE_CLASS_ID ? static_cast<INode*>(r.parent) : nullptr;
    std::vector<Sample> samples;
    for (TimeValue t : times) samples.push_back(node ? LocalTM(node, t) : Read(r.ctrl, r.kind, t));
    Control* fresh = NewKeyable(r);
    if (!fresh) throw BadParam(r.label + ": a " + CtrlClass(r.ctrl) + " track cannot be baked");
    PutController(r, fresh);
    const std::vector<Control*> leaves = Leaves(fresh);
    {
        ForceAnimate animate;
        for (size_t k = 0; k < times.size(); ++k) {
            KeyAt(leaves, times[k]);
            Write(fresh, r.kind, times[k], samples[k]);
        }
    }
    Changed(leaves);
    return (int)times.size();
}

static int Resample(const Track& tr, std::optional<double> start, std::optional<double> end, double step) {
    const std::vector<TimeValue> times = SampleTimes(start, end, step);
    return KeyLeaves(KeyedLeaves(ResolveTrack(tr)), times, false);
}

static int Bake(const Track& tr, std::optional<double> start, std::optional<double> end, double step) {
    const std::vector<TimeValue> times = SampleTimes(start, end, step);
    const ResolvedTrack r = ResolveTrack(tr);
    if (!r.ctrl) throw BadParam(r.label + " is not animated; there is nothing to bake");
    const std::vector<Control*> leaves = Leaves(r.ctrl);
    if (std::all_of(leaves.begin(), leaves.end(), Keyable)) return KeyLeaves(leaves, times, true);
    return BakeProcedural(r, times);
}

static ControllerValue Match(const Track& tr, std::optional<double> source, std::optional<double> target) {
    const ResolvedTrack r = ResolveTrack(tr);
    const std::vector<Control*> leaves = KeyedLeaves(r);
    std::set<TimeValue> keyed;
    for (Control* leaf : leaves)
        for (int i = 0; i < leaf->NumKeys(); ++i) keyed.insert(leaf->GetKeyTime(i));
    if ((!source || !target) && keyed.empty()) throw BadParam(r.label + " has no keys; pass source and target frames");
    const TimeValue from = source ? CheckedTicks(*source, "source") : *keyed.begin();
    const TimeValue to = target ? CheckedTicks(*target, "target") : *keyed.rbegin();
    std::vector<Sample> values;
    for (Control* leaf : leaves) values.push_back(Read(leaf, KindOf(leaf), from));
    {
        ForceAnimate animate;
        KeyAt(leaves, to);
        for (size_t j = 0; j < leaves.size(); ++j) Write(leaves[j], KindOf(leaves[j]), to, values[j]);
    }
    Changed(leaves);
    return ControllerValue{ValueAt(r, to)};  // keyed leaves: the track has a controller
}

// ── Out-of-range types ───────────────────────────────────────────

// Indexed by ORT_CONSTANT..ORT_RELATIVE_REPEAT.
const char* const kOrts[] = {"", "constant", "cycle", "loop", "ping_pong", "linear", "identity", "relative_repeat"};

static std::string OrtKey(std::string name) {
    name = Lower(name);
    name.erase(std::remove_if(name.begin(), name.end(), [](char c) { return c == '_' || c == '#'; }), name.end());
    return name;
}

static int OrtType(nb::handle value) {
    if (value.is_none()) return -1;
    if (!nb::isinstance<nb::str>(value)) throw BadParam("an out-of-range type is a name like 'cycle', not " + TypeNameOf(value));
    const std::string name = nb::cast<std::string>(value);
    for (int i = ORT_CONSTANT; i <= ORT_RELATIVE_REPEAT; ++i)
        if (i != ORT_IDENTITY && OrtKey(kOrts[i]) == OrtKey(name)) return i;
    throw BadParam("unknown out-of-range type '" + name + "'; use constant, cycle, loop, ping_pong, linear or relative_repeat");
}

static const char* OrtName(int type) { return type >= ORT_CONSTANT && type <= ORT_RELATIVE_REPEAT ? kOrts[type] : "unknown"; }

// (before, after) by name, or nothing for a parameter that is not animated.
static std::optional<std::pair<std::string, std::string>> GetOrt(const Track& tr) {
    const ResolvedTrack r = ResolveTrack(tr);
    if (!r.ctrl) return std::nullopt;
    const std::vector<Control*> leaves = Leaves(r.ctrl);
    auto keyed = std::find_if(leaves.begin(), leaves.end(), [](Control* c) { return c->NumKeys() > 0; });
    Control* c = keyed != leaves.end() ? *keyed : r.ctrl;
    return std::make_pair(std::string(OrtName(c->GetORT(ORT_BEFORE))), std::string(OrtName(c->GetORT(ORT_AFTER))));
}

static void SetOrt(const ResolvedTrack& r, int before, int after) {
    if (!r.ctrl) throw BadParam(r.label + " is not animated; key it before setting out-of-range types");
    std::vector<Control*> all = Leaves(r.ctrl);
    if (std::find(all.begin(), all.end(), r.ctrl) == all.end()) all.push_back(r.ctrl);
    for (Control* c : all) {
        if (before >= 0) c->SetORT(before, ORT_BEFORE);
        if (after >= 0) c->SetORT(after, ORT_AFTER);
    }
    Changed(all);
}

static void SetOrtValue(const Track& tr, OrtArg given) {
    const nb::handle value = given.value;
    int before = -1, after = -1;
    if (nb::isinstance<nb::tuple>(value) || nb::isinstance<nb::list>(value)) {
        std::vector<nb::handle> pair;
        for (nb::handle item : value) pair.push_back(item);
        if (pair.size() != 2) throw BadParam("ort takes one type or a (before, after) pair");
        before = OrtType(pair[0]);
        after = OrtType(pair[1]);
    } else {
        before = after = OrtType(value);
    }
    SetOrt(ResolveTrack(tr), before, after);
}

// ── TCB keys ─────────────────────────────────────────────────────

static bool IsTcb(Control* c) {
    for (ULONG id : {TCBINTERP_FLOAT_CLASS_ID, TCBINTERP_POSITION_CLASS_ID, TCBINTERP_ROTATION_CLASS_ID,
                     TCBINTERP_POINT3_CLASS_ID, TCBINTERP_SCALE_CLASS_ID, TCBINTERP_POINT4_CLASS_ID})
        if (IsClass(c, id)) return true;
    return false;
}

// MAXScript's TCB key properties, so values keep MAXScript's 0-50 scale.
static int SetTcb(const Track& tr, std::optional<double> tension, std::optional<double> continuity,
                  std::optional<double> bias, std::optional<double> easeTo, std::optional<double> easeFrom,
                  const std::optional<std::vector<double>>& frames, std::optional<double> start,
                  std::optional<double> end) {
    std::string assign;
    const std::pair<const char*, std::optional<double>> props[] = {
        {"tension", tension}, {"continuity", continuity}, {"bias", bias}, {"easeTo", easeTo}, {"easeFrom", easeFrom}};
    for (const auto& [name, value] : props) {
        if (!value) continue;
        if (!std::isfinite(*value) || *value < 0 || *value > 50) throw BadParam(std::string(name) + " must be 0-50");
        assign += "; k." + std::string(name) + " = " + std::to_string(*value);
    }
    if (assign.empty()) throw BadParam("pass tension, continuity, bias, ease_to or ease_from");
    const ResolvedTrack r = ResolveTrack(tr);
    std::vector<LeafKeys> picks;
    for (const LeafKeys& keys : PickKeys(r, MakeRange(frames, start, end)))
        if (IsTcb(keys.leaf)) picks.push_back(keys);
    if (picks.empty()) throw BadParam(r.label + ": no TCB keys there; Bezier keys take tangents() instead");
    std::string code = "(\n";
    for (const LeafKeys& keys : picks) {
        std::string indices;
        for (int i : keys.picked) indices += (indices.empty() ? "" : ",") + std::to_string(i + 1);
        code += "(local c = " + MxsAnim(keys.leaf) + "; for i in #(" +
                indices + ") do (local k = getKey c i" + assign + "))\n";
    }
    NativeJson("maxscript", {{"code", code + "true\n)"}});
    Changed(LeavesOf(picks));
    return FrameCount(picks);
}

// ── Track discovery ──────────────────────────────────────────────

constexpr size_t kMaxTracks = 2000;
constexpr size_t kMaxVisits = 100000;

// Each sub-anim's path step: [#Name], or [index] where the name is missing,
// shared with a sibling or not writable in a path.
static std::vector<std::string> Steps(Animatable* parent) {
    const int count = parent->NumSubs();
    std::vector<std::string> names(count);
    std::unordered_map<std::string, int> uses;
    for (int i = 0; i < count; ++i) ++uses[StepKey(names[i] = SubName(parent, i, false))];
    std::vector<std::string> steps(count);
    for (int i = 0; i < count; ++i) {
        const bool usable = !names[i].empty() && names[i].find(']') == std::string::npos &&
                            !std::all_of(names[i].begin(), names[i].end(), [](unsigned char c) { return std::isdigit(c); });
        steps[i] = usable && uses[StepKey(names[i])] == 1 ? "[#" + names[i] + "]" : "[" + std::to_string(i + 1) + "]";
    }
    return steps;
}

struct TrackWalk {
    AnimHandle node = 0;
    std::string filter;
    std::vector<Track> found;
    std::unordered_set<Animatable*> seen;
    size_t visits = 0;
};

static void Walk(TrackWalk& w, Animatable* anim, const std::string& path, int depth) {
    const std::vector<std::string> steps = Steps(anim);
    for (int i = 0; i < (int)steps.size(); ++i) {
        if (++w.visits > kMaxVisits)
            throw BadParam("this node has too many sub-anims to walk; pass a smaller depth=");
        Animatable* child = anim->SubAnim(i);
        const std::string here = path + steps[i];
        if (child ? IsController(child) : IsParamSlot(anim, i)) {
            const bool keep = w.filter.empty() || Lower(here).find(w.filter) != std::string::npos ||
                              (child && Lower(CtrlClass(AsController(child))).find(w.filter) != std::string::npos);
            if (keep) w.found.push_back(Track{w.node, 0, here, false});
            if (w.found.size() > kMaxTracks)
                throw BadParam("more than " + std::to_string(kMaxTracks) + " tracks; narrow with filter= or a smaller depth=");
        }
        if (child && depth > 1 && w.seen.insert(child).second) Walk(w, child, here, depth - 1);
    }
}

std::vector<Track> NodeTracks(const Node& n, const std::optional<std::string>& filter, int depth) {
    INode* node = Resolve(n);
    if (depth < 1 || depth > 12) throw BadParam("depth must be 1-12");
    TrackWalk w;
    w.node = n.handle;
    w.filter = filter ? Lower(*filter) : "";
    w.seen.insert(node);
    Walk(w, node, "", depth);
    return w.found;
}

static std::string TrackRepr(const Track& tr) {
    try {
        const ResolvedTrack r = ResolveTrack(tr);
        if (!r.ctrl) return "<Track " + r.label + ": not animated>";
        return "<Track " + r.label + ": " + CtrlClass(r.ctrl) + ", " + std::to_string(Keys(r).size()) + " keys>";
    } catch (const MaxError&) {
        return "<Track " + TrackLabel(tr) + ", unavailable>";
    }
}

// ── Binding ──────────────────────────────────────────────────────

void BindTracks(nb::module_& m) {
    nb::class_<AtTime>(m, "AtTime",
        "What at(frame) returns: inside `with sdk.at(10):`, reads and edits happen at that frame instead of the time slider's.")
        .def("__enter__", [](const AtTime& a) { RequireMain(); TimeStack().push_back(a.time); })
        .def("__exit__", [](const AtTime&, nb::args) { if (!TimeStack().empty()) TimeStack().pop_back(); });

    nb::class_<AnimateMode>(m, "AnimateMode",
        "What animate(on) returns: inside `with sdk.animate():`, edits set keys (Auto Key); animate(False) turns keying "
        "off for the block. The mode before the block comes back after it.")
        .def("__enter__", [](AnimateMode& a) {
            RequireMain();
            a.previous = Animating();
            if (a.on) AnimateOn(); else AnimateOff();
        })
        .def("__exit__", [](AnimateMode& a, nb::args) { if (a.previous) AnimateOn(); else AnimateOff(); });

    nb::class_<Key>(m, "Key", "A key: frame, value, in_tangent, out_tangent (a snapshot).")
        .def_ro("frame", &Key::frame, "The key's time, in frames.")
        .def_prop_ro("value", [](const Key& k) { return ControllerValue{k.value}; },
            "The key's value, as the track's value() gives it at the key: a number, a Point3 (a rotation's Euler "
            "degrees), four Point3 rows on a transform track, None on a kind value() does not read.")
        .def_ro("in_tangent", &Key::in_tangent,
            "Incoming tangent type: smooth, linear, step, fast, slow, auto or custom; '' on a controller without tangents.")
        .def_ro("out_tangent", &Key::out_tangent, "Outgoing tangent type, named like in_tangent.")
        .def("__repr__", [](const Key& k) {
            std::string tangents = k.in_tangent.empty() ? "" : " " + k.in_tangent + "/" + k.out_tangent;
            return "<Key " + ReprOf(nb::float_(k.frame)) + ": " + ReprOf(k.value) + tangents + ">";
        });

    using Frames = std::optional<std::vector<double>>;
    using Frame = std::optional<double>;
    nb::class_<Track>(m, "Track",
        "An animation track: node.track('pos.z'), params.track('radius'), node.tracks()[i]. Node paths: 'pos', "
        "'pos.x'..'pos.z', 'rot' and 'rot.x'..'rot.z' (degrees), 'scale', 'transform', a sub-anim path "
        "'[#Transform][#Position]', or a path plus steps: 'pos[2]' is a list controller's 2nd layer. Key edits "
        "take frames=[...] and/or start=/end=.")
        .def_prop_ro("path", [](const Track& tr) { return tr.path; }, "The track's path, or its parameter's name.")
        .def_prop_ro("keys", [](const Track& tr) { return Keys(ResolveTrack(tr)); }, "The keys, in time order.")
        .def("key", &SetKey, "frame"_a, "value"_a.none() = nb::none(), "tangent"_a = nb::none(),
             "in_tangent"_a = nb::none(), "out_tangent"_a = nb::none(),
             "Set a key at frame (value None keys the current value): a number, (x, y, z), a rotation's Euler degrees, "
             "or on a parameter track what the parameter takes. tangent: smooth, linear, step, fast, slow, auto.")
        .def("tangents", [](const Track& tr, const std::optional<std::string>& tangent,
                            const std::optional<std::string>& inTangent, const std::optional<std::string>& outTangent,
                            const Frames& frames, Frame start, Frame end) {
            return SetTangents(ResolveTrack(tr), MakeRange(frames, start, end), TangentType(inTangent ? inTangent : tangent),
                               TangentType(outTangent ? outTangent : tangent));
        }, "tangent"_a = nb::none(), "in_tangent"_a = nb::none(), "out_tangent"_a = nb::none(), "frames"_a = nb::none(),
           "start"_a = nb::none(), "end"_a = nb::none(),
           "Set Bezier tangent types (all keys by default); returns how many keys changed: tangents('smooth', start=0, end=50).")
        .def("tcb", &SetTcb, "tension"_a = nb::none(), "continuity"_a = nb::none(), "bias"_a = nb::none(),
             "ease_to"_a = nb::none(), "ease_from"_a = nb::none(), "frames"_a = nb::none(), "start"_a = nb::none(),
             "end"_a = nb::none(),
             "Set TCB key settings, 0-50 as in MAXScript (all keys by default); returns how many keys changed.")
        .def("value", [](const Track& tr, std::optional<double> frame) {
            RequireMain();
            return TrackValue{ValueAt(ResolveTrack(tr), frame ? CheckedTicks(*frame, "frame") : Now())};
        }, "frame"_a = nb::none(),
           "The track's value at frame (default: the current one): a number, a Point3 (a rotation as Euler degrees), "
           "four Point3 rows for 'transform', None for a kind that is not read (point4). A parameter that is not "
           "animated gives its own value.")
        .def("clear", [](const Track& tr) {
            const std::vector<Control*> leaves = Leaves(ResolveTrack(tr).ctrl);
            for (Control* leaf : leaves) leaf->DeleteKeys(TRACK_DOALL);
            Changed(leaves);
        }, "Delete every key; the value at the current frame stays.")
        .def("delete_keys", &DeleteKeys, "frames"_a = nb::none(), "start"_a = nb::none(), "end"_a = nb::none(),
             "Delete the keys at frames or in start..end; returns how many: delete_keys(start=10, end=20).")
        .def("move_keys", &MoveKeys, "offset"_a, "frames"_a = nb::none(), "start"_a = nb::none(), "end"_a = nb::none(),
             "Shift keys by offset frames (all keys by default); refuses to land on another key. Returns how many moved.")
        .def("scale_keys", &ScaleKeys, "factor"_a, "pivot"_a = nb::none(), "frames"_a = nb::none(),
             "start"_a = nb::none(), "end"_a = nb::none(),
             "Scale key times around pivot (default start, else the first key): scale_keys(2.0) doubles the timing.")
        .def("resample", &Resample, "start"_a = nb::none(), "end"_a = nb::none(), "step"_a = 1.0,
             "Add a key every step frames in start..end (default: the timeline), keeping the curve; returns keys per axis.")
        .def("bake", &Bake, "start"_a = nb::none(), "end"_a = nb::none(), "step"_a = 1.0,
             "Replace start..end (default: the timeline) with a key every step frames of the evaluated value; a script, "
             "constraint, wire, noise or list controller becomes a keyable one. Returns keys per axis.")
        .def("match", &Match, "source"_a = nb::none(), "target"_a = nb::none(),
             "Key at target the value at source (default: the first key onto the last); returns the new value.")
        .def("loop", [](const Track& tr, std::optional<double> start, std::optional<double> end) {
            Match(tr, start, end);
            SetOrt(ResolveTrack(tr), ORT_CYCLE, ORT_CYCLE);
        }, "start"_a = nb::none(), "end"_a = nb::none(),
           "Close a cycle: key at end the value at start (default first and last key) and repeat it outside (ort cycle).")
        .def_prop_rw("ort", &GetOrt, &SetOrtValue,
            "Out-of-range types (before, after), None if not animated; set one name or a pair, where None keeps "
            "that side: constant, cycle, loop, ping_pong, linear, relative_repeat.")
        .def("__len__", [](const Track& tr) { return Keys(ResolveTrack(tr)).size(); })
        .def("__repr__", &TrackRepr);

    AddMethod<Node>("tracks", &NodeTracks, "filter"_a = nb::none(), "depth"_a = 6,
        "Every animatable track under the node as sub-anim path tracks, filtered by a substring of the path or "
        "controller class: node.tracks('radius').");

    m.def("at", [](double frame) { return AtTime{CheckedTicks(frame, "frame")}; }, "frame"_a,
          "with at(frame): reads and edits happen at that frame; the time slider does not move.");
    m.def("animate", [](bool on) { return AnimateMode{on}; }, "on"_a = true,
          "with animate(): edits create keys, like the Auto Key button.");
    m.def("frame", [] { RequireMain(); return TicksToFrame(GetCOREInterface()->GetTime()); }, "Current frame.");
    m.def("set_frame", [](double frame) {
        RequireMain();
        GetCOREInterface()->SetTime(CheckedTicks(frame, "frame"));
    }, "frame"_a, "Move the time slider.");
    m.def("timeline", [](std::optional<double> start, std::optional<double> end,
                         std::optional<int> fps) -> nb::typed<nb::tuple, double, double, int> {
        RequireMain();
        Interface* ip = GetCOREInterface();
        if (fps) {
            if (*fps < 1 || *fps > 1000) throw BadParam("fps must be 1-1000");
            SetFrameRate(*fps);
        }
        if (start || end) {
            const Interval current = ip->GetAnimRange();
            const TimeValue s = start ? CheckedTicks(*start, "start") : current.Start();
            const TimeValue e = end ? CheckedTicks(*end, "end") : current.End();
            if (e <= s) throw BadParam("timeline end must be after start");
            ip->SetAnimRange(Interval(s, e));
        }
        const Interval range = ip->GetAnimRange();
        return nb::make_tuple(TicksToFrame(range.Start()), TicksToFrame(range.End()), GetFrameRate());
    }, "start"_a = nb::none(), "end"_a = nb::none(), "fps"_a = nb::none(),
       "Animation range and frame rate: (start, end, fps); pass any to change it.");
}

} // namespace MX_ABI_TAG
} // namespace mx
