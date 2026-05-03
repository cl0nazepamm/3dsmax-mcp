#include "mcp_bridge/command_dispatcher.h"
#include "mcp_bridge/bridge_gup.h"
#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/main_thread_executor.h"
#include <nlohmann/json.hpp>
#include <chrono>
#include <unordered_set>
#include <shlobj.h>

#include <max.h>
#include <maxapi.h>
#include <maxscript/maxscript.h>
#include <maxscript/foundation/strings.h>
#include <CoreFunctions.h>

using json = nlohmann::json;

// ── Safe mode config ───────────────────────────────────────────
// Read from %LOCALAPPDATA%\3dsmax-mcp\mcp_config.ini
// [mcp] safe_mode = true|false  (default: true)
static bool g_safeMode = true;
static bool g_safeModeLoaded = false;

static bool LoadSafeModeSetting() {
    char localAppData[MAX_PATH];
    if (FAILED(SHGetFolderPathA(nullptr, CSIDL_LOCAL_APPDATA, nullptr, 0, localAppData)))
        return true;  // default: safe

    std::string iniPath = std::string(localAppData) + "\\3dsmax-mcp\\mcp_config.ini";

    char buf[16] = {};
    GetPrivateProfileStringA("mcp", "safe_mode", "true", buf, 16, iniPath.c_str());

    std::string val(buf);
    return !(val == "false" || val == "0" || val == "off");
}

static bool IsSafeModeEnabled() {
    if (!g_safeModeLoaded) {
        g_safeMode = LoadSafeModeSetting();
        g_safeModeLoaded = true;
    }
    return g_safeMode;
}

// ── Thread mode classification ──────────────────────────────────
// Read-only handlers run directly on the pipe worker thread,
// skipping the main-thread PostMessage roundtrip. ~25 handlers
// benefit: scene reads, plugin introspection, reference walking.
// Mutation handlers (create/delete/transform/assign) and MAXScript
// execution always marshal to the main thread.
static bool IsDirectHandler(const std::string& cmd_type) {
    static const std::unordered_set<std::string> kDirect = {
        // Scene reads
        "native:scene_info",
        "native:selection",
        "native:scene_snapshot",
        "native:selection_snapshot",
        "native:find_class_instances",
        "native:get_hierarchy",
        "native:scene_delta",
        // Object reads
        "native:get_object_properties",
        "native:analyze_node_orientation",
        "native:inspect_object",
        "native:inspect_properties",
        // Material reads
        "native:get_materials",
        "native:get_material_slots",
        // Scene query
        "native:find_objects_by_property",
        "native:get_instances",
        "native:get_dependencies",
        // Plugin introspection (pure DllDir/ClassDesc reads)
        "native:list_plugin_classes",
        "native:discover_classes",
        "native:introspect_class",
        "native:introspect_instance",
        // Controller reads
        "native:inspect_track_view",
        "native:list_wireable_params",
        // Deep SDK learning (read-only scene analysis)
        "native:walk_references",
        "native:map_class_relationships",
        "native:learn_scene_patterns",
        "native:watch_scene",
        // Effects (read-only)
        "native:get_effects",
        // Wire params (read-only)
        "native:get_wired_params",
        // State sets (read-only)
        "native:get_state_sets",
        "native:get_camera_sequence",
        // System discovery (read-only)
        "native:list_macroscripts",
        "native:list_action_tables",
    };
    return kDirect.count(cmd_type) > 0;
}

// RAII guard — enables direct mode on construction, disables on destruction
struct DirectModeGuard {
    bool active;
    DirectModeGuard(bool enable) : active(enable) {
        if (active) MainThreadExecutor::EnableDirectMode();
    }
    ~DirectModeGuard() {
        if (active) MainThreadExecutor::DisableDirectMode();
    }
};

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
        {"safeMode", IsSafeModeEnabled()},
        {"durationMs", duration_ms},
        {"transport", "namedpipe"},
        {"threadMode", MainThreadExecutor::IsDirectMode() ? "direct" : "mainThread"}
    };
    return resp.dump();
}

// ── Safe mode filter ────────────────────────────────────────────
static bool ContainsBlockedCommand(const std::string& cmd) {
    // Case-insensitive check for dangerous MAXScript commands
    std::string lower = cmd;
    for (auto& c : lower) c = (char)tolower((unsigned char)c);

    static const char* blocked[] = {
        "doscommand",
        "shelllaunch",
        "deletefile",
        "python.execute",
        "createfile",
        "hiddendoscommand",
    };
    for (const char* b : blocked) {
        if (lower.find(b) != std::string::npos) return true;
    }
    return false;
}

// ── MAXScript handler ───────────────────────────────────────────
static std::string HandleMaxScript(
    const std::string& command,
    MCPBridgeGUP* gup) {

    if (IsSafeModeEnabled() && ContainsBlockedCommand(command)) {
        throw std::runtime_error("Blocked by safe mode: command contains a restricted function. Set safe_mode=false in %LOCALAPPDATA%\\3dsmax-mcp\\mcp_config.ini to disable.");
    }

    return gup->GetExecutor().ExecuteSync([&command]() -> std::string {
        std::wstring wcmd = Utf8ToWide(command);

        FPValue fpv;
        BOOL ok = FALSE;

        try {
            ok = ExecuteMAXScriptScript(
                wcmd.c_str(),
                MAXScript::ScriptSource::NonEmbedded,
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
        if (fpv.type == TYPE_BOOL) {
            return fpv.i ? "true" : "false";
        }

        // Do not re-evaluate the command just to stringify the result.
        // That doubled work on non-trivial MAXScript paths.
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
    const std::string& json_request,
    MCPBridgeGUP* gup,
    const std::string& client_session_id) {

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

    // Route to handler — read-only handlers run directly on pipe thread
    // _forceMainThread flag allows benchmarking the same handler both ways
    bool forceMain = req.value("_forceMainThread", false);
    bool direct = !forceMain && IsDirectHandler(cmd_type);
    DirectModeGuard dmg(direct);

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
        } else if (cmd_type == "native:scene_delta") {
            result = NativeHandlers::SceneDelta(command, gup, client_session_id);
        // Phase 1: Object operations
        } else if (cmd_type == "native:get_object_properties") {
            result = NativeHandlers::GetObjectProperties(command, gup);
        } else if (cmd_type == "native:analyze_node_orientation") {
            result = NativeHandlers::AnalyzeNodeOrientation(command, gup);
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
        } else if (cmd_type == "native:create_shell_material") {
            result = NativeHandlers::CreateShellMaterial(command, gup);
        // Plugin enumeration
        } else if (cmd_type == "native:list_plugin_classes") {
            result = NativeHandlers::ListPluginClasses(command, gup);
        // Controller / track inspection
        } else if (cmd_type == "native:inspect_track_view") {
            result = NativeHandlers::InspectTrackView(command, gup);
        } else if (cmd_type == "native:list_wireable_params") {
            result = NativeHandlers::ListWireableParams(command, gup);
        // Plugin introspection (deep SDK reflection)
        } else if (cmd_type == "native:discover_classes") {
            result = NativeHandlers::DiscoverClasses(command, gup);
        } else if (cmd_type == "native:introspect_class") {
            result = NativeHandlers::IntrospectClass(command, gup);
        } else if (cmd_type == "native:introspect_instance") {
            result = NativeHandlers::IntrospectInstance(command, gup);
        // Scene organization
        } else if (cmd_type == "native:manage_layers") {
            result = NativeHandlers::ManageLayers(command, gup);
        } else if (cmd_type == "native:manage_groups") {
            result = NativeHandlers::ManageGroups(command, gup);
        } else if (cmd_type == "native:manage_selection_sets") {
            result = NativeHandlers::ManageSelectionSets(command, gup);
        // Deep SDK learning
        } else if (cmd_type == "native:walk_references") {
            result = NativeHandlers::WalkReferences(command, gup);
        } else if (cmd_type == "native:map_class_relationships") {
            result = NativeHandlers::MapClassRelationships(command, gup);
        } else if (cmd_type == "native:learn_scene_patterns") {
            result = NativeHandlers::LearnScenePatterns(command, gup);
        } else if (cmd_type == "native:watch_scene") {
            result = NativeHandlers::WatchScene(command, gup);
        // Effects
        } else if (cmd_type == "native:get_effects") {
            result = NativeHandlers::GetEffects(command, gup);
        } else if (cmd_type == "native:toggle_effect") {
            result = NativeHandlers::ToggleEffect(command, gup);
        } else if (cmd_type == "native:delete_effect") {
            result = NativeHandlers::DeleteEffect(command, gup);
        // Render
        } else if (cmd_type == "native:render_scene") {
            result = NativeHandlers::RenderScene(command, gup);
        // Material replace
        } else if (cmd_type == "native:replace_material") {
            result = NativeHandlers::ReplaceMaterial(command, gup);
        } else if (cmd_type == "native:batch_replace_materials") {
            result = NativeHandlers::BatchReplaceMaterials(command, gup);
        // Texture map ops
        } else if (cmd_type == "native:create_texture_map") {
            result = NativeHandlers::CreateTextureMap(command, gup);
        } else if (cmd_type == "native:set_texture_map_properties") {
            result = NativeHandlers::SetTextureMapProperties(command, gup);
        } else if (cmd_type == "native:set_sub_material") {
            result = NativeHandlers::SetSubMaterial(command, gup);
        // Controllers (extended)
        } else if (cmd_type == "native:assign_controller") {
            result = NativeHandlers::AssignController(command, gup);
        } else if (cmd_type == "native:inspect_controller") {
            result = NativeHandlers::InspectController(command, gup);
        } else if (cmd_type == "native:set_controller_props") {
            result = NativeHandlers::SetControllerProps(command, gup);
        } else if (cmd_type == "native:add_controller_target") {
            result = NativeHandlers::AddControllerTarget(command, gup);
        // Wire params
        } else if (cmd_type == "native:wire_params") {
            result = NativeHandlers::WireParams(command, gup);
        } else if (cmd_type == "native:get_wired_params") {
            result = NativeHandlers::GetWiredParams(command, gup);
        } else if (cmd_type == "native:unwire_params") {
            result = NativeHandlers::UnwireParams(command, gup);
        // State sets
        } else if (cmd_type == "native:get_state_sets") {
            result = NativeHandlers::GetStateSets(command, gup);
        } else if (cmd_type == "native:get_camera_sequence") {
            result = NativeHandlers::GetCameraSequence(command, gup);
        // System discovery
        } else if (cmd_type == "native:list_macroscripts") {
            result = NativeHandlers::ListMacroscripts(command, gup);
        } else if (cmd_type == "native:list_action_tables") {
            result = NativeHandlers::ListActionTables(command, gup);
        } else if (cmd_type == "native:introspect_interface") {
            result = NativeHandlers::IntrospectInterface(command, gup);
        } else if (cmd_type == "native:invoke_interface") {
            result = NativeHandlers::InvokeInterface(command, gup);
        } else if (cmd_type == "native:run_macroscript") {
            result = NativeHandlers::RunMacroscript(command, gup);
        // Chat UI (v0.7.0)
        } else if (cmd_type == "native:chat_ui") {
            result = NativeHandlers::ChatUI(command, gup);
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
