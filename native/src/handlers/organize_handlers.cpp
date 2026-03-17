#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/bridge_gup.h"

#include <ILayerManager.h>
#include <INamedSelectionSetManager.h>

using json = nlohmann::json;
using namespace HandlerHelpers;

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

            MSTR mname(wname.c_str());
            ILayer* layer = lm->CreateLayer(mname);
            if (!layer)
                throw std::runtime_error("Failed to create layer: " + name);

            // Apply optional properties
            if (p.contains("color") && p["color"].size() == 3) {
                DWORD col = RGB(p["color"][0].get<int>(), p["color"][1].get<int>(), p["color"][2].get<int>());
                layer->SetWireColor(col);
            }
            if (p.contains("hidden"))
                layer->Hide(p["hidden"].get<bool>());
            if (p.contains("frozen"))
                layer->Freeze(p["frozen"].get<bool>());
            if (p.contains("renderable"))
                layer->SetRenderable(p["renderable"].get<bool>());
            if (p.contains("parent")) {
                std::wstring wparent = Utf8ToWide(p["parent"].get<std::string>());
                ILayer* parentLayer = lm->GetLayer(wparent.c_str());
                if (parentLayer)
                    layer->SetParentLayer(parentLayer);
            }

            json result;
            result["created"] = name;
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
            MSTR mname(wname.c_str());
            lm->SetCurrentLayer(mname);
            json result;
            result["current"] = p["name"].get<std::string>();
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

            json applied = json::array();
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
            if (p.contains("color") && p["color"].size() == 3) {
                DWORD col = RGB(p["color"][0].get<int>(), p["color"][1].get<int>(), p["color"][2].get<int>());
                layer->SetWireColor(col);
                applied.push_back("color");
            }
            if (p.contains("rename")) {
                std::wstring wnew = Utf8ToWide(p["rename"].get<std::string>());
                MSTR mname(wnew.c_str());
                layer->SetName(mname);
                applied.push_back("rename");
            }

            json result;
            result["layer"] = p["name"].get<std::string>();
            result["applied"] = applied;
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
            json notFound = json::array();

            for (auto& nameVal : p["names"]) {
                std::string name = nameVal.get<std::string>();
                INode* node = FindNodeByName(name);
                if (node) {
                    layer->AddToLayer(node);
                    moved.push_back(name);
                } else {
                    notFound.push_back(name);
                }
            }

            json result;
            result["layer"] = p["layer"].get<std::string>();
            result["moved"] = moved;
            result["notFound"] = notFound;
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

            json result;
            result["layer"] = p["name"].get<std::string>();
            result["selected"] = true;
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
            json notFound = json::array();
            for (auto& nameVal : p["names"]) {
                std::string name = nameVal.get<std::string>();
                INode* node = FindNodeByName(name);
                if (node) {
                    nodes.AppendNode(node);
                } else {
                    notFound.push_back(name);
                }
            }

            if (nodes.Count() == 0)
                throw std::runtime_error("No valid objects found to group");

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
            result["notFound"] = notFound;
            return result.dump();
        }

        // ── ungroup ─────────────────────────────────────────────
        if (action == "ungroup") {
            if (!p.contains("name"))
                throw std::runtime_error("name is required for ungroup");

            INode* node = FindNodeByName(p["name"].get<std::string>());
            if (!node)
                throw std::runtime_error("Object not found: " + p["name"].get<std::string>());
            if (!node->IsGroupHead())
                throw std::runtime_error("Object is not a group head: " + p["name"].get<std::string>());

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

            INode* node = FindNodeByName(p["name"].get<std::string>());
            if (!node || !node->IsGroupHead())
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

            INode* node = FindNodeByName(p["name"].get<std::string>());
            if (!node || !node->IsGroupHead())
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

            INode* groupHead = FindNodeByName(p["group"].get<std::string>());
            if (!groupHead || !groupHead->IsGroupHead())
                throw std::runtime_error("Group not found: " + p["group"].get<std::string>());

            INodeTab nodes;
            json notFound = json::array();
            for (auto& nameVal : p["names"]) {
                std::string name = nameVal.get<std::string>();
                INode* node = FindNodeByName(name);
                if (node) {
                    nodes.AppendNode(node);
                } else {
                    notFound.push_back(name);
                }
            }

            if (nodes.Count() > 0)
                ip->AttachNodesToGroup(nodes, *groupHead);

            json result;
            result["group"] = p["group"].get<std::string>();
            result["attached"] = nodes.Count();
            result["notFound"] = notFound;
            return result.dump();
        }

        // ── detach ──────────────────────────────────────────────
        if (action == "detach") {
            if (!p.contains("names"))
                throw std::runtime_error("names is required for detach");

            INodeTab nodes;
            json notFound = json::array();
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

            for (auto& nameVal : p["names"]) {
                std::string name = nameVal.get<std::string>();
                INode* node = FindNodeByName(name);
                if (node) {
                    if (!node->IsGroupMember()) {
                        notGroupMembers.push_back(name);
                        continue;
                    }

                    INode* parent = node->GetParentNode();
                    if (parent && parent->IsGroupHead()) {
                        ensureGroupOpen(parent);
                    }

                    nodes.AppendNode(node);
                } else {
                    notFound.push_back(name);
                }
            }

            const int requestedDetach = nodes.Count();
            if (requestedDetach > 0) {
                ip->DetachNodesFromGroup(nodes);
            }

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
            result["notFound"] = notFound;
            result["notGroupMembers"] = notGroupMembers;
            result["autoOpenedGroups"] = autoOpenedGroups;
            result["failedDetach"] = failedDetach;
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
            json notFound = json::array();
            for (auto& nameVal : p["names"]) {
                std::string name = nameVal.get<std::string>();
                INode* node = FindNodeByName(name);
                if (node) {
                    nodes.Append(1, &node);
                } else {
                    notFound.push_back(name);
                }
            }

            if (!mgr->AddNewNamedSelSet(nodes, mname))
                throw std::runtime_error("Failed to create selection set: " + setName);

            json result;
            result["created"] = setName;
            result["memberCount"] = nodes.Count();
            result["notFound"] = notFound;
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
            for (auto& nameVal : p["names"]) {
                INode* node = FindNodeByName(nameVal.get<std::string>());
                if (node) nodes.Append(1, &node);
            }

            if (!mgr->ReplaceNamedSelSet(nodes, mname))
                throw std::runtime_error("Failed to replace selection set: " + setName);

            json result;
            result["replaced"] = setName;
            result["memberCount"] = nodes.Count();
            return result.dump();
        }

        throw std::runtime_error("Unknown selection set action: " + action);
    });
}
