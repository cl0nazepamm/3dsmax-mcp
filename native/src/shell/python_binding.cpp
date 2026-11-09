// In-process entry point for Python running inside 3ds Max (maxmcp.inmax / mx).
//
// mcp_bridge_call takes the same request JSON a pipe client sends and returns the
// same response, routed through CoreHost::Dispatch: the reload gate, CORE_BUSY,
// safe mode and main-thread rules are identical. Called on Max's main thread (the
// usual case for pymxs scripts), main-thread work runs inline, as for a handler
// that re-enters Dispatch; from another thread it is marshalled like a pipe request.
//
// The returned pointer is valid until the same thread's next call. Plain C types
// only, so ctypes can call it without a compiled Python extension.
#include "mcp_bridge/core_api.h"
#include "mcp_bridge/core_host.h"

#include <cstddef>
#include <exception>
#include <string>

namespace {

// scene_delta cursors and in-flight labels for in-process callers.
constexpr const char* kPythonSession = "python-inproc";

}  // namespace

extern "C" __declspec(dllexport) const char* mcp_bridge_call(const char* request, size_t length,
                                                             size_t* responseLength) {
    thread_local std::string response;
    try {
        response = CoreHost::Dispatch(std::string(request ? request : "", request ? length : 0), kPythonSession);
    } catch (const std::exception& e) {
        response = McpInternalErrorResponse(e.what());
    } catch (...) {
        response = McpInternalErrorResponse("unknown exception");
    }
    if (responseLength) *responseLength = response.size();
    return response.c_str();
}
