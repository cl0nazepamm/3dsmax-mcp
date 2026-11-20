// Shell side of the boundary: the McpHostApi table over the shell's services.
// The core's lifecycle and request routing live in core_control.cpp.
#include "mcp_bridge/core_host.h"
#include "mcp_bridge/core_api.h"
#include "mcp_bridge/host_api.h"
#include "mcp_bridge/main_thread_executor.h"
#include "mcp_bridge/dialog_watch.h"
#include "mcp_bridge/scene_journal.h"
#include "mcp_bridge/gdiplus_runtime.h"
#include "mcp_bridge/agent_viewport.h"
#include "mcp_bridge/render_host.h"
#include "mcp_bridge/undo_records.h"

#include <cstring>
#include <map>
#include <mutex>
#include <stdexcept>
#include <type_traits>
#include <utility>

namespace {

// ── Host services ────────────────────────────────────────────────
// Each runs one shell call for the core; no exception crosses back.
template <typename Body>
int Guarded(McpSink error, Body&& body) noexcept {
    try {
        body();
        return 0;
    } catch (const std::exception& e) {
        McpWriteText(error, e.what());
    } catch (...) {
        McpWriteText(error, "Unknown exception in a bridge host service");
    }
    return 1;
}

nlohmann::json ParseArgument(const char* text, size_t length) {
    return nlohmann::json::parse(std::string(text, length));
}

MainThreadExecutor* Executor(void* ctx) { return static_cast<MainThreadExecutor*>(ctx); }

// MainThreadExecutor
int IsMainThread(void* ctx) {
    return Executor(ctx) && Executor(ctx)->IsOwnThread() ? 1 : 0;
}

int ExecuteSync(void* ctx, McpWorkFn work, void* workCtx, unsigned long timeoutMs, McpSink result,
                McpSink error) {
    return Guarded(error, [&] {
        // Same message as an uninitialized MainThreadExecutor.
        if (!Executor(ctx)) throw std::runtime_error("MainThreadExecutor not initialized");
        McpWrite(result, Executor(ctx)->ExecuteSync([work, workCtx]() -> std::string {
            std::string text, failure;
            if (work(workCtx, McpStringSink(text), McpStringSink(failure)) != 0)
                throw std::runtime_error(failure);
            return text;
        }, timeoutMs));
    });
}

void SetDirectMode(void*, int enabled) {
    if (enabled) MainThreadExecutor::EnableDirectMode();
    else MainThreadExecutor::DisableDirectMode();
}

int GetDirectMode(void*) { return MainThreadExecutor::IsDirectMode() ? 1 : 0; }

// DialogWatch
void* BeginRequest(void*, const char* requestId, size_t requestIdLen, const char* commandType,
                   size_t commandTypeLen, McpSink error) {
    DialogWatch::RequestContext* request = nullptr;
    Guarded(error, [&] {
        request = new DialogWatch::RequestContext(std::string(requestId, requestIdLen),
                                                  std::string(commandType, commandTypeLen));
    });
    return request;
}

void EndRequest(void*, void* request) {
    delete static_cast<DialogWatch::RequestContext*>(request);
}

int ThrowIfDialogDismissed(void*, McpSink error) {
    return Guarded(error, [] { DialogWatch::ThrowIfDismissed(); });
}

int OpenDialogs(void*, McpSink out, McpSink error) {
    return Guarded(error, [&] { McpWrite(out, DialogWatch::OpenDialogs().dump()); });
}

int DialogControl(void*, const char* command, size_t commandLen, McpSink out, McpSink error) {
    return Guarded(error, [&] { McpWrite(out, DialogWatch::Control(std::string(command, commandLen))); });
}

// SceneJournal
int JournalReset(void*, McpSink error) {
    return Guarded(error, [] { SceneJournal::Reset(); });
}

int JournalEpoch(void*, unsigned long long* epoch, McpSink error) {
    return Guarded(error, [&] { *epoch = SceneJournal::Epoch(); });
}

int JournalIsRegistered(void*) { return SceneJournal::IsRegistered() ? 1 : 0; }

int JournalFlushPending(void*, int* flushed, McpSink error) {
    return Guarded(error, [&] { *flushed = SceneJournal::FlushPending() ? 1 : 0; });
}

int JournalCurrentSeq(void*, unsigned long long* seq, McpSink error) {
    return Guarded(error, [&] { *seq = SceneJournal::CurrentSeq(); });
}

int JournalCurrentMutationSeq(void*, unsigned long long* seq, McpSink error) {
    return Guarded(error, [&] { *seq = SceneJournal::CurrentMutationSeq(); });
}

int JournalAppendSynthetic(void*, const char* type, size_t typeLen, const char* details, size_t detailsLen,
                           unsigned long long* mutationSeq, McpSink error) {
    return Guarded(error, [&] {
        *mutationSeq = SceneJournal::AppendSynthetic(std::string(type, typeLen), ParseArgument(details, detailsLen));
    });
}

int JournalChangesSince(void*, unsigned long long since, size_t limit, McpSink out, McpSink error) {
    return Guarded(error, [&] { McpWrite(out, SceneJournal::ChangesSince(since, limit).dump()); });
}

int JournalMutationChangesSince(void*, unsigned long long since, size_t limit, McpSink out, McpSink error) {
    return Guarded(error, [&] { McpWrite(out, SceneJournal::MutationChangesSince(since, limit).dump()); });
}

// GdiPlusRuntime
int GdiplusEnsureStarted(void*, int* started, McpSink error) {
    return Guarded(error, [&] { *started = GdiPlusRuntime::EnsureStarted() ? 1 : 0; });
}

// AgentViewport
static_assert(std::is_trivially_copyable_v<AgentViewport::CameraState>,
              "McpAgentCameraState copies AgentViewport::CameraState as bytes");
static_assert(sizeof(AgentViewport::CameraState) <= sizeof(McpAgentCameraState::bytes) &&
              alignof(AgentViewport::CameraState) <= alignof(McpAgentCameraState),
              "McpAgentCameraState is too small for AgentViewport::CameraState");

int AgentViewportIsOwned(void*, int* owned, McpSink error) {
    return Guarded(error, [&] { *owned = AgentViewport::IsOwned() ? 1 : 0; });
}

int AgentViewportIsRequested(void*, const char* params, size_t paramsLen, int* requested, McpSink error) {
    return Guarded(error, [&] { *requested = AgentViewport::IsRequested(ParseArgument(params, paramsLen)) ? 1 : 0; });
}

int AgentViewportExecute(void*, const char* params, size_t paramsLen, McpSink out, McpSink error) {
    return Guarded(error, [&] { McpWrite(out, AgentViewport::Execute(ParseArgument(params, paramsLen)).dump()); });
}

int AgentViewportGet(void*, ViewExp** view, McpSink error) {
    return Guarded(error, [&] { *view = &AgentViewport::Get(); });
}

int AgentViewportRedraw(void*, McpSink error) {
    return Guarded(error, [] { AgentViewport::Redraw(); });
}

int AgentViewportSnapshot(void*, McpSink out, McpSink error) {
    return Guarded(error, [&] { McpWrite(out, AgentViewport::Snapshot().dump()); });
}

int AgentViewportConfigure(void*, const char* params, size_t paramsLen, McpSink error) {
    return Guarded(error, [&] { AgentViewport::Configure(ParseArgument(params, paramsLen)); });
}

int AgentViewportSaveCamera(void*, McpAgentCameraState* state, McpSink error) {
    return Guarded(error, [&] {
        const AgentViewport::CameraState saved = AgentViewport::SaveCamera();
        std::memcpy(state->bytes, &saved, sizeof(saved));
    });
}

int AgentViewportRestoreCamera(void*, const McpAgentCameraState* state, McpSink error) {
    return Guarded(error, [&] {
        AgentViewport::CameraState saved;
        std::memcpy(&saved, state->bytes, sizeof(saved));
        AgentViewport::RestoreCamera(saved);
    });
}

int AgentViewportProject(void*, const char* points, size_t pointsLen, McpSink out, McpSink error) {
    return Guarded(error, [&] { McpWrite(out, AgentViewport::Project(ParseArgument(points, pointsLen)).dump()); });
}

// RenderHost
int RenderJobActive(void*, int* active, McpSink jobId, McpSink error) {
    return Guarded(error, [&] {
        std::string id;
        *active = RenderHost::ActiveJob(id) ? 1 : 0;
        McpWrite(jobId, id);
    });
}

int RenderJobArm(void*, const char* jobId, size_t jobIdLen, const char* output, size_t outputLen,
                 const char* signalPath, size_t signalPathLen, int framesExpected, McpSink error) {
    return Guarded(error, [&] {
        RenderHost::Arm(std::string(jobId, jobIdLen), std::string(output, outputLen),
                        std::string(signalPath, signalPathLen), framesExpected);
    });
}

int RenderJobCancel(void*, const char* expectedJobId, size_t expectedJobIdLen, int* outcome, int* hadActiveJob,
                    McpSink jobId, int* framesDone, int* framesExpected, McpSink error) {
    return Guarded(error, [&] {
        bool active = false;
        std::string id;
        *outcome = RenderHost::Cancel(std::string(expectedJobId, expectedJobIdLen), active, id, *framesDone,
                                      *framesExpected);
        *hadActiveJob = active ? 1 : 0;
        McpWrite(jobId, id);
    });
}

// UndoRecords
int PutNodeFlagsRestore(void*, INode* node, unsigned before, unsigned after, McpSink error) {
    return Guarded(error, [&] { UndoRecords::PutNodeFlagsRestore(node, before, after); });
}

int PutEnvironmentRestore(void*, Texmap* before, int beforeOn, Texmap* after, int afterOn, McpSink error) {
    return Guarded(error, [&] { UndoRecords::PutEnvironmentRestore(before, beforeOn, after, afterOn); });
}

void* MakePin(void*, ReferenceTarget* target, McpSink error) {
    ReferenceMaker* pin = nullptr;
    Guarded(error, [&] { pin = UndoRecords::MakePin(target); });
    return pin;
}

void DestroyPin(void*, void* pin) {
    try { UndoRecords::DestroyPin(static_cast<ReferenceMaker*>(pin)); } catch (...) {}
}

ReferenceTarget* PinTarget(void*, void* pin) { return UndoRecords::PinTarget(static_cast<ReferenceMaker*>(pin)); }

int IsPin(void*, ReferenceMaker* maker) { return UndoRecords::IsPin(maker) ? 1 : 0; }

// scene_delta cursors: session -> {journalSeq, epoch}. Shell state, so a core
// reload keeps them; pipe workers of different sessions call concurrently.
std::mutex g_cursorMutex;
std::map<std::string, std::pair<unsigned long long, unsigned long long>> g_cursors;

int SceneDeltaGetCursor(void*, const char* session, size_t sessionLen, int* found, unsigned long long* journalSeq,
                        unsigned long long* epoch, McpSink error) {
    return Guarded(error, [&] {
        std::lock_guard<std::mutex> lock(g_cursorMutex);
        const auto cursor = g_cursors.find(std::string(session, sessionLen));
        *found = cursor != g_cursors.end() ? 1 : 0;
        if (*found) {
            *journalSeq = cursor->second.first;
            *epoch = cursor->second.second;
        }
    });
}

int SceneDeltaSetCursor(void*, const char* session, size_t sessionLen, unsigned long long journalSeq,
                        unsigned long long epoch, McpSink error) {
    return Guarded(error, [&] {
        std::lock_guard<std::mutex> lock(g_cursorMutex);
        g_cursors[std::string(session, sessionLen)] = {journalSeq, epoch};
    });
}

void SceneDeltaReleaseCursor(void*, const char* session, size_t sessionLen) {
    try {
        std::lock_guard<std::mutex> lock(g_cursorMutex);
        g_cursors.erase(std::string(session, sessionLen));
    } catch (...) {}
}

}  // namespace

namespace CoreHost {

const McpHostApi* HostApi(MainThreadExecutor* executor) {
    static McpHostApi api = [] {
        McpHostApi table{};
        table.version = kMcpHostApiVersion;
        table.isMainThread = &IsMainThread;
        table.executeSync = &ExecuteSync;
        table.setDirectMode = &SetDirectMode;
        table.getDirectMode = &GetDirectMode;
        table.beginRequest = &BeginRequest;
        table.endRequest = &EndRequest;
        table.throwIfDialogDismissed = &ThrowIfDialogDismissed;
        table.openDialogs = &OpenDialogs;
        table.dialogControl = &DialogControl;
        table.journalReset = &JournalReset;
        table.journalEpoch = &JournalEpoch;
        table.journalIsRegistered = &JournalIsRegistered;
        table.journalFlushPending = &JournalFlushPending;
        table.journalCurrentSeq = &JournalCurrentSeq;
        table.journalCurrentMutationSeq = &JournalCurrentMutationSeq;
        table.journalAppendSynthetic = &JournalAppendSynthetic;
        table.journalChangesSince = &JournalChangesSince;
        table.journalMutationChangesSince = &JournalMutationChangesSince;
        table.gdiplusEnsureStarted = &GdiplusEnsureStarted;
        table.agentViewportIsOwned = &AgentViewportIsOwned;
        table.agentViewportIsRequested = &AgentViewportIsRequested;
        table.agentViewportExecute = &AgentViewportExecute;
        table.agentViewportGet = &AgentViewportGet;
        table.agentViewportRedraw = &AgentViewportRedraw;
        table.agentViewportSnapshot = &AgentViewportSnapshot;
        table.agentViewportConfigure = &AgentViewportConfigure;
        table.agentViewportSaveCamera = &AgentViewportSaveCamera;
        table.agentViewportRestoreCamera = &AgentViewportRestoreCamera;
        table.agentViewportProject = &AgentViewportProject;
        table.renderJobActive = &RenderJobActive;
        table.renderJobArm = &RenderJobArm;
        table.renderJobCancel = &RenderJobCancel;
        table.putNodeFlagsRestore = &PutNodeFlagsRestore;
        table.putEnvironmentRestore = &PutEnvironmentRestore;
        table.makePin = &MakePin;
        table.destroyPin = &DestroyPin;
        table.pinTarget = &PinTarget;
        table.isPin = &IsPin;
        table.sceneDeltaGetCursor = &SceneDeltaGetCursor;
        table.sceneDeltaSetCursor = &SceneDeltaSetCursor;
        table.sceneDeltaReleaseCursor = &SceneDeltaReleaseCursor;
        return table;
    }();
    // Cores read ctx on any thread; bind it once (CoreHost::Start).
    if (api.ctx != executor) api.ctx = executor;
    return &api;
}

void ReleaseSceneDeltaCursor(const std::string& session) {
    SceneDeltaReleaseCursor(nullptr, session.data(), session.size());
}

}  // namespace CoreHost
