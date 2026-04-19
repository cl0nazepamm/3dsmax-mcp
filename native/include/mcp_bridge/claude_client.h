#pragma once
#include <string>
#include <vector>
#include <nlohmann/json.hpp>

class MCPBridgeGUP;

// Provider-agnostic LLM client using OpenAI-compatible chat completions API.
// Works with: OpenAI, Minimax, Groq, Together, Mistral, Ollama, LM Studio, etc.
namespace LLMClient {

using json = nlohmann::json;

struct Config {
    std::string apiKey;
    std::string baseUrl;    // e.g. "https://api.minimaxi.chat" or "https://api.openai.com"
    std::string model;      // e.g. "MiniMax-M1-80k" or "gpt-4o"
    std::string configPath; // where the config file lives
    int maxTokens = 4096;
    float temperature = 0.7f;
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

// Load config from JSON file next to the .gup
void Init(const std::string& pluginDir);

// Get/set config
Config& GetConfig();
void SaveConfig();

// Check if ready
bool IsConfigured();

// Send chat completion request (blocking — call from background thread)
Response Chat(const std::vector<Message>& messages, const json& tools = json::array());

// Execute a tool call using native handlers (on main thread via ExecuteSync)
std::string ExecuteTool(const std::string& toolName, const json& input, MCPBridgeGUP* gup);

// Build scene context for system prompt
std::string BuildSceneContext(MCPBridgeGUP* gup);

// Get tool definitions in OpenAI format
json GetToolDefinitions();

} // namespace LLMClient
