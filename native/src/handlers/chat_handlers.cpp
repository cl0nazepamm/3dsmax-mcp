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

// Completion callback for `send` action: receives final assistant text,
// a JSON array of tool calls made during the turn, and an error string
// (empty on success). Called once per ProcessChatMessage invocation.
using ChatCompleteCallback =
    std::function<void(const std::string& replyText, const json& toolCalls, const std::string& error)>;

// ── Process a user message in background ────────────────────────
void ProcessChatMessage(const std::string& text,
                        MCPBridgeGUP* gup,
                        ChatCompleteCallback onComplete) {
    // Slash commands — handled inline, no LLM call
    if (text == "/reload" || text == "/refresh") {
        LLMClient::Init();
        const auto& cfg = LLMClient::GetConfig();
        MCPChatUI::AppendMessage("ai", "Config reloaded. Model: " + cfg.model + " | Base: " + cfg.baseUrl);
        if (onComplete) onComplete("", json::array(), "");
        return;
    }
    if (text == "/clear") {
        std::lock_guard<std::mutex> lock(g_convMutex);
        g_conversation.clear();
        MCPChatUI::AppendMessage("ai", "Conversation cleared.");
        if (onComplete) onComplete("", json::array(), "");
        return;
    }
    if (text == "/help") {
        MCPChatUI::AppendMessage("system",
            "Slash commands:\n"
            "  /reload  — re-read .env and mcp_config.ini (model/key change without restart)\n"
            "  /clear   — drop conversation history (Ctrl+L)\n"
            "  /help    — this message\n\n"
            "Keyboard:\n"
            "  Enter        — send\n"
            "  Shift+Enter  — newline\n"
            "  Ctrl+Enter   — newline\n"
            "  Ctrl+L       — /clear\n"
            "  Ctrl+R       — /reload");
        if (onComplete) onComplete("", json::array(), "");
        return;
    }

    if (g_processing.load()) {
        if (onComplete) onComplete("", json::array(), "Chat is busy — another turn is in progress.");
        return;
    }
    g_processing = true;
    MCPChatUI::SetStatus("thinking...");

    std::thread([text, gup, onComplete]() {
        std::string finalReply;
        json toolCallsSummary = json::array();
        std::string err;

        try {
            // Add user message
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

            json tools = LLMClient::GetToolDefinitions();

            int maxLoops = 5;
            for (int loop = 0; loop < maxLoops; loop++) {
                MCPChatUI::SetStatus("calling API...");
                auto response = LLMClient::Chat(messages, tools);

                if (!response.ok) {
                    MCPChatUI::AppendMessage("error", response.error);
                    err = response.error;
                    break;
                }

                if (!response.text.empty()) {
                    MCPChatUI::AppendMessage("ai", response.text);
                    std::lock_guard<std::mutex> lock(g_convMutex);
                    g_conversation.push_back({"assistant", response.text, "", nullptr});
                    finalReply = response.text;  // last assistant text wins
                }

                if (!response.toolCalls.empty()) {
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

                    {
                        std::lock_guard<std::mutex> lock(g_convMutex);
                        g_conversation.push_back({
                            "assistant",
                            response.text.empty() ? "" : response.text,
                            "",
                            toolCallsJson
                        });
                    }
                    messages.push_back({
                        "assistant",
                        response.text.empty() ? "" : response.text,
                        "",
                        toolCallsJson
                    });

                    for (auto& tc : response.toolCalls) {
                        MCPChatUI::SetStatus("running " + tc.name + "...");

                        std::string toolResult;
                        try {
                            toolResult = LLMClient::ExecuteTool(tc.name, tc.arguments, gup);
                        } catch (const std::exception& e) {
                            toolResult = std::string("{\"error\":\"") + e.what() + "\"}";
                        }

                        const size_t DISPLAY_CAP = 600;
                        if (toolResult.size() > DISPLAY_CAP) {
                            MCPChatUI::AppendMessage("tool",
                                tc.name + "  " + toolResult.substr(0, DISPLAY_CAP) +
                                "  …(+" + std::to_string(toolResult.size() - DISPLAY_CAP) + " chars)");
                        } else {
                            MCPChatUI::AppendMessage("tool", tc.name + "  " + toolResult);
                        }

                        // Summary for caller (MCP send action)
                        std::string truncated = toolResult.size() > 1200
                            ? toolResult.substr(0, 1200) + "…"
                            : toolResult;
                        toolCallsSummary.push_back({
                            {"name", tc.name},
                            {"arguments", tc.arguments},
                            {"result", truncated}
                        });

                        {
                            std::lock_guard<std::mutex> lock(g_convMutex);
                            g_conversation.push_back({"tool", toolResult, tc.id, nullptr});
                        }
                        messages.push_back({"tool", toolResult, tc.id, nullptr});
                    }
                    continue;
                }

                break;  // no more tool calls — turn complete
            }
        } catch (const std::exception& e) {
            err = e.what();
            MCPChatUI::AppendMessage("error", err);
        } catch (...) {
            err = "Unknown exception in chat loop";
            MCPChatUI::AppendMessage("error", err);
        }

        MCPChatUI::SetStatus("");
        g_processing = false;

        if (onComplete) onComplete(finalReply, toolCallsSummary, err);
    }).detach();
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
        return json{{"error", "empty message"}}.dump();
    }
    int timeout_ms = p.value("timeout_ms", 180000);
    bool silent    = p.value("silent", false);

    if (!silent) MCPChatUI::AppendMessage("user", message);

    struct Waiter {
        std::mutex m;
        std::condition_variable cv;
        bool done = false;
        std::string reply;
        std::string err;
        json toolCalls = json::array();
    };
    auto w = std::make_shared<Waiter>();

    ProcessChatMessage(message, gup,
        [w](const std::string& reply, const json& tools, const std::string& err) {
            std::lock_guard<std::mutex> lock(w->m);
            w->reply = reply;
            w->toolCalls = tools;
            w->err = err;
            w->done = true;
            w->cv.notify_all();
        });

    std::unique_lock<std::mutex> lock(w->m);
    bool finished = w->cv.wait_for(lock,
        std::chrono::milliseconds(timeout_ms),
        [&]{ return w->done; });

    json result;
    if (!finished) {
        result["error"] = "Timed out waiting for chat reply (timeout_ms=" +
                          std::to_string(timeout_ms) + ")";
        return result.dump();
    }
    if (!w->err.empty()) {
        result["error"] = w->err;
        return result.dump();
    }
    result["reply"] = w->reply;
    result["toolCalls"] = w->toolCalls;
    result["model"] = LLMClient::GetConfig().model;
    return result.dump();
}

// ═════════════════════════════════════════════════════════════════
// native:chat_ui handler
// ═════════════════════════════════════════════════════════════════

std::string NativeHandlers::ChatUI(const std::string& params, MCPBridgeGUP* gup) {
    json p = params.empty() ? json::object() : json::parse(params, nullptr, false);
    std::string action = p.value("action", "status");

    // `send` must NOT marshal to main thread — it spawns a worker and blocks
    // on a condvar. Running it on main would freeze Max's UI during the HTTP
    // call. Tools invoked by the LLM still marshal via CommandDispatcher.
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
            return result.dump();
        }

        if (action == "hide") {
            MCPChatUI::Hide();
            return json{{"visible", false}}.dump();
        }

        if (action == "reload") {
            LLMClient::Init();
            const auto& cfg = LLMClient::GetConfig();
            json result;
            result["configured"] = LLMClient::IsConfigured();
            result["model"] = cfg.model;
            result["baseUrl"] = cfg.baseUrl;
            return result.dump();
        }

        if (action == "clear") {
            std::lock_guard<std::mutex> lock(g_convMutex);
            g_conversation.clear();
            return json{{"cleared", true}}.dump();
        }

        // status (default)
        json result;
        result["visible"] = MCPChatUI::IsVisible();
        result["configured"] = LLMClient::IsConfigured();
        result["model"] = LLMClient::GetConfig().model;
        result["baseUrl"] = LLMClient::GetConfig().baseUrl;
        result["processing"] = g_processing.load();
        result["conversationLength"] = g_conversation.size();
        return result.dump();
    });
}
