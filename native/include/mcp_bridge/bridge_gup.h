#pragma once
#include <max.h>
#include <gup.h>
#include <iparamb2.h>
#include <memory>

#include "main_thread_executor.h"
#include "pipe_server.h"

// "MCPB" + "RIDG" as hex
#define MCP_BRIDGE_CLASS_ID Class_ID(0x4D435042, 0x52494447)

class MCPBridgeGUP : public GUP {
public:
    MCPBridgeGUP() = default;
    ~MCPBridgeGUP() override = default;

    // GUP interface
    DWORD Start() override;
    void Stop() override;
    void DeleteThis() override;
    DWORD_PTR Control(DWORD parameter) override { return 0; }

    MainThreadExecutor& GetExecutor() { return executor_; }

private:
    std::unique_ptr<PipeServer> pipe_server_;
    MainThreadExecutor executor_;
};

extern HINSTANCE hInstance;
ClassDesc2* GetMCPBridgeDesc();
