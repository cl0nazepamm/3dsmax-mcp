#pragma once
#include <string>

class MCPBridgeGUP;

namespace CommandDispatcher {
    // Takes raw JSON request string, returns JSON response string.
    std::string Dispatch(const std::string& json_request, MCPBridgeGUP* gup);
}
