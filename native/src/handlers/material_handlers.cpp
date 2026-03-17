#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/bridge_gup.h"

#include <iparamb2.h>

using json = nlohmann::json;
using namespace HandlerHelpers;

// ── Helper: navigate to target material (with sub-material support) ──
static Mtl* GetTargetMaterial(INode* node, int subMatIndex) {
    Mtl* mtl = node->GetMtl();
    if (!mtl) return nullptr;
    if (subMatIndex <= 0) return mtl;
    int idx = subMatIndex - 1;
    if (idx >= mtl->NumSubMtls()) return nullptr;
    return mtl->GetSubMtl(idx);
}

// ── Helper: parse MAXScript-style key:value param string ────
static std::vector<std::pair<std::string, std::string>> ParseMtlParams(const std::string& s) {
    std::vector<std::pair<std::string, std::string>> result;
    size_t i = 0;
    while (i < s.size()) {
        while (i < s.size() && s[i] == ' ') i++;
        if (i >= s.size()) break;
        size_t keyStart = i;
        while (i < s.size() && s[i] != ':') i++;
        if (i >= s.size()) break;
        std::string key = s.substr(keyStart, i - keyStart);
        i++;
        size_t valStart = i;
        if (i < s.size() && (s[i] == '[' || s[i] == '(')) {
            char open = s[i], close = (open == '[') ? ']' : ')';
            int depth = 1; i++;
            while (i < s.size() && depth > 0) {
                if (s[i] == open) depth++;
                else if (s[i] == close) depth--;
                i++;
            }
        } else if (i < s.size() && s[i] == '"') {
            i++;
            while (i < s.size() && s[i] != '"') { if (s[i] == '\\') i++; i++; }
            if (i < s.size()) i++;
        } else {
            while (i < s.size() && s[i] != ' ') i++;
        }
        result.push_back({key, s.substr(valStart, i - valStart)});
    }
    return result;
}

static json ParseJsonOrRaw(const std::string& raw, const char* raw_key = "raw") {
    json parsed = json::parse(raw, nullptr, false);
    if (!parsed.is_discarded()) return parsed;
    json fallback;
    fallback[raw_key] = raw;
    return fallback;
}

// ── native:assign_material (Pure SDK) ───────────────────────
std::string NativeHandlers::AssignMaterial(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        auto names = p.value("names", std::vector<std::string>{});
        std::string matClass = p.value("material_class", "");
        std::string matName = p.value("material_name", "");
        std::string matParams = p.value("params", "");

        if (names.empty()) throw std::runtime_error("names is required");
        if (matClass.empty()) throw std::runtime_error("material_class is required");

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        // Find material ClassDesc — try SDK DllDir first
        ClassDesc* cd = FindClassDescByName(matClass, MATERIAL_CLASS_ID);
        if (!cd) cd = FindClassDescByName(matClass);

        Mtl* mtl = nullptr;

        if (cd) {
            // Pure SDK path
            mtl = (Mtl*)ip->CreateInstance(cd->SuperClassID(), cd->ClassID());
        }

        if (!mtl) {
            // Fallback: some plugins (Arnold, scripted materials) don't register
            // in DllDir under their MAXScript class name. Create via MAXScript.
            std::string nameParam = matName.empty() ? "" : " name:\"" + JsonEscape(matName) + "\"";
            std::string script = "(" + matClass + nameParam + " " + matParams + ")";
            try {
                RunMAXScript("global __mcp_tmp_mtl = " + script);
                // Now assign via MAXScript too since we can't get the Mtl* back easily
                int assignCount = 0;
                json notFound = json::array();
                for (const auto& name : names) {
                    INode* node = FindNodeByName(name);
                    if (node) {
                        std::string assignScript = "(getNodeByName \"" + JsonEscape(name) +
                            "\").material = __mcp_tmp_mtl";
                        RunMAXScript(assignScript);
                        assignCount++;
                    } else {
                        notFound.push_back(name);
                    }
                }
                // Get the material name back
                std::string mtlName = RunMAXScript("__mcp_tmp_mtl.name");
                std::string mtlClass = RunMAXScript("(classOf __mcp_tmp_mtl) as string");
                ip->RedrawViews(t);

                std::string msg = "Created " + mtlClass + " \"" + mtlName +
                    "\" and assigned to " + std::to_string(assignCount) + " object(s)";
                if (!notFound.empty())
                    msg += " | Not found: " + std::to_string(notFound.size());
                return msg;
            } catch (...) {
                throw std::runtime_error("Unknown material class: " + matClass);
            }
        }

        // Pure SDK path continues — set name
        if (!matName.empty()) {
            std::wstring wname = Utf8ToWide(matName);
            mtl->SetName(wname.c_str());
        }

        // Set params via IParamBlock2
        if (!matParams.empty()) {
            auto kvPairs = ParseMtlParams(matParams);
            for (auto& [key, val] : kvPairs) {
                SetParamByName((Animatable*)mtl, key, val, t);
            }
        }

        // Assign to nodes
        int assignCount = 0;
        json notFound = json::array();
        for (const auto& name : names) {
            INode* node = FindNodeByName(name);
            if (node) {
                node->SetMtl(mtl);
                assignCount++;
            } else {
                notFound.push_back(name);
            }
        }

        mtl->NotifyDependents(FOREVER, PART_ALL, REFMSG_CHANGE);
        ip->RedrawViews(t);

        std::string msg = "Created " + WideToUtf8(mtl->ClassName().data()) + " \"" +
                          WideToUtf8(mtl->GetName().data()) + "\" and assigned to " +
                          std::to_string(assignCount) + " object(s)";
        if (!notFound.empty())
            msg += " | Not found: " + std::to_string(notFound.size());
        return msg;
    });
}

// ── native:set_material_property (Pure SDK + minimal fallback) ──
std::string NativeHandlers::SetMaterialProperty(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string name = p.value("name", "");
        std::string prop = p.value("property", "");
        std::string value = p.value("value", "");
        int subMatIndex = p.value("sub_material_index", 0);

        if (name.empty()) throw std::runtime_error("name is required");
        if (prop.empty()) throw std::runtime_error("property is required");

        INode* node = FindNodeByName(name);
        if (!node) throw std::runtime_error("Object not found: " + name);

        Mtl* mtl = GetTargetMaterial(node, subMatIndex);
        if (!mtl) {
            if (subMatIndex > 0)
                throw std::runtime_error("Sub-material index " + std::to_string(subMatIndex) + " not found");
            throw std::runtime_error("No material assigned to " + name);
        }

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        // Pure SDK path: try IParamBlock2 first
        if (SetParamByName((Animatable*)mtl, prop, value, t)) {
            mtl->NotifyDependents(FOREVER, PART_ALL, REFMSG_CHANGE);
            ip->RedrawViews(t);
            return "Set " + WideToUtf8(mtl->GetName().data()) + "." + prop;
        }

        // For texture map / material reference assignments that reference a MAXScript
        // global variable (e.g. value="FresnelGlow"), we must resolve the variable name
        // to a pointer. This is the ONE case where pure SDK can't work — MAXScript globals
        // have no SDK accessor. We use a single targeted RunMAXScript call.
        std::string matExpr;
        if (subMatIndex > 0)
            matExpr = "(getNodeByName \"" + JsonEscape(name) + "\").material[" + std::to_string(subMatIndex) + "]";
        else
            matExpr = "(getNodeByName \"" + JsonEscape(name) + "\").material";

        std::string script = "try (" + matExpr + "." + prop + " = " + value +
                             "; \"Set " + JsonEscape(prop) + "\") catch (\"Error: \" + getCurrentException())";
        std::string result = RunMAXScript(script);
        ip->RedrawViews(t);
        return result;
    });
}

// ── native:set_material_properties (Pure SDK + minimal fallback) ──
std::string NativeHandlers::SetMaterialProperties(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string name = p.value("name", "");
        auto properties = p.value("properties", std::map<std::string, std::string>{});
        int subMatIndex = p.value("sub_material_index", 0);

        if (name.empty()) throw std::runtime_error("name is required");
        if (properties.empty()) throw std::runtime_error("properties is required");

        INode* node = FindNodeByName(name);
        if (!node) throw std::runtime_error("Object not found: " + name);

        Mtl* mtl = GetTargetMaterial(node, subMatIndex);
        if (!mtl) {
            if (subMatIndex > 0)
                throw std::runtime_error("Sub-material index " + std::to_string(subMatIndex) + " not found");
            throw std::runtime_error("No material assigned to " + name);
        }

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        json okList = json::array();
        json errList = json::array();

        // Build MAXScript mat expression for fallback
        std::string matExpr;
        if (subMatIndex > 0)
            matExpr = "(getNodeByName \"" + JsonEscape(name) + "\").material[" + std::to_string(subMatIndex) + "]";
        else
            matExpr = "(getNodeByName \"" + JsonEscape(name) + "\").material";

        for (auto& [prop, value] : properties) {
            // Try pure SDK IParamBlock2 first
            if (SetParamByName((Animatable*)mtl, prop, value, t)) {
                okList.push_back(prop);
                continue;
            }

            // Fallback for texture map / reference assignments
            try {
                std::string script = matExpr + "." + prop + " = " + value;
                RunMAXScript(script);
                okList.push_back(prop);
            } catch (...) {
                errList.push_back(prop + ": failed to set");
            }
        }

        mtl->NotifyDependents(FOREVER, PART_ALL, REFMSG_CHANGE);
        ip->RedrawViews(t);

        std::string msg = "Set " + std::to_string(okList.size()) + " properties on " +
                          WideToUtf8(mtl->GetName().data());
        if (!okList.empty()) {
            msg += ":";
            for (size_t i = 0; i < okList.size(); i++)
                msg += (i > 0 ? ", " : " ") + okList[i].get<std::string>();
        }
        if (!errList.empty()) {
            msg += " | Errors:";
            for (size_t i = 0; i < errList.size(); i++)
                msg += (i > 0 ? "; " : " ") + errList[i].get<std::string>();
        }
        return msg;
    });
}

// ── native:set_material_verified (composed native workflow) ──
std::string NativeHandlers::SetMaterialVerified(const std::string& params, MCPBridgeGUP* gup) {
    json p = json::parse(params, nullptr, false);
    std::string name = p.value("name", "");
    auto properties = p.value("properties", std::map<std::string, std::string>{});
    int subMatIndex = p.value("sub_material_index", 0);

    if (name.empty()) throw std::runtime_error("name is required");
    if (properties.empty()) throw std::runtime_error("properties is required");

    json slotReq = {
        {"name", name},
        {"sub_material_index", subMatIndex},
        {"slot_scope", "all"},
        {"include_values", true},
        {"max_per_group", 50},
    };

    std::string beforeRaw = NativeHandlers::GetMaterialSlots(slotReq.dump(), gup);
    std::string setRaw = NativeHandlers::SetMaterialProperties(params, gup);
    std::string afterRaw = NativeHandlers::GetMaterialSlots(slotReq.dump(), gup);
    std::string objectRaw = NativeHandlers::InspectObject(json{{"name", name}}.dump(), gup);

    json beforeSlots = ParseJsonOrRaw(beforeRaw);
    json afterSlots = ParseJsonOrRaw(afterRaw);
    json objectJson = ParseJsonOrRaw(objectRaw);

    auto collectSlots = [](const json& payload) {
        std::map<std::string, std::string> values;
        static const char* keys[] = {
            "mapSlots",
            "colorSlots",
            "numericSlots",
            "boolSlots",
            "otherSlots",
        };
        for (const char* key : keys) {
            if (!payload.contains(key) || (payload[key]).type() != json::value_t::array) continue;
            for (const auto& item : payload[key]) {
                if ((item).type() != json::value_t::object || !item.contains("name")) continue;
                std::string slotName = item.value("name", "");
                std::string slotValue = item.contains("value") && (item["value"]).type() != json::value_t::null
                    ? item["value"].dump()
                    : std::string("null");
                if (item.contains("value") && (item["value"]).type() == json::value_t::string) {
                    slotValue = item["value"].get<std::string>();
                }
                values[slotName] = slotValue;
            }
        }
        return values;
    };

    auto beforeMap = collectSlots(beforeSlots);
    auto afterMap = collectSlots(afterSlots);

    json slotChanges = json::object();
    for (const auto& [prop, _] : properties) {
        json change;
        auto beforeIt = beforeMap.find(prop);
        auto afterIt = afterMap.find(prop);
        change["before"] = beforeIt != beforeMap.end() ? json(beforeIt->second) : json(nullptr);
        change["after"] = afterIt != afterMap.end() ? json(afterIt->second) : json(nullptr);
        slotChanges[prop] = change;
    }

    json result;
    result["setResult"] = setRaw;
    result["delta"] = {
        {"nativeWorkflow", true},
        {"captured", false},
        {"reason", "Scene delta is skipped in the native verified material workflow."},
    };
    result["object"] = objectJson;
    result["slotChanges"] = slotChanges;
    result["materialSlotsBefore"] = beforeSlots;
    result["materialSlots"] = afterSlots;
    return result.dump();
}
