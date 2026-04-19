#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/bridge_gup.h"
#include "mcp_bridge/chat_ui.h"
#include "mcp_bridge/llm_client.h"

#include <thread>
#include <mutex>

using json = nlohmann::json;
using namespace HandlerHelpers;

// ── Conversation state ──────────────────────────────────────────
static std::mutex g_convMutex;
static std::vector<LLMClient::Message> g_conversation;
static std::atomic<bool> g_processing{false};

// ── Process a user message in background ────────────────────────
void ProcessChatMessage(const std::string& text, MCPBridgeGUP* gup) {
    // Handle slash commands locally
    if (text == "/reload" || text == "/refresh") {
        LLMClient::Init();
        const auto& cfg = LLMClient::GetConfig();
        MCPChatUI::AppendMessage("ai", "Config reloaded. Model: " + cfg.model + " | Base: " + cfg.baseUrl);
        return;
    }
    if (text == "/clear") {
        std::lock_guard<std::mutex> lock(g_convMutex);
        g_conversation.clear();
        MCPChatUI::AppendMessage("ai", "Conversation cleared.");
        return;
    }
    if (text == "/help") {
        MCPChatUI::AppendMessage("ai",
            "/reload - Reload config (switch model without restart)\n"
            "/clear - Clear conversation history\n"
            "/help - Show this help");
        return;
    }

    if (g_processing.load()) return;
    g_processing = true;
    MCPChatUI::SetStatus("thinking...");

    std::thread([text, gup]() {
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

        // Get tool definitions
        json tools = LLMClient::GetToolDefinitions();

        // Call LLM — may loop for tool calls
        int maxLoops = 5;
        for (int loop = 0; loop < maxLoops; loop++) {
            MCPChatUI::SetStatus("calling API...");
            auto response = LLMClient::Chat(messages, tools);

            if (!response.ok) {
                MCPChatUI::AppendMessage("ai", "Error: " + response.error);
                break;
            }

            // Text response
            if (!response.text.empty()) {
                MCPChatUI::AppendMessage("ai", response.text);
                std::lock_guard<std::mutex> lock(g_convMutex);
                g_conversation.push_back({"assistant", response.text, "", nullptr});
            }

            // Tool calls
            if (!response.toolCalls.empty()) {
                // Build assistant message with tool_calls for conversation
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

                // Add to messages for next iteration
                messages.push_back({
                    "assistant",
                    response.text.empty() ? "" : response.text,
                    "",
                    toolCallsJson
                });

                // Execute each tool call
                for (auto& tc : response.toolCalls) {
                    MCPChatUI::SetStatus("running " + tc.name + "...");

                    std::string toolResult;
                    try {
                        toolResult = LLMClient::ExecuteTool(tc.name, tc.arguments, gup);
                    } catch (const std::exception& e) {
                        toolResult = std::string("{\"error\":\"") + e.what() + "\"}";
                    }

                    // Show truncated tool result in UI
                    if (toolResult.size() > 200) {
                        MCPChatUI::AppendMessage("tool", tc.name + " -> " + toolResult.substr(0, 180) + "...");
                    } else {
                        MCPChatUI::AppendMessage("tool", tc.name + " -> " + toolResult);
                    }

                    // Add tool result to conversation
                    {
                        std::lock_guard<std::mutex> lock(g_convMutex);
                        g_conversation.push_back({"tool", toolResult, tc.id, nullptr});
                    }
                    messages.push_back({"tool", toolResult, tc.id, nullptr});
                }

                // Continue loop to get LLM's response after tool results
                continue;
            }

            // No tool calls — we're done
            break;
        }

        MCPChatUI::SetStatus("");
        g_processing = false;
    }).detach();
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

// ═════════════════════════════════════════════════════════════════
// native:chat_ui handler
// ═════════════════════════════════════════════════════════════════

std::string NativeHandlers::ChatUI(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params, gup]() -> std::string {
        json p = params.empty() ? json::object() : json::parse(params, nullptr, false);
        std::string action = p.value("action", "status");

        if (action == "show") {
            // Wire up callbacks
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
                MCPChatUI::AppendMessage("ai",
                    "No API key configured. Edit %LOCALAPPDATA%\\3dsmax-mcp\\mcp_config.ini:\n\n"
                    "[llm]\napi_key = sk-or-...\nbase_url = https://openrouter.ai/api/v1\nmodel = anthropic/claude-sonnet-4.6\n\n"
                    "Then type /reload to pick up the changes.");
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

        // status
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
