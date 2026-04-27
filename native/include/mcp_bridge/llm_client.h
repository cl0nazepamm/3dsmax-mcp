#pragma once
#include <string>
#include <vector>
#include <nlohmann/json.hpp>

class MCPBridgeGUP;

// Provider-agnostic LLM client using OpenAI-compatible chat completions API.
// Default target: OpenRouter. Also works with OpenAI, Groq, Together, Mistral,
// Minimax, Ollama, LM Studio, etc.
//
// Config is read from %LOCALAPPDATA%\3dsmax-mcp\mcp_config.ini [llm] section,
// the same file that gates [mcp] safe_mode. Env-var fallback for api_key:
// OPENROUTER_API_KEY → LLM_API_KEY → OPENAI_API_KEY.
namespace LLMClient {

using json = nlohmann::json;

struct Config {
    std::string apiKey;
    std::string baseUrl;    // e.g. "https://openrouter.ai/api/v1"
    std::string model;      // e.g. "anthropic/claude-sonnet-4.6"
    int maxTokens = 4096;
    float temperature = 0.7f;
    std::string promptMode = "compact"; // compact, full, none
    std::string toolProfile = "core";   // core, full
    bool includeSceneSnapshot = true;
    int maxSceneRoots = 25;
    int maxPromptChars = 12000;
    int maxToolResultChars = 12000;
    int maxHistoryToolChars = 1800;
    int maxToolSummaryChars = 600;
    int maxDisplayToolChars = 600;
    int maxToolLoops = 4;
};

struct Message {
    std::string role;       // "user", "assistant", "system", "tool"
    std::string content;
    std::string toolCallId; // for tool results
    json toolCalls;         // for assistant tool_use
};

struct ToolCall {
    std::string id;
    std::string name;
    json arguments;
};

struct Response {
    std::string text;
    std::vector<ToolCall> toolCalls;
    std::string finishReason; // "stop", "tool_calls", "length"
    bool ok = false;
    std::string error;
};

// Load config from %LOCALAPPDATA%\3dsmax-mcp\mcp_config.ini [llm] section
// plus env-var fallback. Called at plugin Start() and by /reload slash command.
void Init();

// Read-only accessor (post-Init).
const Config& GetConfig();
std::string GetApiKeySource();
std::string GetApiKeyFingerprint();

// True when api_key + base_url are set.
bool IsConfigured();

// Send chat completion request (blocking — call from background thread).
Response Chat(
    const std::vector<Message>& messages,
    const json& tools = json::array(),
    int timeoutMs = 180000);

// Execute a tool call by routing through CommandDispatcher::Dispatch —
// inherits safe_mode filter (for execute_maxscript) and main-thread
// marshaling. Returns the handler's result JSON string, or an error JSON.
std::string ExecuteTool(const std::string& toolName, const json& input, MCPBridgeGUP* gup);

// Build system prompt: runtime preamble + cached SKILL.md + current scene snapshot.
// SKILL.md is loaded from %LOCALAPPDATA%\3dsmax-mcp\skill\SKILL.md (deployed by
// native/deploy.bat) and cached across turns; scene snapshot is re-read each turn.
std::string BuildSystemPrompt(MCPBridgeGUP* gup);

// OpenAI-format tool definitions — auto-generated from src/tools/*.py by
// scripts/gen_tool_registry.py into native/generated/chat_tool_registry.inc.
json GetToolDefinitions();

} // namespace LLMClient
