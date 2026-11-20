#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/core_bridge.h"

#include <ILayerManager.h>
#include <INamedSelectionSetManager.h>

using json = nlohmann::json;
using namespace HandlerHelpers;

// Layer wire colors are [r,g,b] bytes. Anything else is rejected instead of
// being skipped, so a malformed color never reports as applied.
static DWORD LayerColorFromPayload(const json& value) {
    if (value.type() != json::value_t::array || value.size() != 3 ||
        !std::all_of(value.begin(), value.end(), [](const json& part) {
            return part.is_number_integer() && part.get<int>() >= 0 && part.get<int>() <= 255;
        })) {
        throw std::runtime_error("color must be [r,g,b] with integers from 0 to 255");
    }
    return RGB(value[0].get<int>(), value[1].get<int>(), value[2].get<int>());
}

// Resolves object names for an organization edit. Every name is looked up
// before the first change; the caller reports the unresolved ones.
static NamedNodes ResolveNamedNodes(const json& names) {
    if (names.type() != json::value_t::array)
        throw std::runtime_error("names must be an array of object names");
    std::vector<std::string> requested;
    for (const auto& nameVal : names) requested.push_back(nameVal.get<std::string>());
    return ResolveNodesByNames(requested);
}

// The group a group action names. Only group heads qualify, so a member that
// shares its group's name does not make the group ambiguous; two groups with
// one name do.
static INode* FindGroupHead(const std::string& name) {
    std::vector<INode*> heads;
    for (INode* node : CollectNodesByName(name))
        if (node->IsGroupHead()) heads.push_back(node);
    if (heads.size() > 1) ThrowAmbiguousName(name, heads, kUniqueNamesAdvice);
    return heads.empty() ? nullptr : heads.front();
}

static void AddUnresolvedNamesWarnings(json& result, const NamedNodes& resolved) {
    result["notFound"] = resolved.notFound;
    if (!resolved.notFound.empty()) {
        result["status"] = "partial";
        result["warnings"].push_back(std::to_string(resolved.notFound.size()) +
            " requested name(s) matched no object and were skipped: " + JoinNames(resolved.notFound));
    }
    AddAmbiguousNamesWarning(result, resolved.ambiguous);
}

// ═════════════════════════════════════════════════════════════════
// native:manage_layers — full layer management
// ═════════════════════════════════════════════════════════════════
std::string NativeHandlers::ManageLayers(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = params.empty() ? json::object() : json::parse(params, nullptr, false);
        if (!p.is_object() || !p.contains("action"))
            throw std::runtime_error("action is required");

        std::string action = p["action"].get<std::string>();
        Interface14* ip14 = GetCOREInterface14();
        ILayerManager* lm = ip14 ? ip14->GetLayerManager() : nullptr;
        if (!lm)
            throw std::runtime_error("Cannot access layer manager");

        // ── list ────────────────────────────────────────────────
        if (action == "list") {
            json result;
            result["layers"] = json::array();
            int count = lm->GetLayerCount();
            ILayer* current = lm->GetCurrentLayer();

            for (int i = 0; i < count; i++) {
                ILayer* layer = lm->GetLayer(i);
                if (!layer) continue;

                json lj;
                lj["name"] = WideToUtf8(layer->GetName().data());
                lj["hidden"] = layer->IsHidden(false);
                lj["frozen"] = layer->IsFrozen(false);
                lj["renderable"] = layer->Renderable();
                lj["current"] = (layer == current);

                DWORD c = layer->GetWireColor();
                lj["color"] = json::array({GetRValue(c), GetGValue(c), GetBValue(c)});

                lj["boxMode"] = layer->GetBoxMode();
                lj["backCull"] = layer->GetBackCull();

                // Count objects on this layer
                ILayerProperties* lp = (ILayerProperties*)layer->GetInterface(LAYERPROPERTIES_INTERFACE);
                if (lp) {
                    Tab<INode*> nodes;
                    lp->Nodes(nodes);
                    lj["objectCount"] = nodes.Count();
                } else {
                    lj["objectCount"] = 0;
                }

                // Parent layer
                ILayer* parent = layer->GetParentLayer();
                if (parent) {
                    lj["parent"] = WideToUtf8(parent->GetName().data());
                }

                result["layers"].push_back(lj);
            }
            result["count"] = count;
            return result.dump();
        }

        // ── create ──────────────────────────────────────────────
        if (action == "create") {
            if (!p.contains("name"))
                throw std::runtime_error("name is required for create");
            std::string name = p["name"].get<std::string>();
            std::wstring wname = Utf8ToWide(name);

            // Check if already exists
            if (lm->GetLayer(wname.c_str()))
                throw std::runtime_error("Layer already exists: " + name);

            // Resolve optional inputs before creating, so a bad parent or
            // color fails cleanly instead of leaving a half-configured layer.
            const DWORD* color = nullptr;
            DWORD parsedColor = 0;
            if (p.contains("color")) {
                parsedColor = LayerColorFromPayload(p["color"]);
                color = &parsedColor;
            }
            ILayer* parentLayer = nullptr;
            if (p.contains("parent")) {
                const std::string parentName = p["parent"].get<std::string>();
                std::wstring wparent = Utf8ToWide(parentName);
                parentLayer = lm->GetLayer(wparent.c_str());
                if (!parentLayer)
                    throw std::runtime_error("Parent layer not found: " + parentName);
            }

            MSTR mname(wname.c_str());
            ILayer* layer = lm->CreateLayer(mname);
            if (!layer)
                throw std::runtime_error("Failed to create layer: " + name);

            // Apply optional properties
            if (color) layer->SetWireColor(*color);
            if (p.contains("hidden"))
                layer->Hide(p["hidden"].get<bool>());
            if (p.contains("frozen"))
                layer->Freeze(p["frozen"].get<bool>());
            if (p.contains("renderable"))
                layer->SetRenderable(p["renderable"].get<bool>());
            if (parentLayer && !layer->SetParentLayer(parentLayer))
                throw std::runtime_error("Max rejected parent layer " + p["parent"].get<std::string>() +
                    " for " + name);

            json result;
            result["created"] = WideToUtf8(layer->GetName().data());
            if (parentLayer) result["parent"] = WideToUtf8(parentLayer->GetName().data());
            return result.dump();
        }

        // ── delete ──────────────────────────────────────────────
        if (action == "delete") {
            if (!p.contains("name"))
                throw std::runtime_error("name is required for delete");
            std::string name = p["name"].get<std::string>();
            std::wstring wname = Utf8ToWide(name);
            MSTR mname(wname.c_str());

            if (!lm->DeleteLayer(mname))
                throw std::runtime_error("Failed to delete layer (may have objects or be the default): " + name);

            json result;
            result["deleted"] = name;
            return result.dump();
        }

        // ── set_current ─────────────────────────────────────────
        if (action == "set_current") {
            if (!p.contains("name"))
                throw std::runtime_error("name is required for set_current");
            std::wstring wname = Utf8ToWide(p["name"].get<std::string>());
            if (!lm->GetLayer(wname.c_str()))
                throw std::runtime_error("Layer not found: " + p["name"].get<std::string>());
            MSTR mname(wname.c_str());
            lm->SetCurrentLayer(mname);
            ILayer* current = lm->GetCurrentLayer();
            const std::string currentName = current ? WideToUtf8(current->GetName().data()) : "";
            if (_wcsicmp(Utf8ToWide(currentName).c_str(), wname.c_str()) != 0)
                throw std::runtime_error("Max did not make " + p["name"].get<std::string>() +
                    " the current layer (current: " + currentName + ")");
            json result;
            result["current"] = currentName;
            return result.dump();
        }

        // ── set_properties ──────────────────────────────────────
        if (action == "set_properties") {
            if (!p.contains("name"))
                throw std::runtime_error("name is required for set_properties");
            std::wstring wname = Utf8ToWide(p["name"].get<std::string>());
            ILayer* layer = lm->GetLayer(wname.c_str());
            if (!layer)
                throw std::runtime_error("Layer not found: " + p["name"].get<std::string>());

            // Validate every requested change before the first edit.
            DWORD color = 0;
            if (p.contains("color")) color = LayerColorFromPayload(p["color"]);
            ILayer* parentLayer = nullptr;
            if (p.contains("parent")) {
                const std::string parentName = p["parent"].get<std::string>();
                parentLayer = lm->GetLayer(Utf8ToWide(parentName).c_str());
                if (!parentLayer)
                    throw std::runtime_error("Parent layer not found: " + parentName);
            }
            if (p.contains("rename")) {
                const std::string newName = p["rename"].get<std::string>();
                if (newName.empty())
                    throw std::runtime_error("rename must be a non-empty layer name");
                ILayer* existing = lm->GetLayer(Utf8ToWide(newName).c_str());
                if (existing && existing != layer)
                    throw std::runtime_error("Layer already exists: " + newName);
            }

            json applied = json::array();
            if (parentLayer) {
                if (!layer->SetParentLayer(parentLayer))
                    throw std::runtime_error("Max rejected parent layer " + p["parent"].get<std::string>() +
                        " (a layer cannot be parented to itself or its children)");
                applied.push_back("parent");
            }
            if (p.contains("hidden")) { layer->Hide(p["hidden"].get<bool>()); applied.push_back("hidden"); }
            if (p.contains("frozen")) { layer->Freeze(p["frozen"].get<bool>()); applied.push_back("frozen"); }
            if (p.contains("renderable")) { layer->SetRenderable(p["renderable"].get<bool>()); applied.push_back("renderable"); }
            if (p.contains("boxMode")) { layer->BoxMode(p["boxMode"].get<bool>()); applied.push_back("boxMode"); }
            if (p.contains("backCull")) { layer->BackCull(p["backCull"].get<bool>()); applied.push_back("backCull"); }
            if (p.contains("allEdges")) { layer->AllEdges(p["allEdges"].get<bool>()); applied.push_back("allEdges"); }
            if (p.contains("vertTicks")) { layer->VertTicks(p["vertTicks"].get<bool>()); applied.push_back("vertTicks"); }
            if (p.contains("trajectory")) { layer->Trajectory(p["trajectory"].get<bool>()); applied.push_back("trajectory"); }
            if (p.contains("xRayMtl")) { layer->XRayMtl(p["xRayMtl"].get<bool>()); applied.push_back("xRayMtl"); }
            if (p.contains("castShadows")) { layer->SetCastShadows(p["castShadows"].get<bool>()); applied.push_back("castShadows"); }
            if (p.contains("rcvShadows")) { layer->SetRcvShadows(p["rcvShadows"].get<bool>()); applied.push_back("rcvShadows"); }
            if (p.contains("primaryVisibility")) { layer->SetPrimaryVisibility(p["primaryVisibility"].get<bool>()); applied.push_back("primaryVisibility"); }
            if (p.contains("secondaryVisibility")) { layer->SetSecondaryVisibility(p["secondaryVisibility"].get<bool>()); applied.push_back("secondaryVisibility"); }
            if (p.contains("color")) {
                layer->SetWireColor(color);
                applied.push_back("color");
            }
            if (p.contains("rename")) {
                std::wstring wnew = Utf8ToWide(p["rename"].get<std::string>());
                MSTR mname(wnew.c_str());
                layer->SetName(mname);
                applied.push_back("rename");
            }

            if (applied.empty())
                throw std::runtime_error("set_properties needs at least one property to change "
                    "(hidden, frozen, renderable, color, parent, rename, or a display flag)");

            const std::string currentName = WideToUtf8(layer->GetName().data());
            json result;
            result["layer"] = p["name"].get<std::string>();
            result["name"] = currentName;
            result["applied"] = applied;
            if (p.contains("rename") && currentName != p["rename"].get<std::string>()) {
                result["status"] = "partial";
                result["warnings"] = json::array({"Requested rename to " + p["rename"].get<std::string>() +
                    ", but the layer is now named " + currentName + "."});
            }
            return result.dump();
        }

        // ── add_objects ─────────────────────────────────────────
        if (action == "add_objects") {
            if (!p.contains("layer") || !p.contains("names"))
                throw std::runtime_error("layer and names are required for add_objects");

            std::wstring wlayer = Utf8ToWide(p["layer"].get<std::string>());
            ILayer* layer = lm->GetLayer(wlayer.c_str());
            if (!layer)
                throw std::runtime_error("Layer not found: " + p["layer"].get<std::string>());

            json moved = json::array();
            json failed = json::array();
            const NamedNodes resolved = ResolveNamedNodes(p["names"]);
            if (resolved.nodes.empty()) ThrowUnresolvedNames(resolved, "no object was moved to a layer.");

            for (INode* node : resolved.nodes) {
                const std::string name = WideToUtf8(node->GetName());
                // AddToLayer reports false for a node it did not move.
                if (layer->AddToLayer(node) || NodeLayerName(node) == WideToUtf8(layer->GetName().data()))
                    moved.push_back(name);
                else
                    failed.push_back(name);
            }

            json result;
            result["layer"] = p["layer"].get<std::string>();
            result["moved"] = moved;
            AddUnresolvedNamesWarnings(result, resolved);
            if (!failed.empty()) {
                if (moved.empty())
                    throw std::runtime_error("Max did not move any object to layer " +
                        p["layer"].get<std::string>() + ": " + JoinNames(failed));
                result["failed"] = failed;
                result["status"] = "partial";
                result["warnings"].push_back(std::to_string(failed.size()) +
                    " object(s) stayed on their layer: " + JoinNames(failed));
            }
            return result.dump();
        }

        // ── select_objects ──────────────────────────────────────
        if (action == "select_objects") {
            if (!p.contains("name"))
                throw std::runtime_error("name is required for select_objects");
            std::wstring wname = Utf8ToWide(p["name"].get<std::string>());
            ILayer* layer = lm->GetLayer(wname.c_str());
            if (!layer)
                throw std::runtime_error("Layer not found: " + p["name"].get<std::string>());

            layer->SelectObjects();

            int layerNodeCount = 0;
            if (ILayerProperties* lp = (ILayerProperties*)layer->GetInterface(LAYERPROPERTIES_INTERFACE)) {
                Tab<INode*> layerNodes;
                lp->Nodes(layerNodes);
                layerNodeCount = layerNodes.Count();
            }

            json result;
            result["layer"] = p["name"].get<std::string>();
            result["selected"] = true;
            result["objectCount"] = layerNodeCount;
            result["selectionCount"] = GetCOREInterface()->GetSelNodeCount();
            if (layerNodeCount == 0) {
                result["status"] = "no_objects";
                result["warnings"] = json::array({"Layer " + p["name"].get<std::string>() +
                    " has no objects, so nothing was selected."});
            }
            return result.dump();
        }

        throw std::runtime_error("Unknown layer action: " + action);
    });
}

// ═════════════════════════════════════════════════════════════════
// native:manage_groups — group/ungroup/open/close/attach/detach
// ═════════════════════════════════════════════════════════════════
std::string NativeHandlers::ManageGroups(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = params.empty() ? json::object() : json::parse(params, nullptr, false);
        if (!p.is_object() || !p.contains("action"))
            throw std::runtime_error("action is required");

        std::string action = p["action"].get<std::string>();
        Interface* ip = GetCOREInterface();

        // ── list ────────────────────────────────────────────────
        if (action == "list") {
            INode* root = ip->GetRootNode();
            std::vector<INode*> all;
            CollectNodes(root, all);

            json groups = json::array();
            for (INode* n : all) {
                if (!n->IsGroupHead()) continue;

                json gj;
                gj["name"] = WideToUtf8(n->GetName());
                gj["open"] = (bool)n->IsOpenGroupHead();
                gj["hidden"] = n->IsHidden();

                // Collect members
                json members = json::array();
                for (int i = 0; i < n->NumberOfChildren(); i++) {
                    INode* child = n->GetChildNode(i);
                    if (child && child->IsGroupMember()) {
                        members.push_back(WideToUtf8(child->GetName()));
                    }
                }
                gj["members"] = members;
                gj["memberCount"] = members.size();

                groups.push_back(gj);
            }

            json result;
            result["groups"] = groups;
            result["count"] = groups.size();
            return result.dump();
        }

        // ── create ──────────────────────────────────────────────
        if (action == "create") {
            if (!p.contains("names"))
                throw std::runtime_error("names (array of object names) is required for create");

            INodeTab nodes;
            const NamedNodes resolved = ResolveNamedNodes(p["names"]);
            for (INode* node : resolved.nodes) nodes.AppendNode(node);

            if (nodes.Count() == 0) ThrowUnresolvedNames(resolved, "no group was created.");

            MSTR groupName;
            if (p.contains("name")) {
                std::wstring wname = Utf8ToWide(p["name"].get<std::string>());
                groupName = MSTR(wname.c_str());
            }

            INode* groupHead = ip->GroupNodes(&nodes, p.contains("name") ? &groupName : nullptr, TRUE);
            if (!groupHead)
                throw std::runtime_error("Failed to create group");

            json result;
            result["group"] = WideToUtf8(groupHead->GetName());
            result["memberCount"] = nodes.Count();
            AddUnresolvedNamesWarnings(result, resolved);
            return result.dump();
        }

        // ── ungroup ─────────────────────────────────────────────
        if (action == "ungroup") {
            if (!p.contains("name"))
                throw std::runtime_error("name is required for ungroup");

            const std::string name = p["name"].get<std::string>();
            INode* node = FindGroupHead(name);
            if (!node)
                throw std::runtime_error((CollectNodesByName(name).empty()
                    ? "Object not found: " : "Object is not a group head: ") + name);

            INodeTab nodes;
            nodes.AppendNode(node);
            ip->UngroupNodes(&nodes);

            json result;
            result["ungrouped"] = p["name"].get<std::string>();
            return result.dump();
        }

        // ── open ────────────────────────────────────────────────
        if (action == "open") {
            if (!p.contains("name"))
                throw std::runtime_error("name is required for open");

            INode* node = FindGroupHead(p["name"].get<std::string>());
            if (!node)
                throw std::runtime_error("Group not found: " + p["name"].get<std::string>());

            INodeTab nodes;
            nodes.AppendNode(node);
            ip->OpenGroup(&nodes, TRUE);

            json result;
            result["opened"] = p["name"].get<std::string>();
            return result.dump();
        }

        // ── close ───────────────────────────────────────────────
        if (action == "close") {
            if (!p.contains("name"))
                throw std::runtime_error("name is required for close");

            INode* node = FindGroupHead(p["name"].get<std::string>());
            if (!node)
                throw std::runtime_error("Group not found: " + p["name"].get<std::string>());

            INodeTab nodes;
            nodes.AppendNode(node);
            ip->CloseGroup(&nodes, TRUE);

            json result;
            result["closed"] = p["name"].get<std::string>();
            return result.dump();
        }

        // ── attach ──────────────────────────────────────────────
        if (action == "attach") {
            if (!p.contains("group") || !p.contains("names"))
                throw std::runtime_error("group and names are required for attach");

            INode* groupHead = FindGroupHead(p["group"].get<std::string>());
            if (!groupHead)
                throw std::runtime_error("Group not found: " + p["group"].get<std::string>());

            INodeTab nodes;
            const NamedNodes resolved = ResolveNamedNodes(p["names"]);
            for (INode* node : resolved.nodes) nodes.AppendNode(node);
            if (nodes.Count() == 0) ThrowUnresolvedNames(resolved, "nothing was attached to the group.");

            if (!ip->AttachNodesToGroup(nodes, *groupHead))
                throw std::runtime_error("Max could not attach the objects to group " +
                    p["group"].get<std::string>() +
                    " (members of another group cannot be attached; detach them first)");

            // Read back membership: AttachNodesToGroup reports one result for the batch.
            int attached = 0;
            json failed = json::array();
            for (int i = 0; i < nodes.Count(); ++i) {
                INode* node = nodes[i];
                bool inGroup = false;
                for (INode* parent = node->GetParentNode(); node->IsGroupMember() && parent &&
                     !parent->IsRootNode(); parent = parent->GetParentNode()) {
                    if (parent == groupHead) { inGroup = true; break; }
                }
                if (inGroup) attached++;
                else failed.push_back(WideToUtf8(node->GetName()));
            }

            json result;
            result["group"] = p["group"].get<std::string>();
            result["attached"] = attached;
            AddUnresolvedNamesWarnings(result, resolved);
            if (!failed.empty()) {
                result["failed"] = failed;
                result["status"] = "partial";
                result["warnings"].push_back(std::to_string(failed.size()) +
                    " object(s) are not members of the group after attach: " + JoinNames(failed));
            }
            return result.dump();
        }

        // ── detach ──────────────────────────────────────────────
        if (action == "detach") {
            if (!p.contains("names"))
                throw std::runtime_error("names is required for detach");

            INodeTab nodes;
            json notGroupMembers = json::array();
            json autoOpenedGroups = json::array();
            std::vector<INode*> groupsToReclose;

            auto ensureGroupOpen = [&](INode* groupHead) {
                if (!groupHead || !groupHead->IsGroupHead() || groupHead->IsOpenGroupHead()) {
                    return;
                }

                bool alreadyQueued = false;
                for (INode* queued : groupsToReclose) {
                    if (queued == groupHead) {
                        alreadyQueued = true;
                        break;
                    }
                }
                if (alreadyQueued) {
                    return;
                }

                INodeTab oneGroup;
                oneGroup.AppendNode(groupHead);
                ip->OpenGroup(&oneGroup, TRUE);
                groupsToReclose.push_back(groupHead);
                autoOpenedGroups.push_back(WideToUtf8(groupHead->GetName()));
            };

            const NamedNodes resolved = ResolveNamedNodes(p["names"]);
            for (INode* node : resolved.nodes) {
                if (!node->IsGroupMember()) {
                    notGroupMembers.push_back(WideToUtf8(node->GetName()));
                    continue;
                }

                INode* parent = node->GetParentNode();
                if (parent && parent->IsGroupHead()) {
                    ensureGroupOpen(parent);
                }

                nodes.AppendNode(node);
            }

            const int requestedDetach = nodes.Count();
            if (requestedDetach == 0) {
                // Nothing was opened above: only group members queue a group to reopen.
                const json hint = {{"notGroupMembers", notGroupMembers}};
                if (notGroupMembers.empty()) ThrowUnresolvedNames(resolved, "nothing was detached.", hint);
                throw std::runtime_error(StructuredErrorPayload(
                    "BAD_PARAM",
                    "None of the requested objects is a group member; nothing was detached.",
                    UnresolvedNamesHint(resolved, hint)));
            }
            ip->DetachNodesFromGroup(nodes);

            int detachedCount = 0;
            json failedDetach = json::array();
            for (int i = 0; i < requestedDetach; ++i) {
                INode* node = nodes[i];
                if (node && !node->IsGroupMember()) {
                    detachedCount++;
                } else if (node) {
                    failedDetach.push_back(WideToUtf8(node->GetName()));
                }
            }

            for (INode* groupHead : groupsToReclose) {
                INodeTab oneGroup;
                oneGroup.AppendNode(groupHead);
                ip->CloseGroup(&oneGroup, TRUE);
            }

            json result;
            result["detached"] = detachedCount;
            result["requested"] = requestedDetach;
            result["notGroupMembers"] = notGroupMembers;
            result["autoOpenedGroups"] = autoOpenedGroups;
            result["failedDetach"] = failedDetach;
            if (detachedCount == 0)
                throw std::runtime_error(StructuredErrorPayload(
                    "BAD_PARAM", "Max did not detach any of the requested group members.",
                    {{"failedDetach", failedDetach}}));
            AddUnresolvedNamesWarnings(result, resolved);
            if (!notGroupMembers.empty()) {
                result["status"] = "partial";
                result["warnings"].push_back(std::to_string(notGroupMembers.size()) +
                    " object(s) were not group members and were skipped: " + JoinNames(notGroupMembers));
            }
            if (!failedDetach.empty()) {
                result["status"] = "partial";
                result["warnings"].push_back(std::to_string(failedDetach.size()) +
                    " group member(s) are still in a group: " + JoinNames(failedDetach));
            }
            return result.dump();
        }

        throw std::runtime_error("Unknown group action: " + action);
    });
}

// ═════════════════════════════════════════════════════════════════
// native:manage_selection_sets — named selection set CRUD
// ═════════════════════════════════════════════════════════════════
std::string NativeHandlers::ManageSelectionSets(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = params.empty() ? json::object() : json::parse(params, nullptr, false);
        if (!p.is_object() || !p.contains("action"))
            throw std::runtime_error("action is required");

        std::string action = p["action"].get<std::string>();
        INamedSelectionSetManager* mgr = INamedSelectionSetManager::GetInstance();
        if (!mgr)
            throw std::runtime_error("Cannot access selection set manager");

        // ── list ────────────────────────────────────────────────
        if (action == "list") {
            json sets = json::array();
            int count = mgr->GetNumNamedSelSets();
            for (int i = 0; i < count; i++) {
                json sj;
                const MCHAR* name = mgr->GetNamedSelSetName(i);
                sj["name"] = name ? WideToUtf8(name) : "";
                sj["count"] = mgr->GetNamedSelSetItemCount(i);

                // List member names
                json members = json::array();
                int itemCount = mgr->GetNamedSelSetItemCount(i);
                for (int j = 0; j < itemCount; j++) {
                    INode* node = mgr->GetNamedSelSetItem(i, j);
                    if (node) members.push_back(WideToUtf8(node->GetName()));
                }
                sj["members"] = members;
                sets.push_back(sj);
            }

            json result;
            result["selectionSets"] = sets;
            result["count"] = count;
            return result.dump();
        }

        // ── create ──────────────────────────────────────────────
        if (action == "create") {
            if (!p.contains("name") || !p.contains("names"))
                throw std::runtime_error("name and names are required for create");

            std::string setName = p["name"].get<std::string>();
            std::wstring wname = Utf8ToWide(setName);
            MSTR mname(wname.c_str());

            Tab<INode*> nodes;
            const NamedNodes resolved = ResolveNamedNodes(p["names"]);
            for (INode* node : resolved.nodes) nodes.Append(1, &node);
            if (nodes.Count() == 0) ThrowUnresolvedNames(resolved, "the selection set was not created.");

            // A set of that name is replaced, never duplicated: Max keeps two
            // sets of one name, and a later delete would remove only one.
            bool exists = false;
            for (int i = 0; i < mgr->GetNumNamedSelSets() && !exists; i++) {
                const MCHAR* existing = mgr->GetNamedSelSetName(i);
                exists = existing && wname == existing;
            }
            if (exists ? !mgr->ReplaceNamedSelSet(nodes, mname) : !mgr->AddNewNamedSelSet(nodes, mname))
                throw std::runtime_error("Failed to create selection set: " + setName);

            json result;
            result["created"] = setName;
            result["replaced"] = exists;
            result["memberCount"] = nodes.Count();
            AddUnresolvedNamesWarnings(result, resolved);
            return result.dump();
        }

        // ── delete ──────────────────────────────────────────────
        if (action == "delete") {
            if (!p.contains("name"))
                throw std::runtime_error("name is required for delete");

            std::string setName = p["name"].get<std::string>();
            std::wstring wname = Utf8ToWide(setName);
            MSTR mname(wname.c_str());

            if (!mgr->RemoveNamedSelSet(mname))
                throw std::runtime_error("Failed to delete selection set: " + setName);

            json result;
            result["deleted"] = setName;
            return result.dump();
        }

        // ── select ──────────────────────────────────────────────
        if (action == "select") {
            if (!p.contains("name"))
                throw std::runtime_error("name is required for select");

            std::string setName = p["name"].get<std::string>();
            int count = mgr->GetNumNamedSelSets();
            int foundIdx = -1;
            for (int i = 0; i < count; i++) {
                const MCHAR* name = mgr->GetNamedSelSetName(i);
                if (name && WideToUtf8(name) == setName) {
                    foundIdx = i;
                    break;
                }
            }
            if (foundIdx < 0)
                throw std::runtime_error("Selection set not found: " + setName);

            Interface* ip = GetCOREInterface();
            ip->ClearNodeSelection(FALSE);
            int itemCount = mgr->GetNamedSelSetItemCount(foundIdx);
            for (int j = 0; j < itemCount; j++) {
                INode* node = mgr->GetNamedSelSetItem(foundIdx, j);
                if (node) ip->SelectNode(node, FALSE);
            }
            ip->RedrawViews(ip->GetTime());

            json result;
            result["selected"] = setName;
            result["count"] = itemCount;
            result["selectionCount"] = ip->GetSelNodeCount();
            if (ip->GetSelNodeCount() == 0) {
                result["status"] = "no_objects";
                result["warnings"] = json::array({"Selection set " + setName +
                    " has no live objects; the selection is now empty."});
            }
            return result.dump();
        }

        // ── replace ─────────────────────────────────────────────
        if (action == "replace") {
            if (!p.contains("name") || !p.contains("names"))
                throw std::runtime_error("name and names are required for replace");

            std::string setName = p["name"].get<std::string>();
            std::wstring wname = Utf8ToWide(setName);
            MSTR mname(wname.c_str());

            Tab<INode*> nodes;
            const NamedNodes resolved = ResolveNamedNodes(p["names"]);
            for (INode* node : resolved.nodes) nodes.Append(1, &node);
            if (nodes.Count() == 0) ThrowUnresolvedNames(resolved, "the selection set was not replaced.");

            if (!mgr->ReplaceNamedSelSet(nodes, mname))
                throw std::runtime_error("Failed to replace selection set: " + setName);

            json result;
            result["replaced"] = setName;
            result["memberCount"] = nodes.Count();
            AddUnresolvedNamesWarnings(result, resolved);
            return result.dump();
        }

        throw std::runtime_error("Unknown selection set action: " + action);
    });
}
