#include "mcp_bridge/bridge_gup.h"
#include "mcp_bridge/core_host.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/gdiplus_runtime.h"
#include "mcp_bridge/scene_journal.h"
#include "mcp_bridge/agent_viewport.h"
#include "mcp_bridge/dialog_watch.h"
#include "mcp_bridge/qt_dialogs.h"
#include "mcp_bridge/render_host.h"
#include <maxapi.h>
#include <notify.h>
#include <shlobj.h>
#include <fstream>
#include <sstream>
#include <string>

// ── ClassDesc2 ──────────────────────────────────────────────────
class MCPBridgeClassDesc : public ClassDesc2 {
public:
    int IsPublic() override { return TRUE; }
    void* Create(BOOL) override { return new MCPBridgeShellGUP(); }
    const TCHAR* ClassName() override { return _T("MCP Bridge"); }
    const TCHAR* NonLocalizedClassName() override { return _T("MCP Bridge"); }
    SClass_ID SuperClassID() override { return GUP_CLASS_ID; }
    Class_ID ClassID() override { return MCP_BRIDGE_CLASS_ID; }
    const TCHAR* Category() override { return _T("MCP"); }
    const TCHAR* InternalName() override { return _T("MCPBridge"); }
    HINSTANCE HInstance() override { return hInstance; }
};

static MCPBridgeClassDesc mcpBridgeDesc;
ClassDesc2* GetMCPBridgeDesc() { return &mcpBridgeDesc; }

// ── Register macroscripts after Max is fully loaded ─────────────
static MCPBridgeShellGUP* g_gupInstance = nullptr;

static std::string LocalAppDataDir() {
    char buf[MAX_PATH];
    if (FAILED(SHGetFolderPathA(nullptr, CSIDL_LOCAL_APPDATA, nullptr, 0, buf))) {
        return {};
    }
    return std::string(buf) + "\\3dsmax-mcp";
}

static std::string InstancesDir() {
    auto dir = LocalAppDataDir();
    return dir.empty() ? "" : (dir + "\\instances");
}

static std::string ActiveInstancePath() {
    auto dir = LocalAppDataDir();
    return dir.empty() ? "" : (dir + "\\active_instance.json");
}

static std::string InstancePath(const std::string& instance_id) {
    auto dir = InstancesDir();
    return dir.empty() ? "" : (dir + "\\" + instance_id + ".json");
}

static void EnsureRegistryDirs() {
    std::string base = LocalAppDataDir();
    std::string instances = InstancesDir();
    if (!base.empty()) CreateDirectoryA(base.c_str(), nullptr);
    if (!instances.empty()) CreateDirectoryA(instances.c_str(), nullptr);
}

static std::string JsonEscape(const std::string& value) {
    std::string out;
    out.reserve(value.size() + 8);
    for (unsigned char c : value) {
        switch (c) {
            case '\\': out += "\\\\"; break;
            case '"': out += "\\\""; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 0x20) {
                    char buf[7];
                    sprintf_s(buf, "\\u%04x", c);
                    out += buf;
                } else {
                    out += static_cast<char>(c);
                }
        }
    }
    return out;
}

static bool WriteTextFile(const std::string& path, const std::string& text) {
    if (path.empty()) return false;
    std::ofstream f(path, std::ios::binary | std::ios::trunc);
    if (!f.is_open()) return false;
    f << text;
    return true;
}

static std::string ReadTextFile(const std::string& path) {
    if (path.empty()) return {};
    std::ifstream f(path, std::ios::binary);
    if (!f.is_open()) return {};
    std::ostringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

static void LogBridge(const std::wstring& message, int type = SYSLOG_INFO) {
    Interface* ip = GetCOREInterface();
    LogSys* log = ip ? ip->Log() : nullptr;
    if (log) {
        log->LogEntry(type, NO_DIALOG, _T("MCP Bridge"), message.c_str());
    }
}

static void SetNativePipeRunningFlag(bool running) {
    // Shell code passes its own dialog check; the one-argument RunMAXScript
    // reaches the main thread and DialogWatch through the core's host binding.
    HandlerHelpers::RunMAXScript(std::string("global MCP_NativePipeRunning; MCP_NativePipeRunning = ") +
        (running ? "true" : "false"), &DialogWatch::ThrowIfDismissed);
}

static std::string BuildInstanceJson(
    const std::string& instance_id,
    const std::string& pipe_name) {
    DWORD pid = GetCurrentProcessId();
    std::ostringstream ss;
    ss << "{\n"
       << "  \"instance_id\": \"" << JsonEscape(instance_id) << "\",\n"
       << "  \"pid\": " << pid << ",\n"
       << "  \"pipe\": \"" << JsonEscape(pipe_name) << "\",\n"
       << "  \"max_version\": " << MAX_SDK_VERSION << "\n"
       << "}\n";
    return ss.str();
}

void MCPBridgeShellGUP::RegisterInstance() {
    EnsureRegistryDirs();
    WriteTextFile(InstancePath(instance_id_), BuildInstanceJson(instance_id_, pipe_name_utf8_));
}

void MCPBridgeShellGUP::UnregisterInstance() {
    std::string active = ReadTextFile(ActiveInstancePath());
    if (active.find("\"instance_id\": \"" + instance_id_ + "\"") != std::string::npos) {
        DeleteFileA(ActiveInstancePath().c_str());
    }
    DeleteFileA(InstancePath(instance_id_).c_str());
}

void MCPBridgeShellGUP::ClaimInstance() {
    RegisterInstance();
    if (WriteTextFile(ActiveInstancePath(), BuildInstanceJson(instance_id_, pipe_name_utf8_))) {
        LogBridge(L"MCP Bridge: this 3ds Max instance is now the active MCP target");
    } else {
        LogBridge(L"MCP Bridge: failed to write active instance file", SYSLOG_WARN);
    }
}

void MCPBridgeShellGUP::StartPipe() {
    if (pipe_server_ && pipe_server_->IsRunning()) {
        SetNativePipeRunningFlag(true);
        return;
    }

    pipe_server_ = std::make_unique<PipeServer>(HandlerHelpers::Utf8ToWide(pipe_name_utf8_));
    pipe_server_->Start();
    SetNativePipeRunningFlag(true);
    RegisterInstance();
    LogBridge(L"MCP Bridge: native pipe server started on " + HandlerHelpers::Utf8ToWide(pipe_name_utf8_));
}

void MCPBridgeShellGUP::StopPipe() {
    if (pipe_server_) {
        pipe_server_->Stop();
        pipe_server_.reset();
    }
    SetNativePipeRunningFlag(false);
}

bool MCPBridgeShellGUP::IsPipeRunning() const {
    return pipe_server_ && pipe_server_->IsRunning();
}

void ClaimNativeInstance() {
    if (g_gupInstance) g_gupInstance->ClaimInstance();
}

// ── GUP implementation ──────────────────────────────────────────
DWORD MCPBridgeShellGUP::Start() {
    g_gupInstance = this;
    executor_.Initialize();
    SetNativePipeRunningFlag(false);
    instance_id_ = "pid-" + std::to_string(GetCurrentProcessId());
    pipe_name_utf8_ = "\\\\.\\pipe\\3dsmax-mcp-" + instance_id_;

    // Before the pipe, which dispatches requests to the core. Linked into this
    // .gup, or loaded from core/mcp_core.dll (MCP_HOT_CORE). On failure the
    // pipe still answers ping, native:max_dialogs and native:core.
    std::string coreError;
    if (!CoreHost::Start(executor_, coreError)) {
        LogBridge(L"MCP Bridge: native core unavailable: " + HandlerHelpers::Utf8ToWide(coreError), SYSLOG_ERROR);
    }

    // Before the pipe: control requests rely on the monitor and its lane.
    // A stale heartbeat (3 periods) means the main thread is not pumping: the
    // dialog lane then never sends into it.
    DialogWatch::SetMainThreadPumping([] {
        const long long age = MainThreadExecutor::HeartbeatAgeMs();
        return age < 0 || age <= 3LL * MainThreadExecutor::kHeartbeatMs;
    });
    DialogWatch::Start({QtDialogs::Snapshot, QtDialogs::Click});
    StartPipe();
    SceneJournal::Register();

    // Render automation: hook NOTIFY_POST_RENDER so render_start jobs emit a
    // filesystem done-signal at the real completion event (no polling).
    RenderHost::RegisterNotifications();

    return GUPRESULT_KEEP;
}

void MCPBridgeShellGUP::Stop() {
    // FIRST: pipe threads waiting on requests that will never start would hold
    // the StopPipe join below (Max's exit stalled for up to the executor timeout
    // per in-flight request); fail those requests and refuse new ones.
    // executor_.Shutdown() still runs last to destroy the window.
    executor_.StopAccepting();
    AgentViewport::Shutdown(true);
    SceneJournal::Unregister();
    RenderHost::UnregisterNotifications();

    StopPipe();
    // No request is in flight once the pipe has stopped.
    CoreHost::Shutdown();
    DialogWatch::Stop();
    GdiPlusRuntime::Shutdown();
    UnregisterInstance();
    executor_.Shutdown();
    g_gupInstance = nullptr;
}

void MCPBridgeShellGUP::DeleteThis() {
    delete this;
}
