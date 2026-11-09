#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/core_bridge.h"
#include <sfx.h>

using json = nlohmann::json;
using namespace HandlerHelpers;

// The effect's own name (SpecialFX::name, MAXScript .name), else the plug-in's
// name: GetName() names the plug-in ("Blur"), not this effect.
static std::string EffectInstanceName(SpecialFX* fx) {
    std::string name = WideToUtf8(fx->name.data());
    return name.empty() ? WideToUtf8(fx->GetName(false).data()) : name;
}

// ── native:get_effects ──────────────────────────────────────────
std::string NativeHandlers::GetEffects(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&]() -> std::string {
        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        json atmospherics = json::array();
        int numAtmos = ip->NumAtmospheric();
        for (int i = 0; i < numAtmos; i++) {
            Atmospheric* atmos = ip->GetAtmospheric(i);
            if (!atmos) continue;

            json entry;
            entry["index"] = i + 1; // 1-based for MAXScript compat
            entry["name"] = EffectInstanceName(atmos);
            entry["class"] = WideToUtf8(atmos->ClassName().data());
            entry["active"] = atmos->Active(t) ? true : false;

            // Dependent nodes (gizmos)
            json nodes = json::array();
            int numGiz = atmos->NumGizmos();
            for (int g = 0; g < numGiz; g++) {
                INode* gizNode = atmos->GetGizmo(g);
                if (gizNode) {
                    nodes.push_back(WideToUtf8(gizNode->GetName()));
                }
            }
            entry["dependentNodes"] = nodes;
            atmospherics.push_back(entry);
        }

        json renderEffects = json::array();
        int numFx = ip->NumEffects();
        for (int i = 0; i < numFx; i++) {
            Effect* fx = ip->GetEffect(i);
            if (!fx) continue;

            json entry;
            entry["index"] = i + 1;
            entry["name"] = EffectInstanceName(fx);
            entry["class"] = WideToUtf8(fx->ClassName().data());
            entry["active"] = fx->Active(t) ? true : false;
            renderEffects.push_back(entry);
        }

        json result;
        result["atmospherics"] = atmospherics;
        result["renderEffects"] = renderEffects;
        return result.dump();
    });
}

// ── native:toggle_effect ────────────────────────────────────────
std::string NativeHandlers::ToggleEffect(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&]() -> std::string {
        json p = json::parse(params);
        int index = p.value("index", 0);
        std::string effectType = p.value("effect_type", "atmospheric");
        bool active = p.value("active", true);
        if (effectType != "atmospheric" && effectType != "render")
            throw std::runtime_error("effect_type must be \"atmospheric\" or \"render\", got \"" + effectType + "\"");

        Interface* ip = GetCOREInterface();

        // setActive is a MAXScript-only function — no direct SDK equivalent
        std::string msIndex = std::to_string(index);
        std::string msActive = active ? "true" : "false";
        std::string script;

        if (effectType == "atmospheric") {
            int count = ip->NumAtmospheric();
            if (index < 1 || index > count)
                throw std::runtime_error("Atmospheric index " + msIndex + " out of range (1-" + std::to_string(count) + ")");
            script = "setActive (getAtmospheric " + msIndex + ") " + msActive;
        } else {
            int count = ip->NumEffects();
            if (index < 1 || index > count)
                throw std::runtime_error("Effect index " + msIndex + " out of range (1-" + std::to_string(count) + ")");
            script = "setActive (getEffect " + msIndex + ") " + msActive;
        }

        RunMAXScriptChecked(script);

        json result;
        result["status"] = "ok";
        result["effect_type"] = effectType;
        result["index"] = index;
        result["active"] = active;
        return result.dump();
    });
}

// ── native:delete_effect ────────────────────────────────────────
std::string NativeHandlers::DeleteEffect(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&]() -> std::string {
        json p = json::parse(params);
        int index = p.value("index", 0);
        std::string effectType = p.value("effect_type", "atmospheric");
        if (effectType != "atmospheric" && effectType != "render")
            throw std::runtime_error("effect_type must be \"atmospheric\" or \"render\", got \"" + effectType + "\"");

        Interface* ip = GetCOREInterface();

        std::string name;
        if (effectType == "atmospheric") {
            int count = ip->NumAtmospheric();
            if (index < 1 || index > count)
                throw std::runtime_error("Atmospheric index out of range");
            Atmospheric* atmos = ip->GetAtmospheric(index - 1);
            if (atmos) name = EffectInstanceName(atmos);
            ip->DeleteAtmosphere(index - 1);
        } else {
            int count = ip->NumEffects();
            if (index < 1 || index > count)
                throw std::runtime_error("Effect index out of range");
            Effect* fx = ip->GetEffect(index - 1);
            if (fx) name = EffectInstanceName(fx);
            ip->DeleteEffect(index - 1);
        }

        json result;
        result["status"] = "deleted";
        result["effect_type"] = effectType;
        result["index"] = index;
        result["name"] = name;
        return result.dump();
    });
}
