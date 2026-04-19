#include "mcp_bridge/claude_client.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/bridge_gup.h"
#include "mcp_bridge/native_handlers.h"

#include <windows.h>
#include <winhttp.h>
#include <fstream>
#include <sstream>

#pragma comment(lib, "winhttp.lib")

using json = nlohmann::json;
using namespace HandlerHelpers;

static LLMClient::Config g_config;

// ── Config file I/O ─────────────────────────────────────────────

void LLMClient::Init(const std::string& pluginDir) {
    g_config.configPath = pluginDir + "\\mcp_llm_config.json";

    // Try loading config file
    std::ifstream f(g_config.configPath);
    if (f.is_open()) {
        try {
            json cfg = json::parse(f);
            g_config.apiKey = cfg.value("apiKey", "");
            g_config.baseUrl = cfg.value("baseUrl", "https://api.minimaxi.chat");
            g_config.model = cfg.value("model", "MiniMax-M1-80k");
            g_config.maxTokens = cfg.value("maxTokens", 4096);
            g_config.temperature = cfg.value("temperature", 0.7f);
        } catch (...) {}
        f.close();
    }

    // Fallback: check environment variables
    if (g_config.apiKey.empty()) {
        char* envKey = nullptr;
        size_t len = 0;
        if (_dupenv_s(&envKey, &len, "LLM_API_KEY") == 0 && envKey) {
            g_config.apiKey = envKey;
            free(envKey);
        }
    }
    if (g_config.apiKey.empty()) {
        char* envKey = nullptr;
        size_t len = 0;
        if (_dupenv_s(&envKey, &len, "MINIMAX_API_KEY") == 0 && envKey) {
            g_config.apiKey = envKey;
            free(envKey);
        }
    }
    if (g_config.apiKey.empty()) {
        char* envKey = nullptr;
        size_t len = 0;
        if (_dupenv_s(&envKey, &len, "OPENAI_API_KEY") == 0 && envKey) {
            g_config.apiKey = envKey;
            free(envKey);
        }
    }
}

LLMClient::Config& LLMClient::GetConfig() { return g_config; }

void LLMClient::SaveConfig() {
    json cfg;
    cfg["apiKey"] = g_config.apiKey;
    cfg["baseUrl"] = g_config.baseUrl;
    cfg["model"] = g_config.model;
    cfg["maxTokens"] = g_config.maxTokens;
    cfg["temperature"] = g_config.temperature;

    std::ofstream f(g_config.configPath);
    if (f.is_open()) {
        f << cfg.dump(2);
        f.close();
    }
}

bool LLMClient::IsConfigured() {
    return !g_config.apiKey.empty() && !g_config.baseUrl.empty();
}

// ── WinHTTP POST ────────────────────────────────────────────────

static std::string HttpPost(
    const std::string& url,
    const std::string& body,
    const std::string& apiKey) {

    // Parse URL
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

    // Open session
    HINTERNET hSession = WinHttpOpen(
        L"3dsmax-mcp/1.0",
        WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
        WINHTTP_NO_PROXY_NAME,
        WINHTTP_NO_PROXY_BYPASS,
        0);
    if (!hSession) return "{\"error\":\"WinHttpOpen failed\"}";

    // Connect
    HINTERNET hConnect = WinHttpConnect(hSession, hostBuf, port, 0);
    if (!hConnect) {
        WinHttpCloseHandle(hSession);
        return "{\"error\":\"WinHttpConnect failed\"}";
    }

    // Request
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

    // Headers
    std::wstring headers = L"Content-Type: application/json\r\n";
    headers += L"Authorization: Bearer " + Utf8ToWide(apiKey) + L"\r\n";

    WinHttpAddRequestHeaders(hRequest, headers.c_str(), -1, WINHTTP_ADDREQ_FLAG_ADD);

    // Send
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

    // Receive
    if (!WinHttpReceiveResponse(hRequest, nullptr)) {
        WinHttpCloseHandle(hRequest);
        WinHttpCloseHandle(hConnect);
        WinHttpCloseHandle(hSession);
        return "{\"error\":\"WinHttpReceiveResponse failed\"}";
    }

    // Read response body
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
        resp.error = "LLM not configured. Set API key in mcp_llm_config.json or LLM_API_KEY env var.";
        return resp;
    }

    // Build request body (OpenAI-compatible format)
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

    std::string url = g_config.baseUrl + "/v1/chat/completions";
    std::string reqBody = body.dump();

    // Make the HTTP call
    std::string rawResp = HttpPost(url, reqBody, g_config.apiKey);

    // Parse response
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

        // Extract text content
        if (msg.contains("content") && !msg["content"].is_null()) {
            resp.text = msg["content"].get<std::string>();
        }

        // Extract tool calls
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

// ── Tool execution ──────────────────────────────────────────────

std::string LLMClient::ExecuteTool(
    const std::string& toolName,
    const json& input,
    MCPBridgeGUP* gup) {

    std::string params = input.dump();

    // Map tool names to native handlers
    try {
        if (toolName == "get_scene_info") return NativeHandlers::SceneInfo(params, gup);
        if (toolName == "get_selection") return NativeHandlers::Selection(params, gup);
        if (toolName == "get_scene_snapshot") return NativeHandlers::SceneSnapshot(params, gup);
        if (toolName == "get_selection_snapshot") return NativeHandlers::SelectionSnapshot(params, gup);
        if (toolName == "get_hierarchy") return NativeHandlers::GetHierarchy(params, gup);
        if (toolName == "get_object_properties") return NativeHandlers::GetObjectProperties(params, gup);
        if (toolName == "set_object_property") return NativeHandlers::SetObjectProperty(params, gup);
        if (toolName == "create_object") return NativeHandlers::CreateObject(params, gup);
        if (toolName == "delete_objects") return NativeHandlers::DeleteObjects(params, gup);
        if (toolName == "transform_object") return NativeHandlers::TransformObject(params, gup);
        if (toolName == "select_objects") return NativeHandlers::SelectObjects(params, gup);
        if (toolName == "set_visibility") return NativeHandlers::SetVisibility(params, gup);
        if (toolName == "clone_objects") return NativeHandlers::CloneObjects(params, gup);
        if (toolName == "add_modifier") return NativeHandlers::AddModifier(params, gup);
        if (toolName == "remove_modifier") return NativeHandlers::RemoveModifier(params, gup);
        if (toolName == "set_modifier_state") return NativeHandlers::SetModifierState(params, gup);
        if (toolName == "inspect_object") return NativeHandlers::InspectObject(params, gup);
        if (toolName == "inspect_properties") return NativeHandlers::InspectProperties(params, gup);
        if (toolName == "get_materials") return NativeHandlers::GetMaterials(params, gup);
        if (toolName == "assign_material") return NativeHandlers::AssignMaterial(params, gup);
        if (toolName == "set_material_property") return NativeHandlers::SetMaterialProperty(params, gup);
        if (toolName == "get_material_slots") return NativeHandlers::GetMaterialSlots(params, gup);
        if (toolName == "manage_layers") return NativeHandlers::ManageLayers(params, gup);
        if (toolName == "manage_groups") return NativeHandlers::ManageGroups(params, gup);
        if (toolName == "manage_selection_sets") return NativeHandlers::ManageSelectionSets(params, gup);
        if (toolName == "set_parent") return NativeHandlers::SetParent(params, gup);
        if (toolName == "batch_rename_objects") return NativeHandlers::BatchRenameObjects(params, gup);
        if (toolName == "capture_viewport") return NativeHandlers::CaptureViewport(params, gup);
        if (toolName == "introspect_class") return NativeHandlers::IntrospectClass(params, gup);
        if (toolName == "introspect_instance") return NativeHandlers::IntrospectInstance(params, gup);
        if (toolName == "discover_classes") return NativeHandlers::DiscoverClasses(params, gup);
        if (toolName == "learn_scene_patterns") return NativeHandlers::LearnScenePatterns(params, gup);
        if (toolName == "walk_references") return NativeHandlers::WalkReferences(params, gup);
        if (toolName == "execute_maxscript") {
            // Route through HandleMaxScript path (with safe mode)
            return RunMAXScript(input.value("code", ""));
        }
    } catch (const std::exception& e) {
        return std::string("{\"error\":\"") + e.what() + "\"}";
    }

    return "{\"error\":\"Unknown tool: " + toolName + "\"}";
}

// ── Scene context ───────────────────────────────────────────────

std::string LLMClient::BuildSceneContext(MCPBridgeGUP* gup) {
    try {
        std::string snapshot = NativeHandlers::SceneSnapshot("", gup);
        return "Current 3ds Max scene state:\n" + snapshot;
    } catch (...) {
        return "Could not read scene state.";
    }
}

// ── Tool definitions (OpenAI format) ────────────────────────────

json LLMClient::GetToolDefinitions() {
    json tools = json::array();

    auto addTool = [&](const char* name, const char* desc, json params) {
        tools.push_back({
            {"type", "function"},
            {"function", {
                {"name", name},
                {"description", desc},
                {"parameters", params}
            }}
        });
    };

    addTool("get_scene_info", "Get scene object list with optional filters",
        {{"type", "object"}, {"properties", {
            {"class_name", {{"type", "string"}, {"description", "Filter by class"}}},
            {"pattern", {{"type", "string"}, {"description", "Wildcard name filter"}}},
            {"layer", {{"type", "string"}, {"description", "Filter by layer"}}}
        }}});

    addTool("get_scene_snapshot", "Compact scene overview: class counts, materials, modifiers, layers",
        {{"type", "object"}, {"properties", {}}});

    addTool("get_selection", "Get currently selected objects",
        {{"type", "object"}, {"properties", {}}});

    addTool("get_selection_snapshot", "Detailed info for selected objects",
        {{"type", "object"}, {"properties", {}}});

    addTool("inspect_object", "Deep inspection of one object",
        {{"type", "object"}, {"properties", {
            {"name", {{"type", "string"}, {"description", "Object name"}}}
        }}, {"required", json::array({"name"})}});

    addTool("introspect_instance", "SDK-level introspection with live param values",
        {{"type", "object"}, {"properties", {
            {"name", {{"type", "string"}, {"description", "Object name"}}},
            {"include_subanims", {{"type", "boolean"}, {"description", "Include SubAnim tree"}}}
        }}, {"required", json::array({"name"})}});

    addTool("create_object", "Create a scene object",
        {{"type", "object"}, {"properties", {
            {"type", {{"type", "string"}, {"description", "Object type (Box, Sphere, etc.)"}}},
            {"name", {{"type", "string"}, {"description", "Object name"}}},
            {"params", {{"type", "string"}, {"description", "MAXScript params (width:10 height:20)"}}}
        }}, {"required", json::array({"type"})}});

    addTool("delete_objects", "Delete objects by name",
        {{"type", "object"}, {"properties", {
            {"names", {{"type", "array"}, {"items", {{"type", "string"}}}, {"description", "Object names to delete"}}}
        }}, {"required", json::array({"names"})}});

    addTool("transform_object", "Move, rotate, or scale an object",
        {{"type", "object"}, {"properties", {
            {"name", {{"type", "string"}, {"description", "Object name"}}},
            {"position", {{"type", "array"}, {"items", {{"type", "number"}}}, {"description", "[x,y,z]"}}},
            {"rotation", {{"type", "array"}, {"items", {{"type", "number"}}}, {"description", "[x,y,z] degrees"}}},
            {"scale", {{"type", "array"}, {"items", {{"type", "number"}}}, {"description", "[x,y,z]"}}}
        }}, {"required", json::array({"name"})}});

    addTool("select_objects", "Select objects by name, pattern, or class",
        {{"type", "object"}, {"properties", {
            {"names", {{"type", "array"}, {"items", {{"type", "string"}}}}},
            {"pattern", {{"type", "string"}}},
            {"class_name", {{"type", "string"}}},
            {"all", {{"type", "boolean"}}}
        }}});

    addTool("set_visibility", "Hide/show/freeze/unfreeze objects",
        {{"type", "object"}, {"properties", {
            {"names", {{"type", "array"}, {"items", {{"type", "string"}}}}},
            {"action", {{"type", "string"}, {"description", "hide/show/toggle/freeze/unfreeze"}}}
        }}, {"required", json::array({"names", "action"})}});

    addTool("add_modifier", "Add a modifier to an object",
        {{"type", "object"}, {"properties", {
            {"name", {{"type", "string"}, {"description", "Object name"}}},
            {"modifier", {{"type", "string"}, {"description", "Modifier class (TurboSmooth, Chamfer, etc.)"}}},
            {"params", {{"type", "string"}, {"description", "MAXScript params"}}}
        }}, {"required", json::array({"name", "modifier"})}});

    addTool("assign_material", "Create and assign a material",
        {{"type", "object"}, {"properties", {
            {"names", {{"type", "array"}, {"items", {{"type", "string"}}}, {"description", "Object names"}}},
            {"material_class", {{"type", "string"}, {"description", "Material class"}}},
            {"material_name", {{"type", "string"}}},
            {"properties", {{"type", "object"}}}
        }}, {"required", json::array({"names", "material_class"})}});

    addTool("get_materials", "List all materials in the scene",
        {{"type", "object"}, {"properties", {}}});

    addTool("manage_layers", "Manage scene layers",
        {{"type", "object"}, {"properties", {
            {"action", {{"type", "string"}, {"description", "list/create/delete/set_properties/add_objects"}}},
            {"name", {{"type", "string"}}},
            {"names", {{"type", "array"}, {"items", {{"type", "string"}}}}},
            {"layer", {{"type", "string"}}}
        }}, {"required", json::array({"action"})}});

    addTool("learn_scene_patterns", "Analyze scene for class usage patterns",
        {{"type", "object"}, {"properties", {}}});

    addTool("execute_maxscript", "Run arbitrary MAXScript code",
        {{"type", "object"}, {"properties", {
            {"code", {{"type", "string"}, {"description", "MAXScript code to execute"}}}
        }}, {"required", json::array({"code"})}});

    addTool("capture_viewport", "Capture viewport screenshot",
        {{"type", "object"}, {"properties", {}}});

    return tools;
}
