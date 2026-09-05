#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/bridge_gup.h"
#include "mcp_bridge/capture_region.h"

#include <notify.h>
#include <mutex>
#include <fstream>
#include <sstream>
#include <algorithm>
#include <vector>

using json = nlohmann::json;
using namespace HandlerHelpers;

// ── native:render_scene ─────────────────────────────────────────
std::string NativeHandlers::RenderScene(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&]() -> std::string {
        json p = params.empty() ? json::object() : json::parse(params, nullptr, false);

        int width = p.value("width", 1920);
        int height = p.value("height", 1080);
        std::string outputPath = p.value("output_path", "");

        // Build MAXScript render command — the SDK render API is complex
        // and MAXScript's render() function handles all the boilerplate
        std::string script = "render outputWidth:" + std::to_string(width) +
                             " outputHeight:" + std::to_string(height) +
                             " vfb:true";

        if (!outputPath.empty()) {
            script += " outputFile:\"" + JsonEscape(outputPath) + "\"";
        }

        RunMAXScript(script);

        json result;
        result["status"] = "rendered";
        result["width"] = width;
        result["height"] = height;
        if (!outputPath.empty()) result["outputFile"] = outputPath;
        return result.dump();
    }, 300000); // 5 minute timeout for rendering
}

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

void NativeHandlers::RegisterRenderNotifications() {
    std::lock_guard<std::mutex> lk(g_job.mtx);
    if (g_job.registered) return;
    RegisterNotification(OnPostRenderFrame, nullptr, NOTIFY_POST_RENDERFRAME);
    RegisterNotification(OnPreRender, nullptr, NOTIFY_PRE_RENDER);
    RegisterNotification(OnPostRender, nullptr, NOTIFY_POST_RENDER);
    g_job.registered = true;
}

void NativeHandlers::UnregisterRenderNotifications() {
    std::lock_guard<std::mutex> lk(g_job.mtx);
    if (!g_job.registered) return;
    UnRegisterNotification(OnPostRenderFrame, nullptr, NOTIFY_POST_RENDERFRAME);
    UnRegisterNotification(OnPreRender, nullptr, NOTIFY_PRE_RENDER);
    UnRegisterNotification(OnPostRender, nullptr, NOTIFY_POST_RENDER);
    g_job.registered = false;
}

std::string NativeHandlers::RenderStart(const std::string& params, MCPBridgeGUP* gup) {
    // This does NOT fire a render — firing a render from the bridge is what made
    // Max loop (a second render auto-starting after the first). It only ARMS the
    // done-signal ("the pinger"): it reads Render Setup for the completion report
    // and records job_id + signal_path, so that when the NEXT render finishes —
    // fired externally by the agent's `max quick render` (execute_maxscript) or by
    // the user hitting Render — NOTIFY_POST_RENDER (OnPostRender, below) writes the
    // signal file the external watcher waits on. Arming is fast; nothing blocks.
    std::string out = gup->GetExecutor().ExecuteSync([params]() -> std::string {
        json p = params.empty() ? json::object() : json::parse(params, nullptr, false);
        std::string jobId      = p.value("job_id", "");
        std::string signalPath = p.value("signal_path", "");
        if (jobId.empty() || signalPath.empty())
            return std::string("ERROR: job_id and signal_path required");

        // One render_start at a time. All arming happens on the main thread, so
        // this check cannot race with another start.
        {
            std::lock_guard<std::mutex> lk(g_job.mtx);
            if (g_job.active)
                return std::string("BUSY:") + g_job.job_id;
        }

        // One read, no mutation: the Render Setup time configuration + output,
        // so the render honors what the agent (or user) already set up.
        std::string info = RunMAXScript(
            "((rendTimeType as integer) as string) + \"|\" + "
            "((rendStart.frame as integer) as string) + \"|\" + "
            "((rendEnd.frame as integer) as string) + \"|\" + "
            "((animationRange.start.frame as integer) as string) + \"|\" + "
            "((animationRange.end.frame as integer) as string) + \"|\" + "
            "((sliderTime.frame as integer) as string) + \"|\" + "
            "(rendNThFrame as string) + \"|\" + rendOutputFilename");
        if (info.rfind(MaxScriptErrorSentinel(), 0) == 0)
            return std::string("ERROR: could not read render settings: ") + info;

        int fields[7] = {1, 0, 0, 0, 0, 0, 1};
        std::string output;
        {
            size_t pos = 0;
            bool parsed = true;
            for (int i = 0; i < 7; ++i) {
                size_t bar = info.find('|', pos);
                if (bar == std::string::npos) { parsed = false; break; }
                try { fields[i] = std::stoi(info.substr(pos, bar - pos)); } catch (...) {}
                pos = bar + 1;
            }
            if (parsed) output = info.substr(pos);
        }
        const int timeType = fields[0];
        const int nth      = (fields[6] >= 1) ? fields[6] : 1;

        // Honor rendTimeType. Rendering rendStart..rendEnd unconditionally made
        // a scene configured for a single frame re-render its stale animation
        // range on every call.
        int fromFrame = fields[1], toFrame = fields[2];
        bool single = false;
        if (timeType == 1) { single = true; fromFrame = toFrame = fields[5]; }
        else if (timeType == 2) { fromFrame = fields[3]; toFrame = fields[4]; }
        // timeType 3 (range) and 4 (frame pickup — render() cannot take a
        // pickup list; the specified range is the nearest match) use rendStart..rendEnd.

        int expected = single ? 1
            : (toFrame >= fromFrame) ? ((toFrame - fromFrame) / nth + 1) : 0;

        // Arm the pinger — do NOT render here. Firing a render from the bridge is
        // what looped; this handler no longer fires anything. It only records the
        // job so that when the NEXT render finishes (fired externally via
        // execute_maxscript's `max quick render`, or by the user hitting Render),
        // OnPostRender writes the signal file to signal_path.
        //
        // Arm LAST — no error path above may leave a half-armed job.
        {
            std::lock_guard<std::mutex> lk(g_job.mtx);
            g_job.active = true;
            g_job.cancel_requested = false;
            g_job.started = false;
            g_job.job_id = jobId;
            g_job.output = output;
            g_job.signal_path = signalPath;
            g_job.frames_expected = expected;
            g_job.frames_done = 0;
        }

        // No frame count is reported at arm time — it would be a guess (the render
        // is fired externally and may differ from this reading). The real count
        // lands in the completion signal as frames_rendered.
        json ok;
        ok["status"] = "armed";
        ok["job_id"] = jobId;
        ok["output"] = output;
        ok["signal_path"] = signalPath;
        return ok.dump();
    }, 60000);  // arming only — fast; no render runs inside this call

    if (out.rfind("BUSY:", 0) == 0) {
        json e;
        e["status"] = "error";
        e["error"] = "a render_start job is already in flight";
        e["active_job_id"] = out.substr(5);
        e["hint"] = "wait for its done-signal (action=status) before starting another render";
        return e.dump();
    }
    if (out.rfind("ERROR", 0) == 0) {
        json e;
        e["status"] = "error";
        e["error"] = out;
        return e.dump();
    }
    return out;
}

// ── native:render_cancel — abort the in-flight render ───────────────────────
//
// Deliberately NOT marshaled to the main thread: while a render runs, the main
// thread is the render, so an ExecuteSync would queue behind the very job it is
// trying to kill. Interface::AbortRender() only raises the abort flag that the
// render pipeline polls between buckets/frames (the same flag the Cancel button
// and Esc set). The public SDK does not document a cross-thread guarantee for
// this API. Keep this isolated cooperative request; never invoke renderer
// shutdown or arbitrary renderer methods on this worker thread.
std::string NativeHandlers::RenderCancel(const std::string& params, MCPBridgeGUP* /*gup*/) {
    const auto p=params.empty() ? json::object() : json::parse(params);
    const auto expectedJob=p.value("job_id","");
    json j;
    std::unique_lock<std::mutex> jobLock(g_job.mtx);
    {
        if(!expectedJob.empty() && (!g_job.active || !g_job.started || expectedJob!=g_job.job_id))
            return json({{"status","not_cancelled"},{"cancel_requested",false},
                {"error","Render job is no longer active or does not match; no global abort was sent"}}).dump();
        j["had_active_job"] = g_job.active;
        if (g_job.active) {
            j["job_id"] = g_job.job_id;
            j["frames_done"] = g_job.frames_done;
            j["frames_expected"] = g_job.frames_expected;
        }
    }

    Interface* ip = GetCOREInterface();
    if (!ip) {
        j["status"] = "error";
        j["error"] = "core interface unavailable";
        return j.dump();
    }
    ip->AbortRender();
    if(g_job.active) g_job.cancel_requested=true;
    jobLock.unlock();

    j["status"] = "cancelling";
    j["cancel_requested"] = true;
    j["stopped"] = nullptr;
    j["hint"] = "Cancellation requested through Max's abort flag. The renderer must poll it; "
                "this response does not confirm that rendering has stopped. "
                "A completion notification alone does not distinguish cancellation from success.";
    return j.dump();
}

// Capture first, preserving the displayed partial image if Cancel closes the
// framebuffer. Both operations stay on one Max connection and skip its UI queue.
std::string NativeHandlers::RenderCancelCapture(const std::string& params, MCPBridgeGUP* gup) {
    const auto p=json::parse(params);
    if(p.value("job_id","").empty()) throw std::runtime_error("cancel_capture requires the armed render job_id");
    const auto target=p.value("target","vray_vfb");
    if(target!="vray_vfb" && target!="screen") throw std::runtime_error("Unknown capture target");
    if(p.contains("crop")) CaptureRegion::Crop({0,0,131072,131072},p.at("crop"));
    json out={{"capture",nullptr},{"captured_before_cancel",true},{"stopped",nullptr},{"converged",nullptr}};
    try { out["capture"]=json::parse(CaptureScreen(p.dump(),gup)); }
    catch(const std::exception& ex) { out["capture_error"]=ex.what(); }
    out["cancellation"]=json::parse(RenderCancel(p.dump(),gup));
    out["hint"]="Partial visible pixels only. Check the done-signal separately; cancellation is cooperative. "
        "This command does not start rendering or change a running renderer's sampler/denoiser.";
    return out.dump();
}
