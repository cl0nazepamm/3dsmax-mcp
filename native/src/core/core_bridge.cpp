// Core side of the host services: forwards to the McpHostApi table the shell
// bound at McpCoreApi::initialize. No shell symbol is referenced here.
#include "mcp_bridge/core_bridge.h"

#include <atomic>
#include <stdexcept>

namespace {

std::atomic<const McpHostApi*> g_host{nullptr};

const McpHostApi& Host() {
    const McpHostApi* host = g_host.load();
    if (!host) throw std::runtime_error("Bridge host services are unavailable: the native core is not initialized");
    return *host;
}

// Rethrows a host service failure with the shell's exception text.
void Check(int status, const std::string& error) {
    if (status != 0) throw std::runtime_error(error);
}

// Runs core work for the host's executor. Exceptions stay in the core; the
// fallback text is the one DialogWatch::Guard gives a non-standard exception.
int RunWork(void* work, McpSink result, McpSink error) {
    try {
        McpWrite(result, (*static_cast<std::function<std::string()>*>(work))());
        return 0;
    } catch (const std::exception& e) {
        McpWriteText(error, e.what());
    } catch (...) {
        McpWriteText(error, "Unknown exception during bridge operation");
    }
    return 1;
}

}  // namespace

// ── HostExecutor ─────────────────────────────────────────────────
std::string HostExecutor::ExecuteSync(std::function<std::string()> work, DWORD timeout_ms) {
    const McpHostApi& host = Host();
    // Re-entry on the executor's own thread runs inline, as MainThreadExecutor
    // does. Nothing crosses the boundary, so exceptions keep their types.
    if (host.isMainThread(host.ctx)) return work();
    std::string result, error;
    Check(host.executeSync(host.ctx, &RunWork, &work, timeout_ms, McpStringSink(result), McpStringSink(error)), error);
    return result;
}

void HostExecutor::EnableDirectMode() {
    const McpHostApi& host = Host();
    host.setDirectMode(host.ctx, 1);
}

void HostExecutor::DisableDirectMode() {
    // Runs from destructors: never throws.
    if (const McpHostApi* host = g_host.load()) host->setDirectMode(host->ctx, 0);
}

bool HostExecutor::IsMainThread() {
    const McpHostApi* host = g_host.load();
    return host && host->isMainThread(host->ctx) != 0;
}

bool HostExecutor::IsDirectMode() {
    const McpHostApi* host = g_host.load();
    return host && host->getDirectMode(host->ctx) != 0;
}

namespace McpHost {

void Bind(const McpHostApi* host) { g_host.store(host); }

std::string RunOnMainThread(const std::function<std::string()>& work) {
    const McpHostApi& host = Host();
    if (host.isMainThread(host.ctx)) return work();
    // The pipe thread only waits here; restore its mode on every exit.
    struct LeaveDirectMode {
        const McpHostApi& host;
        const bool direct = host.getDirectMode(host.ctx) != 0;
        explicit LeaveDirectMode(const McpHostApi& h) : host(h) {
            if (direct) host.setDirectMode(host.ctx, 0);
        }
        ~LeaveDirectMode() {
            if (direct) host.setDirectMode(host.ctx, 1);
        }
    } leave(host);
    return HostExecutor().ExecuteSync(work);
}

// ── SceneJournal ─────────────────────────────────────────────────
namespace SceneJournal {

void Reset() {
    const McpHostApi& host = Host();
    std::string error;
    Check(host.journalReset(host.ctx, McpStringSink(error)), error);
}

unsigned long long Epoch() {
    const McpHostApi& host = Host();
    unsigned long long epoch = 0;
    std::string error;
    Check(host.journalEpoch(host.ctx, &epoch, McpStringSink(error)), error);
    return epoch;
}

bool IsRegistered() {
    const McpHostApi& host = Host();
    return host.journalIsRegistered(host.ctx) != 0;
}

bool FlushPending() {
    const McpHostApi& host = Host();
    int flushed = 0;
    std::string error;
    Check(host.journalFlushPending(host.ctx, &flushed, McpStringSink(error)), error);
    return flushed != 0;
}

unsigned long long CurrentSeq() {
    const McpHostApi& host = Host();
    unsigned long long seq = 0;
    std::string error;
    Check(host.journalCurrentSeq(host.ctx, &seq, McpStringSink(error)), error);
    return seq;
}

unsigned long long CurrentMutationSeq() {
    const McpHostApi& host = Host();
    unsigned long long seq = 0;
    std::string error;
    Check(host.journalCurrentMutationSeq(host.ctx, &seq, McpStringSink(error)), error);
    return seq;
}

unsigned long long AppendSynthetic(const std::string& type, const nlohmann::json& details) {
    const McpHostApi& host = Host();
    const std::string text = details.dump();
    unsigned long long seq = 0;
    std::string error;
    Check(host.journalAppendSynthetic(host.ctx, type.data(), type.size(), text.data(), text.size(), &seq,
                                      McpStringSink(error)), error);
    return seq;
}

nlohmann::json ChangesSince(unsigned long long since, size_t limit) {
    const McpHostApi& host = Host();
    std::string out, error;
    Check(host.journalChangesSince(host.ctx, since, limit, McpStringSink(out), McpStringSink(error)), error);
    return nlohmann::json::parse(out);
}

nlohmann::json MutationChangesSince(unsigned long long since, size_t limit) {
    const McpHostApi& host = Host();
    std::string out, error;
    Check(host.journalMutationChangesSince(host.ctx, since, limit, McpStringSink(out), McpStringSink(error)),
          error);
    return nlohmann::json::parse(out);
}

}  // namespace SceneJournal

// ── SceneDeltaCursors ────────────────────────────────────────────
namespace SceneDeltaCursors {

bool Get(const std::string& session, Cursor& cursor) {
    const McpHostApi& host = Host();
    int found = 0;
    std::string error;
    Check(host.sceneDeltaGetCursor(host.ctx, session.data(), session.size(), &found, &cursor.journalSeq,
                                   &cursor.epoch, McpStringSink(error)), error);
    return found != 0;
}

void Set(const std::string& session, const Cursor& cursor) {
    const McpHostApi& host = Host();
    std::string error;
    Check(host.sceneDeltaSetCursor(host.ctx, session.data(), session.size(), cursor.journalSeq, cursor.epoch,
                                   McpStringSink(error)), error);
}

}  // namespace SceneDeltaCursors

// ── DialogWatch ──────────────────────────────────────────────────
namespace DialogWatch {

RequestContext::RequestContext(const std::string& request, const std::string& command)
    : host_(&Host()), handle_(nullptr) {
    std::string error;
    handle_ = host_->beginRequest(host_->ctx, request.data(), request.size(), command.data(), command.size(),
                                  McpStringSink(error));
    if (!handle_) throw std::runtime_error(error);
}

RequestContext::~RequestContext() {
    if (handle_) host_->endRequest(host_->ctx, handle_);
}

void ThrowIfDismissed() {
    const McpHostApi& host = Host();
    std::string error;
    Check(host.throwIfDialogDismissed(host.ctx, McpStringSink(error)), error);
}

nlohmann::json OpenDialogs() {
    const McpHostApi& host = Host();
    std::string out, error;
    Check(host.openDialogs(host.ctx, McpStringSink(out), McpStringSink(error)), error);
    return nlohmann::json::parse(out);
}

std::string Control(const std::string& command) {
    const McpHostApi& host = Host();
    std::string out, error;
    Check(host.dialogControl(host.ctx, command.data(), command.size(), McpStringSink(out), McpStringSink(error)),
          error);
    return out;
}

}  // namespace DialogWatch

// ── GdiPlusRuntime ───────────────────────────────────────────────
namespace GdiPlusRuntime {

bool EnsureStarted() {
    const McpHostApi& host = Host();
    int started = 0;
    std::string error;
    Check(host.gdiplusEnsureStarted(host.ctx, &started, McpStringSink(error)), error);
    return started != 0;
}

}  // namespace GdiPlusRuntime

// ── AgentViewport ────────────────────────────────────────────────
namespace AgentViewport {

nlohmann::json Execute(const nlohmann::json& params) {
    const McpHostApi& host = Host();
    const std::string text = params.dump();
    std::string out, error;
    Check(host.agentViewportExecute(host.ctx, text.data(), text.size(), McpStringSink(out), McpStringSink(error)),
          error);
    return nlohmann::json::parse(out);
}

ViewExp& Get() {
    const McpHostApi& host = Host();
    ViewExp* view = nullptr;
    std::string error;
    Check(host.agentViewportGet(host.ctx, &view, McpStringSink(error)), error);
    if (!view) throw std::runtime_error("Agent viewport is unavailable");
    return *view;
}

bool IsOwned() {
    const McpHostApi& host = Host();
    int owned = 0;
    std::string error;
    Check(host.agentViewportIsOwned(host.ctx, &owned, McpStringSink(error)), error);
    return owned != 0;
}

bool IsRequested(const nlohmann::json& params) {
    const McpHostApi& host = Host();
    const std::string text = params.dump();
    int requested = 0;
    std::string error;
    Check(host.agentViewportIsRequested(host.ctx, text.data(), text.size(), &requested, McpStringSink(error)),
          error);
    return requested != 0;
}

void Redraw() {
    const McpHostApi& host = Host();
    std::string error;
    Check(host.agentViewportRedraw(host.ctx, McpStringSink(error)), error);
}

nlohmann::json Snapshot() {
    const McpHostApi& host = Host();
    std::string out, error;
    Check(host.agentViewportSnapshot(host.ctx, McpStringSink(out), McpStringSink(error)), error);
    return nlohmann::json::parse(out);
}

void Configure(const nlohmann::json& params) {
    const McpHostApi& host = Host();
    const std::string text = params.dump();
    std::string error;
    Check(host.agentViewportConfigure(host.ctx, text.data(), text.size(), McpStringSink(error)), error);
}

CameraState SaveCamera() {
    const McpHostApi& host = Host();
    CameraState state{};
    std::string error;
    Check(host.agentViewportSaveCamera(host.ctx, &state, McpStringSink(error)), error);
    return state;
}

void RestoreCamera(const CameraState& state) {
    const McpHostApi& host = Host();
    std::string error;
    Check(host.agentViewportRestoreCamera(host.ctx, &state, McpStringSink(error)), error);
}

nlohmann::json Project(const nlohmann::json& points) {
    const McpHostApi& host = Host();
    const std::string text = points.dump();
    std::string out, error;
    Check(host.agentViewportProject(host.ctx, text.data(), text.size(), McpStringSink(out), McpStringSink(error)),
          error);
    return nlohmann::json::parse(out);
}

}  // namespace AgentViewport

// ── RenderHost ───────────────────────────────────────────────────
namespace RenderHost {

bool ActiveJob(std::string& jobId) {
    const McpHostApi& host = Host();
    int active = 0;
    std::string id, error;
    Check(host.renderJobActive(host.ctx, &active, McpStringSink(id), McpStringSink(error)), error);
    if (active) jobId = id;
    return active != 0;
}

void Arm(const std::string& jobId, const std::string& output, const std::string& signalPath,
         int framesExpected) {
    const McpHostApi& host = Host();
    std::string error;
    Check(host.renderJobArm(host.ctx, jobId.data(), jobId.size(), output.data(), output.size(), signalPath.data(),
                            signalPath.size(), framesExpected, McpStringSink(error)), error);
}

CancelReport Cancel(const std::string& expectedJobId) {
    const McpHostApi& host = Host();
    CancelReport report;
    int hadActiveJob = 0;
    std::string error;
    Check(host.renderJobCancel(host.ctx, expectedJobId.data(), expectedJobId.size(), &report.outcome,
                               &hadActiveJob, McpStringSink(report.jobId), &report.framesDone,
                               &report.framesExpected, McpStringSink(error)), error);
    report.hadActiveJob = hadActiveJob != 0;
    return report;
}

}  // namespace RenderHost

// ── UndoRecords ──────────────────────────────────────────────────
namespace UndoRecords {

void PutNodeFlagsRestore(INode* node, unsigned before, unsigned after) {
    const McpHostApi& host = Host();
    std::string error;
    Check(host.putNodeFlagsRestore(host.ctx, node, before, after, McpStringSink(error)), error);
}

void PutEnvironmentRestore(Texmap* before, BOOL beforeOn, Texmap* after, BOOL afterOn) {
    const McpHostApi& host = Host();
    std::string error;
    Check(host.putEnvironmentRestore(host.ctx, before, beforeOn, after, afterOn, McpStringSink(error)), error);
}

bool IsPin(ReferenceMaker* maker) {
    const McpHostApi& host = Host();
    return host.isPin(host.ctx, maker) != 0;
}

Pin::Pin(ReferenceTarget* target) : host_(&Host()), handle_(nullptr) {
    std::string error;
    handle_ = host_->makePin(host_->ctx, target, McpStringSink(error));
    if (!handle_) throw std::runtime_error(error);
}

Pin::~Pin() {
    if (handle_) host_->destroyPin(host_->ctx, handle_);
}

ReferenceTarget* Pin::GetReference(int) const { return host_->pinTarget(host_->ctx, handle_); }

}  // namespace UndoRecords

}  // namespace McpHost
