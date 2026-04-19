#include "mcp_bridge/llm_client.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/bridge_gup.h"
#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/command_dispatcher.h"

#include <windows.h>
#include <winhttp.h>
#include <shlobj.h>
#include <fstream>
#include <sstream>
#include <mutex>

#pragma comment(lib, "winhttp.lib")

using json = nlohmann::json;
using namespace HandlerHelpers;

// ── Config state ────────────────────────────────────────────────
static LLMClient::Config g_config;

// Cached SKILL.md content — read once per Init() and reused across turns.
static std::string g_skillPromptCache;
static bool g_skillPromptLoaded = false;
static std::mutex g_skillMutex;

// ── Paths under %LOCALAPPDATA%\3dsmax-mcp\ ──────────────────────
static std::string LocalAppDataDir() {
    char buf[MAX_PATH];
    if (FAILED(SHGetFolderPathA(nullptr, CSIDL_LOCAL_APPDATA, nullptr, 0, buf))) {
        return {};
    }
    return std::string(buf) + "\\3dsmax-mcp";
}

static std::string ConfigIniPath() {
    auto dir = LocalAppDataDir();
    return dir.empty() ? "" : (dir + "\\mcp_config.ini");
}

static std::string SkillMdPath() {
    auto dir = LocalAppDataDir();
    return dir.empty() ? "" : (dir + "\\skill\\SKILL.md");
}

static std::string DotEnvPath() {
    auto dir = LocalAppDataDir();
    return dir.empty() ? "" : (dir + "\\.env");
}

// ── INI read helper ─────────────────────────────────────────────
static std::string ReadIni(const std::string& ini, const char* section,
                           const char* key, const char* defaultVal) {
    char buf[1024] = {};
    GetPrivateProfileStringA(section, key, defaultVal, buf, sizeof(buf), ini.c_str());
    return std::string(buf);
}

// Read from the Win32 process env (not the CRT cache). SetEnvironmentVariableA
// — which LoadDotEnv() uses — updates this table; _dupenv_s/getenv do NOT see
// those writes because the CRT snapshots the env at process start.
static std::string ReadEnv(const char* name) {
    char buf[4096];
    DWORD n = GetEnvironmentVariableA(name, buf, sizeof(buf));
    if (n == 0 || n >= sizeof(buf)) return {};
    return std::string(buf, n);
}

// ── .env loader ─────────────────────────────────────────────────
// Minimal dotenv parser: `KEY=value` per line, `#` comments, `export KEY=value`
// accepted, surrounding single/double quotes stripped. Sets process-level env
// vars so the existing ReadEnv fallback picks them up. Does NOT overwrite vars
// already set in the real environment — real env wins (lets CI/one-off shells
// override committed .env safely).
static void LoadDotEnv() {
    std::string path = DotEnvPath();
    if (path.empty()) return;

    std::ifstream f(path);
    if (!f.is_open()) return;

    std::string line;
    while (std::getline(f, line)) {
        // Trim leading whitespace
        size_t start = line.find_first_not_of(" \t\r\n");
        if (start == std::string::npos) continue;
        line.erase(0, start);

        // Skip comments and blank lines
        if (line.empty() || line[0] == '#') continue;

        // Strip optional `export ` prefix
        if (line.rfind("export ", 0) == 0) line.erase(0, 7);

        // Split on first '='
        size_t eq = line.find('=');
        if (eq == std::string::npos) continue;

        std::string key = line.substr(0, eq);
        std::string val = line.substr(eq + 1);

        // Trim key whitespace
        while (!key.empty() && (key.back() == ' ' || key.back() == '\t')) key.pop_back();
        if (key.empty()) continue;

        // Trim value whitespace + optional surrounding quotes + trailing CR
        while (!val.empty() && (val.back() == ' ' || val.back() == '\t' ||
                                val.back() == '\r' || val.back() == '\n')) {
            val.pop_back();
        }
        while (!val.empty() && (val.front() == ' ' || val.front() == '\t')) val.erase(0, 1);
        if (val.size() >= 2 &&
            ((val.front() == '"'  && val.back() == '"') ||
             (val.front() == '\'' && val.back() == '\''))) {
            val = val.substr(1, val.size() - 2);
        }

        // Don't overwrite vars already present in the real environment
        if (!ReadEnv(key.c_str()).empty()) continue;

        SetEnvironmentVariableA(key.c_str(), val.c_str());
    }
}

// ── Init: .env → INI → env vars ─────────────────────────────────
void LLMClient::Init() {
    // Load .env first so the env-var fallback below can pick up keys placed
    // there. Real env vars (set via setx / shell) still win — LoadDotEnv skips
    // names already defined.
    LoadDotEnv();

    std::string ini = ConfigIniPath();

    g_config.apiKey     = ReadIni(ini, "llm", "api_key",     "");
    g_config.baseUrl    = ReadIni(ini, "llm", "base_url",    "https://openrouter.ai/api/v1");
    g_config.model      = ReadIni(ini, "llm", "model",       "anthropic/claude-sonnet-4.6");
    g_config.maxTokens  = std::stoi(ReadIni(ini, "llm", "max_tokens",  "4096"));

    std::string temp    = ReadIni(ini, "llm", "temperature", "0.7");
    try { g_config.temperature = std::stof(temp); } catch (...) { g_config.temperature = 0.7f; }

    // Env-var fallback for api_key (populated by .env, setx, or shell)
    if (g_config.apiKey.empty()) g_config.apiKey = ReadEnv("OPENROUTER_API_KEY");
    if (g_config.apiKey.empty()) g_config.apiKey = ReadEnv("LLM_API_KEY");
    if (g_config.apiKey.empty()) g_config.apiKey = ReadEnv("OPENAI_API_KEY");

    // Invalidate skill cache so /reload re-reads SKILL.md too
    {
        std::lock_guard<std::mutex> lock(g_skillMutex);
        g_skillPromptCache.clear();
        g_skillPromptLoaded = false;
    }
}

const LLMClient::Config& LLMClient::GetConfig() { return g_config; }

bool LLMClient::IsConfigured() {
    return !g_config.apiKey.empty() && !g_config.baseUrl.empty();
}

// ── WinHTTP POST ────────────────────────────────────────────────

static std::string HttpPost(
    const std::string& url,
    const std::string& body,
    const std::string& apiKey) {

    std::wstring wurl = Utf8ToWide(url);

    URL_COMPONENTS urlComp = {};
    urlComp.dwStructSize = sizeof(urlComp);
    wchar_t hostBuf[256] = {};
    wchar_t pathBuf[1024] = {};
    urlComp.lpszHostName = hostBuf;
    urlComp.dwHostNameLength = 256;
    urlComp.lpszUrlPath = pathBuf;
    urlComp.dwUrlPathLength = 1024;

    if (!WinHttpCrackUrl(wurl.c_str(), 0, 0, &urlComp)) {
        return "{\"error\":\"Failed to parse URL: " + url + "\"}";
    }

    bool isHttps = (urlComp.nScheme == INTERNET_SCHEME_HTTPS);
    INTERNET_PORT port = urlComp.nPort;

    HINTERNET hSession = WinHttpOpen(
        L"3dsmax-mcp/0.6.0",
        WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
        WINHTTP_NO_PROXY_NAME,
        WINHTTP_NO_PROXY_BYPASS,
        0);
    if (!hSession) return "{\"error\":\"WinHttpOpen failed\"}";

    HINTERNET hConnect = WinHttpConnect(hSession, hostBuf, port, 0);
    if (!hConnect) {
        WinHttpCloseHandle(hSession);
        return "{\"error\":\"WinHttpConnect failed\"}";
    }

    DWORD flags = isHttps ? WINHTTP_FLAG_SECURE : 0;
    HINTERNET hRequest = WinHttpOpenRequest(
        hConnect, L"POST", pathBuf,
        nullptr, WINHTTP_NO_REFERER,
        WINHTTP_DEFAULT_ACCEPT_TYPES,
        flags);
    if (!hRequest) {
        WinHttpCloseHandle(hConnect);
        WinHttpCloseHandle(hSession);
        return "{\"error\":\"WinHttpOpenRequest failed\"}";
    }

    std::wstring headers = L"Content-Type: application/json\r\n";
    headers += L"Authorization: Bearer " + Utf8ToWide(apiKey) + L"\r\n";
    WinHttpAddRequestHeaders(hRequest, headers.c_str(), -1, WINHTTP_ADDREQ_FLAG_ADD);

    BOOL sent = WinHttpSendRequest(
        hRequest,
        WINHTTP_NO_ADDITIONAL_HEADERS, 0,
        (LPVOID)body.c_str(), (DWORD)body.size(),
        (DWORD)body.size(),
        0);

    if (!sent) {
        DWORD err = GetLastError();
        WinHttpCloseHandle(hRequest);
        WinHttpCloseHandle(hConnect);
        WinHttpCloseHandle(hSession);
        return "{\"error\":\"WinHttpSendRequest failed: " + std::to_string(err) + "\"}";
    }

    if (!WinHttpReceiveResponse(hRequest, nullptr)) {
        WinHttpCloseHandle(hRequest);
        WinHttpCloseHandle(hConnect);
        WinHttpCloseHandle(hSession);
        return "{\"error\":\"WinHttpReceiveResponse failed\"}";
    }

    std::string response;
    DWORD bytesAvailable = 0;
    while (WinHttpQueryDataAvailable(hRequest, &bytesAvailable) && bytesAvailable > 0) {
        std::vector<char> buf(bytesAvailable);
        DWORD bytesRead = 0;
        WinHttpReadData(hRequest, buf.data(), bytesAvailable, &bytesRead);
        response.append(buf.data(), bytesRead);
    }

    WinHttpCloseHandle(hRequest);
    WinHttpCloseHandle(hConnect);
    WinHttpCloseHandle(hSession);

    return response;
}

// ── Chat completion ─────────────────────────────────────────────

LLMClient::Response LLMClient::Chat(
    const std::vector<Message>& messages,
    const json& tools) {

    Response resp;

    if (!IsConfigured()) {
        resp.error = "LLM not configured. Put OPENROUTER_API_KEY in %LOCALAPPDATA%\\3dsmax-mcp\\.env or set it as an env var, then /reload.";
        return resp;
    }

    json body;
    body["model"] = g_config.model;
    body["max_tokens"] = g_config.maxTokens;
    body["temperature"] = g_config.temperature;

    json msgs = json::array();
    for (auto& m : messages) {
        json msg;
        msg["role"] = m.role;

        if (m.role == "tool") {
            msg["content"] = m.content;
            msg["tool_call_id"] = m.toolCallId;
        } else if (m.role == "assistant" && !m.toolCalls.is_null() && !m.toolCalls.empty()) {
            msg["content"] = m.content.empty() ? nullptr : json(m.content);
            msg["tool_calls"] = m.toolCalls;
        } else {
            msg["content"] = m.content;
        }
        msgs.push_back(msg);
    }
    body["messages"] = msgs;

    if (!tools.empty()) {
        body["tools"] = tools;
    }

    std::string url = g_config.baseUrl + "/chat/completions";
    std::string reqBody = body.dump();

    std::string rawResp = HttpPost(url, reqBody, g_config.apiKey);

    try {
        json r = json::parse(rawResp);

        if (r.contains("error")) {
            resp.error = r["error"].value("message", r["error"].dump());
            return resp;
        }

        if (!r.contains("choices") || r["choices"].empty()) {
            resp.error = "No choices in response";
            return resp;
        }

        auto& choice = r["choices"][0];
        auto& msg = choice["message"];

        resp.finishReason = choice.value("finish_reason", "stop");
        resp.ok = true;

        if (msg.contains("content") && !msg["content"].is_null()) {
            resp.text = msg["content"].get<std::string>();
        }

        if (msg.contains("tool_calls") && !msg["tool_calls"].is_null()) {
            for (auto& tc : msg["tool_calls"]) {
                ToolCall call;
                call.id = tc.value("id", "");
                call.name = tc["function"].value("name", "");
                std::string args = tc["function"].value("arguments", "{}");
                try { call.arguments = json::parse(args); }
                catch (...) { call.arguments = json::object(); }
                resp.toolCalls.push_back(call);
            }
        }

    } catch (const std::exception& e) {
        resp.error = std::string("JSON parse error: ") + e.what() + " | Raw: " + rawResp.substr(0, 200);
    }

    return resp;
}

// ── Tool registry (generated) ───────────────────────────────────
// chat_tool_registry.inc is generated by scripts/gen_tool_registry.py from
// src/tools/*.py. It defines:
//   struct ChatTool { const char* name; const char* cmdType;
//                     const char* description; const char* schemaJson; };
//   static const ChatTool kChatTools[] = { ... };
//   static const size_t kChatToolCount = ...;

struct ChatTool {
    const char* name;
    const char* cmdType;
    const char* description;
    const char* schemaJson;
};

#if __has_include("generated/chat_tool_registry.inc")
#include "generated/chat_tool_registry.inc"
#else
// Fallback registry when the generator hasn't run. Covers the common tools
// so chat still works in a dev build before Python is wired up.
static const ChatTool kChatTools[] = {
    {"get_scene_info",        "native:scene_info",        "Get scene object list with optional filters", "{\"type\":\"object\",\"properties\":{\"class_name\":{\"type\":\"string\"},\"pattern\":{\"type\":\"string\"},\"layer\":{\"type\":\"string\"}}}"},
    {"get_scene_snapshot",    "native:scene_snapshot",    "Compact scene overview", "{\"type\":\"object\",\"properties\":{}}"},
    {"get_selection",         "native:selection",         "Get currently selected objects", "{\"type\":\"object\",\"properties\":{}}"},
    {"get_selection_snapshot","native:selection_snapshot","Detailed info for selected objects", "{\"type\":\"object\",\"properties\":{}}"},
    {"get_hierarchy",         "native:get_hierarchy",     "Scene hierarchy tree", "{\"type\":\"object\",\"properties\":{}}"},
    {"get_object_properties", "native:get_object_properties", "Object properties by name", "{\"type\":\"object\",\"properties\":{\"name\":{\"type\":\"string\"}},\"required\":[\"name\"]}"},
    {"set_object_property",   "native:set_object_property", "Set an object property", "{\"type\":\"object\",\"properties\":{\"name\":{\"type\":\"string\"},\"property\":{\"type\":\"string\"},\"value\":{}},\"required\":[\"name\",\"property\"]}"},
    {"create_object",         "native:create_object",     "Create a scene object", "{\"type\":\"object\",\"properties\":{\"type\":{\"type\":\"string\"},\"name\":{\"type\":\"string\"},\"params\":{\"type\":\"string\"}},\"required\":[\"type\"]}"},
    {"delete_objects",        "native:delete_objects",    "Delete objects by name", "{\"type\":\"object\",\"properties\":{\"names\":{\"type\":\"array\",\"items\":{\"type\":\"string\"}}},\"required\":[\"names\"]}"},
    {"transform_object",      "native:transform_object",  "Move/rotate/scale an object", "{\"type\":\"object\",\"properties\":{\"name\":{\"type\":\"string\"},\"position\":{\"type\":\"array\",\"items\":{\"type\":\"number\"}},\"rotation\":{\"type\":\"array\",\"items\":{\"type\":\"number\"}},\"scale\":{\"type\":\"array\",\"items\":{\"type\":\"number\"}}},\"required\":[\"name\"]}"},
    {"select_objects",        "native:select_objects",    "Select objects by name/pattern/class", "{\"type\":\"object\",\"properties\":{\"names\":{\"type\":\"array\",\"items\":{\"type\":\"string\"}},\"pattern\":{\"type\":\"string\"},\"class_name\":{\"type\":\"string\"},\"all\":{\"type\":\"boolean\"}}}"},
    {"set_visibility",        "native:set_visibility",    "Hide/show/freeze/unfreeze", "{\"type\":\"object\",\"properties\":{\"names\":{\"type\":\"array\",\"items\":{\"type\":\"string\"}},\"action\":{\"type\":\"string\"}},\"required\":[\"names\",\"action\"]}"},
    {"clone_objects",         "native:clone_objects",     "Clone objects", "{\"type\":\"object\",\"properties\":{\"names\":{\"type\":\"array\",\"items\":{\"type\":\"string\"}},\"mode\":{\"type\":\"string\"},\"count\":{\"type\":\"integer\"}},\"required\":[\"names\"]}"},
    {"add_modifier",          "native:add_modifier",      "Add a modifier to an object", "{\"type\":\"object\",\"properties\":{\"name\":{\"type\":\"string\"},\"modifier\":{\"type\":\"string\"},\"params\":{\"type\":\"string\"}},\"required\":[\"name\",\"modifier\"]}"},
    {"inspect_object",        "native:inspect_object",    "Deep inspection of one object", "{\"type\":\"object\",\"properties\":{\"name\":{\"type\":\"string\"}},\"required\":[\"name\"]}"},
    {"inspect_properties",    "native:inspect_properties","Live param values for an object", "{\"type\":\"object\",\"properties\":{\"name\":{\"type\":\"string\"}},\"required\":[\"name\"]}"},
    {"get_materials",         "native:get_materials",     "List all materials in the scene", "{\"type\":\"object\",\"properties\":{}}"},
    {"assign_material",       "native:assign_material",   "Create + assign a material", "{\"type\":\"object\",\"properties\":{\"names\":{\"type\":\"array\",\"items\":{\"type\":\"string\"}},\"material_class\":{\"type\":\"string\"},\"material_name\":{\"type\":\"string\"},\"properties\":{\"type\":\"object\"}},\"required\":[\"names\",\"material_class\"]}"},
    {"set_material_property", "native:set_material_property", "Set a material property", "{\"type\":\"object\",\"properties\":{\"material\":{\"type\":\"string\"},\"property\":{\"type\":\"string\"},\"value\":{}},\"required\":[\"material\",\"property\"]}"},
    {"manage_layers",         "native:manage_layers",     "Layer list/create/delete/set", "{\"type\":\"object\",\"properties\":{\"action\":{\"type\":\"string\"},\"name\":{\"type\":\"string\"},\"names\":{\"type\":\"array\",\"items\":{\"type\":\"string\"}},\"layer\":{\"type\":\"string\"}},\"required\":[\"action\"]}"},
    {"manage_groups",         "native:manage_groups",     "Group operations", "{\"type\":\"object\",\"properties\":{\"action\":{\"type\":\"string\"},\"names\":{\"type\":\"array\",\"items\":{\"type\":\"string\"}}},\"required\":[\"action\"]}"},
    {"set_parent",            "native:set_parent",        "Reparent objects", "{\"type\":\"object\",\"properties\":{\"names\":{\"type\":\"array\",\"items\":{\"type\":\"string\"}},\"parent\":{\"type\":\"string\"}},\"required\":[\"names\"]}"},
    {"batch_rename_objects",  "native:batch_rename_objects","Rename objects by pattern", "{\"type\":\"object\",\"properties\":{\"names\":{\"type\":\"array\",\"items\":{\"type\":\"string\"}},\"prefix\":{\"type\":\"string\"},\"suffix\":{\"type\":\"string\"},\"replace\":{\"type\":\"object\"}}}"},
    {"capture_viewport",      "native:capture_viewport",  "Viewport screenshot to disk", "{\"type\":\"object\",\"properties\":{}}"},
    {"introspect_class",      "native:introspect_class",  "SDK-level class introspection", "{\"type\":\"object\",\"properties\":{\"class_name\":{\"type\":\"string\"}},\"required\":[\"class_name\"]}"},
    {"introspect_instance",   "native:introspect_instance","Live param tree for an instance", "{\"type\":\"object\",\"properties\":{\"name\":{\"type\":\"string\"},\"include_subanims\":{\"type\":\"boolean\"}},\"required\":[\"name\"]}"},
    {"list_plugin_classes",   "native:list_plugin_classes","Enumerate installed plugin classes", "{\"type\":\"object\",\"properties\":{\"super_class\":{\"type\":\"string\"}}}"},
    {"learn_scene_patterns",  "native:learn_scene_patterns","Analyze scene for class usage patterns", "{\"type\":\"object\",\"properties\":{}}"},
    {"walk_references",       "native:walk_references",   "Walk the reference graph from a node", "{\"type\":\"object\",\"properties\":{\"name\":{\"type\":\"string\"},\"depth\":{\"type\":\"integer\"}},\"required\":[\"name\"]}"},
    {"execute_maxscript",     "maxscript",                "Run arbitrary MAXScript (subject to safe_mode filter)", "{\"type\":\"object\",\"properties\":{\"code\":{\"type\":\"string\"}},\"required\":[\"code\"]}"},
};
static const size_t kChatToolCount = sizeof(kChatTools) / sizeof(kChatTools[0]);
#endif

// ── OpenAI-format tool definitions ──────────────────────────────

json LLMClient::GetToolDefinitions() {
    json tools = json::array();
    for (size_t i = 0; i < kChatToolCount; ++i) {
        const auto& t = kChatTools[i];
        json schema = json::parse(t.schemaJson, nullptr, false);
        if (schema.is_discarded()) schema = json{{"type","object"},{"properties",json::object()}};
        tools.push_back({
            {"type", "function"},
            {"function", {
                {"name", t.name},
                {"description", t.description},
                {"parameters", schema}
            }}
        });
    }
    return tools;
}

// ── Tool execution via CommandDispatcher ────────────────────────
// Routing through Dispatch inherits:
//   - safe_mode filter (blocks dangerous MAXScript in execute_maxscript)
//   - main-thread marshaling for mutation handlers
//   - consistent error wrapping
std::string LLMClient::ExecuteTool(
    const std::string& toolName,
    const json& input,
    MCPBridgeGUP* gup) {

    // Find tool in registry
    const ChatTool* tool = nullptr;
    for (size_t i = 0; i < kChatToolCount; ++i) {
        if (toolName == kChatTools[i].name) { tool = &kChatTools[i]; break; }
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
    req["requestId"] = std::string("chat-") + toolName;

    std::string raw = CommandDispatcher::Dispatch(req.dump(), gup, "chat-session");

    // Dispatch returns JSON with success/result/error — unwrap for the LLM
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

// ── System prompt: runtime preamble + SKILL.md + scene snapshot ─

static std::string LoadSkillMdOnce() {
    std::lock_guard<std::mutex> lock(g_skillMutex);
    if (g_skillPromptLoaded) return g_skillPromptCache;

    g_skillPromptLoaded = true;
    std::string path = SkillMdPath();
    if (path.empty()) return {};

    std::ifstream f(path, std::ios::binary);
    if (!f.is_open()) return {};

    std::stringstream ss;
    ss << f.rdbuf();
    g_skillPromptCache = ss.str();
    return g_skillPromptCache;
}

std::string LLMClient::BuildSystemPrompt(MCPBridgeGUP* gup) {
    std::string prompt =
        "You are an AI assistant embedded inside Autodesk 3ds Max, running "
        "via the native C++ bridge. You have direct access to scene-read/write "
        "tools and can call execute_maxscript for anything not covered by a "
        "dedicated tool. Be concise. Use tools instead of explaining them. "
        "When asked to do something, do it — don't narrate the plan.\n\n";

    std::string skill = LoadSkillMdOnce();
    if (!skill.empty()) {
        prompt += "=== SKILL REFERENCE (3ds Max + MCP patterns) ===\n";
        prompt += skill;
        prompt += "\n=== END SKILL ===\n\n";
    }

    try {
        std::string snapshot = NativeHandlers::SceneSnapshot("", gup);
        prompt += "=== CURRENT SCENE ===\n";
        prompt += snapshot;
        prompt += "\n=== END SCENE ===\n";
    } catch (...) {
        prompt += "(Could not read scene snapshot.)\n";
    }

    return prompt;
}
