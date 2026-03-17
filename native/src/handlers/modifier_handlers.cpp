#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/bridge_gup.h"

#include <modstack.h>
#include <iparamb2.h>

using json = nlohmann::json;
using namespace HandlerHelpers;

// ── Helper: get IDerivedObject, creating one if needed ────────
static IDerivedObject* GetOrCreateDerivedObject(INode* node) {
    Object* objRef = node->GetObjectRef();
    if (objRef && objRef->SuperClassID() == GEN_DERIVOB_CLASS_ID) {
        return (IDerivedObject*)objRef;
    }
    // Create a derived object wrapper
    IDerivedObject* dobj = CreateDerivedObject(objRef);
    node->SetObjectRef(dobj);
    return dobj;
}

// ── Helper: find modifier by name on a node ──────────────────
static int FindModifierIndex(INode* node, const std::string& modName) {
    Object* objRef = node->GetObjectRef();
    if (!objRef || objRef->SuperClassID() != GEN_DERIVOB_CLASS_ID) return -1;
    IDerivedObject* dobj = (IDerivedObject*)objRef;
    std::wstring wname = Utf8ToWide(modName);
    for (int i = 0; i < dobj->NumModifiers(); i++) {
        Modifier* mod = dobj->GetModifier(i);
        if (mod && _wcsicmp(mod->GetName(false).data(), wname.c_str()) == 0) {
            return i;
        }
    }
    return -1;
}

static json ParseJsonOrRaw(const std::string& raw, const char* raw_key = "raw") {
    json parsed = json::parse(raw, nullptr, false);
    if (!parsed.is_discarded()) return parsed;
    json fallback;
    fallback[raw_key] = raw;
    return fallback;
}

// ── native:add_modifier (Pure SDK) ──────────────────────────
std::string NativeHandlers::AddModifier(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string name = p.value("name", "");
        std::string modClass = p.value("modifier", "");
        std::string modParams = p.value("params", "");

        if (name.empty()) throw std::runtime_error("name is required");
        if (modClass.empty()) throw std::runtime_error("modifier is required");

        INode* node = FindNodeByName(name);
        if (!node) throw std::runtime_error("Object not found: " + name);

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        // Find the modifier ClassDesc
        ClassDesc* cd = FindClassDescByName(modClass, OSM_CLASS_ID);
        if (!cd) cd = FindClassDescByName(modClass, WSM_CLASS_ID);
        if (!cd) cd = FindClassDescByName(modClass);
        if (!cd) throw std::runtime_error("Unknown modifier class: " + modClass);

        // Create modifier instance
        Modifier* mod = (Modifier*)ip->CreateInstance(cd->SuperClassID(), cd->ClassID());
        if (!mod) throw std::runtime_error("Failed to create modifier: " + modClass);

        // Set params via IParamBlock2 if provided
        if (!modParams.empty()) {
            // Reuse ParseParamString pattern from object_handlers
            // Simple key:value parsing
            size_t i = 0;
            while (i < modParams.size()) {
                while (i < modParams.size() && modParams[i] == ' ') i++;
                if (i >= modParams.size()) break;
                size_t keyStart = i;
                while (i < modParams.size() && modParams[i] != ':') i++;
                if (i >= modParams.size()) break;
                std::string key = modParams.substr(keyStart, i - keyStart);
                i++; // skip ':'
                size_t valStart = i;
                if (i < modParams.size() && modParams[i] == '[') {
                    int depth = 1; i++;
                    while (i < modParams.size() && depth > 0) {
                        if (modParams[i] == '[') depth++;
                        else if (modParams[i] == ']') depth--;
                        i++;
                    }
                } else if (i < modParams.size() && modParams[i] == '"') {
                    i++;
                    while (i < modParams.size() && modParams[i] != '"') {
                        if (modParams[i] == '\\') i++;
                        i++;
                    }
                    if (i < modParams.size()) i++;
                } else {
                    while (i < modParams.size() && modParams[i] != ' ') i++;
                }
                std::string val = modParams.substr(valStart, i - valStart);
                SetParamByName(mod, key, val, t);
            }
        }

        // Add modifier to node
        GetOrCreateDerivedObject(node);
        Object* objRef = node->GetObjectRef();
        if (objRef && objRef->SuperClassID() == GEN_DERIVOB_CLASS_ID) {
            IDerivedObject* dobj = (IDerivedObject*)objRef;
            dobj->AddModifier(mod);
        }

        ip->RedrawViews(t);
        return "Added " + WideToUtf8(mod->ClassName().data()) + " to " + WideToUtf8(node->GetName());
    });
}

// ── native:add_modifier_verified (composed native workflow) ──
std::string NativeHandlers::AddModifierVerified(const std::string& params, MCPBridgeGUP* gup) {
    json p = json::parse(params, nullptr, false);
    std::string name = p.value("name", "");
    std::string modifierHint = p.value("modifier", "");

    if (name.empty()) throw std::runtime_error("name is required");
    if (modifierHint.empty()) throw std::runtime_error("modifier is required");

    std::string addRaw = NativeHandlers::AddModifier(params, gup);
    std::string objectRaw = NativeHandlers::InspectObject(json{{"name", name}}.dump(), gup);
    json objectJson = ParseJsonOrRaw(objectRaw);

    std::string hintLower = modifierHint;
    std::transform(hintLower.begin(), hintLower.end(), hintLower.begin(), ::tolower);

    int modifierIndex = 0;
    if (objectJson.contains("modifiers") && (objectJson["modifiers"]).type() == json::value_t::array) {
        const json& mods = objectJson["modifiers"];
        if (!mods.empty()) modifierIndex = 1;
        for (size_t i = 0; i < mods.size(); ++i) {
            if (!mods[i].is_object()) continue;
            std::string modClass = mods[i].value("class", "");
            std::string modName = mods[i].value("name", "");
            std::transform(modClass.begin(), modClass.end(), modClass.begin(), ::tolower);
            std::transform(modName.begin(), modName.end(), modName.begin(), ::tolower);
            if (modClass == hintLower || modName == hintLower || modName.find(hintLower) != std::string::npos) {
                modifierIndex = static_cast<int>(i) + 1;
                break;
            }
        }
    }

    json modifierJson = nullptr;
    if (modifierIndex > 0) {
        std::string modifierRaw = NativeHandlers::InspectProperties(
            json{
                {"name", name},
                {"target", "modifier"},
                {"modifier_index", modifierIndex},
            }.dump(),
            gup
        );
        modifierJson = ParseJsonOrRaw(modifierRaw);
    }

    json result;
    result["addResult"] = addRaw;
    result["delta"] = {
        {"nativeWorkflow", true},
        {"captured", false},
        {"reason", "Scene delta is skipped in the native verified modifier workflow."},
    };
    result["object"] = objectJson;
    result["modifier"] = modifierJson;
    return result.dump();
}

// ── native:remove_modifier (Pure SDK) ───────────────────────
std::string NativeHandlers::RemoveModifier(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string name = p.value("name", "");
        std::string modName = p.value("modifier", "");

        if (name.empty()) throw std::runtime_error("name is required");
        if (modName.empty()) throw std::runtime_error("modifier name is required");

        INode* node = FindNodeByName(name);
        if (!node) throw std::runtime_error("Object not found: " + name);

        Object* objRef = node->GetObjectRef();
        if (!objRef || objRef->SuperClassID() != GEN_DERIVOB_CLASS_ID) {
            throw std::runtime_error("No modifiers on " + name);
        }

        IDerivedObject* dobj = (IDerivedObject*)objRef;
        int idx = FindModifierIndex(node, modName);
        if (idx < 0) {
            throw std::runtime_error("Modifier \"" + modName + "\" not found on " + name);
        }

        dobj->DeleteModifier(idx);
        GetCOREInterface()->RedrawViews(GetCOREInterface()->GetTime());
        return "Removed modifier \"" + modName + "\" from " + WideToUtf8(node->GetName());
    });
}

// ── native:set_modifier_state (Pure SDK) ────────────────────
std::string NativeHandlers::SetModifierState(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string name = p.value("name", "");
        std::string modName = p.value("modifier_name", "");
        int modIndex = p.value("modifier_index", 0);

        if (name.empty()) throw std::runtime_error("name is required");

        INode* node = FindNodeByName(name);
        if (!node) throw std::runtime_error("Object not found: " + name);

        Object* objRef = node->GetObjectRef();
        if (!objRef || objRef->SuperClassID() != GEN_DERIVOB_CLASS_ID) {
            throw std::runtime_error("No modifiers on " + name);
        }

        IDerivedObject* dobj = (IDerivedObject*)objRef;
        int idx = -1;

        if (modIndex > 0) {
            // 1-based index from user → 0-based SDK index
            idx = modIndex - 1;
            if (idx >= dobj->NumModifiers()) {
                throw std::runtime_error("Modifier index " + std::to_string(modIndex) +
                    " out of range (has " + std::to_string(dobj->NumModifiers()) + ")");
            }
        } else if (!modName.empty()) {
            idx = FindModifierIndex(node, modName);
            if (idx < 0) {
                throw std::runtime_error("Modifier \"" + modName + "\" not found on " + name);
            }
        } else {
            throw std::runtime_error("Either modifier_name or modifier_index is required");
        }

        Modifier* mod = dobj->GetModifier(idx);
        if (!mod) throw std::runtime_error("Could not get modifier at index");

        bool changed = false;

        if (p.contains("enabled") && !p["enabled"].is_null()) {
            bool val = p["enabled"].get<bool>();
            if (val) mod->EnableMod();
            else mod->DisableMod();
            changed = true;
        }

        if (p.contains("enabled_in_views") && !p["enabled_in_views"].is_null()) {
            bool val = p["enabled_in_views"].get<bool>();
            if (val) mod->EnableModInViews();
            else mod->DisableModInViews();
            changed = true;
        }

        if (p.contains("enabled_in_renders") && !p["enabled_in_renders"].is_null()) {
            bool val = p["enabled_in_renders"].get<bool>();
            if (val) mod->EnableModInRender();
            else mod->DisableModInRender();
            changed = true;
        }

        if (!changed) return "No state changes specified.";

        GetCOREInterface()->RedrawViews(GetCOREInterface()->GetTime());

        std::string modDisplayName = WideToUtf8(mod->GetName(false).data());
        return "Set state on " + modDisplayName + " (" + WideToUtf8(node->GetName()) + "): " +
               "enabled=" + (mod->IsEnabled() ? "true" : "false") +
               " views=" + (mod->IsEnabledInViews() ? "true" : "false") +
               " renders=" + (mod->IsEnabledInRender() ? "true" : "false");
    });
}

// ── native:collapse_modifier_stack (Pure SDK) ───────────────
std::string NativeHandlers::CollapseModifierStack(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string name = p.value("name", "");
        int toIndex = p.value("to_index", 0);

        if (name.empty()) throw std::runtime_error("name is required");

        INode* node = FindNodeByName(name);
        if (!node) throw std::runtime_error("Object not found: " + name);

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        Object* objRef = node->GetObjectRef();
        if (!objRef || objRef->SuperClassID() != GEN_DERIVOB_CLASS_ID) {
            return "No modifier stack to collapse on " + name;
        }

        IDerivedObject* dobj = (IDerivedObject*)objRef;
        int numMods = dobj->NumModifiers();

        if (toIndex > 0) {
            // Collapse to specific index (1-based)
            if (toIndex > numMods) {
                throw std::runtime_error("Index " + std::to_string(toIndex) +
                    " out of range (stack has " + std::to_string(numMods) + " modifiers)");
            }
            // Evaluate pipeline at that modifier and replace
            // SDK approach: delete modifiers above the target index
            // In Max SDK, modifier 0 is the top of stack
            // toIndex=1 means keep only the topmost, collapse rest
            // Actually, CollapseNodeTo in MAXScript does: evaluate pipeline down to that modifier
            // Pure SDK: we evaluate the world state, convert to mesh, replace object ref
            // For partial collapse, use RunMAXScript as it maps to maxOps.CollapseNodeTo
            std::string script = "maxOps.CollapseNodeTo (getNodeByName \"" +
                JsonEscape(name) + "\") " + std::to_string(toIndex) + " off";
            RunMAXScript(script);
            ip->RedrawViews(t);
            return "Collapsed " + WideToUtf8(node->GetName()) + " to modifier index " + std::to_string(toIndex);
        }

        // Full collapse: evaluate pipeline, get final object, replace
        ObjectState os = node->EvalWorldState(t);
        if (!os.obj) throw std::runtime_error("Failed to evaluate object");

        // Convert to editable mesh
        if (os.obj->CanConvertToType(triObjectClassID)) {
            TriObject* tri = (TriObject*)os.obj->ConvertToType(t, triObjectClassID);
            if (tri && tri != os.obj) {
                node->SetObjectRef(tri);
            } else if (tri == os.obj) {
                // Already a tri, but need to strip the derived object wrapper
                // Use the simple approach
                std::string script = "maxOps.CollapseNode (getNodeByName \"" +
                    JsonEscape(name) + "\") off";
                RunMAXScript(script);
            }
        } else {
            // Fallback for non-mesh objects
            std::string script = "maxOps.CollapseNode (getNodeByName \"" +
                JsonEscape(name) + "\") off";
            RunMAXScript(script);
        }

        ip->RedrawViews(t);

        // Get resulting class
        ObjectState finalOs = node->EvalWorldState(t);
        std::string resultClass = finalOs.obj ? WideToUtf8(finalOs.obj->ClassName().data()) : "Unknown";
        return "Collapsed entire stack on " + WideToUtf8(node->GetName()) + " — now: " + resultClass;
    });
}

// ── native:make_modifier_unique (Pure SDK) ──────────────────
std::string NativeHandlers::MakeModifierUnique(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string name = p.value("name", "");
        int modIndex = p.value("modifier_index", 0);

        if (name.empty()) throw std::runtime_error("name is required");
        if (modIndex <= 0) throw std::runtime_error("modifier_index (1-based) is required");

        INode* node = FindNodeByName(name);
        if (!node) throw std::runtime_error("Object not found: " + name);

        Object* objRef = node->GetObjectRef();
        if (!objRef || objRef->SuperClassID() != GEN_DERIVOB_CLASS_ID) {
            throw std::runtime_error("No modifiers on " + name);
        }

        IDerivedObject* dobj = (IDerivedObject*)objRef;
        int idx = modIndex - 1; // 0-based
        if (idx >= dobj->NumModifiers()) {
            throw std::runtime_error("Index " + std::to_string(modIndex) + " out of range");
        }

        Modifier* mod = dobj->GetModifier(idx);
        if (!mod) throw std::runtime_error("Could not get modifier at index");

        std::string modDisplayName = WideToUtf8(mod->GetName(false).data());

        // Clone the modifier and replace
        RemapDir* remap = NewRemapDir();
        Modifier* cloned = (Modifier*)mod->Clone(*remap);
        remap->DeleteThis();

        if (!cloned) throw std::runtime_error("Failed to clone modifier");

        // Replace: delete old, add cloned at same position
        dobj->DeleteModifier(idx);
        dobj->AddModifier(cloned, nullptr, idx);

        GetCOREInterface()->RedrawViews(GetCOREInterface()->GetTime());
        return "Made modifier " + modDisplayName + " unique on " + WideToUtf8(node->GetName());
    });
}

// ── native:batch_modify (Pure SDK) ──────────────────────────
std::string NativeHandlers::BatchModify(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string modClassName = p.value("modifier_class", "");
        std::string propName = p.value("property_name", "");
        std::string propValue = p.value("property_value", "");
        auto names = p.value("names", std::vector<std::string>{});
        bool selectionOnly = p.value("selection_only", false);

        if (modClassName.empty()) throw std::runtime_error("modifier_class is required");
        if (propName.empty()) throw std::runtime_error("property_name is required");

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        // Collect target nodes
        std::vector<INode*> targets;
        if (!names.empty()) {
            for (const auto& n : names) {
                INode* node = FindNodeByName(n);
                if (node) targets.push_back(node);
            }
        } else if (selectionOnly) {
            int selCount = ip->GetSelNodeCount();
            for (int i = 0; i < selCount; i++) {
                targets.push_back(ip->GetSelNode(i));
            }
        } else {
            INode* root = ip->GetRootNode();
            CollectNodes(root, targets);
        }

        // Find target modifier class name for comparison
        std::wstring wModClass = Utf8ToWide(modClassName);
        int modCount = 0;

        ip->DisableSceneRedraw();

        for (INode* node : targets) {
            Object* objRef = node->GetObjectRef();
            if (!objRef || objRef->SuperClassID() != GEN_DERIVOB_CLASS_ID) continue;

            IDerivedObject* dobj = (IDerivedObject*)objRef;
            for (int m = 0; m < dobj->NumModifiers(); m++) {
                Modifier* mod = dobj->GetModifier(m);
                if (!mod) continue;

                // Compare class name
                const MCHAR* cn = mod->ClassName().data();
                if (_wcsicmp(cn, wModClass.c_str()) != 0) continue;

                // Set the property via IParamBlock2
                if (SetParamByName(mod, propName, propValue, t)) {
                    mod->NotifyDependents(FOREVER, PART_ALL, REFMSG_CHANGE);
                    modCount++;
                }
            }
        }

        ip->EnableSceneRedraw();
        ip->RedrawViews(t);

        return "Modified " + std::to_string(modCount) + " " + modClassName +
               " modifiers: " + propName + " = " + propValue;
    });
}
