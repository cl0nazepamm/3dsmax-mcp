// Tracks resolved to their controller and the slot that holds it, shared by
// mx_anim.cpp (time, keys, key edits) and mx_animation.cpp (controllers,
// wiring, script controllers).
#pragma once

#include "mx_common.h"

namespace mx {
inline namespace MX_ABI_TAG {

enum class TrackKind { Float, Point3, Scale, Rotation, Transform, Other };

struct ResolvedTrack {
    Control* ctrl = nullptr;            // null: a parameter that is not animated yet
    TrackKind kind = TrackKind::Float;
    double scale = 1.0;                 // external value = internal * scale
    Animatable* owner = nullptr;        // parameter tracks: the parameter's owner ...
    std::optional<ParamRef> ref;        // ... the parameter ...
    std::string name;                   // ... and its name
    ReferenceTarget* parent = nullptr;  // holds the track as its sub-anim `sub`:
    int sub = -1;                       //   new controllers and wires go there
    std::string label;
};

// A frame as ticks; BadParam names `what` when 3ds Max cannot hold the time.
TimeValue CheckedTicks(double frame, const char* what);
ResolvedTrack ResolveTrack(const Track& tr);
TrackKind KindOf(Control* c);
// The controller superclass the track takes: a new controller must be of it.
SClass_ID TrackSuper(const ResolvedTrack& r);
nb::object ValueAt(const ResolvedTrack& r, TimeValue t);
// Put c in the track's slot (c is null: a parameter becomes unanimated).
void PutController(const ResolvedTrack& r, Control* c);
bool IsController(Animatable* anim);
// Every track under the node: controllers and animatable parameters.
std::vector<Track> NodeTracks(const Node& n, const std::optional<std::string>& filter, int depth);

// Keeps a controller alive while it is out of any slot (an old one that may go
// back, a new one not attached yet); afterwards it is freed if nothing
// references it.
struct KeepAlive {
    Control* ctrl;
    explicit KeepAlive(Control* c) : ctrl(c && !c->TestAFlag(A_LOCK_TARGET) ? c : nullptr) {
        if (ctrl) ctrl->SetAFlag(A_LOCK_TARGET);
    }
    ~KeepAlive() {
        if (!ctrl) return;
        ctrl->ClearAFlag(A_LOCK_TARGET);
        ctrl->MaybeAutoDelete();
    }
    KeepAlive(const KeepAlive&) = delete;
    KeepAlive& operator=(const KeepAlive&) = delete;
};

} // namespace MX_ABI_TAG
} // namespace mx
