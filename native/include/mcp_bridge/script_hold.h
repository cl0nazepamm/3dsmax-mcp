#pragma once
// The undo step a run_python script runs in (maxmcp.inmax.runner).
//
// Everything the script changes — guarded tool calls, _mx edits and raw pymxs
// edits — is one undo step, rolled back if the script raises. Raw edits put
// restore records only while theHold is holding, so the script runs inside an
// open hold, grouped by SuperBegin. Handlers and tool scripts refuse to join an
// open hold (USER_BUSY), so each in-process bridge call the script makes steps
// out of it (Accept merges what was recorded into the super hold) and back in.
//
// Undo, reset and fetch cannot run inside an open step: they close the
// script's step first (committing what it did so far as its own undo entry)
// and open a new one after; a later failure rolls back only that new step.
#include <string>

namespace ScriptHold {

// native:script_hold {"action": "begin" | "end" | "status" | "break" | "resume",
// "label": "...", "commit": true}. begin refuses with USER_BUSY while any undo
// operation is open; end with commit=false rolls the step back.
std::string Handle(const std::string& params);

// A script's undo step is open.
bool Active();

// Around one call the dispatcher runs. Acts only for in-process calls made on
// the main thread while a step is active, and only for the outermost one (a
// handler's own nested dispatch runs inside that handler's hold).
class Step {
public:
    Step(const std::string& session, const std::string& cmdType, const std::string& command);
    ~Step();
    Step(const Step&) = delete;
    Step& operator=(const Step&) = delete;

private:
    bool counted_ = false;
    bool paused_ = false;
    bool broke_ = false;
};

namespace Detail {
// Calls that replace the scene or walk the undo stack: they break the step.
bool BreaksStep(const std::string& cmdType, const std::string& command);
}  // namespace Detail

}  // namespace ScriptHold
