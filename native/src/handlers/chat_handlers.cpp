#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/bridge_gup.h"
#include "mcp_bridge/chat_ui.h"
#include "mcp_bridge/llm_client.h"

#include <thread>
#include <mutex>
#include <condition_variable>
#include <functional>
#include <chrono>

using json = nlohmann::json;
using namespace HandlerHelpers;

// ── Conversation state ──────────────────────────────────────────
static std::mutex g_convMutex;
static std::vector<LLMClient::Message> g_conversation;
static std::atomic<bool> g_processing{false};

// ── Detached chat-thread tracking ──────────────────────────────
// ProcessChatMessage launches std::thread(...).detach() per user message.
// The thread captures MCPBridgeGUP* and can sit in WinHTTP for up to 180 s.
// Without a drain on shutdown, MCPBridgeGUP::Stop() deletes the executor
// while the thread is mid-call → use-after-free. We track in-flight count
// here and expose WaitForChatTurns() to bridge_gup.cpp Stop().
static std::atomic<int> g_inFlightTurns{0};
static std::mutex g_inFlightMutex;
static std::condition_variable g_inFlightCv;

// Completion callback for `send` action: receives final assistant text,
// a JSON array of tool calls made during the turn, and an error string
// (empty on success). Called once per ProcessChatMessage invocation.
using ChatCompleteCallback =
    std::function<void(const std::string& replyText, const json& toolCalls, const std::string& error)>;

struct ChatTurnResult {
    std::string reply;
    json toolCalls = json::array();
};

static std::string ChatTimeoutError(int timeout_ms) {
    return "Timed out waiting for chat reply (timeout_ms=" + std::to_string(timeout_ms) + ")";
}

static void ThrowIfTimedOut(
    const std::chrono::steady_clock::time_point& deadline,
    int timeout_ms) {
    if (timeout_ms <= 0) return;
    if (std::chrono::steady_clock::now() >= deadline) {
        throw std::runtime_error(ChatTimeoutError(timeout_ms));
    }
}

static int RemainingTimeoutMs(
    const std::chrono::steady_clock::time_point& deadline,
    int timeout_ms) {
    if (timeout_ms <= 0) return timeout_ms;
    auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(
        deadline - std::chrono::steady_clock::now()).count();
    if (remaining <= 0) {
        throw std::runtime_error(ChatTimeoutError(timeout_ms));
    }
    return static_cast<int>(remaining);
}

static size_t ConversationLength() {
    std::lock_guard<std::mutex> lock(g_convMutex);
    return g_conversation.size();
}

// Walk back to the last UTF-8 lead byte so the cut never falls in the middle
// of a multi-byte sequence — nlohmann::json::dump() throws type_error.316 on
// invalid UTF-8 and would brick the conversation history with a single
// non-ASCII name (CJK, accented Latin, em-dash, smart quotes, etc.).
static size_t Utf8SafeCut(const std::string& s, size_t cap) {
    if (cap >= s.size()) return s.size();
    while (cap > 0 && (static_cast<unsigned char>(s[cap]) & 0xC0) == 0x80) {
        --cap;
    }
    return cap;
}

static ChatTurnResult RunChatTurn(const std::string& text, MCPBridgeGUP* gup, int timeout_ms = 0) {
    ChatTurnResult turn;

    // Slash commands and full turns share the gate so that /reload (which
    // overwrites g_config std::string fields) and /clear (which wipes
    // g_conversation mid-turn, leaving orphan tool messages the API will
    // reject) cannot race with an in-flight HTTP call.
    bool expected = false;
    if (!g_processing.compare_exchange_strong(expected, true)) {
        throw std::runtime_error("Chat is busy - another turn is in progress.");
    }

    struct ProcessingReset {
        ~ProcessingReset() {
            MCPChatUI::SetStatus("");
            g_processing = false;
        }
    } reset;

    // Slash commands — handled inline, no LLM call
    if (text == "/reload" || text == "/refresh") {
        LLMClient::Init();
        MCPChatUI::SetStatus("");
        const auto& cfg = LLMClient::GetConfig();
        std::string fp = LLMClient::GetApiKeyFingerprint();
        MCPChatUI::AppendMessage(
            "ai",
            "Config reloaded. Model: " + cfg.model +
            " | Base: " + cfg.baseUrl +
            " | Auth: " + LLMClient::GetApiKeySource() +
            (fp.empty() ? "" : (" #" + fp.substr(0, 8)))
        );
        return turn;
    }
    if (text == "/clear") {
        {
            std::lock_guard<std::mutex> lock(g_convMutex);
            g_conversation.clear();
        }
        MCPChatUI::ClearHistory();
        MCPChatUI::AppendMessage("ai", "Conversation cleared.");
        return turn;
    }
    if (text == "/help") {
        MCPChatUI::AppendMessage("system",
            "Slash commands:\n"
            "  /reload  - re-read .env and mcp_config.ini (model/key change without restart)\n"
            "  /clear   - drop conversation history (Ctrl+L)\n"
            "  /help    - this message\n\n"
            "Keyboard:\n"
            "  Enter        - send\n"
            "  Shift+Enter  - newline\n"
            "  Ctrl+Enter   - newline\n"
            "  Ctrl+L       - /clear\n"
            "  Ctrl+R       - /reload");
        return turn;
    }

    MCPChatUI::SetStatus("thinking...");

    auto deadline = timeout_ms > 0
        ? (std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms))
        : std::chrono::steady_clock::time_point::max();

    {
        std::lock_guard<std::mutex> lock(g_convMutex);
        g_conversation.push_back({"user", text, "", nullptr});
    }

    // System prompt: runtime preamble + cached SKILL.md + current scene snapshot
    std::string systemPrompt;
    try {
        systemPrompt = LLMClient::BuildSystemPrompt(gup);
    } catch (...) {
        systemPrompt = "You are an AI assistant inside 3ds Max.";
    }

    std::vector<LLMClient::Message> messages;
    messages.push_back({"system", systemPrompt, "", nullptr});
    {
        std::lock_guard<std::mutex> lock(g_convMutex);
        messages.insert(messages.end(), g_conversation.begin(), g_conversation.end());
    }

    const auto& cfg = LLMClient::GetConfig();
    json tools = LLMClient::GetToolDefinitions();

    int maxLoops = cfg.maxToolLoops;
    for (int loop = 0; loop < maxLoops; loop++) {
        MCPChatUI::SetStatus("calling API...");
        auto response = LLMClient::Chat(messages, tools, RemainingTimeoutMs(deadline, timeout_ms));
        ThrowIfTimedOut(deadline, timeout_ms);

        if (!response.ok) {
            throw std::runtime_error(response.error);
        }

        json toolCallsJson = json::array();
        for (auto& tc : response.toolCalls) {
            toolCallsJson.push_back({
                {"id", tc.id},
                {"type", "function"},
                {"function", {
                    {"name", tc.name},
                    {"arguments", tc.arguments.dump()}
                }}
            });
        }

        if (!response.text.empty()) {
            MCPChatUI::AppendMessage("ai", response.text);
            turn.reply = response.text;
        }

        if (!response.text.empty() || !response.toolCalls.empty()) {
            json assistantToolCalls = response.toolCalls.empty() ? json(nullptr) : toolCallsJson;
            LLMClient::Message assistantMessage{
                "assistant",
                response.text.empty() ? "" : response.text,
                "",
                assistantToolCalls
            };

            {
                std::lock_guard<std::mutex> lock(g_convMutex);
                g_conversation.push_back(assistantMessage);
            }
            messages.push_back(assistantMessage);
        }

        if (response.toolCalls.empty()) {
            break;
        }

        for (auto& tc : response.toolCalls) {
            ThrowIfTimedOut(deadline, timeout_ms);
            MCPChatUI::SetStatus("running " + tc.name + "...");

            std::string toolResult;
            try {
                toolResult = LLMClient::ExecuteTool(tc.name, tc.arguments, gup);
            } catch (const std::exception& e) {
                toolResult = std::string("{\"error\":\"") + e.what() + "\"}";
            }

            ThrowIfTimedOut(deadline, timeout_ms);

            const size_t DISPLAY_CAP = (size_t)cfg.maxDisplayToolChars;
            if (toolResult.size() > DISPLAY_CAP) {
                MCPChatUI::AppendMessage("tool",
                    tc.name + "  " + toolResult.substr(0, Utf8SafeCut(toolResult, DISPLAY_CAP)) +
                    "  ...(+" + std::to_string(toolResult.size() - DISPLAY_CAP) + " chars)");
            } else {
                MCPChatUI::AppendMessage("tool", tc.name + "  " + toolResult);
            }

            const size_t SUMMARY_CAP = (size_t)cfg.maxToolSummaryChars;
            std::string truncated = toolResult.size() > SUMMARY_CAP
                ? toolResult.substr(0, Utf8SafeCut(toolResult, SUMMARY_CAP)) + "..."
                : toolResult;
            turn.toolCalls.push_back({
                {"name", tc.name},
                {"arguments", tc.arguments},
                {"result", truncated}
            });

            // The live turn gets a larger configurable cap; historical copies
            // are tighter so one deep introspection cannot bloat every later turn.
            const size_t LIVE_CAP = (size_t)cfg.maxToolResultChars;
            std::string liveResult = toolResult.size() > LIVE_CAP
                ? toolResult.substr(0, Utf8SafeCut(toolResult, LIVE_CAP)) +
                  "\n...[truncated for live turn, " +
                  std::to_string(toolResult.size() - LIVE_CAP) + " chars omitted]"
                : toolResult;

            const size_t HISTORY_CAP = (size_t)cfg.maxHistoryToolChars;
            std::string historyResult = toolResult.size() > HISTORY_CAP
                ? toolResult.substr(0, Utf8SafeCut(toolResult, HISTORY_CAP)) +
                  "\n...[truncated for history, " +
                  std::to_string(toolResult.size() - HISTORY_CAP) + " chars omitted]"
                : toolResult;

            LLMClient::Message liveToolMessage{"tool", liveResult,    tc.id, nullptr};
            LLMClient::Message histToolMessage{"tool", historyResult, tc.id, nullptr};
            {
                std::lock_guard<std::mutex> lock(g_convMutex);
                g_conversation.push_back(histToolMessage);
            }
            messages.push_back(liveToolMessage);
        }
    }

    return turn;
}

// ── Process a user message in background ────────────────────────
void ProcessChatMessage(const std::string& text,
                        MCPBridgeGUP* gup,
                        ChatCompleteCallback onComplete) {
    g_inFlightTurns.fetch_add(1, std::memory_order_acq_rel);
    std::thread([text, gup, onComplete]() {
        struct Decrement {
            ~Decrement() {
                if (g_inFlightTurns.fetch_sub(1, std::memory_order_acq_rel) == 1) {
                    std::lock_guard<std::mutex> lock(g_inFlightMutex);
                    g_inFlightCv.notify_all();
                }
            }
        } dec;
        try {
            ChatTurnResult result = RunChatTurn(text, gup);
            if (onComplete) onComplete(result.reply, result.toolCalls, "");
        } catch (const std::exception& e) {
            std::string err = e.what();
            MCPChatUI::AppendMessage("error", err);
            if (onComplete) onComplete("", json::array(), err);
        } catch (...) {
            std::string err = "Unknown exception in chat loop";
            MCPChatUI::AppendMessage("error", err);
            if (onComplete) onComplete("", json::array(), err);
        }
    }).detach();
}

// Called from MCPBridgeGUP::Stop() before destroying the executor / chat UI.
// Returns true if all detached chat threads completed within timeout_ms.
bool WaitForChatTurns(int timeout_ms) {
    std::unique_lock<std::mutex> lock(g_inFlightMutex);
    return g_inFlightCv.wait_for(
        lock,
        std::chrono::milliseconds(timeout_ms),
        []{ return g_inFlightTurns.load(std::memory_order_acquire) == 0; });
}

// Overload so existing extern declarations in bridge_gup.cpp
// (`extern void ProcessChatMessage(string, MCPBridgeGUP*)`) still link.
void ProcessChatMessage(const std::string& text, MCPBridgeGUP* gup) {
    ProcessChatMessage(text, gup, nullptr);
}

// ── Process action button clicks ────────────────────────────────
void ProcessChatAction(const std::string& action, const std::string& detail, MCPBridgeGUP* gup) {
    if (action == "analyze_selection") {
        if (detail.empty()) {
            MCPChatUI::AppendMessage("ai", "Nothing selected. Select objects first.");
            return;
        }
        ProcessChatMessage("Analyze the selected object(s): " + detail + ". Give me a quick overview.", gup);
    } else if (action == "capture") {
        ProcessChatMessage("Capture the viewport and describe what you see.", gup);
    } else if (action == "done") {
        MCPChatUI::AppendMessage("ai", "Got it. Let me know when you need anything.");
    }
}

// ── `send` action: called from pipe worker thread, blocks until reply ─

static std::string HandleSend(const json& p, MCPBridgeGUP* gup) {
    std::string message = p.value("message", "");
    if (message.empty()) {
        throw std::runtime_error("empty message");
    }
    int timeout_ms = p.value("timeout_ms", 180000);
    bool silent    = p.value("silent", false);

    if (!silent) MCPChatUI::AppendMessage("user", message);
    try {
        ChatTurnResult turn = RunChatTurn(message, gup, timeout_ms);
        json result;
        result["reply"] = turn.reply;
        result["toolCalls"] = turn.toolCalls;
        result["model"] = LLMClient::GetConfig().model;
        result["toolProfile"] = LLMClient::GetConfig().toolProfile;
        result["promptMode"] = LLMClient::GetConfig().promptMode;
        result["apiKeySource"] = LLMClient::GetApiKeySource();
        result["apiKeyFingerprint"] = LLMClient::GetApiKeyFingerprint();
        return result.dump();
    } catch (const std::exception& e) {
        MCPChatUI::AppendMessage("error", e.what());
        throw;
    }
}

// ═════════════════════════════════════════════════════════════════
// native:chat_ui handler
// ═════════════════════════════════════════════════════════════════

std::string NativeHandlers::ChatUI(const std::string& params, MCPBridgeGUP* gup) {
    json p = params.empty() ? json::object() : json::parse(params, nullptr, false);
    std::string action = p.value("action", "status");

    // `send` must NOT marshal to main thread — it performs the full turn
    // synchronously on the pipe worker thread. Running it on main would freeze
    // Max's UI during the HTTP call. Tools invoked by the LLM still marshal via
    // CommandDispatcher.
    if (action == "send") {
        return HandleSend(p, gup);
    }

    // Everything else touches the Win32 chat window — must run on main thread.
    return gup->GetExecutor().ExecuteSync([p, action, gup]() -> std::string {
        if (action == "show") {
            MCPChatUI::SetMessageCallback([gup](const std::string& text) {
                ProcessChatMessage(text, gup);
            });
            MCPChatUI::SetActionCallback([gup](const std::string& act, const std::string& detail) {
                ProcessChatAction(act, detail, gup);
            });

            MCPChatUI::Show(gup);

            if (LLMClient::IsConfigured()) {
                MCPChatUI::AppendMessage("ai", "Chat ready. Model: " + LLMClient::GetConfig().model);
            } else {
                MCPChatUI::AppendMessage("system",
                    "No API key found. Edit %LOCALAPPDATA%\\3dsmax-mcp\\.env:\n\n"
                    "    OPENROUTER_API_KEY=sk-or-...\n\n"
                    "Then type /reload (or Ctrl+R). Model + base_url are in mcp_config.ini.");
            }

            json result;
            result["visible"] = true;
            result["configured"] = LLMClient::IsConfigured();
            result["model"] = LLMClient::GetConfig().model;
            result["apiKeySource"] = LLMClient::GetApiKeySource();
            result["apiKeyFingerprint"] = LLMClient::GetApiKeyFingerprint();
            return result.dump();
        }

        if (action == "hide") {
            MCPChatUI::Hide();
            return json{{"visible", false}}.dump();
        }

        if (action == "reload") {
            // Same gate as RunChatTurn — Init() rewrites g_config string
            // fields and concurrent HTTP read in LLMClient::Chat is UB.
            bool expected = false;
            if (!g_processing.compare_exchange_strong(expected, true)) {
                throw std::runtime_error("Chat is busy - another turn is in progress.");
            }
            struct R { ~R(){ g_processing = false; } } r;
            LLMClient::Init();
            MCPChatUI::SetStatus("");
            const auto& cfg = LLMClient::GetConfig();
            json result;
            result["configured"] = LLMClient::IsConfigured();
            result["model"] = cfg.model;
            result["baseUrl"] = cfg.baseUrl;
            result["toolProfile"] = cfg.toolProfile;
            result["promptMode"] = cfg.promptMode;
            result["includeSceneSnapshot"] = cfg.includeSceneSnapshot;
            result["apiKeySource"] = LLMClient::GetApiKeySource();
            result["apiKeyFingerprint"] = LLMClient::GetApiKeyFingerprint();
            return result.dump();
        }

        if (action == "clear") {
            // Gate so we can't wipe the vector between an in-flight turn's
            // snapshot and its later push of assistant/tool messages —
            // would leave orphan rows the API rejects.
            bool expected = false;
            if (!g_processing.compare_exchange_strong(expected, true)) {
                throw std::runtime_error("Chat is busy - another turn is in progress.");
            }
            struct R { ~R(){ g_processing = false; } } r;
            {
                std::lock_guard<std::mutex> lock(g_convMutex);
                g_conversation.clear();
            }
            MCPChatUI::ClearHistory();
            return json{{"cleared", true}}.dump();
        }

        // status (default)
        json result;
        result["visible"] = MCPChatUI::IsVisible();
        result["configured"] = LLMClient::IsConfigured();
        result["model"] = LLMClient::GetConfig().model;
        result["baseUrl"] = LLMClient::GetConfig().baseUrl;
        result["toolProfile"] = LLMClient::GetConfig().toolProfile;
        result["promptMode"] = LLMClient::GetConfig().promptMode;
        result["includeSceneSnapshot"] = LLMClient::GetConfig().includeSceneSnapshot;
        result["processing"] = g_processing.load();
        result["conversationLength"] = ConversationLength();
        result["apiKeySource"] = LLMClient::GetApiKeySource();
        result["apiKeyFingerprint"] = LLMClient::GetApiKeyFingerprint();
        return result.dump();
    });
}
