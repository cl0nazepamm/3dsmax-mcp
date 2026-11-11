#include "mcp_bridge/command_dispatcher.h"
#include "mcp_bridge/core_bridge.h"
#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/quiet_policy.h"
#include "mcp_bridge/response_envelope.h"
#include "mcp_bridge/script_hold.h"
#include "mcp_bridge/transaction_policy.h"
#include <nlohmann/json.hpp>
#include <algorithm>
#include <chrono>
#include <initializer_list>
#include <optional>
#include <unordered_set>
#include <shlobj.h>

#include <max.h>
#include <maxapi.h>
#include <hold.h>
#include <maxscript/maxscript.h>
#include <maxscript/foundation/strings.h>
#include <CoreFunctions.h>

using json = nlohmann::json;

// ── Safe mode config ───────────────────────────────────────────
// Read from %LOCALAPPDATA%\3dsmax-mcp\mcp_config.ini
// [mcp] safe_mode = true|false  (default: true)
static bool g_safeMode = true;
static bool g_safeModeLoaded = false;

static bool IsSafeModeEnabled() {
    if (!g_safeModeLoaded) {
        g_safeMode = McpEnvelope::ReadSafeModeSetting();
        g_safeModeLoaded = true;
    }
    return g_safeMode;
}

// ── Thread mode classification ──────────────────────────────────
// Read-only handlers run directly on the pipe worker thread,
// skipping the main-thread PostMessage roundtrip. ~25 handlers
// benefit: scene reads, plugin introspection, reference walking.
// Mutation handlers (create/delete/transform/assign) and MAXScript
// execution always marshal to the main thread, including reads that run
// any MAXScript (introspect_instance, get_wired_params, state sets).
// The MAXScript paths themselves (RunMAXScript, EvaluateMAXScript,
// HandleMaxScript, the PB1 modifier reads of inspect_properties) reach the
// main thread through McpHost::RunOnMainThread: script reached from a direct
// handler waits for the main thread instead of running on this one.
// ping and native:max_dialogs never reach the core: the shell answers them
// (core_control.cpp). watch_scene's reads run direct; the handler marshals its
// start and stop, which register with Max, to the main thread itself.
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
        "native:scene_qa_scan",
        // Object reads
        "native:get_object_properties",
        "native:analyze_node_orientation",
        "native:inspect_object",
        "native:inspect_properties",
        // Material reads
        "native:get_materials",
        "native:get_material_slots",
        "native:inspect_material_network",
        "native:replicate_material_preview",
        // Scene query
        "native:find_objects_by_property",
        "native:get_instances",
        "native:get_dependencies",
        // Plugin introspection (pure DllDir/ClassDesc reads)
        "native:list_plugin_classes",
        "native:discover_classes",
        "native:introspect_class",
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
        // System discovery (read-only)
        "native:list_macroscripts",
        "native:list_action_tables",
        // Reads only its own table, while the call it reports holds the main thread
        "native:job_progress",
    };
    return kDirect.count(cmd_type) > 0;
}

static bool IsMutatingNativeHandler(const std::string& cmd_type) {
    static const std::unordered_set<std::string> kMutating = {
        "native:set_object_property",
        "native:create_object",
        "native:delete_objects",
        "native:transform_object",
        "native:select_objects",
        "native:set_visibility",
        "native:clone_objects",
        "native:add_modifier",
        "native:remove_modifier",
        "native:set_modifier_state",
        "native:collapse_modifier_stack",
        "native:make_modifier_unique",
        "native:set_modifier_property",
        "native:batch_modify",
        "native:mcg_apply_modifier",
        "native:mcg_set_node_parameter",
        "native:replicate_material",
        "native:write_osl_shader",
        "native:set_parent",
        "native:batch_rename_objects",
        "native:manage_scene",
        "native:merge_from_file",
        "native:assign_material",
        "native:set_material_property",
        "native:set_material_properties",
        "native:create_shell_material",
        "native:manage_layers",
        "native:manage_groups",
        "native:manage_selection_sets",
        "native:toggle_effect",
        "native:delete_effect",
        "native:replace_material",
        "native:batch_replace_materials",
        "native:create_texture_map",
        "native:set_texture_map_properties",
        "native:set_sub_material",
        "native:replicate_material_apply",
        "native:assign_controller",
        "native:set_controller_props",
        "native:add_controller_target",
        "native:keyframe_tracks",
        "native:wire_params",
        "native:unwire_params",
        "native:invoke_interface",
        "native:run_macroscript",
        "native:scene_patch",
        "native:plugin_patch",
        "native:parameters",
        "native:scene_qa_fix",
        "native:mesh_script",
        "native:tyflow_operator",
    };
    return kMutating.count(cmd_type) > 0;
}

static bool RequestIsReadOnlyAction(const std::string& cmd_type, const std::string& command) {
    // These handlers expose reads and writes through the same native route.
    // Reads must remain available while the user owns an undo hold.
    const bool organization = cmd_type == "native:manage_layers" ||
        cmd_type == "native:manage_groups" || cmd_type == "native:manage_selection_sets";
    if (cmd_type == "native:parameters") {
        json payload = json::parse(command, nullptr, false);
        if (!payload.is_object()) return false;
        std::string action = payload.value("action", "describe");
        std::transform(action.begin(), action.end(), action.begin(), ::tolower);
        return action != "set";
    }
    if (cmd_type == "native:tyflow_operator") {
        json payload = json::parse(command, nullptr, false);
        if (!payload.is_object()) return false;
        const std::string function = payload.value("function", "");
        return function.size() > 3 && _strnicmp(function.c_str(), "get", 3) == 0;
    }
    if (!organization && cmd_type != "native:manage_scene" &&
        cmd_type != "native:keyframe_tracks") return false;
    json payload = json::parse(command, nullptr, false);
    if (!payload.is_object()) return false;
    std::string action = payload.value("action", "");
    if (organization) return action == "list";
    std::transform(action.begin(), action.end(), action.begin(), ::tolower);
    if (cmd_type == "native:manage_scene") return action == "info";
    if (action == "list" || action == "inspect" || action == "query" || action == "summary")
        return true;
    if (action == "timeline" || action == "time_config" || action == "timeline_config") {
        return !payload.contains("frame_rate") && !payload.contains("current_frame") &&
            !payload.contains("range_start") && !payload.contains("range_end");
    }
    return false;
}

static bool ResultLooksLikeError(const std::string& result) {
    json payload = json::parse(result, nullptr, false);
    if (payload.is_discarded() || !payload.is_object()) return false;
    if (payload.contains("error") && !payload["error"].is_null()) return true;
    std::string status = payload.value("status", "");
    std::transform(status.begin(), status.end(), status.begin(), ::tolower);
    return status == "error" || status == "failed";
}

// Shared with the shell's own answers (response_envelope.h).
static std::string NormalizeNativeError(const std::string& message) {
    return McpEnvelope::NormalizeError(message);
}

class NativeUndoTransaction {
public:
    explicit NativeUndoTransaction(const std::string& cmd_type, const std::string& label = "")
        : active_(false) {
        // Never append MCP writes to another operation's global undo hold.
        // Reject before invoke() so its restore records and scene stay untouched.
        if (theHold.Holding()) {
            throw std::runtime_error(json{
                {"type", "NativeError"},
                {"code", "USER_BUSY"},
                {"message", "3ds Max has an open undo operation. Finish or cancel that operation, then retry."},
                {"retryable", true}
            }.dump());
        }
        std::wstring text = HandlerHelpers::Utf8ToWide(label.empty() ? "MCP " + cmd_type : label);
        label_ = MSTR(text.c_str());
        theHold.Begin();
        active_ = true;
    }

    ~NativeUndoTransaction() {
        if (active_) {
            try { theHold.Cancel(); } catch (...) {}
        }
    }

    void Accept() {
        if (!active_) return;
        theHold.Accept(label_);
        active_ = false;
    }

    void Cancel() {
        if (!active_) return;
        theHold.Cancel();
        active_ = false;
    }

private:
    bool active_;
    MSTR label_;
};

// MAXScript that a scene-changing tool sends with an "undo" label (the
// tool's own script runs without a hold, so Max could not undo it, and a
// failed run_python script could not roll it back). Refused like a native
// write while another undo operation is open; a MAXScript error cancels it.
static std::string HandleMaxScript(const std::string& command, bool quiet);
static std::string HandleMaxScriptUndoable(const std::string& command, bool quiet, const std::string& label) {
    return McpHost::RunOnMainThread([&]() -> std::string {
        NativeUndoTransaction tx("maxscript", label);
        std::string result = HandleMaxScript(command, quiet);
        const std::string sentinel = HandlerHelpers::MaxScriptErrorSentinel();
        if (result.compare(0, sentinel.size(), sentinel) == 0) tx.Cancel();
        else tx.Accept();
        return result;
    });
}

// RAII guard — enables direct mode on construction and restores the previous
// mode on destruction, so a nested Dispatch never clears its caller's mode.
struct DirectModeGuard {
    bool active;
    bool previous;
    DirectModeGuard(bool enable) : active(enable), previous(HostExecutor::IsDirectMode()) {
        if (active) HostExecutor::EnableDirectMode();
    }
    ~DirectModeGuard() {
        if (!active) return;
        if (previous) HostExecutor::EnableDirectMode();
        else HostExecutor::DisableDirectMode();
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

// Set on the pipe thread while dispatching one request; BuildResponse reports
// it so callers can see that a script ran with Max's quiet mode off.
static thread_local std::string tl_quiet_override;

// ── Build JSON response ─────────────────────────────────────────
static std::string BuildResponse(
    bool success,
    const std::string& result,
    const std::string& error,
    const std::string& request_id,
    const std::string& cmd_type,
    int duration_ms) {
    // Same envelope as the shell's own answers (response_envelope.h).
    const bool safe_mode = IsSafeModeEnabled();
    const bool direct = HostExecutor::IsDirectMode();
    std::string response = McpEnvelope::Build(success, result, error, request_id, cmd_type, duration_ms,
                                              safe_mode, direct, McpHost::DialogWatch::OpenDialogs());
    if (tl_quiet_override.empty()) return response;
    // A script ran with Max's quiet mode off although the caller left quiet
    // unset. Rare, so the envelope is reparsed only then.
    json resp = json::parse(response);
    resp["meta"]["quietOverride"] = tl_quiet_override;
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

static void RejectBlockedScript(const std::string& script) {
    if (IsSafeModeEnabled() && ContainsBlockedCommand(script)) {
        throw std::runtime_error("Blocked by safe mode: command contains a restricted function. Set safe_mode=false in %LOCALAPPDATA%\\3dsmax-mcp\\mcp_config.ini to disable.");
    }
}

// Native payloads that carry MAXScript get the maxscript route's screen. The
// decoded strings are checked: JSON escapes can spell a blocked name.
static void RejectBlockedPayloadScripts(const std::string& command, std::initializer_list<const char*> keys) {
    if (!IsSafeModeEnabled()) return;
    json payload = json::parse(command, nullptr, false);
    if (!payload.is_object()) return;
    for (const char* key : keys) {
        auto it = payload.find(key);
        if (it != payload.end() && it->type() == json::value_t::string)
            RejectBlockedScript(it->get<std::string>());
    }
}

// ── MAXScript handler ───────────────────────────────────────────
static std::string HandleMaxScript(
    const std::string& command,
    bool quiet) {

    RejectBlockedScript(command);

    // MAXScript runs on Max's main thread only, whatever the thread mode.
    return McpHost::RunOnMainThread([&command, quiet]() -> std::string {
        HandlerHelpers::RequireMaxScriptThread();
        // Quiet mode lets prompts take their defaults, as batch scripts expect.
        // quiet=false forces quiet mode off for the script (restored after),
        // even if a plugin or script left Max quiet, so prompts reach the
        // agent as blocking dialogs. Only the main thread changes the
        // process-wide flag; this lambda always runs there.
        // quietErrors keeps errors textual either way.
        std::optional<TempQuietMode> quiet_mode;
        quiet_mode.emplace(quiet ? TRUE : FALSE);
        std::wstring wcmd = HandlerHelpers::WrapForErrorCapture(
            HandlerHelpers::Utf8ToWide(command));

        FPValue fpv;
        BOOL ok = FALSE;

        try {
            ok = ExecuteMAXScriptScript(
                wcmd.c_str(),
                MAXScript::ScriptSource::NonEmbedded,
                TRUE,    // quietErrors: diagnostics belong in the response/log
                &fpv,    // result goes here
                TRUE     // logQuietErrors
            );
        } catch (...) {
            throw std::runtime_error("MAXScript execution exception");
        }

        if (!ok) {
            // Runtime errors are caught inside MAXScript and surface as a
            // sentinel string in fpv below. FALSE means a parse error OR an
            // abort try/catch cannot catch (quitMax, escape): classify it,
            // with Max's own error text as the fallback detail.
            const std::string detail = fpv.type == TYPE_TSTR && fpv.tstr ? WideToUtf8(fpv.tstr->data()) : "";
            std::string code;
            std::string message = HandlerHelpers::MaxScriptFailureMessage(wcmd, &code, detail);
            throw std::runtime_error(HandlerHelpers::StructuredErrorPayload(code, message));
        }
        McpHost::DialogWatch::ThrowIfDismissed();

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
        // Point3, color, matrix, array, node...: print the value as MAXScript
        // shows it ("[10,20,0]"), not "OK". Printed from the FPValue itself;
        // the command is never evaluated again.
        if (fpv.type != TYPE_VOID) {
            try {
                ScopedMaxScriptEvaluationContext context;
                if (Value* value = InterfaceFunction::FPValue_to_val(fpv)) {
                    StringStream* text = new StringStream(0);
                    value->sprin1(text);
                    return WideToUtf8(text->to_string());
                }
            } catch (...) {}
        }

        return "OK";
    });
}

// ── Dispatcher ──────────────────────────────────────────────────
std::string CommandDispatcher::Dispatch(
    const std::string& json_request,
    MCPBridgeGUP* gup,
    const std::string& client_session_id) {

    auto start = std::chrono::steady_clock::now();
    tl_quiet_override.clear();  // set below only for this request's script

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
    // health and native:max_dialogs never reach the core: the shell answers
    // them on the pipe thread, and labels every core request for health
    // (core_control.cpp).
    McpHost::DialogWatch::RequestContext request_context(request_id, cmd_type);

    // Route to handler — read-only handlers run directly on pipe thread
    // _forceMainThread flag allows benchmarking the same handler both ways
    bool forceMain = req.value("_forceMainThread", false);
    // A Dispatch nested on the main thread (invoke_tool, run_tool_smoke) is
    // already where main-thread work runs: direct mode would only make it look
    // like a pipe thread (no quiet mode, no compile check, threadMode "direct").
    bool direct = !forceMain && IsDirectHandler(cmd_type) && !HostExecutor::IsMainThread();
    DirectModeGuard dmg(direct);

    try {
        // A run_python script's own call steps out of the script's open undo
        // step for its duration (script_hold.h).
        ScriptHold::Step scriptStep(client_session_id, cmd_type, command);
        auto invoke = [&]() -> std::string {
        std::string result;

        if (cmd_type == "native:script_hold") {
            result = ScriptHold::Handle(command);
        } else if (cmd_type == "native:plugin_inspect") {
            result = NativeHandlers::PluginInspect(command, gup);
        } else if (cmd_type == "native:plugin_patch") {
            result = NativeHandlers::PluginPatch(command, gup);
        } else if (cmd_type == "native:parameters") {
            result = NativeHandlers::Parameters(command, gup);
        } else if (cmd_type == "native:lighting_context") {
            result = NativeHandlers::LightingContext(command, gup);
        } else if (cmd_type == "native:assign_renderer") {
            result = NativeHandlers::AssignRenderer(command, gup);
        } else if (cmd_type == "maxscript") {
            if (command.empty()) {
                throw std::runtime_error("Empty MAXScript command");
            }
            // Quiet mode unless the caller chose, or the script could prompt
            // to save or discard the scene (quiet would answer for them).
            int explicitQuiet = -1;
            if (req.contains("quiet") && req["quiet"].is_boolean())
                explicitQuiet = req["quiet"].get<bool>() ? 1 : 0;
            bool overridden = false;
            const bool quiet = QuietPolicy::ResolveQuiet(command, explicitQuiet, &overridden);
            if (overridden) tl_quiet_override = "file_command";
            const std::string undoLabel = req.contains("undo") && (req["undo"].is_string)()
                ? req["undo"].get<std::string>() : std::string();
            // Never around a file command: replacing the scene inside a hold is unsafe.
            result = undoLabel.empty() || QuietPolicy::MentionsSceneFileCommand(command)
                ? HandleMaxScript(command, quiet) : HandleMaxScriptUndoable(command, quiet, undoLabel);
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
        } else if (cmd_type == "native:resolve_node_refs") {
            result = NativeHandlers::ResolveNodeRefs(command, gup);
        } else if (cmd_type == "native:scene_patch") {
            result = NativeHandlers::ScenePatch(command, gup);
        } else if (cmd_type == "native:scene_qa_scan") {
            result = NativeHandlers::SceneQAScan(command, gup);
        } else if (cmd_type == "native:scene_qa_fix") {
            result = NativeHandlers::SceneQAFix(command, gup);
        } else if (cmd_type == "native:mesh_script") {
            RejectBlockedPayloadScripts(command, {"resolve", "script", "finish"});
            result = NativeHandlers::MeshScript(command, gup);
        } else if (cmd_type == "native:geometry_snapshot") {
            result = NativeHandlers::GeometrySnapshot(command, gup);
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
        } else if (cmd_type == "native:set_modifier_property" || cmd_type == "native:batch_modify") {
            result = NativeHandlers::SetModifierProperty(command, gup);
        // Max Creation Graph scripted modifiers
        } else if (cmd_type == "native:mcg_resolve_class") {
            result = NativeHandlers::MCGResolveClass(command, gup);
        } else if (cmd_type == "native:mcg_apply_modifier") {
            result = NativeHandlers::MCGApplyModifier(command, gup);
        } else if (cmd_type == "native:mcg_set_node_parameter") {
            result = NativeHandlers::MCGSetNodeParameter(command, gup);
        } else if (cmd_type == "native:mcg_inspect_instance") {
            result = NativeHandlers::MCGInspectInstance(command, gup);
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
        } else if (cmd_type == "native:get_material_library") {
            result = NativeHandlers::GetMaterialLibrary(command, gup);
        } else if (cmd_type == "native:inspect_material_network") {
            result = NativeHandlers::InspectMaterialNetwork(command, gup);
        } else if (cmd_type == "native:replicate_material") {
            result = NativeHandlers::ReplicateMaterial(command, gup);
        } else if (cmd_type == "native:replicate_material_preview") {
            result = NativeHandlers::ReplicateMaterialPreview(command, gup);
        } else if (cmd_type == "native:write_osl_shader") {
            result = NativeHandlers::WriteOSLShader(command, gup);
        // Phase 4: Scene management
        } else if (cmd_type == "native:set_parent") {
            result = NativeHandlers::SetParent(command, gup);
        } else if (cmd_type == "native:batch_rename_objects") {
            result = NativeHandlers::BatchRenameObjects(command, gup);
        } else if (cmd_type == "native:manage_scene") {
            result = NativeHandlers::ManageScene(command, gup);
        } else if (cmd_type == "native:undo_last") {
            result = NativeHandlers::UndoLast(command, gup);
        // File access
        } else if (cmd_type == "native:inspect_max_file") {
            result = NativeHandlers::InspectMaxFile(command, gup);
        } else if (cmd_type == "native:merge_from_file") {
            result = NativeHandlers::MergeFromFile(command, gup);
        } else if (cmd_type == "native:batch_file_info") {
            result = NativeHandlers::BatchFileInfo(command, gup);
        // Viewport capture
        } else if (cmd_type == "native:agent_viewport") {
            result = NativeHandlers::AgentViewportCommand(command, gup);
        } else if (cmd_type == "native:capture_multi_view") {
            result = NativeHandlers::CaptureMultiView(command, gup);
        } else if (cmd_type == "native:job_progress") {
            result = NativeHandlers::GetJobProgress(command, gup);
        } else if (cmd_type == "native:capture_viewport") {
            result = NativeHandlers::CaptureViewport(command, gup);
        } else if (cmd_type == "native:capture_screen") {
            result = NativeHandlers::CaptureScreen(command, gup);
        } else if (cmd_type == "native:isolate_and_capture_selected") {
            result = NativeHandlers::IsolateAndCaptureSelected(command, gup);
        // Phase 6: Material writes
        } else if (cmd_type == "native:assign_material") {
            result = NativeHandlers::AssignMaterial(command, gup);
        } else if (cmd_type == "native:set_material_property") {
            result = NativeHandlers::SetMaterialProperty(command, gup);
        } else if (cmd_type == "native:set_material_properties") {
            result = NativeHandlers::SetMaterialProperties(command, gup);
        } else if (cmd_type == "native:create_shell_material") {
            result = NativeHandlers::CreateShellMaterial(command, gup);
        } else if (cmd_type == "native:backup_material_library") {
            result = NativeHandlers::BackupMaterialLibrary(command, gup);
        // Plugin enumeration
        } else if (cmd_type == "native:list_plugin_classes") {
            result = NativeHandlers::ListPluginClasses(command, gup);
        } else if (cmd_type == "native:get_plugin_capabilities") {
            result = NativeHandlers::GetPluginCapabilities(command, gup);
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
        } else if (cmd_type == "native:render_start") {
            result = NativeHandlers::RenderStart(command, gup);
        } else if (cmd_type == "native:render_cancel") {
            result = NativeHandlers::RenderCancel(command, gup);
        } else if (cmd_type == "native:render_cancel_capture") {
            result = NativeHandlers::RenderCancelCapture(command, gup);
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
        } else if (cmd_type == "native:replicate_material_apply") {
            result = NativeHandlers::ReplicateMaterialApply(command, gup);
        // Controllers (extended)
        } else if (cmd_type == "native:assign_controller") {
            result = NativeHandlers::AssignController(command, gup);
        } else if (cmd_type == "native:inspect_controller") {
            result = NativeHandlers::InspectController(command, gup);
        } else if (cmd_type == "native:set_controller_props") {
            result = NativeHandlers::SetControllerProps(command, gup);
        } else if (cmd_type == "native:add_controller_target") {
            result = NativeHandlers::AddControllerTarget(command, gup);
        } else if (cmd_type == "native:keyframe_tracks") {
            result = NativeHandlers::KeyframeTracks(command, gup);
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
        } else if (cmd_type == "native:main_thread") {
            result = NativeHandlers::MainThread(command, gup);
        } else if (cmd_type == "native:introspect_interface") {
            result = NativeHandlers::IntrospectInterface(command, gup);
        } else if (cmd_type == "native:invoke_interface") {
            result = NativeHandlers::InvokeInterface(command, gup);
        } else if (cmd_type == "native:run_macroscript") {
            result = NativeHandlers::RunMacroscript(command, gup);
        // tyFlow graph access
        } else if (cmd_type == "native:tyflow_graph_stream") {
            result = NativeHandlers::TyFlowGraphStream(command, gup);
        } else if (cmd_type == "native:tyflow_operator") {
            result = NativeHandlers::TyFlowOperator(command, gup);
        // Live tool smoke testing
        } else if (cmd_type == "native:invoke_tool") {
            result = NativeHandlers::InvokeTool(command, gup);
        } else if (cmd_type == "native:tool_smoke") {
            result = NativeHandlers::RunToolSmoke(command, gup);
        } else {
            throw std::runtime_error("Unknown command type: " + cmd_type);
        }

        return result;
        };

        std::string result;
        // scene_patch owns a strict hold so it can reject an already-open user
        // transaction and report rollback only after Cancel() has completed.
        // mesh_script opens its labeled hold for edits only; reads open none.
        const bool handlerOwnsTransaction = cmd_type == "native:scene_patch" ||
            cmd_type == "native:plugin_patch" || cmd_type == "native:mesh_script";
        const bool transact =
            IsMutatingNativeHandler(cmd_type) &&
            !CommandDispatcher::Detail::RequestIsDryRunOrPreview(cmd_type, command) &&
            !RequestIsReadOnlyAction(cmd_type, command) &&
            !handlerOwnsTransaction;

        if (transact) {
            result = gup->GetExecutor().ExecuteSync([&]() -> std::string {
                NativeUndoTransaction tx(cmd_type);
                std::string inner = invoke();
                McpHost::DialogWatch::ThrowIfDismissed();
                if (ResultLooksLikeError(inner)) {
                    tx.Cancel();
                } else {
                    tx.Accept();
                }
                return inner;
            });
        } else {
            result = invoke();
        }

        auto end = std::chrono::steady_clock::now();
        int ms = (int)std::chrono::duration_cast<std::chrono::milliseconds>(end - start).count();
        return BuildResponse(true, result, "", request_id, cmd_type, ms);

    } catch (const std::exception& e) {
        auto end = std::chrono::steady_clock::now();
        int ms = (int)std::chrono::duration_cast<std::chrono::milliseconds>(end - start).count();
        return BuildResponse(false, "", NormalizeNativeError(e.what()), request_id, cmd_type, ms);
    }
}
