#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/bridge_gup.h"

#include <control.h>

#include <unordered_set>
#include <vector>

using json = nlohmann::json;
using namespace HandlerHelpers;

// ── Helper: get compact value string from a SubAnim ─────────
static std::string CompactValue(Animatable* sa, TimeValue t) {
    if (!sa) return "";
    try {
        Control* ctrl = (Control*)sa->GetInterface(I_CONTROL);
        if (!ctrl) return "";

        SClass_ID scid = ctrl->SuperClassID();
        if (scid == CTRL_FLOAT_CLASS_ID) {
            float fVal = 0;
            Interval valid = FOREVER;
            ctrl->GetValue(t, &fVal, valid, CTRL_ABSOLUTE);
            char buf[64];
            snprintf(buf, sizeof(buf), "%.6g", fVal);
            return buf;
        }
        if (scid == CTRL_POINT3_CLASS_ID || scid == CTRL_POSITION_CLASS_ID) {
            Point3 pt(0, 0, 0);
            Interval valid = FOREVER;
            ctrl->GetValue(t, &pt, valid, CTRL_ABSOLUTE);
            char buf[128];
            snprintf(buf, sizeof(buf), "[%.4g,%.4g,%.4g]", pt.x, pt.y, pt.z);
            return buf;
        }
    }
    catch (...) {}
    return "";
}

// ── Helper: recursive SubAnim tree walk ─────────────────────
static json WalkSubAnims(Animatable* anim, const std::string& path,
                         const std::string& trackName, int depthLeft,
                         const std::string& filter, bool includeValues,
                         TimeValue t) {
    if (!anim || depthLeft < 0) return nullptr;

    // Controller info
    Animatable* ctrlAnim = anim->SubAnim(anim->NumSubs() > 0 ? 0 : -1); // dummy
    Control* ctrl = nullptr;
    std::string ctrlClass, ctrlSuper;

    // Check if this animatable has a controller interface
    ctrl = (Control*)anim->GetInterface(I_CONTROL);
    if (ctrl) {
        ctrlClass = WideToUtf8(ctrl->ClassName().data());
        SClass_ID scid = ctrl->SuperClassID();
        if (scid == CTRL_FLOAT_CLASS_ID) ctrlSuper = "float";
        else if (scid == CTRL_POINT3_CLASS_ID) ctrlSuper = "point3";
        else if (scid == CTRL_POSITION_CLASS_ID) ctrlSuper = "position";
        else if (scid == CTRL_ROTATION_CLASS_ID) ctrlSuper = "rotation";
        else if (scid == CTRL_SCALE_CLASS_ID) ctrlSuper = "scale";
        else if (scid == CTRL_MATRIX3_CLASS_ID) ctrlSuper = "matrix3";
        else ctrlSuper = "controller";
    }

    // Build children
    json children = json::array();
    int childCount = 0;

    if (depthLeft > 0) {
        int numSubs = anim->NumSubs();
        for (int i = 0; i < numSubs; i++) {
            Animatable* child = anim->SubAnim(i);
            if (!child) continue;

            MSTR childNameM = anim->SubAnimName(i, false);
            if (childNameM.isNull() || childNameM.Length() == 0) continue;

            std::string childName = WideToUtf8(childNameM.data());
            std::string childPath = path + "[#" + childName + "]";

            json childJson = WalkSubAnims(child, childPath, childName,
                                          depthLeft - 1, filter, includeValues, t);
            if (!childJson.is_null()) {
                children.push_back(childJson);
                childCount++;
            }
        }
    }

    // Filter check
    if (!filter.empty()) {
        std::string haystack = trackName + " " + path + " " + ctrlClass;
        std::transform(haystack.begin(), haystack.end(), haystack.begin(), ::tolower);
        if (haystack.find(filter) == std::string::npos && childCount == 0) {
            return nullptr;
        }
    }

    // Build result node
    json node;
    node["name"] = trackName;
    node["path"] = path;
    if (!ctrlClass.empty()) node["controller"] = ctrlClass;
    if (!ctrlSuper.empty()) node["controllerSuperclass"] = ctrlSuper;
    if (includeValues) {
        std::string val = CompactValue(anim, t);
        if (!val.empty()) node["value"] = val;
    }
    node["childCount"] = childCount;
    node["children"] = children;

    return node;
}

// ── native:inspect_track_view ───────────────────────────────
std::string NativeHandlers::InspectTrackView(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string name = p.value("name", "");
        int depth = p.value("depth", 4);
        std::string filter = p.value("filter", "");
        bool includeValues = p.value("include_values", true);

        if (name.empty()) throw std::runtime_error("name is required");
        depth = std::max(1, std::min(depth, 6));

        INode* node = FindNodeByName(name);
        if (!node) throw std::runtime_error("Object not found: " + name);

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        // Lowercase filter for comparison
        std::string lowerFilter = filter;
        std::transform(lowerFilter.begin(), lowerFilter.end(), lowerFilter.begin(), ::tolower);

        // Walk root sub-anims
        json tracks = json::array();
        int rootCount = 0;
        int numSubs = node->NumSubs();

        for (int i = 0; i < numSubs; i++) {
            Animatable* child = node->SubAnim(i);
            if (!child) continue;

            MSTR childNameM = node->SubAnimName(i, false);
            if (childNameM.isNull() || childNameM.Length() == 0) continue;

            std::string childName = WideToUtf8(childNameM.data());
            std::string childPath = "[#" + childName + "]";

            json childJson = WalkSubAnims(child, childPath, childName,
                                          depth - 1, lowerFilter, includeValues, t);
            if (!childJson.is_null()) {
                tracks.push_back(childJson);
                rootCount++;
            }
        }

        json result;
        result["object"] = WideToUtf8(node->GetName());
        result["class"] = NodeClassName(node);
        result["depth"] = depth;
        result["filter"] = filter;
        result["rootTrackCount"] = rootCount;
        result["tracks"] = tracks;
        return result.dump();
    });
}

// ── native:list_wireable_params ─────────────────────────────
//
// Sub-anim trees can explode on rigs (Skin/biped/CAT bone tables),
// Multi/Sub materials, particle systems, and modifier stacks with
// keyed tracks. A naive DFS visits 100k+ Animatables and blocks
// Max's main thread for minutes.
//
// This walk is bounded on four axes — total visits, result count,
// wall-clock time, and per-node fan-out — and uses a visited set
// to skip shared subtrees. Each Animatable call is wrapped in
// try/catch because third-party plugins can throw from NumSubs/
// SubAnim/SubAnimName/ClassName. When any cap trips, results are
// returned as-is plus a synthetic terminal entry with path
// "__truncated__" so existing array consumers stay valid and the
// agent can decide whether to refine the query.
std::string NativeHandlers::ListWireableParams(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string name = p.value("name", "");
        std::string filter = p.value("filter", "");
        int depth = p.value("depth", 3);

        // Caller-tunable safety budgets. Defaults chosen to feel
        // instant on normal scenes and survive a Skin-on-biped.
        int maxVisits  = std::max(100, p.value("max_visits",  20000));
        int maxResults = std::max(10,  p.value("max_results", 500));
        int maxMs      = std::max(100, p.value("max_ms",      5000));
        int maxFanout  = std::max(8,   p.value("max_fanout",  200));

        if (name.empty()) throw std::runtime_error("name is required");
        depth = std::max(1, std::min(depth, 5));

        INode* node = FindNodeByName(name);
        if (!node) throw std::runtime_error("Object not found: " + name);

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        std::string lowerFilter = filter;
        std::transform(lowerFilter.begin(), lowerFilter.end(),
                       lowerFilter.begin(), ::tolower);

        json results = json::array();
        std::unordered_set<Animatable*> visited;
        visited.reserve(1024);

        const DWORD startTick = GetTickCount();
        int visits = 0;
        int fanoutSkips = 0;
        const char* truncReason = nullptr;

        struct Frame {
            Animatable* anim;
            std::string path;
            int depthLeft;
        };
        std::vector<Frame> stack;
        stack.reserve(64);
        stack.push_back({static_cast<Animatable*>(node), std::string(), depth});

        while (!stack.empty()) {
            // Cheap budget checks every iteration.
            if (static_cast<int>(results.size()) >= maxResults) {
                truncReason = "results"; break;
            }
            if (visits >= maxVisits) {
                truncReason = "visits"; break;
            }
            // GetTickCount is cheap but still skip most of the time.
            if ((visits & 0xFF) == 0 &&
                static_cast<int>(GetTickCount() - startTick) > maxMs) {
                truncReason = "time"; break;
            }

            Frame f = stack.back();
            stack.pop_back();
            if (!f.anim || f.depthLeft <= 0) continue;
            if (!visited.insert(f.anim).second) continue;

            int numSubs = 0;
            try { numSubs = f.anim->NumSubs(); }
            catch (...) { continue; }
            if (numSubs <= 0) continue;

            // Cap fan-out per node — protects against bone tables,
            // Multi/Sub material slots, particle operator arrays, etc.
            int walkLimit = numSubs;
            if (numSubs > maxFanout) {
                walkLimit = maxFanout;
                fanoutSkips += (numSubs - maxFanout);
            }

            for (int i = 0; i < walkLimit; i++) {
                visits++;

                Animatable* child = nullptr;
                try { child = f.anim->SubAnim(i); }
                catch (...) { continue; }
                if (!child) continue;

                MSTR childNameM;
                try { childNameM = f.anim->SubAnimName(i, false); }
                catch (...) { continue; }
                if (childNameM.isNull() || childNameM.Length() == 0) continue;

                std::string childName = WideToUtf8(childNameM.data());
                std::string childPath = f.path + "[#" + childName + "]";

                Control* ctrl = nullptr;
                try { ctrl = (Control*)child->GetInterface(I_CONTROL); }
                catch (...) { ctrl = nullptr; }
                bool isWireable = ctrl != nullptr;

                int childSubs = 0;
                try { childSubs = child->NumSubs(); }
                catch (...) { childSubs = 0; }

                // Add as a result if leaf or wireable. Filter is
                // applied to the leaf path here, but recursion
                // proceeds regardless so deeper matches aren't lost.
                if (childSubs == 0 || isWireable) {
                    bool include = true;
                    if (!lowerFilter.empty()) {
                        std::string lower = childPath;
                        std::transform(lower.begin(), lower.end(),
                                       lower.begin(), ::tolower);
                        if (lower.find(lowerFilter) == std::string::npos)
                            include = false;
                    }
                    if (include) {
                        json entry;
                        entry["path"] = childPath;
                        entry["is_wireable"] = isWireable;
                        try {
                            entry["type"] = ctrl
                                ? WideToUtf8(ctrl->ClassName().data())
                                : "none";
                        } catch (...) { entry["type"] = "?"; }
                        try {
                            std::string val = CompactValue(child, t);
                            entry["value"] = val.empty() ? "?" : val;
                        } catch (...) { entry["value"] = "?"; }
                        results.push_back(entry);
                        if (static_cast<int>(results.size()) >= maxResults) {
                            truncReason = "results";
                            break;
                        }
                    }
                }

                if (childSubs > 0 && f.depthLeft > 1) {
                    stack.push_back({child, childPath, f.depthLeft - 1});
                }
            }

            if (truncReason) break;
        }

        // Surface fan-out truncation only if nothing harder tripped.
        if (!truncReason && fanoutSkips > 0) truncReason = "fanout";

        if (truncReason) {
            char buf[320];
            snprintf(buf, sizeof(buf),
                "Walk hit %s cap (visited=%d, results=%zu, depth=%d, "
                "fanout_skips=%d, elapsed_ms=%u). Refine with "
                "filter:\"keyword\" or depth:1, or raise "
                "max_visits/max_results/max_ms/max_fanout.",
                truncReason, visits, results.size(), depth,
                fanoutSkips,
                static_cast<unsigned>(GetTickCount() - startTick));
            json warn;
            warn["path"] = "__truncated__";
            warn["is_wireable"] = false;
            warn["type"] = "warning";
            warn["value"] = buf;
            results.push_back(warn);
        }

        return results.dump();
    });
}

// ── native:assign_controller ────────────────────────────────────
std::string NativeHandlers::AssignController(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&]() -> std::string {
        json p = json::parse(params);
        std::string name = p.contains("name") && !p["name"].is_null() ? p["name"].get<std::string>() : "";
        std::string paramPath = p.contains("param_path") && !p["param_path"].is_null() ? p["param_path"].get<std::string>() : "";
        std::string ctrlType = p.contains("controller_type") && !p["controller_type"].is_null() ? p["controller_type"].get<std::string>() : "";
        std::string script = p.contains("script") && !p["script"].is_null() ? p["script"].get<std::string>() : "";
        // Handle null values explicitly — json::value() doesn't use default for null
        auto variables = p.contains("variables") && !p["variables"].is_null()
                         ? p["variables"] : json::array();
        auto ctrlParams = p.contains("params") && !p["params"].is_null()
                          ? p["params"] : json::object();
        bool layer = p.contains("layer") && !p["layer"].is_null() ? p["layer"].get<bool>() : false;

        if (name.empty() || paramPath.empty() || ctrlType.empty())
            throw std::runtime_error("name, param_path, and controller_type are required");

        // Controller type -> MAXScript class mapping
        static const std::map<std::string, std::string> ctrlMap = {
            {"float_script", "Float_Script"}, {"position_script", "Position_Script"},
            {"rotation_script", "Rotation_Script"}, {"scale_script", "Scale_Script"},
            {"point3_script", "Point3_Script"},
            {"position_constraint", "Position_Constraint"}, {"orientation_constraint", "Orientation_Constraint"},
            {"lookat_constraint", "LookAt_Constraint"}, {"path_constraint", "Path_Constraint"},
            {"surface_constraint", "Surface_Constraint"}, {"link_constraint", "Link_Constraint"},
            {"attachment_constraint", "Attachment"}, {"noise_float", "Noise_Float"},
            {"noise_position", "Noise_Position"}, {"noise_rotation", "Noise_Rotation"},
            {"noise_scale", "Noise_Scale"},
            {"float_list", "Float_List"}, {"position_list", "Position_List"},
            {"rotation_list", "Rotation_List"}, {"scale_list", "Scale_List"},
            {"float_expression", "Float_Expression"}, {"position_expression", "Position_Expression"},
            {"spring", "Spring"},
        };

        auto it = ctrlMap.find(ctrlType);
        std::string msClass = (it != ctrlMap.end()) ? it->second : ctrlType;

        // Build MAXScript
        std::string ms;
        ms += "(\n";
        ms += "  local obj = getNodeByName \"" + JsonEscape(name) + "\"\n";
        ms += "  if obj == undefined do throw \"Object not found\"\n";
        ms += "  local sa = execute (\"$'\" + obj.name + \"'\" + \"" + JsonEscape(paramPath) + "\")\n";
        ms += "  if sa == undefined do throw \"Track not found: " + JsonEscape(paramPath) + "\"\n";

        if (layer) {
            // Layer mode: create/reuse list controller, add new ctrl on top
            ms += "  local existCtrl = sa.controller\n";
            ms += "  local listCtrl\n";
            ms += "  if (classOf existCtrl) as string == \"" + msClass + "\" or ";
            ms += "(matchPattern ((classOf existCtrl) as string) pattern:\"*_List\") then (\n";
            ms += "    listCtrl = existCtrl\n";
            ms += "  ) else (\n";
            ms += "    listCtrl = " + msClass.substr(0, msClass.find("_")) + "_List()\n";
            ms += "    sa.controller = listCtrl\n";
            ms += "  )\n";
            ms += "  local newCtrl = " + msClass + "()\n";
            ms += "  listCtrl[listCtrl.count].controller = newCtrl\n";
            ms += "  local ctrl = newCtrl\n";
        } else {
            ms += "  local ctrl = " + msClass + "()\n";
            ms += "  sa.controller = ctrl\n";
        }

        // Script text for script controllers
        if (!script.empty()) {
            std::string escaped = script;
            // Escape backslashes first, then quotes, then newlines
            std::string safe;
            for (char c : escaped) {
                if (c == '\\') safe += "\\\\";
                else if (c == '"') safe += "\\\"";
                else if (c == '\n') safe += "\\n";
                else if (c == '\r') continue;
                else if (c == '\t') safe += "\\t";
                else safe += c;
            }
            ms += "  ctrl.script = \"" + safe + "\"\n";
        }

        // Node variables for script controllers
        for (const auto& v : variables) {
            std::string varName = v.value("name", "");
            std::string targetObj = v.value("object", v.value("target", ""));
            if (!varName.empty() && !targetObj.empty()) {
                ms += "  local tgt = getNodeByName \"" + JsonEscape(targetObj) + "\"\n";
                ms += "  if tgt != undefined do ctrl.addNode \"" + varName + "\" tgt\n";
            }
        }

        // Constraint targets
        bool isConstraint = ctrlType.find("constraint") != std::string::npos;
        if (isConstraint) {
            for (const auto& v : variables) {
                std::string targetObj = v.value("object", v.value("target", ""));
                float weight = v.value("weight", 50.0f);
                if (!targetObj.empty()) {
                    ms += "  local tgt = getNodeByName \"" + JsonEscape(targetObj) + "\"\n";
                    if (ctrlType == "link_constraint") {
                        int frame = v.value("frame", 0);
                        ms += "  if tgt != undefined do ctrl.addTarget tgt " + std::to_string(frame) + "\n";
                    } else {
                        ms += "  if tgt != undefined do ctrl.appendTarget tgt " +
                              std::to_string(weight) + "\n";
                    }
                }
            }
        }

        // Extra properties (avoid is_string() etc. — MAXScript macro collision)
        for (auto& [key, val] : ctrlParams.items()) {
            std::string valStr;
            auto vt = val.type();
            if (vt == json::value_t::string) valStr = val.get<std::string>();
            else if (vt == json::value_t::number_float) valStr = std::to_string(val.get<double>());
            else if (vt == json::value_t::number_integer || vt == json::value_t::number_unsigned) valStr = std::to_string(val.get<int>());
            else if (vt == json::value_t::boolean) valStr = val.get<bool>() ? "true" : "false";
            else valStr = val.dump();
            ms += "  try (ctrl." + key + " = " + valStr + ") catch ()\n";
        }

        ms += "  \"OK\"\n";
        ms += ")\n";

        RunMAXScript(ms);

        json result;
        result["controller"] = msClass;
        result["object"] = name;
        result["param_path"] = paramPath;
        if (layer) result["layered"] = true;
        return result.dump();
    });
}

// ── native:inspect_controller ───────────────────────────────────
std::string NativeHandlers::InspectController(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&]() -> std::string {
        json p = json::parse(params);
        std::string name = p.value("name", "");
        std::string paramPath = p.value("param_path", "");

        if (name.empty() || paramPath.empty())
            throw std::runtime_error("name and param_path are required");

        // Use MAXScript for rich controller inspection — too many controller
        // types with custom properties to replicate in pure C++
        std::string ms;
        ms += "(\n";
        ms += "  local obj = getNodeByName \"" + JsonEscape(name) + "\"\n";
        ms += "  if obj == undefined do throw \"Object not found\"\n";
        ms += "  local sa = execute (\"$'\" + obj.name + \"'\" + \"" + JsonEscape(paramPath) + "\")\n";
        ms += "  if sa == undefined do throw \"Track not found\"\n";
        ms += "  local ctrl = sa.controller\n";
        ms += "  if ctrl == undefined do throw \"No controller assigned\"\n";
        ms += "  local BS = bit.intAsChar 92 as string; local DQ = bit.intAsChar 34 as string; fn __mcp_esc s = (s = substituteString s BS (BS+BS); s = substituteString s DQ (BS+DQ); s = substituteString s \"\\n\" (BS+\"n\"); s = substituteString s \"\\r\" \"\"; s = substituteString s \"\\t\" (BS+\"t\"); s)\n";
        ms += "  local cls = (classOf ctrl) as string\n";
        ms += "  local scls = (superClassOf ctrl) as string\n";
        ms += "  local valStr = try ((ctrl.value as string)) catch (\"?\")\n";
        ms += "  local r = \"{\\\"class\\\":\\\"\" + __mcp_esc cls + \"\\\",\"\n";
        ms += "  r += \"\\\"superClass\\\":\\\"\" + __mcp_esc scls + \"\\\",\"\n";
        ms += "  r += \"\\\"value\\\":\\\"\" + __mcp_esc valStr + \"\\\",\"\n";
        // Properties
        ms += "  local props = getPropNames ctrl\n";
        ms += "  r += \"\\\"properties\\\":{\"\n";
        ms += "  for i = 1 to props.count do (\n";
        ms += "    local pn = props[i] as string\n";
        ms += "    local pv = try ((getProperty ctrl props[i]) as string) catch (\"?\")\n";
        ms += "    if i > 1 do r += \",\"\n";
        ms += "    r += \"\\\"\" + __mcp_esc pn + \"\\\":\\\"\" + __mcp_esc pv + \"\\\"\"\n";
        ms += "  )\n";
        ms += "  r += \"},\"\n";
        // Script text (for script controllers)
        ms += "  local hasScript = try (ctrl.script != undefined) catch (false)\n";
        ms += "  if hasScript then (\n";
        ms += "    r += \"\\\"scriptText\\\":\\\"\" + __mcp_esc ctrl.script + \"\\\",\"\n";
        ms += "  )\n";
        // Sub-controllers count
        ms += "  r += \"\\\"numSubAnims\\\":\" + (sa.numsubs as string)\n";
        ms += "  r += \"}\"\n";
        ms += "  r\n";
        ms += ")\n";

        return RunMAXScript(ms);
    });
}

// ── native:set_controller_props ─────────────────────────────────
std::string NativeHandlers::SetControllerProps(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&]() -> std::string {
        json p = json::parse(params);
        std::string name = p.contains("name") && !p["name"].is_null() ? p["name"].get<std::string>() : "";
        std::string paramPath = p.contains("param_path") && !p["param_path"].is_null() ? p["param_path"].get<std::string>() : "";
        std::string script = p.contains("script") && !p["script"].is_null() ? p["script"].get<std::string>() : "";
        auto ctrlParams = p.contains("params") && !p["params"].is_null()
                          ? p["params"] : json::object();

        if (name.empty() || paramPath.empty())
            throw std::runtime_error("name and param_path are required");

        std::string ms;
        ms += "(\n";
        ms += "  local obj = getNodeByName \"" + JsonEscape(name) + "\"\n";
        ms += "  if obj == undefined do throw \"Object not found\"\n";
        ms += "  local sa = execute (\"$'\" + obj.name + \"'\" + \"" + JsonEscape(paramPath) + "\")\n";
        ms += "  local ctrl = sa.controller\n";
        ms += "  if ctrl == undefined do throw \"No controller\"\n";

        if (!script.empty()) {
            std::string safe;
            for (char c : script) {
                if (c == '\\') safe += "\\\\";
                else if (c == '"') safe += "\\\"";
                else if (c == '\n') safe += "\\n";
                else if (c == '\r') continue;
                else safe += c;
            }
            ms += "  try (ctrl.script = \"" + safe + "\") catch (try (ctrl.SetExpression \"" + safe + "\"; ctrl.Update()) catch ())\n";
        }

        // Avoid is_string() etc. — MAXScript macro collision
        for (auto& [key, val] : ctrlParams.items()) {
            std::string valStr;
            auto vt = val.type();
            if (vt == json::value_t::string) valStr = val.get<std::string>();
            else if (vt == json::value_t::number_float) valStr = std::to_string(val.get<double>());
            else if (vt == json::value_t::number_integer || vt == json::value_t::number_unsigned) valStr = std::to_string(val.get<int>());
            else if (vt == json::value_t::boolean) valStr = val.get<bool>() ? "true" : "false";
            else valStr = val.dump();
            ms += "  try (ctrl." + key + " = " + valStr + ") catch ()\n";
        }

        ms += "  \"OK\"\n";
        ms += ")\n";

        RunMAXScript(ms);

        json result;
        result["status"] = "ok";
        result["object"] = name;
        result["param_path"] = paramPath;
        return result.dump();
    });
}

// ── native:add_controller_target ────────────────────────────────
std::string NativeHandlers::AddControllerTarget(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&]() -> std::string {
        json p = json::parse(params);
        std::string name = p.contains("name") && !p["name"].is_null() ? p["name"].get<std::string>() : "";
        std::string paramPath = p.contains("param_path") && !p["param_path"].is_null() ? p["param_path"].get<std::string>() : "";
        std::string targetObj = p.contains("target_object") && !p["target_object"].is_null() ? p["target_object"].get<std::string>() : "";
        std::string varName = p.contains("var_name") && !p["var_name"].is_null() ? p["var_name"].get<std::string>() : "";
        float weight = p.value("weight", 50.0f);
        int frame = p.value("frame", 0);

        if (name.empty() || paramPath.empty() || targetObj.empty())
            throw std::runtime_error("name, param_path, and target_object are required");

        // Pure SDK: find node, walk sub-anim path, get controller
        INode* node = FindNodeByName(name);
        if (!node) throw std::runtime_error("Object not found: " + name);

        INode* tgtNode = FindNodeByName(targetObj);
        if (!tgtNode) throw std::runtime_error("Target object not found: " + targetObj);

        Animatable* sa = ResolveSubAnimPath(node, paramPath);
        if (!sa) throw std::runtime_error("Track not found: " + paramPath);

        // The resolved sub-anim might be a SubAnim wrapper or the controller itself.
        // Try: if it has sub-anims, the first one is typically the controller.
        // Also check if the animatable itself is a Control.
        Control* ctrl = nullptr;
        SClass_ID scid = sa->SuperClassID();
        if (scid == CTRL_FLOAT_CLASS_ID || scid == CTRL_POSITION_CLASS_ID ||
            scid == CTRL_ROTATION_CLASS_ID || scid == CTRL_SCALE_CLASS_ID ||
            scid == CTRL_POINT3_CLASS_ID || scid == CTRL_MATRIX3_CLASS_ID) {
            ctrl = (Control*)sa;
        }
        // If not a controller directly, check first sub-anim (SubAnim wrapper pattern)
        if (!ctrl && sa->NumSubs() > 0) {
            Animatable* child = sa->SubAnim(0);
            if (child) {
                SClass_ID cscid = child->SuperClassID();
                if (cscid == CTRL_FLOAT_CLASS_ID || cscid == CTRL_POSITION_CLASS_ID ||
                    cscid == CTRL_ROTATION_CLASS_ID || cscid == CTRL_SCALE_CLASS_ID ||
                    cscid == CTRL_POINT3_CLASS_ID || cscid == CTRL_MATRIX3_CLASS_ID) {
                    ctrl = (Control*)child;
                }
            }
        }
        if (!ctrl) throw std::runtime_error("No controller at track: " + paramPath);

        // Get controller class name via SDK
        MSTR clsName;
        ctrl->GetClassName(clsName, false);
        std::string cls = WideToUtf8(clsName.data());

        // Determine controller category and build minimal MAXScript for FP call
        std::string vn = varName.empty() ? targetObj : varName;
        std::string safeName = JsonEscape(name);
        std::string safeTgt = JsonEscape(targetObj);
        std::string safeVn = JsonEscape(vn);
        std::string safePath = JsonEscape(NormalizeSubAnimPath(paramPath));
        std::string sep = (safePath[0] == '[') ? "" : ".";

        std::string ms;
        std::string lowerCls = cls;
        std::transform(lowerCls.begin(), lowerCls.end(), lowerCls.begin(), ::tolower);

        if (lowerCls.find("script") != std::string::npos) {
            // Script controllers: addNode
            ms = "$'" + safeName + "'" + sep + safePath + ".controller.addNode \"" + safeVn + "\" (getNodeByName \"" + safeTgt + "\")";
        } else if (lowerCls.find("expression") != std::string::npos) {
            // Expression controllers: addScalarTarget
            ms = "try ($'" + safeName + "'" + sep + safePath + ".controller.addScalarTarget \"" + safeVn + "\" (getNodeByName \"" + safeTgt + "\") 0) catch ($'" + safeName + "'" + sep + safePath + ".controller.addVectorTarget \"" + safeVn + "\" (getNodeByName \"" + safeTgt + "\") 0)";
        } else if (cls == "Link_Constraint") {
            ms = "$'" + safeName + "'" + sep + safePath + ".controller.addTarget (getNodeByName \"" + safeTgt + "\") " + std::to_string(frame);
        } else if (lowerCls.find("constraint") != std::string::npos) {
            ms = "$'" + safeName + "'" + sep + safePath + ".controller.appendTarget (getNodeByName \"" + safeTgt + "\") " + std::to_string(weight);
        } else {
            throw std::runtime_error("Controller type '" + cls + "' does not support node targets. Use float_script or constraint controllers.");
        }

        RunMAXScript(ms);

        json result;
        result["status"] = "ok";
        result["object"] = name;
        result["target"] = targetObj;
        result["param_path"] = paramPath;
        return result.dump();
    });
}

// ── native:wire_params ──────────────────────────────────────────
std::string NativeHandlers::WireParams(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&]() -> std::string {
        json p = json::parse(params);
        std::string srcObj = p.value("source_object", "");
        std::string srcParam = p.value("source_param", "");
        std::string tgtObj = p.value("target_object", "");
        std::string tgtParam = p.value("target_param", "");
        std::string expression = p.value("expression", "");
        bool twoWay = p.value("two_way", false);
        std::string reverseExpr = p.value("reverse_expression", "");

        if (srcObj.empty() || srcParam.empty() || tgtObj.empty() || tgtParam.empty())
            throw std::runtime_error("source_object, source_param, target_object, target_param are required");

        // Normalize paths — [#Object (Box)] -> .baseObject to avoid execute() parse errors
        std::string normSrc = NormalizeSubAnimPath(srcParam);
        std::string normTgt = NormalizeSubAnimPath(tgtParam);

        std::string ms;
        ms += "(\n";
        ms += "  local srcN = getNodeByName \"" + JsonEscape(srcObj) + "\"\n";
        ms += "  local tgtN = getNodeByName \"" + JsonEscape(tgtObj) + "\"\n";
        ms += "  if srcN == undefined do throw \"Source object not found\"\n";
        ms += "  if tgtN == undefined do throw \"Target object not found\"\n";
        ms += "  local srcSA = execute (\"$'\" + srcN.name + \"'\" + \"" + JsonEscape(normSrc) + "\")\n";
        ms += "  local tgtSA = execute (\"$'\" + tgtN.name + \"'\" + \"" + JsonEscape(normTgt) + "\")\n";
        ms += "  if srcSA == undefined do throw \"Source track not found\"\n";
        ms += "  if tgtSA == undefined do throw \"Target track not found\"\n";

        if (twoWay) {
            std::string fwdExpr = expression.empty() ? "target_value" : expression;
            std::string revExpr = reverseExpr.empty() ? "target_value" : reverseExpr;
            ms += "  paramWire.connect2way srcSA tgtSA \"" + JsonEscape(fwdExpr) + "\" \"" + JsonEscape(revExpr) + "\"\n";
        } else {
            std::string expr = expression.empty() ? "source_value" : expression;
            ms += "  paramWire.connect srcSA tgtSA \"" + JsonEscape(expr) + "\"\n";
        }

        ms += "  \"OK\"\n";
        ms += ")\n";

        RunMAXScript(ms);

        json result;
        result["status"] = "wired";
        result["source"] = srcObj + srcParam;
        result["target"] = tgtObj + tgtParam;
        result["two_way"] = twoWay;
        return result.dump();
    });
}

// ── native:get_wired_params ─────────────────────────────────────
std::string NativeHandlers::GetWiredParams(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&]() -> std::string {
        json p = json::parse(params);
        std::string name = p.value("name", "");

        if (name.empty()) throw std::runtime_error("name is required");

        // Walk sub-anims looking for Wire controllers
        // Paths use [#name] format so they work directly with MAXScript execute()
        std::string ms;
        ms += "(\n";
        ms += "  local obj = getNodeByName \"" + JsonEscape(name) + "\"\n";
        ms += "  if obj == undefined do throw \"Object not found\"\n";
        ms += "  local results = #()\n";
        ms += "  local BS = bit.intAsChar 92 as string; local DQ = bit.intAsChar 34 as string; fn __mcp_esc s = (s = substituteString s BS (BS+BS); s = substituteString s DQ (BS+DQ); s = substituteString s \"\\n\" (BS+\"n\"); s = substituteString s \"\\r\" \"\"; s = substituteString s \"\\t\" (BS+\"t\"); s)\n";
        ms += "  fn findWires sa path depth = (\n";
        ms += "    if depth <= 0 do return()\n";
        ms += "    for i = 1 to sa.numsubs do (\n";
        ms += "      local child = sa[i]\n";
        ms += "      local childPath = path + \"[#\" + (sa[i].name as string) + \"]\"\n";
        ms += "      local ctrl = child.controller\n";
        ms += "      if ctrl != undefined and (matchPattern ((classOf ctrl) as string) pattern:\"*Wire*\") do (\n";
        ms += "        local numW = try (ctrl.numWires) catch (0)\n";
        ms += "        append results #(childPath, (classOf ctrl) as string, numW)\n";
        ms += "      )\n";
        ms += "      findWires child childPath (depth - 1)\n";
        ms += "    )\n";
        ms += "  )\n";
        ms += "  findWires obj \"\" 5\n";
        ms += "  local r = \"[\"\n";
        ms += "  for i = 1 to results.count do (\n";
        ms += "    if i > 1 do r += \",\"\n";
        ms += "    r += \"{\\\"param_path\\\":\\\"\" + __mcp_esc results[i][1] + \"\\\",\"\n";
        ms += "    r += \"\\\"controller_class\\\":\\\"\" + __mcp_esc results[i][2] + \"\\\",\"\n";
        ms += "    r += \"\\\"num_wires\\\":\" + (results[i][3] as string) + \"}\"\n";
        ms += "  )\n";
        ms += "  r += \"]\"\n";
        ms += "  r\n";
        ms += ")\n";

        return RunMAXScript(ms);
    });
}

// ── native:unwire_params ────────────────────────────────────────
std::string NativeHandlers::UnwireParams(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&]() -> std::string {
        json p = json::parse(params);
        std::string name = p.value("object_name", p.value("name", ""));
        std::string paramPath = p.value("param_path", "");

        if (name.empty() || paramPath.empty())
            throw std::runtime_error("name and param_path are required");

        std::string normPath = NormalizeSubAnimPath(paramPath);

        std::string ms;
        ms += "(\n";
        ms += "  local obj = getNodeByName \"" + JsonEscape(name) + "\"\n";
        ms += "  if obj == undefined do throw \"Object not found\"\n";
        ms += "  local sa = execute (\"$'\" + obj.name + \"'\" + \"" + JsonEscape(normPath) + "\")\n";
        ms += "  if sa == undefined do throw \"Track not found\"\n";
        ms += "  paramWire.disconnect sa\n";
        ms += "  \"OK\"\n";
        ms += ")\n";

        RunMAXScript(ms);

        json result;
        result["status"] = "unwired";
        result["object"] = name;
        result["param_path"] = paramPath;
        return result.dump();
    });
}
