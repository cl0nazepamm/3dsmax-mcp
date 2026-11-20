#include "mcp_bridge/script_hold.h"

#include "mcp_bridge/core_bridge.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/quiet_policy.h"

#include <control.h>
#include <hold.h>
#include <nlohmann/json.hpp>
#include <stdexcept>
#include <vector>

using json = nlohmann::json;

namespace ScriptHold {
namespace {

// The shell's session for in-process calls (python_binding.cpp).
constexpr const char* kInProcessSession = "python-inproc";

struct State {
    bool active = false;
    bool open = false;       // our theHold.Begin() is open
    bool superOpen = false;  // our theHold.SuperBegin() is open
    std::wstring label = L"MCP run_python";
    int putBase = 0;         // theHold.GetGlobalPutCount() when the current step opened
    int ownPuts = 0;         // records since then that are the step's own (its canaries)
    int breaks = 0;          // steps committed early by undo, reset or fetch
    int depth = 0;           // in-process dispatches in progress
    AnimHandle canary = 0;   // see PutCanary: the open hold's
    std::vector<AnimHandle> canaries;  // every canary of the current step
    bool lost = false;       // the current step lost records it had made
    bool lostEarlier = false;  // a step committed early (breaks) had lost records
    bool unrecorded = false;   // the script left the hold suspended
};

State& S() {
    static State state;
    return state;
}

bool Changed() { return theHold.GetGlobalPutCount() != S().putBase + S().ownPuts; }

// A canary for the records of the hold that was just opened. 3ds Max defers
// the delete of an unreferenced object while it is holding, in a record of its
// own, and the object dies when that record is thrown away unrestored. So a
// canary that is gone while its hold is still open says the hold's records were
// discarded: a full MAXScript gc() releases them, and so does a script's own
// theHold.Cancel(). The record is 3ds Max's, so nothing of this DLL stays in
// the undo history (undo_records.h). Holds already accepted into the step are
// not affected by a collection; only the open one needs watching.
void PutCanary() {
    State& s = S();
    s.canary = 0;
    if (!theHold.Holding()) return;
    const int before = theHold.GetGlobalPutCount();
    if (Control* canary = NewDefaultFloatController()) {
        const AnimHandle handle = Animatable::GetHandleByAnim(canary);
        canary->MaybeAutoDelete();
        // Not deferred after all: it is already gone, and there is no canary.
        if (Animatable::GetAnimByHandle(handle)) {
            s.canary = handle;
            s.canaries.push_back(handle);
        }
    }
    s.ownPuts += theHold.GetGlobalPutCount() - before;
}

// A step that is cancelled restores its canaries' deferred deletes, which
// leaves them alive with no owner; a committed step's belong to its undo entry.
void FreeCanaries(bool kept) {
    State& s = S();
    if (!kept) {
        for (const AnimHandle handle : s.canaries) {
            Animatable* canary = Animatable::GetAnimByHandle(handle);
            if (canary) static_cast<Control*>(canary)->MaybeAutoDelete();
        }
    }
    s.canaries.clear();
}

// Before the open hold is accepted or cancelled: did it keep its records?
void CheckCanary() {
    State& s = S();
    if (s.canary && !Animatable::GetAnimByHandle(s.canary)) s.lost = true;
    s.canary = 0;
}

void OpenStep() {
    State& s = S();
    theHold.SuperBegin();
    s.superOpen = true;
    s.putBase = theHold.GetGlobalPutCount();
    s.ownPuts = 0;
    theHold.Begin();
    s.open = true;
    PutCanary();
}

// Close the hold and the step. commit=false rolls them back. A step that
// recorded nothing is cancelled, so it leaves no empty undo entry.
bool CloseStep(bool commit) {
    State& s = S();
    // The step opened with the hold not suspended (begin refuses otherwise), so
    // a suspension now is one the script left behind. While it lasts Accept and
    // Cancel do nothing and the step would close without restoring anything.
    for (int guard = 0; theHold.IsSuspended() && guard < 4096; ++guard) {
        theHold.Resume();
        s.unrecorded = true;
    }
    CheckCanary();
    const bool changed = Changed();
    // A leaked hold inside the step is closed with it.
    for (int guard = 0; theHold.Holding() && guard < 32; ++guard) {
        if (commit) theHold.Accept(s.label.c_str());
        else theHold.Cancel();
    }
    s.open = false;
    bool kept = false;
    if (s.superOpen) {
        if (commit && changed) {
            theHold.SuperAccept(s.label.c_str());
            kept = true;
        } else {
            theHold.SuperCancel();
        }
        s.superOpen = false;
    }
    FreeCanaries(kept);
    return kept;
}

// After a close that threw: a restore object failed inside 3ds Max, which
// leaves its hold suspended, mid-restore and a super level deep, so nothing
// after it would be undoable. Nothing more is restored here. What is still
// open is accepted, the undo history is dropped (its records may point at
// freed objects) and an empty begin/cancel puts the hold's own state back.
void Recover() {
    const State s = S();
    try {
        for (int guard = 0; theHold.IsSuspended() && guard < 32; ++guard) theHold.Resume();
        for (int guard = 0; theHold.Holding() && guard < 32; ++guard) theHold.Accept(s.label.c_str());
        if (s.superOpen && theHold.GetSuperBeginDepth() > 0) theHold.SuperAccept(s.label.c_str());
        GetCOREInterface()->FlushUndoBuffer();
        theHold.Begin();
        theHold.Cancel();
    } catch (...) {
    }
}

void Pause() {
    State& s = S();
    if (s.open) CheckCanary();
    if (s.open && theHold.Holding()) theHold.Accept(s.label.c_str());
    s.open = false;
}

void Resume() {
    State& s = S();
    if (!s.active) return;
    if (!s.superOpen) {
        OpenStep();
        return;
    }
    if (!theHold.Holding()) {
        theHold.Begin();
        PutCanary();
    }
    s.open = true;
}

void Break() {
    State& s = S();
    if (!s.superOpen) return;
    CloseStep(true);
    ++s.breaks;
    // What the committed step lost says nothing about rolling back the next one.
    s.lostEarlier = s.lostEarlier || s.lost;
    s.lost = false;
}

[[noreturn]] void Fail(const char* code, const std::string& message) {
    throw std::runtime_error(HandlerHelpers::StructuredErrorPayload(code, message));
}

json Status() {
    const State& s = S();
    return {{"active", s.active}, {"breaks", s.breaks}, {"holding", theHold.Holding() != 0}};
}

}  // namespace

bool Active() { return S().active; }

namespace Detail {
bool BreaksStep(const std::string& cmdType, const std::string& command) {
    if (cmdType == "native:undo_last") return true;
    if (cmdType == "native:manage_scene") {
        const json p = json::parse(command, nullptr, false);
        const std::string action = p.is_object() ? p.value("action", "") : "";
        return action == "reset" || action == "fetch";
    }
    return cmdType == "maxscript" && QuietPolicy::MentionsSceneFileCommand(command);
}
}  // namespace Detail

std::string Handle(const std::string& params) {
    if (!HostExecutor::IsMainThread()) Fail("WRONG_THREAD", "script_hold runs on 3ds Max's main thread only");
    const json p = json::parse(params, nullptr, false);
    if (!p.is_object()) Fail("BAD_PARAM", "script_hold takes a JSON object");
    const std::string action = p.value("action", "status");
    State& s = S();
    if (action == "status") return Status().dump();
    if (action == "begin") {
        if (s.active) Fail("USER_BUSY", "a run_python script's undo step is already open");
        if (theHold.Holding() || theHold.GetSuperBeginDepth() > 0)
            Fail("USER_BUSY", "3ds Max has an open undo operation; finish it, then run the script again.");
        // Edits made now would not be recorded, so a failing script could not be rolled back.
        if (theHold.IsSuspended() || theHold.RestoreOrRedoing())
            Fail("USER_BUSY", "3ds Max's undo system is suspended or in the middle of an undo, so the script's "
                              "edits could not be rolled back; let the running operation finish, then run the "
                              "script again.");
        s = State{};
        const std::string label = p.value("label", "");
        if (!label.empty()) s.label = HandlerHelpers::Utf8ToWide(label);
        OpenStep();
        s.active = true;
        return Status().dump();
    }
    if (action == "end") {
        if (!s.active) return json({{"active", false}, {"kept", false}, {"breaks", 0}, {"was_active", false}}).dump();
        const bool commit = p.value("commit", true);
        const int breaks = s.breaks;
        bool kept = false;
        bool failed = false;
        try {
            kept = CloseStep(commit);
        } catch (...) {
            // The step is over either way: left marked open, no script could start again.
            failed = true;
            Recover();
        }
        // What the step could not record or keep: such edits survive a rollback and cannot be undone.
        const bool lost = s.lost, lostEarlier = s.lostEarlier, unrecorded = s.unrecorded;
        s = State{};
        if (failed)
            Fail("UNDO_STEP_FAILED", std::string("3ds Max failed while ") + (commit ? "committing" : "rolling back") +
                 " the script's undo step. Part of what the script changed may remain, and the undo history was "
                 "cleared; inspect the scene before continuing.");
        return json({{"active", false}, {"kept", kept}, {"breaks", breaks}, {"was_active", true},
                     {"records_lost", lost}, {"records_lost_earlier", lostEarlier}, {"left_suspended", unrecorded}})
            .dump();
    }
    // _mx steps out around a scene reset or fetch it runs itself.
    if (action == "break") {
        if (s.active) {
            Pause();
            Break();
        }
        return Status().dump();
    }
    if (action == "resume") {
        Resume();
        return Status().dump();
    }
    Fail("BAD_PARAM", "script_hold action must be begin, end, status, break or resume");
}

Step::Step(const std::string& session, const std::string& cmdType, const std::string& command) {
    State& s = S();
    if (!s.active || session != kInProcessSession || cmdType == "native:script_hold" ||
        !HostExecutor::IsMainThread())
        return;
    counted_ = true;
    if (s.depth++ > 0) return;
    paused_ = true;
    Pause();
    if (Detail::BreaksStep(cmdType, command)) {
        broke_ = true;
        Break();
    }
}

Step::~Step() {
    if (!counted_) return;
    State& s = S();
    --s.depth;
    if (!paused_) return;
    try {
        Resume();
    } catch (...) {
    }
}

}  // namespace ScriptHold
