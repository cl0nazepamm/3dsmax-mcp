// render_start's armed job and the Max notifications that complete it, moved
// verbatim from render_handlers.cpp: Max holds these callbacks, and an armed
// job must outlive a core reload. Core handlers reach it through McpHostApi.
#include "mcp_bridge/render_host.h"
#include "mcp_bridge/host_api.h"
#include "mcp_bridge/handler_helpers.h"

#include <notify.h>
#include <fstream>
#include <mutex>

using json = nlohmann::json;

// ── native:render_start — deferred render + filesystem done-signal ──────────
//
// A production render blocks Max's main thread, and the bridge runs on that
// thread, so completion CANNOT be reported by asking Max. Instead the bridge
// registers Max's own NOTIFY_POST_RENDER and writes a small signal file at the
// exact completion event. An external watcher reacts to that file — nothing
// polls Max, nothing runs inside Max except the render itself.

namespace {

struct RenderJobState {
    std::mutex mtx;
    bool active = false;          // a render_start job is in flight
    bool registered = false;      // notifications hooked
    bool cancel_requested = false;
    bool started = false;
    std::string job_id;
    std::string output;
    std::string signal_path;      // where to write the done-signal
    int frames_expected = 0;
    int frames_done = 0;
};

RenderJobState g_job;

// Atomic-ish write: dump to <path>.tmp, then rename over the target so a
// watcher never observes a half-written file.
void WriteSignalFile(const std::string& path, const json& doc) {
    std::string tmp = path + ".tmp";
    {
        std::ofstream f(tmp, std::ios::binary | std::ios::trunc);
        if (!f) return;
        const std::string s = doc.dump();
        f.write(s.data(), static_cast<std::streamsize>(s.size()));
    }
    std::wstring wtmp = HandlerHelpers::Utf8ToWide(tmp);
    std::wstring wdst = HandlerHelpers::Utf8ToWide(path);
    MoveFileExW(wtmp.c_str(), wdst.c_str(), MOVEFILE_REPLACE_EXISTING);
}

// NOTIFY_POST_RENDERFRAME — fires once per completed frame.
void OnPreRender(void*, NotifyInfo*) {
    std::lock_guard<std::mutex> lk(g_job.mtx);
    if(g_job.active) g_job.started=true;
}

void OnPostRenderFrame(void* /*param*/, NotifyInfo* /*info*/) {
    std::lock_guard<std::mutex> lk(g_job.mtx);
    if (g_job.active) g_job.frames_done++;
}

// NOTIFY_POST_RENDER — fires once when the whole render job finishes.
void OnPostRender(void* /*param*/, NotifyInfo* /*info*/) {
    std::string path;
    json doc;
    {
        std::lock_guard<std::mutex> lk(g_job.mtx);
        if (!g_job.active) return;  // not our render (e.g. a manual one)
        // Report ONLY what was observed. The bridge arms before the render is
        // fired and does not control it, so any arm-time frame prediction is
        // unreliable (an agent must never read "1 frame" for a 101-frame render).
        // frames_rendered is the live NOTIFY_POST_RENDERFRAME count = the truth.
        doc["status"] = "complete";
        doc["job_id"] = g_job.job_id;
        doc["output"] = g_job.output;
        doc["frames_rendered"] = g_job.frames_done;  // actual, counted per frame
        doc["complete"] = true;                       // POST_RENDER = job finished
        doc["cancel_requested"] = g_job.cancel_requested;
        doc["outcome"] = "unknown"; // POST_RENDER also follows cancellation.
        path = g_job.signal_path;
        g_job.active = false;
    }
    WriteSignalFile(path, doc);
}

} // namespace

void RenderHost::RegisterNotifications() {
    std::lock_guard<std::mutex> lk(g_job.mtx);
    if (g_job.registered) return;
    RegisterNotification(OnPostRenderFrame, nullptr, NOTIFY_POST_RENDERFRAME);
    RegisterNotification(OnPreRender, nullptr, NOTIFY_PRE_RENDER);
    RegisterNotification(OnPostRender, nullptr, NOTIFY_POST_RENDER);
    g_job.registered = true;
}

void RenderHost::UnregisterNotifications() {
    std::lock_guard<std::mutex> lk(g_job.mtx);
    if (!g_job.registered) return;
    UnRegisterNotification(OnPostRenderFrame, nullptr, NOTIFY_POST_RENDERFRAME);
    UnRegisterNotification(OnPreRender, nullptr, NOTIFY_PRE_RENDER);
    UnRegisterNotification(OnPostRender, nullptr, NOTIFY_POST_RENDER);
    g_job.registered = false;
}

// ── Job access for the core's render_start / render_cancel ─────────────────
bool RenderHost::ActiveJob(std::string& jobId) {
    std::lock_guard<std::mutex> lk(g_job.mtx);
    if (g_job.active) jobId = g_job.job_id;
    return g_job.active;
}

void RenderHost::Arm(const std::string& jobId, const std::string& output,
                     const std::string& signalPath, int framesExpected) {
    std::lock_guard<std::mutex> lk(g_job.mtx);
    g_job.active = true;
    g_job.cancel_requested = false;
    g_job.started = false;
    g_job.job_id = jobId;
    g_job.output = output;
    g_job.signal_path = signalPath;
    g_job.frames_expected = framesExpected;
    g_job.frames_done = 0;
}

int RenderHost::Cancel(const std::string& expectedJobId, bool& hadActiveJob, std::string& jobId,
                       int& framesDone, int& framesExpected) {
    // One lock across the check, the report and the abort, as render_cancel had.
    std::lock_guard<std::mutex> lk(g_job.mtx);
    if (!expectedJobId.empty() && (!g_job.active || !g_job.started || expectedJobId != g_job.job_id))
        return kMcpRenderCancelRefused;
    hadActiveJob = g_job.active;
    if (g_job.active) {
        jobId = g_job.job_id;
        framesDone = g_job.frames_done;
        framesExpected = g_job.frames_expected;
    }
    Interface* ip = GetCOREInterface();
    if (!ip) return kMcpRenderCancelNoInterface;
    ip->AbortRender();
    if (g_job.active) g_job.cancel_requested = true;
    return kMcpRenderCancelRequested;
}
