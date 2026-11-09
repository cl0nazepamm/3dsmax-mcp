// Every pipe request passes here. ping, health, native:max_dialogs and
// native:core are answered by the shell without entering the core, so health
// checks, dialog recovery and core control keep working while the core is
// missing, failed to load, or is being replaced. Everything else enters the
// bound core through the drain gate (core_reload.h). Each request except
// health and native:max_dialogs is recorded for health while it runs: the
// client registry (bridge_health.h) and the executor's work label.
//
// Also owns the core's lifecycle. The static layout binds the linked core
// once. MCP_HOT_CORE loads core/mcp_core.dll through the shadow-copy loader
// (core_loader.h) and replaces it on native:core reload: load and check the
// new core while the old one serves, drain, swap on Max's main thread, free
// the old one. Any failure before the swap leaves the old core bound.
#include "mcp_bridge/core_host.h"
#include "mcp_bridge/bridge_health.h"
#include "mcp_bridge/core_api.h"
#include "mcp_bridge/core_reload.h"
#include "mcp_bridge/dialog_watch.h"
#include "mcp_bridge/host_api.h"
#include "mcp_bridge/main_thread_executor.h"
#include "mcp_bridge/response_envelope.h"
#include "mcp_bridge/shell_control.h"
#ifdef MCP_HOT_CORE
#include "mcp_bridge/core_loader.h"
#endif

#include <max.h>
#include <CoreFunctions.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>

namespace {

using json = nlohmann::json;
using std::chrono::milliseconds;

#ifdef MCP_HOT_CORE
constexpr bool kHotLayout = true;
#else
constexpr bool kHotLayout = false;
#endif

// The swap's main-thread step gets what the drain left of wait_ms plus this,
// so a reload answers within about wait_ms + 1 s even when Max is busy.
constexpr milliseconds kMainThreadMargin(1000);

MainThreadExecutor* g_executor = nullptr;  // set at Start, before the pipe
// The host table, its ctx bound once at Start; reloads hand the same table to
// the next core instead of rebinding it under cores that read it.
const McpHostApi* g_host = nullptr;
McpCoreGate g_gate;
std::mutex g_reloadMutex;                  // one reload at a time; pipe workers only
std::atomic<long long> g_reloadWaitMs{0};  // wait_ms of the reload holding the gate

// What status reports. Copied from the core when it is bound, so a status
// request never reads core memory a concurrent swap may free.
struct Record {
    bool loaded = false;
    std::string build, bridgeVersion, source, loadedFrom;
    long long loadedAtMs = -1;
    int reloads = 0;
    std::string lastError;
#ifdef MCP_HOT_CORE
    McpCoreLoader::Module module;  // the bound core's mapping
#endif
};
std::mutex g_recordMutex;
Record g_record;

// Sessions that closed while a reload held the gate (core_reload.h).
McpDeferredReleases g_deferredReleases;

long long UnixTimeMs() {
    FILETIME now{};
    GetSystemTimeAsFileTime(&now);
    ULARGE_INTEGER ticks{};
    ticks.LowPart = now.dwLowDateTime;
    ticks.HighPart = now.dwHighDateTime;
    return static_cast<long long>((ticks.QuadPart - 116444736000000000ULL) / 10000ULL);
}

// Under g_recordMutex.
void RecordBound(const McpCoreApi& api, std::string source, std::string loadedFrom) {
    g_record.loaded = true;
    g_record.build = api.build ? api.build : "";
    g_record.bridgeVersion = api.bridgeVersion ? api.bridgeVersion : "";
    g_record.source = std::move(source);
    g_record.loadedFrom = std::move(loadedFrom);
    g_record.loadedAtMs = UnixTimeMs();
    g_record.lastError.clear();
}

// Under g_recordMutex.
void RecordUnbound() {
    g_record.loaded = false;
    g_record.build.clear();
    g_record.bridgeVersion.clear();
    g_record.source.clear();
    g_record.loadedFrom.clear();
    g_record.loadedAtMs = -1;
}

void RecordError(const std::string& message) {
    std::lock_guard<std::mutex> lock(g_recordMutex);
    g_record.lastError = message;
}

McpCoreExpectation ThisShell() {
    return {kMcpCoreApiVersion, kMcpHostApiVersion, MAX_SDK_VERSION, _MSC_FULL_VER, _ITERATOR_DEBUG_LEVEL};
}

McpCoreStatus Status() {
    McpCoreStatus status;
    status.hot = kHotLayout;
    status.inFlight = g_gate.InFlight();
    status.coreApiVersion = kMcpCoreApiVersion;
    status.hostApiVersion = kMcpHostApiVersion;
    std::lock_guard<std::mutex> lock(g_recordMutex);
    status.loaded = g_record.loaded;
    status.build = g_record.build;
    status.bridgeVersion = g_record.bridgeVersion;
    status.source = g_record.source;
    status.loadedFrom = g_record.loadedFrom;
    status.loadedAtMs = g_record.loadedAtMs;
    status.reloads = g_record.reloads;
    status.lastError = g_record.lastError;
    return status;
}

bool SafeMode() {
    static const bool enabled = McpEnvelope::ReadSafeModeSetting();
    return enabled;
}

// The pipe envelope, from the shell's own state (the core fills the same one).
std::string Envelope(bool success, const std::string& result, const std::string& error,
                     const std::string& requestId, const std::string& cmdType, int durationMs) {
    const bool safeMode = SafeMode();
    const bool direct = MainThreadExecutor::IsDirectMode();
    return McpEnvelope::Build(success, result, error, requestId, cmdType, durationMs, safeMode, direct,
                              DialogWatch::OpenDialogs());
}

[[noreturn]] void ThrowCoreError(const std::string& code, const std::string& message, bool retryable, int inFlight,
                                 long long waitMs = -1, const json& extra = json::object()) {
    throw std::runtime_error(McpCoreError(code, message, retryable, inFlight, waitMs, extra));
}

// Direct mode on this pipe worker for one shell request (threadMode).
struct DirectModeScope {
    bool active;
    explicit DirectModeScope(bool enable) : active(enable) {
        if (active) MainThreadExecutor::EnableDirectMode();
    }
    ~DirectModeScope() {
        if (active) MainThreadExecutor::DisableDirectMode();
    }
};

std::string Ping() {
    if (!g_executor) throw std::runtime_error("MainThreadExecutor not initialized");
    return g_executor->ExecuteSync([]() -> std::string {
        json result;
        result["pong"] = true;
        result["server"] = "3dsmax-mcp-native";
        result["protocolVersion"] = 2;
        result["transport"] = "namedpipe";
        DWORD v = Get3DSMAXVersion();
        result["maxVersion"] = 1998 + (HIWORD(v) / 1000);
        return result.dump();
    });
}

// ── health (pipe thread only) ───────────────────────────────────
// Answers from bookkeeping alone: never posts to the main thread and never
// calls into the scene (Get3DSMAXVersion is a compile-time constant). Unlike
// ping it still answers while the main thread is hung, and it says whether an
// MCP request (this client's or another's) or something outside the bridge
// holds the main thread. Nested dispatches inside the core (invoke_tool,
// tool smoke probes) are not listed: their work runs under the outer
// request's label, so health attributes it to that request.
constexpr long long kHeartbeatStaleMs = 3LL * MainThreadExecutor::kHeartbeatMs;

std::string Health(const std::string& clientSessionId) {
    const MainThreadExecutor::Health ex = g_executor ? g_executor->GetHealth() : MainThreadExecutor::Health{};
    const BridgeHealth::ClientsSnapshot clients = BridgeHealth::Snapshot();

    // -1: the heartbeat timer could not be created; never read that as stale.
    const bool heartbeatKnown = ex.heartbeat_age_ms >= 0;
    const bool heartbeatStale = heartbeatKnown && ex.heartbeat_age_ms > kHeartbeatStaleMs;
    std::string state;
    if (!ex.initialized) state = "not_initialized";
    else if (ex.shutting_down) state = "shutting_down";
    else if (ex.running) state = "busy_mcp";        // a bridge request is executing on it
    else if (!heartbeatKnown) state = "unknown";    // no heartbeat: callers fall back to ping
    else if (heartbeatStale) state = "busy_other";  // stopped pumping outside the bridge
    else state = "responsive";

    json result;
    result["server"] = "3dsmax-mcp-native";
#ifdef MCP_BRIDGE_VERSION
    result["bridgeVersion"] = MCP_BRIDGE_VERSION;
#endif
    // 2: posted-message heartbeat (1 used WM_TIMER, which paints and posted
    // messages starve), request ids on executor items, internal clients.
    result["healthVersion"] = 2;
    result["protocolVersion"] = 2;
    result["transport"] = "namedpipe";
    result["pid"] = static_cast<unsigned long>(GetCurrentProcessId());
    DWORD v = Get3DSMAXVersion();
    result["maxVersion"] = 1998 + (HIWORD(v) / 1000);
    result["mainThread"] = {
        {"state", state},
        {"pumping", heartbeatKnown ? json(!heartbeatStale) : json(nullptr)},
        {"heartbeatAgeMs", ex.heartbeat_age_ms < 0 ? json(nullptr) : json(ex.heartbeat_age_ms)},
        {"heartbeatPeriodMs", MainThreadExecutor::kHeartbeatMs},
        {"windowHung", ex.executor_window_hung},
    };
    json running = nullptr;
    if (ex.running) {
        running = {{"cmdType", ex.running_label.cmd_type}, {"runningMs", ex.running_ms},
                   {"clientId", ex.running_label.client_id},
                   {"requestId", ex.running_label.request_id}};
    }
    result["executor"] = {
        {"running", running},
        {"queued", ex.queued},
        {"oldestQueuedMs", ex.queued ? json(ex.oldest_queued_ms) : json(nullptr)},
        {"oldestQueuedCmd", ex.queued ? json(ex.oldest_queued_label.cmd_type) : json(nullptr)},
        {"oldestQueuedRequestId", ex.queued ? json(ex.oldest_queued_label.request_id) : json(nullptr)},
        {"completed", ex.completed},
        {"shuttingDown", ex.shutting_down},
    };
    json inflight = json::array();
    for (const auto& r : clients.inflight) {
        inflight.push_back({
            {"clientId", r.client_id},
            {"requestId", r.request_id},
            {"cmdType", r.cmd_type},
            {"elapsedMs", r.elapsed_ms},
            {"nested", r.nested},
            {"internal", r.internal},
        });
    }
    result["clients"] = {
        {"connected", clients.connected},  // includes the client asking
        {"totalConnections", clients.total_connections},
        {"requestingClient", clientSessionId},
        {"inflight", inflight},
    };
    return result.dump();
}

// A string member of a parsed request, or empty.
std::string StringMember(const json& req, const char* key) {
    const auto it = req.find(key);
    return it != req.end() && it->is_string() ? it->get<std::string>() : std::string();
}

// health runs on the pipe worker, outside the dialog monitor's request context
// and both request registries (it would only ever report itself).
std::string DispatchHealth(const json& req, const std::string& clientSessionId) {
    try {
        const std::string request_id = StringMember(req, "requestId");
        DirectModeScope directMode(true);  // reported as threadMode "direct"
        try {
            return Envelope(true, Health(clientSessionId), "", request_id, "health", 0);
        } catch (const std::exception& e) {
            return Envelope(false, "", McpEnvelope::NormalizeError(e.what()), request_id, "health", 0);
        }
    } catch (const std::exception& e) {
        return McpInternalErrorResponse(e.what());
    } catch (...) {
        return McpInternalErrorResponse("unknown exception");
    }
}

// One request in flight for health: listed under its client, and the
// main-thread work it submits from this pipe worker carries its label.
class InFlight {
public:
    InFlight(const std::string& clientId, const std::string& requestId, const std::string& cmdType)
        : scope_(clientId, requestId, cmdType), previous_(MainThreadExecutor::ThreadLabel()) {
        MainThreadExecutor::SetThreadLabel(MainThreadExecutor::WorkLabel{cmdType, clientId, requestId});
    }
    ~InFlight() { MainThreadExecutor::SetThreadLabel(previous_); }
    InFlight(const InFlight&) = delete;
    InFlight& operator=(const InFlight&) = delete;

private:
    BridgeHealth::RequestScope scope_;
    MainThreadExecutor::WorkLabel previous_;
};

#ifdef MCP_HOT_CORE
const int kModuleAnchor = 0;  // an address inside this .gup

McpCoreLoader::Source ResolveSource(const std::string& requestPath) {
    using namespace McpCoreLoader;
    const std::wstring gupDirectory = ModuleDirectory(&kModuleAnchor);
    Source source = McpCoreLoader::ResolveSource(FromUtf8(requestPath), Environment(L"MCP_CORE_DLL"),
                                                 ReadText(Join(gupDirectory, L"mcp_core.path")), gupDirectory);
    source.path = FullPath(source.path);
    return source;
}

// A shell step on Max's main thread, queued like any work item. It is not a
// core call, so the gate neither counts nor refuses it.
void RunOnMain(const std::function<void()>& step, milliseconds timeout) {
    // Direct mode would run it here, on the pipe worker.
    struct Restore {
        bool direct = MainThreadExecutor::IsDirectMode();
        ~Restore() {
            if (direct) MainThreadExecutor::EnableDirectMode();
        }
    } restore;
    if (restore.direct) MainThreadExecutor::DisableDirectMode();
    const DWORD timeoutMs = static_cast<DWORD>(std::max<long long>(1, timeout.count()));
    g_executor->ExecuteSync([&step]() -> std::string {
        step();
        return {};
    }, timeoutMs);
}

bool StartHot(const McpHostApi* host, std::string& error) {
    using namespace McpCoreLoader;
    const Source source = ResolveSource("");
    const std::wstring directory = DefaultShadowDirectory();
    SweepShadows(directory, L"");
    Module module;
    Failure failure;
    if (!Load(source.path, directory, ThisShell(), module, failure)) {
        error = failure.message;
        RecordError(error);
        return false;
    }
    std::string reason;
    if (module.api->initialize(host, McpStringSink(reason)) != 0) {
        error = "the native core failed to initialize" + (reason.empty() ? std::string() : ": " + reason);
        Unload(module);
        RecordError(error);
        return false;
    }
    g_gate.Swap(module.api);
    std::lock_guard<std::mutex> lock(g_recordMutex);
    RecordBound(*module.api, ToUtf8(module.source), ToUtf8(module.shadow));
    g_record.module = std::exchange(module, Module{});
    return true;
}

// native:core reload, on the pipe worker that received it:
//  1. load the new core into a fresh copy and check its stamps, while the
//     old core keeps serving; a failure ends here and changes nothing;
//  2. close the gate and wait for the calls in flight (new core calls get
//     CORE_BUSY meanwhile; ping, max_dialogs and native:core still answer);
//  3. on Max's main thread: shut the old core down, initialize and bind the
//     new one (or re-initialize the old one if the new one refuses), free
//     the old module and its copy;
//  4. reopen the gate.
json Reload(const McpCoreCommand& command) {
    using namespace McpCoreLoader;
    const auto started = std::chrono::steady_clock::now();
    const milliseconds wait(command.waitMs);

    // Waiting for the drain on the main thread could deadlock: a call in
    // flight may be waiting for that thread.
    if (!g_executor || !g_host)
        ThrowCoreError("BAD_PARAM", "native:core reload is unavailable: the bridge has not started its executor",
                       false, g_gate.InFlight());
    if (g_executor->IsOwnThread())
        ThrowCoreError("BAD_PARAM",
                       "native:core reload cannot run on 3ds Max's main thread, which the reload waits for; "
                       "send it through the pipe",
                       false, g_gate.InFlight());
    std::unique_lock<std::mutex> reloading(g_reloadMutex, std::try_to_lock);
    if (!reloading.owns_lock())
        ThrowCoreError("CORE_BUSY", "another native core reload is in progress", true, g_gate.InFlight(),
                       command.waitMs);

    std::wstring keep;
    std::string previousBuild;
    bool previousLoaded = false;
    {
        std::lock_guard<std::mutex> lock(g_recordMutex);
        keep = g_record.module.shadow;
        previousLoaded = g_record.loaded;
        previousBuild = previousLoaded ? g_record.build : std::string();
    }
    const std::string kept = previousLoaded ? "; the previous core stays loaded" : "; no core is loaded";

    // 1. Load and check.
    const Source source = ResolveSource(command.path);
    const std::wstring directory = DefaultShadowDirectory();
    SweepShadows(directory, keep);
    Module next;
    Failure failure;
    if (!Load(source.path, directory, ThisShell(), next, failure)) {
        RecordError(failure.message);
        ThrowCoreError(failure.code, failure.message + kept, false, g_gate.InFlight());
    }

    // 2-4. Drain and swap.
    const std::string sourceText = ToUtf8(next.source), loadedFrom = ToUtf8(next.shadow);
    McpSwapSteps steps;
    steps.runOnMain = &RunOnMain;
    steps.swapped = [&](const McpCoreApi*) {
        Module retired;
        {
            std::lock_guard<std::mutex> lock(g_recordMutex);
            retired = std::exchange(g_record.module, std::exchange(next, Module{}));
            RecordBound(*g_record.module.api, sourceText, loadedFrom);
            ++g_record.reloads;
        }
        Unload(retired);  // FreeLibrary(old) and delete its copy
    };
    steps.unbound = [](const McpCoreApi*) {
        Module retired;
        {
            std::lock_guard<std::mutex> lock(g_recordMutex);
            retired = std::exchange(g_record.module, Module{});
            RecordUnbound();
        }
        Unload(retired);
    };
    const auto loading = std::chrono::duration_cast<milliseconds>(std::chrono::steady_clock::now() - started);
    const milliseconds drainBudget = wait > loading ? wait - loading : milliseconds(0);
    g_reloadWaitMs = command.waitMs;
    const McpCoreApi* before = g_gate.Bound();
    const McpSwapResult swap = McpSwapCore(g_gate, next.api, g_host, drainBudget, kMainThreadMargin, steps);
    g_reloadWaitMs = 0;
    // Sessions that closed meanwhile: released in the old core if it is still
    // bound, dropped if the swap replaced it.
    g_deferredReleases.Settle(g_gate, swap.status == McpSwapResult::Status::Swapped ? nullptr : before);

    switch (swap.status) {
    case McpSwapResult::Status::Swapped:
        return McpCoreReloadJson(Status(), previousBuild, swap.drainMs);
    case McpSwapResult::Status::Busy:
        Unload(next);
        ThrowCoreError("CORE_BUSY",
                       std::to_string(swap.inFlight) + " call(s) into the native core did not finish within " +
                           std::to_string(command.waitMs) + " ms" + kept,
                       true, swap.inFlight, command.waitMs);
    case McpSwapResult::Status::NotRun:
        Unload(next);
        ThrowCoreError("CORE_BUSY", "3ds Max's main thread did not run the core swap (" + swap.error + ")" + kept,
                       true, g_gate.InFlight(), command.waitMs);
    case McpSwapResult::Status::Refused:
        Unload(next);
        ThrowCoreError("CORE_BUSY", swap.error + kept, true, g_gate.InFlight(), command.waitMs);
    case McpSwapResult::Status::InitializeFailed:
    default: {
        Unload(next);
        // The previous core was shut down before the new one refused, so even
        // when it is restored its watch_scene is off (and its caches were
        // released): it is not the core it was.
        const std::string outcome =
            swap.previousRestored
                ? "; the previous core was restored, but its shutdown stopped watch_scene (start it again if needed)"
                : "; no core is loaded";
        const std::string message = "the new native core failed to initialize: " + swap.error + outcome;
        RecordError(message);
        ThrowCoreError("CORE_LOAD_FAILED", message, false, g_gate.InFlight(), -1,
                       {{"previous_restored", swap.previousRestored},
                        {"watch_scene_stopped", swap.previousShutDown}});
    }
    }
}
#endif

std::string CoreControl(const McpCoreCommand& command, const std::string& invalid) {
    if (!invalid.empty()) ThrowCoreError("BAD_PARAM", invalid, false, g_gate.InFlight());
    if (command.action == "status") return McpCoreStatusJson(Status()).dump();
#ifdef MCP_HOT_CORE
    return Reload(command).dump();
#else
    ThrowCoreError("CORE_STATIC",
                   "this mcp_bridge.gup links its native core statically; reloading needs a build with "
                   "MCP_HOT_CORE=ON",
                   false, g_gate.InFlight());
#endif
}

// ping, native:max_dialogs and native:core, handled as the core's dispatcher
// handled ping and max_dialogs: same envelope, DialogWatch request context,
// thread mode and error normalization.
std::string DispatchShell(const json& req) {
    try {
        const auto start = std::chrono::steady_clock::now();
        const std::string command = req.value("command", "");
        const std::string cmd_type = req.value("type", "maxscript");
        const std::string request_id = req.value("requestId", "");
        DialogWatch::RequestContext request_context(request_id, cmd_type);

        McpCoreCommand core;
        const std::string invalid = cmd_type == "native:core" ? McpParseCoreCommand(command, core) : std::string();
        // max_dialogs runs on this pipe worker, as a direct handler always did
        // (_forceMainThread only relabels it); so does native:core status.
        // ping and reload need Max's main thread.
        const bool forceMain = req.value("_forceMainThread", false);
        const bool direct = cmd_type == "native:max_dialogs" ? !forceMain
                                                             : cmd_type == "native:core" && invalid.empty() &&
                                                                   core.action == "status";
        DirectModeScope directMode(direct);
        const auto elapsedMs = [&] {
            return static_cast<int>(
                std::chrono::duration_cast<milliseconds>(std::chrono::steady_clock::now() - start).count());
        };
        try {
            std::string result;
            if (cmd_type == "native:max_dialogs") {
                // No ExecuteSync, SDK calls, undo hold, or blocked command's pipe.
                result = DialogWatch::Control(command);
            } else if (cmd_type == "ping") {
                result = Ping();
            } else {
                result = CoreControl(core, invalid);
            }
            return Envelope(true, result, "", request_id, cmd_type, elapsedMs());
        } catch (const std::exception& e) {
            return Envelope(false, "", McpEnvelope::NormalizeError(e.what()), request_id, cmd_type, elapsedMs());
        }
    } catch (const std::exception& e) {
        return McpInternalErrorResponse(e.what());
    } catch (...) {
        return McpInternalErrorResponse("unknown exception");
    }
}

// A core request refused while a reload holds the gate.
std::string BusyResponse(const std::string& request, int inFlight) {
    const McpRequestLabel label = McpLabelRequest(request);
    return Envelope(false, "",
                    McpCoreError("CORE_BUSY", "the native core is being reloaded; retry shortly", true, inFlight,
                                 g_reloadWaitMs.load()),
                    label.requestId, label.type, 0);
}

std::string UnloadedMessage() {
    std::lock_guard<std::mutex> lock(g_recordMutex);
    return "the native core is not loaded" + (g_record.lastError.empty() ? std::string() : ": " + g_record.lastError);
}

}  // namespace

namespace CoreHost {

bool Start(MainThreadExecutor& executor, std::string& error) {
    g_executor = &executor;
    g_host = HostApi(&executor);  // the only binding of the table's ctx
    const McpHostApi* host = g_host;
#ifdef MCP_HOT_CORE
    return StartHot(host, error);
#else
    // The core is linked into this .gup; it passes the same checks a loaded one would.
    const McpCoreApi* api = mcp_core_api();
    std::string failure;
    if (!api) {
        failure = "the linked native core returned no table";
    } else if (const std::string mismatch = McpCoreMismatch(*api, ThisShell()); !mismatch.empty()) {
        failure = mismatch;
    } else if (!McpCoreTableComplete(*api)) {
        failure = "the linked native core's entry points are incomplete";
    } else {
        std::string reason;
        if (api->initialize(host, McpStringSink(reason)) != 0)
            failure = "the native core failed to initialize" + (reason.empty() ? std::string() : ": " + reason);
    }
    if (!failure.empty()) {
        error = failure;
        RecordError(failure);
        return false;
    }
    g_gate.Swap(api);
    std::lock_guard<std::mutex> lock(g_recordMutex);
    RecordBound(*api, "", "");
    return true;
#endif
}

void Shutdown() {
    // The pipe has stopped and joined its workers: nothing is in flight and no
    // reload holds the gate. A hot core stays mapped until Max exits (even if
    // it declined to shut down); the next start sweeps its copy.
    if (const McpCoreApi* api = g_gate.Swap(nullptr)) {
        std::string refusal;
        api->shutdown(McpStringSink(refusal));
    }
    {
        std::lock_guard<std::mutex> lock(g_recordMutex);
        RecordUnbound();
    }
    g_deferredReleases.Clear();
    g_executor = nullptr;
}

std::string Dispatch(const std::string& request, const std::string& clientSessionId) {
    if (McpMayBeShellRequest(request)) {
        const json parsed = json::parse(request, nullptr, false);
        const std::string type = parsed.is_discarded() ? std::string() : McpShellType(parsed);
        if (type == "health") return DispatchHealth(parsed, clientSessionId);
        if (!type.empty()) {
            // native:max_dialogs is a control request like health: it never
            // reaches the main thread, and listing the client's 1 s dialog
            // polls as in-flight work would misreport them as other clients'.
            std::optional<InFlight> inflight;
            if (type != "native:max_dialogs") inflight.emplace(clientSessionId, StringMember(parsed, "requestId"), type);
            return DispatchShell(parsed);
        }
    }
    const McpRequestLabel label = McpLabelRequest(request);
    InFlight inflight(clientSessionId, label.requestId, label.type);
    McpCoreGate::Admission admission(g_gate);
    switch (admission.Status()) {
    case McpCoreGate::Admit::Busy:
        return BusyResponse(request, admission.InFlight());
    case McpCoreGate::Admit::Unloaded:
        return McpInternalErrorResponse(UnloadedMessage());
    case McpCoreGate::Admit::Entered:
        break;
    }
    std::string response;
    admission.Api()->dispatch(request.data(), request.size(), clientSessionId.c_str(), McpStringSink(response));
    if (response.empty()) return McpInternalErrorResponse("the native core returned no response");
    return response;
}

void ReleaseSession(const std::string& clientSessionId) {
    ReleaseSceneDeltaCursor(clientSessionId);
    g_deferredReleases.Release(g_gate, clientSessionId);
}

}  // namespace CoreHost
