// Time and animation: at()/animate() contexts, the timeline, tracks and keys.
#include "mx_common.h"

namespace mx {
inline namespace MX_ABI_TAG {

// ── Time contexts ────────────────────────────────────────────────

struct AtTime { TimeValue time = 0; };
struct AnimateMode { bool on = true; int previous = 0; };

// ── Tracks ───────────────────────────────────────────────────────

enum class TrackKind { Float, Point3, Scale };

struct ResolvedTrack {
    Control* ctrl = nullptr;           // null: a parameter that is not animated yet
    TrackKind kind = TrackKind::Float;
    double scale = 1.0;                // external value = internal * scale
    Animatable* owner = nullptr;       // parameter tracks
    std::optional<ParamRef> ref;
    std::string label;
};

static std::string TrackLabel(const Track& tr) {
    std::string where = "#" + std::to_string(tr.node);
    if (INode* node = HandlerHelpers::FindNodeByHandle(tr.node); InScene(node)) where = "'" + NodeName(node) + "'";
    else if (tr.owner) where = "#" + std::to_string(tr.owner);
    return where + " " + tr.path;
}

static ResolvedTrack ResolveTrack(const Track& tr) {
    ResolvedTrack r;
    r.label = TrackLabel(tr);
    if (tr.param) {
        r.owner = ParamOwner(Params{tr.node, tr.owner});
        r.ref = RequireParam(r.owner, tr.path);
        const int type = BaseType(r.ref->def->type);
        r.scale = ParamScale(r.ref->def->type);
        switch (type) {
        case TYPE_POINT3: case TYPE_RGBA: r.kind = TrackKind::Point3; break;
        case TYPE_FLOAT: case TYPE_WORLD: case TYPE_ANGLE: case TYPE_PCNT_FRAC: case TYPE_COLOR_CHANNEL:
        case TYPE_INT: case TYPE_INDEX: case TYPE_BOOL: r.kind = TrackKind::Float; break;
        default: throw BadParam(tr.path + " is " + WithArticle(TypeName(type)) + " parameter, which has no keys");
        }
        if (!(r.ref->def->flags & P_ANIMATABLE)) throw BadParam(tr.path + " is not animatable");
        r.ctrl = r.ref->block->GetControllerByID(r.ref->def->ID);
        return r;
    }
    INode* node = Resolve(Node{tr.node});
    Control* tm = node->GetTMController();
    if (!tm) throw MaxError("'" + NodeName(node) + "' has no transform controller");
    const std::string path = Lower(tr.path);
    auto axis = [&](Control* c, char a) -> Control* {
        Control* sub = a == 'x' ? c->GetXController() : a == 'y' ? c->GetYController() : c->GetZController();
        if (!sub) throw BadParam(tr.path + ": the controller has no separate " + std::string(1, a) + " track");
        return sub;
    };
    if (path == "pos" || path == "position") {
        r.ctrl = tm->GetPositionController(); r.kind = TrackKind::Point3;
    } else if (path.size() == 5 && path.rfind("pos.", 0) == 0 && std::strchr("xyz", path[4])) {
        r.ctrl = axis(tm->GetPositionController(), path[4]);
    } else if (path.size() == 5 && path.rfind("rot.", 0) == 0 && std::strchr("xyz", path[4])) {
        r.ctrl = axis(tm->GetRotationController(), path[4]); r.scale = 180.0 / PI;
    } else if (path == "scale") {
        r.ctrl = tm->GetScaleController(); r.kind = TrackKind::Scale;
    } else if (path == "rot" || path == "rotation") {
        throw BadParam("rotation keys are per axis: use 'rot.x', 'rot.y' or 'rot.z'");
    } else {
        throw BadParam("unknown track '" + tr.path + "'; use 'pos', 'pos.x'..'pos.z', 'rot.x'..'rot.z' or 'scale', "
                       "or params.track(name) for a parameter");
    }
    if (!r.ctrl) throw MaxError(r.label + " has no controller");
    return r;
}

Track MakeTransformTrack(const Node& n, const std::string& path) {
    Track tr{n.handle, 0, path, false};
    ResolveTrack(tr);  // reject a bad path now, not at first use
    return tr;
}

static nb::object ValueAt(const ResolvedTrack& r, TimeValue t) {
    if (!r.ctrl && r.ref) return GetParam(*r.ref, t);
    Interval valid = FOREVER;
    switch (r.kind) {
    case TrackKind::Float: { float v = 0; r.ctrl->GetValue(t, &v, valid, CTRL_ABSOLUTE); return nb::float_(v * r.scale); }
    case TrackKind::Point3: { Point3 v; r.ctrl->GetValue(t, &v, valid, CTRL_ABSOLUTE); return FromPoint3(v); }
    case TrackKind::Scale: {
        ScaleValue v(Point3(1, 1, 1)); r.ctrl->GetValue(t, &v, valid, CTRL_ABSOLUTE); return FromPoint3(v.s);
    }
    }
    return nb::none();
}

static void Leaves(Control* c, std::vector<Control*>& out) {
    if (!c) return;
    Control* subs[3] = {c->GetXController(), c->GetYController(), c->GetZController()};
    if (subs[0] || subs[1] || subs[2]) {
        for (Control* sub : subs) Leaves(sub, out);
        return;
    }
    out.push_back(c);
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

// Tangent types of the keys at `times` (all keys when empty) on every Bezier
// leaf of the track. Returns the number of keys changed.
static int SetTangents(const ResolvedTrack& r, const std::vector<TimeValue>& times, int in, int out) {
    if (in < 0 && out < 0) return 0;
    std::vector<Control*> leaves;
    Leaves(r.ctrl, leaves);
    int changed = 0;
    auto wanted = [&](TimeValue t) { return times.empty() || std::find(times.begin(), times.end(), t) != times.end(); };
    for (Control* leaf : leaves) {
        IKeyControl* kc = GetKeyControlInterface(leaf);
        if (!kc) continue;
        const bool isFloat = IsClass(leaf, HYBRIDINTERP_FLOAT_CLASS_ID);
        const bool isScale = IsClass(leaf, HYBRIDINTERP_SCALE_CLASS_ID);
        if (!isFloat && !isScale) continue;
        for (int i = 0; i < kc->GetNumKeys(); ++i) {
            IBezFloatKey fk;
            IBezScaleKey sk;
            IKey* key = isFloat ? static_cast<IKey*>(&fk) : static_cast<IKey*>(&sk);
            kc->GetKey(i, key);
            if (!wanted(key->time)) continue;
            if (in >= 0) SetInTanType(key->flags, in);
            if (out >= 0) SetOutTanType(key->flags, out);
            kc->SetKey(i, key);
            ++changed;
        }
        leaf->NotifyDependents(FOREVER, PART_ALL, REFMSG_CHANGE);
    }
    if (!changed) throw BadParam(r.label + ": no Bezier keys there to set tangents on");
    return changed;
}

static void SetKey(const Track& tr, double frame, nb::handle value, const std::optional<std::string>& tangent,
                   const std::optional<std::string>& inTangent, const std::optional<std::string>& outTangent) {
    RequireMain();
    const TimeValue t = FrameToTicks(frame);
    const int in = TangentType(inTangent ? inTangent : tangent);
    const int out = TangentType(outTangent ? outTangent : tangent);
    ResolvedTrack r = ResolveTrack(tr);
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
        nb::object v = value.is_none() ? ValueAt(r, t) : nb::borrow(value);
        if (r.ref) {
            SetParam(r.owner, tr.path, v, t);
        } else if (r.kind == TrackKind::Float) {
            float f = float(nb::cast<double>(v) / r.scale);
            r.ctrl->SetValue(t, &f, 1, CTRL_ABSOLUTE);
        } else if (r.kind == TrackKind::Point3) {
            Point3 p = ToPoint3(v, tr.path);
            r.ctrl->SetValue(t, &p, 1, CTRL_ABSOLUTE);
        } else {
            ScaleValue s(ToPoint3(v, tr.path));
            r.ctrl->SetValue(t, &s, 1, CTRL_ABSOLUTE);
        }
    }
    r = ResolveTrack(tr);  // a parameter gets its controller on its first key
    if (!r.ctrl) throw MaxError(r.label + ": 3ds Max did not create a key");
    // Setting an unchanged value records no key; add it explicitly.
    std::vector<Control*> leaves;
    Leaves(r.ctrl, leaves);
    for (Control* leaf : leaves)
        if (!leaf->IsKeyAtTime(t, 0)) leaf->AddNewKey(t, ADDKEY_INTERP);
    SetTangents(r, {t}, in, out);
}

static std::string TrackRepr(const Track& tr) {
    try {
        return "<Track " + TrackLabel(tr) + ", " + std::to_string(Keys(ResolveTrack(tr)).size()) + " keys>";
    } catch (const MaxError&) {
        return "<Track " + TrackLabel(tr) + ", unavailable>";
    }
}

// ── Binding ──────────────────────────────────────────────────────

void BindAnimation(nb::module_& m) {
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

    nb::class_<Key>(m, "Key", "A key: frame, value, in_tangent, out_tangent (a snapshot).")
        .def_ro("frame", &Key::frame)
        .def_ro("value", &Key::value)
        .def_ro("in_tangent", &Key::in_tangent)
        .def_ro("out_tangent", &Key::out_tangent)
        .def("__repr__", [](const Key& k) {
            std::string tangents = k.in_tangent.empty() ? "" : " " + k.in_tangent + "/" + k.out_tangent;
            return "<Key " + ReprOf(nb::float_(k.frame)) + ": " + ReprOf(k.value) + tangents + ">";
        });

    nb::class_<Track>(m, "Track", "An animation track; node.track('pos.z') or params.track('radius').")
        .def_prop_ro("keys", [](const Track& tr) { return Keys(ResolveTrack(tr)); }, "The keys, in time order.")
        .def("key", &SetKey, "frame"_a, "value"_a = nb::none(), "tangent"_a = nb::none(),
             "in_tangent"_a = nb::none(), "out_tangent"_a = nb::none(),
             "Set a key at frame (value None keys the current value). tangent: smooth, linear, step, fast, slow, auto.")
        .def("tangents", [](const Track& tr, const std::optional<std::string>& tangent,
                            const std::optional<std::string>& inTangent, const std::optional<std::string>& outTangent,
                            const std::optional<std::vector<double>>& frames) {
            std::vector<TimeValue> times;
            if (frames) for (double f : *frames) times.push_back(FrameToTicks(f));
            return SetTangents(ResolveTrack(tr), times, TangentType(inTangent ? inTangent : tangent),
                               TangentType(outTangent ? outTangent : tangent));
        }, "tangent"_a = nb::none(), "in_tangent"_a = nb::none(), "out_tangent"_a = nb::none(), "frames"_a = nb::none(),
           "Set tangent types of the keys at frames (all keys by default); returns how many changed.")
        .def("value", [](const Track& tr, std::optional<double> frame) {
            RequireMain();
            return ValueAt(ResolveTrack(tr), frame ? FrameToTicks(*frame) : Now());
        }, "frame"_a = nb::none(), "The track's value at frame (default: the current one).")
        .def("clear", [](const Track& tr) {
            ResolvedTrack r = ResolveTrack(tr);
            std::vector<Control*> leaves;
            Leaves(r.ctrl, leaves);
            for (Control* leaf : leaves) leaf->DeleteKeys(TRACK_DOALL);
        }, "Delete every key; the value at the current frame stays.")
        .def("__len__", [](const Track& tr) { return Keys(ResolveTrack(tr)).size(); })
        .def("__repr__", &TrackRepr);

    m.def("at", [](double frame) { return AtTime{FrameToTicks(frame)}; }, "frame"_a,
          "with at(frame): reads and edits happen at that frame; the time slider does not move.");
    m.def("animate", [](bool on) { return AnimateMode{on}; }, "on"_a = true,
          "with animate(): edits create keys, like the Auto Key button.");
    m.def("frame", [] { RequireMain(); return TicksToFrame(GetCOREInterface()->GetTime()); }, "Current frame.");
    m.def("set_frame", [](double frame) {
        RequireMain();
        GetCOREInterface()->SetTime(FrameToTicks(frame));
    }, "frame"_a, "Move the time slider.");
    m.def("timeline", [](std::optional<double> start, std::optional<double> end, std::optional<int> fps) {
        RequireMain();
        Interface* ip = GetCOREInterface();
        if (fps) {
            if (*fps < 1 || *fps > 1000) throw BadParam("fps must be 1-1000");
            SetFrameRate(*fps);
        }
        if (start || end) {
            const Interval current = ip->GetAnimRange();
            const TimeValue s = start ? FrameToTicks(*start) : current.Start();
            const TimeValue e = end ? FrameToTicks(*end) : current.End();
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
