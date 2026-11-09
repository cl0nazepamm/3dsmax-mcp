#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/plugin_access.h"
#include "mcp_bridge/core_bridge.h"

std::string NativeHandlers::PluginInspect(const std::string& params,MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([params]() { return PluginAccess::Inspect(nlohmann::json::parse(params)).dump(); });
}
std::string NativeHandlers::PluginPatch(const std::string& params,MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([params]() { return PluginAccess::Apply(nlohmann::json::parse(params)).dump(); });
}
std::string NativeHandlers::LightingContext(const std::string&,MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([]() { return PluginAccess::Context().dump(); });
}
