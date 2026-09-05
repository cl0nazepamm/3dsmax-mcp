#include "mcp_bridge/native_tool_registry.h"
#include "mcp_bridge/command_dispatcher.h"

using json = nlohmann::json;

struct NativeTool {
    const char* name;
    const char* cmdType;
};

#include "generated/native_tool_registry.inc"

std::string NativeToolRegistry::ExecuteTool(
    const std::string& toolName,
    const json& input,
    MCPBridgeGUP* gup) {

    // Find tool in registry
    const NativeTool* tool = nullptr;
    for (size_t i = 0; i < kNativeToolCount; ++i) {
        if (toolName == kNativeTools[i].name) { tool = &kNativeTools[i]; break; }
    }
    if (!tool) {
        return json{{"error", "Unknown tool: " + toolName}}.dump();
    }

    // Build command payload:
    //   maxscript: command = raw code string (input["code"])
    //   native:*:  command = input.dump() — handler parses JSON
    std::string cmdType = tool->cmdType;
    std::string command;
    if (cmdType == "maxscript") {
        command = input.value("code", "");
    } else {
        command = input.is_null() ? "{}" : input.dump();
    }

    json req;
    req["type"] = cmdType;
    req["command"] = command;
    req["requestId"] = std::string("invoke-") + toolName;

    std::string raw = CommandDispatcher::Dispatch(req.dump(), gup, "native-tool-probe");

    // Dispatch returns JSON with success/result/error — unwrap for native probe callers
    try {
        json r = json::parse(raw);
        if (r.value("success", false)) {
            // Result may be a JSON string (from handlers that return dump()'d JSON)
            // or plain text; pass through verbatim.
            std::string result = r.value("result", "");
            return result.empty() ? "{}" : result;
        } else {
            return json{{"error", r.value("error", "Unknown error")}}.dump();
        }
    } catch (const std::exception& e) {
        return json{{"error", std::string("Dispatch response parse error: ") + e.what()}}.dump();
    }
}
