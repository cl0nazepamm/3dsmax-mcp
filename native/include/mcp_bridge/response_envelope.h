#pragma once
// The pipe's response envelope and NativeError normalization. Header-only and
// compiled into both sides, so the core's dispatcher and the shell's own
// answers (ping, native:max_dialogs, native:core, busy) share one shape.
// Callers supply what each side reads from its own state.
#include <algorithm>
#include <ctype.h>
#include <string>
#include <nlohmann/json.hpp>

namespace McpEnvelope {

inline std::string ErrorCodeForMessage(const std::string& message) {
    std::string lower = message;
    std::transform(lower.begin(), lower.end(), lower.begin(), ::tolower);
    if (lower.find("user_busy") != std::string::npos) return "USER_BUSY";
    if (lower.find("stale_view") != std::string::npos) return "STALE_VIEW";
    // The executor refused or failed the request before it started (Max is
    // exiting, the bridge never came up, or the post failed): bridge down,
    // and the work provably never ran.
    if (lower.find("main thread execution timed out") != std::string::npos ||
        lower.find("3ds max is exiting; the request did not run") != std::string::npos ||
        lower.find("mainthreadexecutor is shutting down") != std::string::npos ||
        lower.find("mainthreadexecutor not initialized") != std::string::npos ||
        lower.find("failed to post work to main thread") != std::string::npos ||
        lower.find("named pipe") != std::string::npos ||
        (lower.find("bridge") != std::string::npos && lower.find("not found") != std::string::npos))
        return "BRIDGE_DOWN";
    if (lower.find("render busy") != std::string::npos ||
        lower.find("already rendering") != std::string::npos)
        return "RENDER_BUSY";
    if (lower.find("ambiguous") != std::string::npos) return "AMBIGUOUS";
    if (lower.find("unknown object class") != std::string::npos ||
        lower.find("unknown modifier class") != std::string::npos ||
        lower.find("unknown material class") != std::string::npos ||
        lower.find("unknown class") != std::string::npos ||
        (lower.find("plugin") != std::string::npos && lower.find("missing") != std::string::npos))
        return "PLUGIN_MISSING";
    if (lower.find("not found") != std::string::npos ||
        lower.find("no material assigned") != std::string::npos ||
        lower.find("no modifiers on") != std::string::npos)
        return "NOT_FOUND";
    return "BAD_PARAM";
}

// Codes a client may retry: the request did not run, or will run when Max is free.
inline bool IsRetryableCode(const std::string& code) {
    return code == "BRIDGE_DOWN" || code == "RENDER_BUSY" || code == "USER_BUSY";
}

// A structured error passes through; any other message becomes a NativeError.
inline std::string NormalizeError(const std::string& message) {
    nlohmann::json structured = nlohmann::json::parse(message, nullptr, false);
    if (!structured.is_discarded() && structured.is_object() &&
        (structured.contains("code") || structured.contains("message"))) {
        return structured.dump();
    }
    const std::string code = ErrorCodeForMessage(message);
    nlohmann::json payload;
    payload["type"] = "NativeError";
    payload["message"] = message;
    payload["code"] = code;
    payload["retryable"] = IsRetryableCode(code);
    return payload.dump();
}

// Every pipe response. `direct`: the request ran on its pipe worker (threadMode).
// `openDialogs`: DialogWatch::OpenDialogs() at the time of the response.
inline std::string Build(bool success, const std::string& result, const std::string& error,
                         const std::string& requestId, const std::string& cmdType, int durationMs,
                         bool direct, nlohmann::json openDialogs) {
    nlohmann::json resp;
    resp["success"] = success;
    resp["requestId"] = requestId;
    resp["result"] = result;
    resp["error"] = error;
    resp["meta"] = {
        {"protocolVersion", 2},
        {"cmdType", cmdType},
        {"durationMs", durationMs},
        {"transport", "namedpipe"},
        {"threadMode", direct ? "direct" : "mainThread"}
    };
    // Every response reports dialogs blocking Max, whoever opened them.
    if (!openDialogs.empty()) resp["meta"]["openDialogs"] = std::move(openDialogs);
    return resp.dump();
}

}  // namespace McpEnvelope
