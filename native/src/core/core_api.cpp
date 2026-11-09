// The core's export: McpCoreApi over the command dispatcher. Every entry point
// catches everything; no exception leaves the core.
#include "mcp_bridge/script_hold.h"
#include "mcp_bridge/core_api.h"
#include "mcp_bridge/core_bridge.h"
#include "mcp_bridge/command_dispatcher.h"
#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/window_capture.h"

#include <atomic>
#include <ctime>
#include <stdexcept>
#include <string>

namespace {

// Handed to the dispatcher as `gup`; stateless (see core_bridge.h).
MCPBridgeGUP g_bridge;
std::atomic<bool> g_initialized{false};

int Initialize(const McpHostApi* host, McpSink error) noexcept {
    if (!host || host->version != kMcpHostApiVersion) {
        McpWriteText(error, "the bridge host API version does not match this core");
        return 1;
    }
    McpHost::Bind(host);
    g_initialized = true;
    return 0;
}

// Leaves no pointer into this module with Max or the shell, so the shell may
// unload it afterwards. Max's main thread, nothing in flight.
int Shutdown(McpSink error) noexcept {
    if (NativeHandlers::SceneWatcherCallbackActive()) {
        McpWriteText(error, "a 3ds Max notification is running inside the native core; retry");
        return 1;
    }
    // The script's open undo step lives in this module; a new core would not
    // know to close it.
    if (ScriptHold::Active()) {
        McpWriteText(error, "a run_python script is running inside 3ds Max; reload after it finishes");
        return 1;
    }
    g_initialized = false;
    try { NativeHandlers::ShutdownSceneWatcher(); } catch (...) {}
    WindowCapture::ReleaseFactories();
    McpHost::Bind(nullptr);
    return 0;
}

int Dispatch(const char* requestJson, size_t requestLen, const char* clientSessionId, McpSink out) noexcept {
    try {
        try {
            if (!g_initialized) throw std::runtime_error("the native core is not initialized");
            McpWrite(out, CommandDispatcher::Dispatch(
                std::string(requestJson ? requestJson : "", requestJson ? requestLen : 0), &g_bridge,
                clientSessionId ? clientSessionId : ""));
            return 0;
        } catch (const std::exception& e) {
            McpWrite(out, McpInternalErrorResponse(e.what()));
        } catch (...) {
            McpWrite(out, McpInternalErrorResponse("unknown exception"));
        }
    } catch (...) {
        // Out of memory while reporting; the shell answers an empty response.
    }
    return 1;
}

void ReleaseSession(const char* clientSessionId) noexcept {
    try { NativeHandlers::ReleaseSceneDeltaSession(clientSessionId ? clientSessionId : ""); } catch (...) {}
}

// "mcp_core <version> (linked <UTC time>)": the link time of the module that
// holds this code, so every rebuild reports a new stamp after a reload.
std::string BuildStamp() {
    std::string stamp = "mcp_core " MCP_BRIDGE_VERSION;
    HMODULE self = nullptr;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                            reinterpret_cast<LPCWSTR>(&BuildStamp), &self))
        return stamp;
    const auto* dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(self);
    const auto* nt = reinterpret_cast<const IMAGE_NT_HEADERS*>(reinterpret_cast<const BYTE*>(self) + dos->e_lfanew);
    const std::time_t linked = static_cast<std::time_t>(nt->FileHeader.TimeDateStamp);
    std::tm utc{};
    char text[32] = {};
    if (gmtime_s(&utc, &linked) == 0 && std::strftime(text, sizeof(text), "%Y-%m-%d %H:%M:%S", &utc))
        stamp += std::string(" (linked ") + text + " UTC)";
    return stamp;
}

const McpCoreApi& CoreApi() {
    static const std::string build = BuildStamp();
    static const McpCoreApi api = {
        kMcpCoreApiVersion,
        build.c_str(),
        MCP_BRIDGE_VERSION,
        MAX_SDK_VERSION,
        _MSC_FULL_VER,
        kMcpHostApiVersion,
        _ITERATOR_DEBUG_LEVEL,
        &Initialize,
        &Shutdown,
        &Dispatch,
        &ReleaseSession,
    };
    return api;
}

}  // namespace

extern "C" MCP_CORE_EXPORT const McpCoreApi* mcp_core_api() {
    try {
        return &CoreApi();
    } catch (...) {
        return nullptr;  // out of memory building the stamp
    }
}
