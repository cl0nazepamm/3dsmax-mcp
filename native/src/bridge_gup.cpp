#include "mcp_bridge/bridge_gup.h"
#include <maxapi.h>

// ── ClassDesc2 ──────────────────────────────────────────────────
class MCPBridgeClassDesc : public ClassDesc2 {
public:
    int IsPublic() override { return TRUE; }
    void* Create(BOOL) override { return new MCPBridgeGUP(); }
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

// ── GUP implementation ──────────────────────────────────────────
DWORD MCPBridgeGUP::Start() {
    // Initialize main thread executor (must happen on main thread)
    executor_.Initialize();

    // Start pipe server on background thread
    pipe_server_ = std::make_unique<PipeServer>(this);
    pipe_server_->Start();

    // Log to MAXScript Listener
    Interface* ip = GetCOREInterface();
    if (ip) {
        LogSys* log = ip->Log();
        if (log) {
            log->LogEntry(SYSLOG_INFO, NO_DIALOG,
                _T("MCP Bridge"),
                _T("MCP Bridge: Native pipe server started on \\\\.\\pipe\\3dsmax-mcp"));
        }
    }

    return GUPRESULT_KEEP;
}

void MCPBridgeGUP::Stop() {
    if (pipe_server_) {
        pipe_server_->Stop();
        pipe_server_.reset();
    }
    executor_.Shutdown();
}

void MCPBridgeGUP::DeleteThis() {
    delete this;
}
