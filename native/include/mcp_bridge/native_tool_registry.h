#pragma once
#include <string>
#include <nlohmann/json.hpp>

class MCPBridgeGUP;

// Direct native routes used by invoke_tool and the native diagnostic harness.
// Python orchestration still runs through the external MCP server.
namespace NativeToolRegistry {
std::string ExecuteTool(const std::string& toolName,
                        const nlohmann::json& input, MCPBridgeGUP* gup);
}
