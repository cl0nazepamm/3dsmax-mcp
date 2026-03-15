#include "mcp_bridge/command_dispatcher.h"
#include "mcp_bridge/bridge_gup.h"
#include "mcp_bridge/native_handlers.h"
#include <nlohmann/json.hpp>
#include <chrono>

#include <max.h>
#include <maxapi.h>
#include <maxscript/maxscript.h>
#include <maxscript/foundation/strings.h>
#include <CoreFunctions.h>

using json = nlohmann::json;

// ── UTF-8 <-> Wide helpers ──────────────────────────────────────
static std::wstring Utf8ToWide(const std::string& s) {
    if (s.empty()) return {};
    int len = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), nullptr, 0);
    std::wstring w(len, 0);
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), &w[0], len);
    return w;
}

static std::string WideToUtf8(const wchar_t* w) {
    if (!w || !*w) return {};
    int len = WideCharToMultiByte(CP_UTF8, 0, w, -1, nullptr, 0, nullptr, nullptr);
    std::string s(len - 1, 0);
    WideCharToMultiByte(CP_UTF8, 0, w, -1, &s[0], len, nullptr, nullptr);
    return s;
}

// ── Build JSON response ─────────────────────────────────────────
static std::string BuildResponse(
    bool success,
    const std::string& result,
    const std::string& error,
    const std::string& request_id,
    const std::string& cmd_type,
    int duration_ms) {

    json resp;
    resp["success"] = success;
    resp["requestId"] = request_id;
    resp["result"] = result;
    resp["error"] = error;
    resp["meta"] = {
        {"protocolVersion", 2},
        {"cmdType", cmd_type},
        {"safeMode", false},
        {"durationMs", duration_ms},
        {"transport", "namedpipe"}
    };
    return resp.dump();
}

// ── MAXScript handler ───────────────────────────────────────────
static std::string HandleMaxScript(
    const std::string& command,
    MCPBridgeGUP* gup) {

    return gup->GetExecutor().ExecuteSync([&command]() -> std::string {
        std::wstring wcmd = Utf8ToWide(command);

        FPValue fpv;
        BOOL ok = FALSE;

        try {
            ok = ExecuteMAXScriptScript(
                wcmd.c_str(),
                MAXScript::ScriptSource::NotSpecified,
                FALSE,   // quietErrors
                &fpv,    // result goes here
                TRUE     // logQuietErrors
            );
        } catch (...) {
            throw std::runtime_error("MAXScript execution exception");
        }

        if (!ok) {
            throw std::runtime_error("MAXScript execution failed");
        }

        // Convert FPValue to string
        if (fpv.type == TYPE_STRING || fpv.type == TYPE_FILENAME) {
            return WideToUtf8(fpv.s);
        }
        if (fpv.type == TYPE_TSTR) {
            return WideToUtf8(fpv.tstr->data());
        }
        if (fpv.type == TYPE_VALUE && fpv.v != nullptr) {
            try {
                const MCHAR* str = fpv.v->to_string();
                return WideToUtf8(str);
            } catch (...) {}
        }
        if (fpv.type == TYPE_INT) {
            return std::to_string(fpv.i);
        }
        if (fpv.type == TYPE_FLOAT) {
            return std::to_string(fpv.f);
        }

        // Fallback: execute again and capture as string via MAXScript's own stringification
        // For complex results, wrap in a string conversion
        std::wstring wrap = L"(" + wcmd + L") as string";
        FPValue fpv2;
        if (ExecuteMAXScriptScript(wrap.c_str(), MAXScript::ScriptSource::NotSpecified, TRUE, &fpv2)) {
            if (fpv2.type == TYPE_STRING || fpv2.type == TYPE_FILENAME) {
                return WideToUtf8(fpv2.s);
            }
            if (fpv2.type == TYPE_TSTR) {
                return WideToUtf8(fpv2.tstr->data());
            }
        }

        return "OK";
    });
}

// ── Ping handler ────────────────────────────────────────────────
static std::string HandlePing(MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([]() -> std::string {
        Interface* ip = GetCOREInterface();
        json result;
        result["pong"] = true;
        result["server"] = "3dsmax-mcp-native";
        result["protocolVersion"] = 2;
        result["transport"] = "namedpipe";
        DWORD v = Get3DSMAXVersion();
        result["maxVersion"] = 1998 + (HIWORD(v) / 1000);
        return result.dump();
    });
}

// ── Dispatcher ──────────────────────────────────────────────────
std::string CommandDispatcher::Dispatch(
    const std::string& json_request, MCPBridgeGUP* gup) {

    auto start = std::chrono::steady_clock::now();

    // Parse request
    json req;
    try {
        req = json::parse(json_request);
    } catch (...) {
        return BuildResponse(false, "", "JSON parse error", "", "unknown", 0);
    }

    std::string command = req.value("command", "");
    std::string cmd_type = req.value("type", "maxscript");
    std::string request_id = req.value("requestId", "");

    // Route to handler
    try {
        std::string result;

        if (cmd_type == "ping") {
            result = HandlePing(gup);
        } else if (cmd_type == "maxscript") {
            if (command.empty()) {
                throw std::runtime_error("Empty MAXScript command");
            }
            result = HandleMaxScript(command, gup);
        // Native handlers
        } else if (cmd_type == "native:scene_info") {
            result = NativeHandlers::SceneInfo(command, gup);
        } else if (cmd_type == "native:selection") {
            result = NativeHandlers::Selection(command, gup);
        } else if (cmd_type == "native:scene_snapshot") {
            result = NativeHandlers::SceneSnapshot(command, gup);
        } else if (cmd_type == "native:selection_snapshot") {
            result = NativeHandlers::SelectionSnapshot(command, gup);
        } else if (cmd_type == "native:find_class_instances") {
            result = NativeHandlers::FindClassInstances(command, gup);
        } else if (cmd_type == "native:get_hierarchy") {
            result = NativeHandlers::GetHierarchy(command, gup);
        // Phase 1: Object operations
        } else if (cmd_type == "native:get_object_properties") {
            result = NativeHandlers::GetObjectProperties(command, gup);
        } else if (cmd_type == "native:set_object_property") {
            result = NativeHandlers::SetObjectProperty(command, gup);
        } else if (cmd_type == "native:create_object") {
            result = NativeHandlers::CreateObject(command, gup);
        } else if (cmd_type == "native:delete_objects") {
            result = NativeHandlers::DeleteObjects(command, gup);
        } else if (cmd_type == "native:transform_object") {
            result = NativeHandlers::TransformObject(command, gup);
        } else if (cmd_type == "native:select_objects") {
            result = NativeHandlers::SelectObjects(command, gup);
        } else if (cmd_type == "native:set_visibility") {
            result = NativeHandlers::SetVisibility(command, gup);
        } else if (cmd_type == "native:clone_objects") {
            result = NativeHandlers::CloneObjects(command, gup);
        // Phase 2: Modifier operations
        } else if (cmd_type == "native:add_modifier") {
            result = NativeHandlers::AddModifier(command, gup);
        } else if (cmd_type == "native:remove_modifier") {
            result = NativeHandlers::RemoveModifier(command, gup);
        } else if (cmd_type == "native:set_modifier_state") {
            result = NativeHandlers::SetModifierState(command, gup);
        } else if (cmd_type == "native:collapse_modifier_stack") {
            result = NativeHandlers::CollapseModifierStack(command, gup);
        } else if (cmd_type == "native:make_modifier_unique") {
            result = NativeHandlers::MakeModifierUnique(command, gup);
        } else if (cmd_type == "native:batch_modify") {
            result = NativeHandlers::BatchModify(command, gup);
        // Phase 3: Inspect & scene query
        } else if (cmd_type == "native:inspect_object") {
            result = NativeHandlers::InspectObject(command, gup);
        } else if (cmd_type == "native:inspect_properties") {
            result = NativeHandlers::InspectProperties(command, gup);
        } else if (cmd_type == "native:get_materials") {
            result = NativeHandlers::GetMaterials(command, gup);
        } else if (cmd_type == "native:find_objects_by_property") {
            result = NativeHandlers::FindObjectsByProperty(command, gup);
        } else if (cmd_type == "native:get_instances") {
            result = NativeHandlers::GetInstances(command, gup);
        } else if (cmd_type == "native:get_dependencies") {
            result = NativeHandlers::GetDependencies(command, gup);
        } else if (cmd_type == "native:get_material_slots") {
            result = NativeHandlers::GetMaterialSlots(command, gup);
        } else if (cmd_type == "native:write_osl_shader") {
            result = NativeHandlers::WriteOSLShader(command, gup);
        // Phase 4: Scene management
        } else if (cmd_type == "native:set_parent") {
            result = NativeHandlers::SetParent(command, gup);
        } else if (cmd_type == "native:batch_rename_objects") {
            result = NativeHandlers::BatchRenameObjects(command, gup);
        } else if (cmd_type == "native:manage_scene") {
            result = NativeHandlers::ManageScene(command, gup);
        // File access
        } else if (cmd_type == "native:inspect_max_file") {
            result = NativeHandlers::InspectMaxFile(command, gup);
        } else if (cmd_type == "native:merge_from_file") {
            result = NativeHandlers::MergeFromFile(command, gup);
        } else if (cmd_type == "native:batch_file_info") {
            result = NativeHandlers::BatchFileInfo(command, gup);
        // Viewport capture
        } else if (cmd_type == "native:capture_multi_view") {
            result = NativeHandlers::CaptureMultiView(command, gup);
        } else if (cmd_type == "native:capture_viewport") {
            result = NativeHandlers::CaptureViewport(command, gup);
        } else if (cmd_type == "native:capture_screen") {
            result = NativeHandlers::CaptureScreen(command, gup);
        // Phase 6: Material writes
        } else if (cmd_type == "native:assign_material") {
            result = NativeHandlers::AssignMaterial(command, gup);
        } else if (cmd_type == "native:set_material_property") {
            result = NativeHandlers::SetMaterialProperty(command, gup);
        } else if (cmd_type == "native:set_material_properties") {
            result = NativeHandlers::SetMaterialProperties(command, gup);
        } else {
            throw std::runtime_error("Unknown command type: " + cmd_type);
        }

        auto end = std::chrono::steady_clock::now();
        int ms = (int)std::chrono::duration_cast<std::chrono::milliseconds>(end - start).count();
        return BuildResponse(true, result, "", request_id, cmd_type, ms);

    } catch (const std::exception& e) {
        auto end = std::chrono::steady_clock::now();
        int ms = (int)std::chrono::duration_cast<std::chrono::milliseconds>(end - start).count();
        return BuildResponse(false, "", e.what(), request_id, cmd_type, ms);
    }
}
