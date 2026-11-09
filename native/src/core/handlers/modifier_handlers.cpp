#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/spatial_snapshot.h"
#include "mcp_bridge/core_bridge.h"
#include "mcp_bridge/modifier_helpers.h"

#include <iInstanceMgr.h>
#include <modstack.h>
#include <iparamb2.h>
#include <memory>

using json = nlohmann::json;
using namespace HandlerHelpers;

static json ModifierStackJson(INode* node) {
    json stack = json::array();
    if (!node) return stack;
    Object* objRef = node->GetObjectRef();
    if (!objRef || objRef->SuperClassID() != GEN_DERIVOB_CLASS_ID) return stack;
    IDerivedObject* dobj = (IDerivedObject*)objRef;
    for (int i = 0; i < dobj->NumModifiers(); i++) {
        Modifier* mod = dobj->GetModifier(i);
        if (!mod) continue;
        stack.push_back({
            {"index", i + 1},
            {"name", WideToUtf8(mod->GetName(false).data())},
            {"class", WideToUtf8(mod->ClassName().data())},
            {"enabled", mod->IsEnabled() != 0},
            {"enabledInViews", mod->IsEnabledInViews() != 0},
            {"enabledInRenders", mod->IsEnabledInRender() != 0},
        });
    }
    return stack;
}

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

// ── native:add_modifier (Pure SDK) ──────────────────────────
std::string NativeHandlers::AddModifier(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string name = p.value("name", "");
        std::string modClass = p.value("modifier", "");
        std::string modParams = p.value("params", "");

        if (modClass.empty()) throw std::runtime_error("modifier is required");

        INode* node = ResolveNodeFromPayload(p);

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        Modifier* mod = ModifierHelpers::CreateModifier(modClass);
        const auto deleteUnused = [](Modifier* value) { if (value) value->DeleteThis(); };
        std::unique_ptr<Modifier, decltype(deleteUnused)> unusedModifier(mod, deleteUnused);

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
                if (i >= modParams.size()) throw std::runtime_error("Expected modifier parameter key:value");
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
                if (!ModifierHelpers::SetParameter(mod, key, val, t))
                    throw std::runtime_error("Unsupported modifier parameter or value: " + key);
            }
        }

        // Add modifier to node
        GetOrCreateDerivedObject(node);
        Object* objRef = node->GetObjectRef();
        if (objRef && objRef->SuperClassID() == GEN_DERIVOB_CLASS_ID) {
            IDerivedObject* dobj = (IDerivedObject*)objRef;
            unusedModifier.release(); // The stack/reference system takes ownership.
            dobj->AddModifier(mod);
        }

        ip->RedrawViews(t);
        json result = NodeIdentityJson(node);
        result["message"] = "Added " + WideToUtf8(mod->ClassName().data()) + " to " + WideToUtf8(node->GetName());
        result["addedModifier"] = {
            {"name", WideToUtf8(mod->GetName(false).data())},
            {"class", WideToUtf8(mod->ClassName().data())},
        };
        result["stack"] = ModifierStackJson(node);
        return result.dump();
    });
}

// ── native:remove_modifier (Pure SDK) ───────────────────────
std::string NativeHandlers::RemoveModifier(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string name = p.value("name", "");
        std::string modName = p.value("modifier", "");

        if (modName.empty()) throw std::runtime_error("modifier name is required");

        INode* node = ResolveNodeFromPayload(p);

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
        json result = NodeIdentityJson(node);
        result["message"] = "Removed modifier \"" + modName + "\" from " + WideToUtf8(node->GetName());
        result["removedModifier"] = modName;
        result["stack"] = ModifierStackJson(node);
        return result.dump();
    });
}

// ── native:set_modifier_state (Pure SDK) ────────────────────
std::string NativeHandlers::SetModifierState(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string name = p.value("name", "");
        std::string modName = p.value("modifier_name", "");
        int modIndex = p.value("modifier_index", 0);

        INode* node = ResolveNodeFromPayload(p);

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

        if (!changed)
            throw std::runtime_error(
                "No state changes specified: pass enabled, enabled_in_views or enabled_in_renders.");

        GetCOREInterface()->RedrawViews(GetCOREInterface()->GetTime());

        std::string modDisplayName = WideToUtf8(mod->GetName(false).data());
        json result = NodeIdentityJson(node);
        result["message"] = "Set state on " + modDisplayName + " (" + WideToUtf8(node->GetName()) + ")";
        result["modifier"] = {
            {"name", modDisplayName},
            {"index", idx + 1},
            {"class", WideToUtf8(mod->ClassName().data())},
            {"enabled", mod->IsEnabled() != 0},
            {"enabledInViews", mod->IsEnabledInViews() != 0},
            {"enabledInRenders", mod->IsEnabledInRender() != 0},
        };
        result["stack"] = ModifierStackJson(node);
        return result.dump();
    });
}

// ── native:collapse_modifier_stack (Pure SDK) ───────────────
std::string NativeHandlers::CollapseModifierStack(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string name = p.value("name", "");
        int toIndex = p.value("to_index", 0);
        bool dryRun = p.value("dry_run", false);

        INode* node = ResolveNodeFromPayload(p);
        name = WideToUtf8(node->GetName());

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        Object* objRef = node->GetObjectRef();
        if (!objRef || objRef->SuperClassID() != GEN_DERIVOB_CLASS_ID) {
            json result = NodeIdentityJson(node);
            result["status"] = "no_stack";
            result["message"] = "No modifier stack to collapse on " + name;
            result["stack"] = json::array();
            result["warnings"] = json::array({name + " has no modifiers, so nothing was collapsed."});
            return result.dump();
        }

        IDerivedObject* dobj = (IDerivedObject*)objRef;
        int numMods = dobj->NumModifiers();
        json beforeStack = ModifierStackJson(node);

        if (dryRun) {
            json result = NodeIdentityJson(node);
            result["dry_run"] = true;
            result["to_index"] = toIndex;
            result["stack"] = beforeStack;
            result["message"] = toIndex > 0
                ? "Would collapse " + name + " to modifier index " + std::to_string(toIndex)
                : "Would collapse entire stack on " + name;
            return result.dump();
        }

        // The node already resolved, by handle: never look it up again by name. noWarning
        // is true so Max does not stop on a prompt during the call.
        const std::string collapseTarget = "(maxOps.getNodeByHandle " + std::to_string(node->GetHandle()) + ")";

        if (toIndex > 0) {
            // Collapse to specific index (1-based)
            if (toIndex > numMods) {
                throw std::runtime_error("Index " + std::to_string(toIndex) +
                    " out of range (stack has " + std::to_string(numMods) + " modifiers)");
            }
            if (RunMAXScriptChecked("(maxOps.CollapseNodeTo " + collapseTarget + " " +
                    std::to_string(toIndex) + " true) as string") != "true")
                throw std::runtime_error("3ds Max could not collapse " + name + " to modifier index " +
                    std::to_string(toIndex));
            ip->RedrawViews(t);
            json result = NodeIdentityJson(node);
            result["message"] = "Collapsed " + WideToUtf8(node->GetName()) + " to modifier index " + std::to_string(toIndex);
            result["to_index"] = toIndex;
            result["beforeStack"] = beforeStack;
            result["stack"] = ModifierStackJson(node);
            return result.dump();
        }

        // Full collapse. The result keeps the type the stack evaluates to:
        // an Edit Poly stack stays Editable Poly instead of becoming a mesh.
        // CollapseNode returns OK, not its documented boolean: read the stack back.
        RunMAXScriptChecked("maxOps.CollapseNode " + collapseTarget + " true");
        Object* collapsed = node->GetObjectRef();
        if (collapsed && collapsed->SuperClassID() == GEN_DERIVOB_CLASS_ID &&
            static_cast<IDerivedObject*>(collapsed)->NumModifiers() > 0)
            throw std::runtime_error("3ds Max could not collapse the stack on " + name);

        ip->RedrawViews(t);

        // Get resulting class
        ObjectState finalOs = node->EvalWorldState(t);
        std::string resultClass = finalOs.obj ? WideToUtf8(finalOs.obj->ClassName().data()) : "Unknown";
        json result = NodeIdentityJson(node);
        result["message"] = "Collapsed entire stack on " + WideToUtf8(node->GetName());
        result["resultClass"] = resultClass;
        result["beforeStack"] = beforeStack;
        result["stack"] = ModifierStackJson(node);
        result["spatial"] = SpatialSnapshot::BuildSpatialSnapshot(node, t);
        return result.dump();
    });
}

// ── native:make_modifier_unique (Pure SDK) ──────────────────
std::string NativeHandlers::MakeModifierUnique(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string name = p.value("name", "");
        int modIndex = p.value("modifier_index", 0);

        if (modIndex <= 0) throw std::runtime_error("modifier_index (1-based) is required");

        INode* node = ResolveNodeFromPayload(p);

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

        // Max's own Make Unique: it knows which stacks share the modifier and
        // keeps the slot's ModContext (gizmo, sub-object selection).
        INodeTab nodes;
        nodes.AppendNode(node);
        Tab<ReferenceTarget*> mods;
        ReferenceTarget* target = mod;
        mods.Append(1, &target);
        IInstanceMgr* instances = IInstanceMgr::GetInstanceMgr();
        if (!instances->CanMakeModifiersUnique(nodes, mods)) {
            json result = NodeIdentityJson(node);
            result["status"] = "already_unique";
            result["message"] = "Modifier " + modDisplayName + " on " + WideToUtf8(node->GetName()) +
                " is not instanced; nothing was changed";
            result["modifier"] = {{"name", modDisplayName}, {"index", modIndex}};
            result["warnings"] = json::array({result["message"]});
            result["stack"] = ModifierStackJson(node);
            return result.dump();
        }
        instances->MakeModifiersUnique(nodes, mods, INSTANCE_MGR_MAKE_UNIQUE_INDIVIDUAL);
        if (dobj->GetModifier(idx) == mod)
            throw std::runtime_error("3ds Max did not make modifier " + modDisplayName + " unique");

        GetCOREInterface()->RedrawViews(GetCOREInterface()->GetTime());
        json result = NodeIdentityJson(node);
        result["status"] = "made_unique";
        result["message"] = "Made modifier " + modDisplayName + " unique on " + WideToUtf8(node->GetName());
        result["modifier"] = {
            {"name", modDisplayName},
            {"index", modIndex},
        };
        result["stack"] = ModifierStackJson(node);
        return result.dump();
    });
}

// ── native:set_modifier_property (Pure SDK) ─────────────────
std::string NativeHandlers::SetModifierProperty(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string propName = p.value("property_name", "");
        std::string modClassName = p.value("modifier_class", "");
        std::string modName = p.value("modifier_name", "");
        int modIndex = p.value("modifier_index", 0);
        auto names = p.value("names", std::vector<std::string>{});
        bool selectionOnly = p.value("selection_only", false);
        std::string singleName = p.value("name", "");

        if (propName.empty()) throw std::runtime_error("property_name is required");
        if (p.find("property_value") == p.end()) {
            throw std::runtime_error("property_value is required");
        }
        std::string propValue;
        if (p["property_value"].type() == json::value_t::string) {
            propValue = p["property_value"].get<std::string>();
        } else {
            propValue = p["property_value"].dump();
        }

        if (modIndex <= 0 && modName.empty() && modClassName.empty()) {
            throw std::runtime_error(
                "Specify modifier_index, modifier_name, or modifier_class to choose a modifier");
        }

        if (!singleName.empty()) {
            names.insert(names.begin(), singleName);
        }

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        std::vector<INode*> targets;
        NamedNodes resolved;
        const json& notFound = resolved.notFound;
        // Objects named by the caller (or selected) are expected to change; a
        // scene-wide modifier_class sweep naturally passes over most objects.
        const bool explicitScope = PayloadHandleValue(p) != 0ULL || !names.empty() || selectionOnly;
        if (PayloadHandleValue(p) != 0ULL) {
            targets.push_back(ResolveNodeFromPayload(p));
        } else if (!names.empty()) {
            resolved = ResolveNodesByNames(names);
            targets = resolved.nodes;
        } else if (selectionOnly) {
            int selCount = ip->GetSelNodeCount();
            for (int i = 0; i < selCount; i++) {
                targets.push_back(ip->GetSelNode(i));
            }
        } else if (modIndex > 0 || !modName.empty()) {
            throw std::runtime_error("Specify name, names, or selection_only to choose objects");
        } else {
            INode* root = ip->GetRootNode();
            CollectNodes(root, targets);
        }

        if (targets.empty()) {
            if (resolved.Unresolved()) ThrowUnresolvedNames(resolved, "no modifier was updated.");
            throw std::runtime_error("No target objects found");
        }

        std::wstring wModClass = modClassName.empty() ? L"" : Utf8ToWide(modClassName);
        json hits = json::array();
        json skipped = json::array();
        int modCount = 0;
        int rejectedValues = 0;

        // Parameter setters can throw; always restore viewport redraw on exit.
        struct RedrawScope {
            Interface* core;
            explicit RedrawScope(Interface* value) : core(value) { core->DisableSceneRedraw(); }
            ~RedrawScope() { core->EnableSceneRedraw(); }
        };
        {
        RedrawScope redraw(ip);

        for (INode* node : targets) {
            const std::string nodeName = WideToUtf8(node->GetName());
            auto skip = [&](const std::string& reason) {
                if (explicitScope)
                    skipped.push_back({{"object", nodeName}, {"handle", NodeHandle(node)}, {"reason", reason}});
            };

            Object* objRef = node->GetObjectRef();
            if (!objRef || objRef->SuperClassID() != GEN_DERIVOB_CLASS_ID) {
                skip("no modifier stack");
                continue;
            }

            IDerivedObject* dobj = (IDerivedObject*)objRef;
            const int hitsBefore = modCount;
            bool rejected = false;
            bool classMatched = false;

            auto tryModifier = [&](Modifier* mod, int idx) {
                if (!mod) return;
                // Agents know the MAXScript class (Bend); the SDK name can differ (BendMod).
                if (!modClassName.empty() &&
                    _wcsicmp(mod->ClassName().data(), wModClass.c_str()) != 0 &&
                    _stricmp(MaxScriptVisibleClassName(mod).c_str(), modClassName.c_str()) != 0)
                    return;
                classMatched = true;
                if (!ModifierHelpers::SetParameter(mod, propName, propValue, t)) {
                    rejected = true;
                    rejectedValues++;
                    return;
                }

                mod->NotifyDependents(FOREVER, PART_ALL, REFMSG_CHANGE);
                modCount++;
                hits.push_back({
                    {"object", nodeName},
                    {"handle", NodeHandle(node)},
                    {"modifier", WideToUtf8(mod->GetName(false).data())},
                    {"index", idx + 1},
                    {"class", WideToUtf8(mod->ClassName().data())},
                });
            };

            std::string missReason;
            if (modIndex > 0) {
                int idx = modIndex - 1;
                if (idx < dobj->NumModifiers()) {
                    tryModifier(dobj->GetModifier(idx), idx);
                } else {
                    missReason = "no modifier at index " + std::to_string(modIndex);
                }
            } else if (!modName.empty()) {
                int idx = FindModifierIndex(node, modName);
                if (idx >= 0) {
                    tryModifier(dobj->GetModifier(idx), idx);
                } else {
                    missReason = "no modifier named \"" + modName + "\"";
                }
            } else {
                for (int m = 0; m < dobj->NumModifiers(); m++) {
                    tryModifier(dobj->GetModifier(m), m);
                }
            }

            if (modCount > hitsBefore) continue;
            if (!missReason.empty()) skip(missReason);
            else if (rejected) skip("the value was not accepted for " + propName);
            else if (!classMatched)
                skip(modIndex > 0 || !modName.empty()
                    ? "the requested modifier is not a " + modClassName
                    : "no " + modClassName + " modifier");
        }

        }
        ip->RedrawViews(t);

        if (modCount == 0) {
            json hint = {{"message", "Check object names, modifier target, property name and value literal."}};
            if (!notFound.empty()) hint["notFound"] = notFound;
            if (!resolved.ambiguous.empty()) hint["ambiguous"] = resolved.ambiguous;
            if (!skipped.empty()) hint["skipped"] = skipped;
            if (rejectedValues) hint["rejectedValues"] = rejectedValues;
            throw std::runtime_error(StructuredErrorPayload(
                "BAD_PARAM",
                "No modifiers updated — check object names, modifier target, and property name",
                hint));
        }

        json out = {
            {"modified", modCount},
            {"property", propName},
            {"value", propValue},
            {"hits", hits},
        };
        json warnings = json::array();
        if (!notFound.empty()) {
            out["notFound"] = notFound;
            warnings.push_back(std::to_string(notFound.size()) +
                " requested name(s) matched no object: " + JoinNames(notFound));
        }
        if (!skipped.empty()) {
            out["skipped"] = skipped;
            warnings.push_back(std::to_string(skipped.size()) +
                " scoped object(s) were not updated; see skipped for the reason.");
        }
        if (rejectedValues) {
            out["rejectedValues"] = rejectedValues;
            warnings.push_back(std::to_string(rejectedValues) +
                " matching modifier(s) did not accept the value for " + propName + ".");
        }
        if (!warnings.empty()) {
            out["status"] = "partial";
            out["warnings"] = warnings;
        }
        AddAmbiguousNamesWarning(out, resolved.ambiguous);
        return out.dump();
    });
}
