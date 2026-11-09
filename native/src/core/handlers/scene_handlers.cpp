#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/core_bridge.h"
#include <cmath>
#include <map>
#include <memory>
#include <mutex>
#include <set>

using json = nlohmann::json;
using namespace HandlerHelpers;

// ── native:scene_info ───────────────────────────────────────────
std::string NativeHandlers::SceneInfo(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = params.empty() ? json::object() : json::parse(params, nullptr, false);

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();
        INode* root = ip->GetRootNode();

        std::vector<INode*> nodes;
        CollectNodes(root, nodes);

        // Optional filters
        std::string filterClass;
        std::string filterPattern;
        std::string filterLayer;
        bool rootsOnly = false;
        int limit = 100;
        int offset = 0;

        if (p.is_object()) {
            if (p.contains("class_name")) filterClass = p["class_name"].get<std::string>();
            if (p.contains("pattern")) filterPattern = p["pattern"].get<std::string>();
            if (p.contains("layer")) filterLayer = p["layer"].get<std::string>();
            if (p.contains("roots_only")) rootsOnly = p["roots_only"].get<bool>();
            if (p.contains("limit")) limit = p["limit"].get<int>();
            if (p.contains("offset")) offset = p["offset"].get<int>();
        }

        bool hasFilter = !filterClass.empty() || !filterPattern.empty() ||
                         !filterLayer.empty() || rootsOnly;

        if (!hasFilter && offset == 0) {
            // Summary mode — just counts
            int hiddenCount = 0;
            int frozenCount = 0;
            std::map<std::string, int> classCounts;
            std::set<std::string> layers;

            for (INode* n : nodes) {
                if (n->IsHidden()) hiddenCount++;
                if (n->IsFrozen()) frozenCount++;
                classCounts[NodeClassName(n)]++;
                layers.insert(NodeLayerName(n));
            }

            json result;
            result["totalObjects"] = (int)nodes.size();
            result["hiddenCount"] = hiddenCount;
            result["frozenCount"] = frozenCount;
            result["classCounts"] = json::object();
            for (auto& [cls, cnt] : classCounts) {
                result["classCounts"][cls] = cnt;
            }
            result["layers"] = json::array();
            for (auto& l : layers) {
                result["layers"].push_back(l);
            }
            return result.dump();
        }

        // Filtered mode — return per-object details
        std::vector<INode*> matched;
        for (INode* n : nodes) {
            if (!filterClass.empty()) {
                if (NodeClassName(n) != filterClass) continue;
            }
            if (!filterLayer.empty()) {
                if (NodeLayerName(n) != filterLayer) continue;
            }
            if (rootsOnly) {
                INode* parent = n->GetParentNode();
                if (parent && !parent->IsRootNode()) continue;
            }
            // MAXScript matchPattern rules: '*' and '?' anywhere, any case.
            if (!filterPattern.empty() && !WildcardMatch(WideToUtf8(n->GetName()), filterPattern)) continue;
            matched.push_back(n);
        }

        int total = (int)matched.size();
        int start = (std::min)(offset, total);
        int end = (std::min)(offset + limit, total);

        json objects = json::array();
        for (int i = start; i < end; i++) {
            INode* n = matched[i];
            json obj;
            obj["name"] = WideToUtf8(n->GetName());
            obj["class"] = NodeClassName(n);
            obj["position"] = NodePosition(n, t);
            INode* parent = n->GetParentNode();
            obj["parent"] = (parent && !parent->IsRootNode()) ?
                json(WideToUtf8(parent->GetName())) : json(nullptr);
            obj["numChildren"] = n->NumberOfChildren();
            obj["isHidden"] = (bool)n->IsHidden();
            obj["isFrozen"] = (bool)n->IsFrozen();
            obj["layer"] = NodeLayerName(n);
            objects.push_back(obj);
        }

        json result;
        result["totalMatched"] = total;
        result["objects"] = objects;
        return result.dump();
    });
}

// ── native:selection ────────────────────────────────────────────
std::string NativeHandlers::Selection(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([]() -> std::string {
        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();
        int count = ip->GetSelNodeCount();

        json arr = json::array();
        for (int i = 0; i < count; i++) {
            INode* n = ip->GetSelNode(i);
            json obj;
            obj["name"] = WideToUtf8(n->GetName());
            obj["class"] = NodeClassName(n);
            obj["position"] = NodePosition(n, t);
            obj["wirecolor"] = NodeWireColor(n);
            arr.push_back(obj);
        }
        return arr.dump();
    });
}

// ── native:scene_snapshot ───────────────────────────────────────
std::string NativeHandlers::SceneSnapshot(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = params.empty() ? json::object() : json::parse(params, nullptr, false);
        int maxRoots = 50;
        if (p.is_object() && p.contains("max_roots")) {
            maxRoots = p["max_roots"].get<int>();
        }

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();
        INode* root = ip->GetRootNode();

        std::vector<INode*> nodes;
        CollectNodes(root, nodes);

        int hiddenCount = 0;
        int frozenCount = 0;
        std::map<std::string, int> classCounts;
        std::map<std::string, int> matCounts;
        std::map<std::string, int> modCounts;
        std::set<std::string> layers;
        std::vector<std::string> rootNames;

        for (INode* n : nodes) {
            if (n->IsHidden()) hiddenCount++;
            if (n->IsFrozen()) frozenCount++;

            classCounts[NodeClassName(n)]++;
            layers.insert(NodeLayerName(n));

            // Material
            Mtl* mtl = n->GetMtl();
            if (mtl) {
                matCounts[WideToUtf8(mtl->GetName().data())]++;
            }

            // Modifiers
            Object* objRef = n->GetObjectRef();
            if (objRef && objRef->SuperClassID() == GEN_DERIVOB_CLASS_ID) {
                IDerivedObject* dobj = (IDerivedObject*)objRef;
                for (int m = 0; m < dobj->NumModifiers(); m++) {
                    Modifier* mod = dobj->GetModifier(m);
                    if (mod) {
                        modCounts[WideToUtf8(mod->ClassName().data())]++;
                    }
                }
            }

            // Root objects (no parent or parent is scene root)
            INode* parent = n->GetParentNode();
            if (!parent || parent->IsRootNode()) {
                rootNames.push_back(WideToUtf8(n->GetName()));
            }
        }

        json result;
        result["objectCount"] = (int)nodes.size();
        result["hiddenCount"] = hiddenCount;
        result["frozenCount"] = frozenCount;

        result["classCounts"] = json::object();
        for (auto& [cls, cnt] : classCounts) {
            result["classCounts"][cls] = cnt;
        }

        result["materials"] = json::object();
        for (auto& [mat, cnt] : matCounts) {
            result["materials"][mat] = cnt;
        }

        result["modifiers"] = json::object();
        for (auto& [mod, cnt] : modCounts) {
            result["modifiers"][mod] = cnt;
        }

        result["layers"] = json::array();
        for (auto& l : layers) {
            result["layers"].push_back(l);
        }

        int rootCap = (std::min)((int)rootNames.size(), maxRoots);
        result["roots"] = json::array();
        for (int i = 0; i < rootCap; i++) {
            result["roots"].push_back(rootNames[i]);
        }
        result["rootCount"] = (int)rootNames.size();

        return result.dump();
    });
}

// ── native:selection_snapshot ───────────────────────────────────
std::string NativeHandlers::SelectionSnapshot(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = params.empty() ? json::object() : json::parse(params, nullptr, false);
        int maxItems = 50;
        if (p.is_object() && p.contains("max_items")) {
            maxItems = p["max_items"].get<int>();
        }

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();
        int selCount = ip->GetSelNodeCount();
        int limit = (std::min)(selCount, maxItems);

        json objects = json::array();
        for (int i = 0; i < limit; i++) {
            INode* n = ip->GetSelNode(i);
            json obj;
            obj["name"] = WideToUtf8(n->GetName());
            obj["class"] = NodeClassName(n);

            // Parent
            INode* parent = n->GetParentNode();
            if (parent && !parent->IsRootNode()) {
                obj["parent"] = WideToUtf8(parent->GetName());
            } else {
                obj["parent"] = nullptr;
            }

            // Material
            Mtl* mtl = n->GetMtl();
            if (mtl) {
                obj["material"] = WideToUtf8(mtl->GetName().data());
            } else {
                obj["material"] = nullptr;
            }

            // Modifiers (class names only)
            json mods = json::array();
            Object* objRef = n->GetObjectRef();
            if (objRef && objRef->SuperClassID() == GEN_DERIVOB_CLASS_ID) {
                IDerivedObject* dobj = (IDerivedObject*)objRef;
                for (int m = 0; m < dobj->NumModifiers(); m++) {
                    Modifier* mod = dobj->GetModifier(m);
                    if (mod) {
                        mods.push_back(WideToUtf8(mod->ClassName().data()));
                    }
                }
            }
            obj["modifiers"] = mods;

            // Position
            obj["pos"] = NodePosition(n, t);

            // Bounding box
            ObjectState os = n->EvalWorldState(t);
            if (os.obj) {
                Box3 bbox;
                os.obj->GetDeformBBox(t, bbox);
                Matrix3 tm = n->GetNodeTM(t);
                // Transform bbox corners to world space
                Point3 bmin = bbox.Min() * tm;
                Point3 bmax = bbox.Max() * tm;
                // Re-compute actual world-space bbox from all 8 corners
                Point3 corners[8];
                corners[0] = Point3(bbox.Min().x, bbox.Min().y, bbox.Min().z) * tm;
                corners[1] = Point3(bbox.Max().x, bbox.Min().y, bbox.Min().z) * tm;
                corners[2] = Point3(bbox.Min().x, bbox.Max().y, bbox.Min().z) * tm;
                corners[3] = Point3(bbox.Max().x, bbox.Max().y, bbox.Min().z) * tm;
                corners[4] = Point3(bbox.Min().x, bbox.Min().y, bbox.Max().z) * tm;
                corners[5] = Point3(bbox.Max().x, bbox.Min().y, bbox.Max().z) * tm;
                corners[6] = Point3(bbox.Min().x, bbox.Max().y, bbox.Max().z) * tm;
                corners[7] = Point3(bbox.Max().x, bbox.Max().y, bbox.Max().z) * tm;
                Point3 wMin = corners[0], wMax = corners[0];
                for (int c = 1; c < 8; c++) {
                    if (corners[c].x < wMin.x) wMin.x = corners[c].x;
                    if (corners[c].y < wMin.y) wMin.y = corners[c].y;
                    if (corners[c].z < wMin.z) wMin.z = corners[c].z;
                    if (corners[c].x > wMax.x) wMax.x = corners[c].x;
                    if (corners[c].y > wMax.y) wMax.y = corners[c].y;
                    if (corners[c].z > wMax.z) wMax.z = corners[c].z;
                }
                obj["bbox"] = json::array({
                    json::array({wMin.x, wMin.y, wMin.z}),
                    json::array({wMax.x, wMax.y, wMax.z})
                });
            } else {
                obj["bbox"] = nullptr;
            }

            objects.push_back(obj);
        }

        json result;
        result["selected"] = selCount;
        result["objects"] = objects;
        return result.dump();
    });
}

// ── native:find_class_instances ─────────────────────────────────
std::string NativeHandlers::FindClassInstances(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string className = p.value("class_name", "");
        int limit = p.value("limit", 100);

        if (className.empty()) {
            throw std::runtime_error("class_name is required");
        }

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();
        INode* root = ip->GetRootNode();

        std::vector<INode*> allNodes;
        CollectNodes(root, allNodes);

        json instances = json::array();
        int count = 0;

        for (INode* n : allNodes) {
            if (NodeClassName(n) == className) {
                count++;
                if ((int)instances.size() < limit) {
                    json obj;
                    obj["name"] = WideToUtf8(n->GetName());
                    obj["position"] = NodePosition(n, t);
                    obj["isHidden"] = (bool)n->IsHidden();
                    obj["layer"] = NodeLayerName(n);
                    instances.push_back(obj);
                }
            }
        }

        json result;
        result["className"] = className;
        result["totalFound"] = count;
        result["instances"] = instances;
        return result.dump();
    });
}

// ── native:get_hierarchy ────────────────────────────────────────
static json BuildHierarchy(INode* node, TimeValue t, int depth, int maxDepth) {
    json obj;
    obj["name"] = WideToUtf8(node->GetName());
    obj["class"] = NodeClassName(node);

    if (depth < maxDepth) {
        json children = json::array();
        for (int i = 0; i < node->NumberOfChildren(); i++) {
            children.push_back(BuildHierarchy(node->GetChildNode(i), t, depth + 1, maxDepth));
        }
        if (!children.empty()) {
            obj["children"] = children;
        }
    } else if (node->NumberOfChildren() > 0) {
        obj["childCount"] = node->NumberOfChildren();
    }

    return obj;
}

std::string NativeHandlers::GetHierarchy(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string name = p.value("name", "");

        if (name.empty()) {
            throw std::runtime_error("name is required");
        }

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        INode* node = FindUniqueNodeByName(name);
        if (!node) {
            throw std::runtime_error("Object not found: " + name);
        }

        return BuildHierarchy(node, t, 0, 10).dump();
    });
}

// ═════════════════════════════════════════════════════════════════
// native:scene_delta — track changes between calls
// ═════════════════════════════════════════════════════════════════

struct NodeState {
    std::string className;
    float px, py, pz;
    std::string material;
    int modifierCount;
    bool hidden;
};

using SceneDeltaSnapshot = std::map<std::string, NodeState>;

// Each session's cursor (journal position and epoch of its last call) lives in
// the shell (McpHost::SceneDeltaCursors), so a core reload keeps clients'
// deltas. Here only the snapshot fallback for a scene without the journal;
// after a reload it is gone and that session's next call is a baseline.
// Pipe workers of different sessions run scene_delta concurrently. Snapshots
// are immutable and shared, so a poll takes one under the lock without
// copying it and diffs outside the lock.
static std::mutex g_sceneDeltaMutex;
static std::map<std::string, std::shared_ptr<const SceneDeltaSnapshot>> g_sceneDeltaSnapshots;

static std::string NormalizeSceneDeltaSessionId(const std::string& session_id) {
    return session_id.empty() ? "__default__" : session_id;
}

static float RoundSceneDeltaPosition(float value) {
    return std::nearbyint(value * 10.0f) / 10.0f;
}

static std::map<std::string, NodeState> CaptureSceneState(Interface* ip) {
    std::map<std::string, NodeState> state;
    TimeValue t = ip->GetTime();
    INode* root = ip->GetRootNode();
    std::vector<INode*> nodes;
    CollectNodes(root, nodes);

    for (INode* n : nodes) {
        std::string name = WideToUtf8(n->GetName());
        NodeState ns;
        ns.className = NodeClassName(n);

        Matrix3 tm = n->GetNodeTM(t);
        Point3 pos = tm.GetTrans();
        ns.px = pos.x; ns.py = pos.y; ns.pz = pos.z;

        Mtl* mtl = n->GetMtl();
        ns.material = mtl ? WideToUtf8(mtl->GetName().data()) : "";

        ns.modifierCount = 0;
        Object* objRef = n->GetObjectRef();
        if (objRef && objRef->SuperClassID() == GEN_DERIVOB_CLASS_ID) {
            IDerivedObject* dobj = (IDerivedObject*)objRef;
            ns.modifierCount = dobj->NumModifiers();
        }

        ns.hidden = n->IsHidden() != 0;
        state[name] = ns;
    }
    return state;
}

void NativeHandlers::ResetSceneDeltaSessions() {
    {
        std::lock_guard<std::mutex> lock(g_sceneDeltaMutex);
        g_sceneDeltaSnapshots.clear();
    }
    // A new journal epoch: every stored cursor now asks for a baseline.
    McpHost::SceneJournal::Reset();
}

void NativeHandlers::ReleaseSceneDeltaSession(const std::string& session_id) {
    std::lock_guard<std::mutex> lock(g_sceneDeltaMutex);
    g_sceneDeltaSnapshots.erase(NormalizeSceneDeltaSessionId(session_id));
}

std::string NativeHandlers::SceneDelta(
    const std::string& params,
    MCPBridgeGUP* gup,
    const std::string& session_id) {
    return gup->GetExecutor().ExecuteSync([&params, &session_id]() -> std::string {
        json p = params.empty() ? json::object() : json::parse(params, nullptr, false);
        bool forceCapture = p.value("capture", false);
        unsigned long long unchangedSince = p.value("unchanged_since", 0ULL);
        const std::string sessionKey = NormalizeSceneDeltaSessionId(session_id);

        Interface* ip = GetCOREInterface();
        // Epoch before seq: a reset in between then reads as a new epoch.
        const unsigned long long epoch = McpHost::SceneJournal::Epoch();
        const unsigned long long currentSeq = McpHost::SceneJournal::CurrentSeq();

        if (unchangedSince > 0ULL) {
            json changes = McpHost::SceneJournal::ChangesSince(unchangedSince);
            json result;
            result["journal"] = true;
            result["since"] = unchangedSince;
            result["currentSeq"] = currentSeq;
            result["unchanged"] = changes.empty();
            if (!changes.empty()) {
                result["changes"] = changes;
                result["counts"] = {{"changed", changes.size()}};
            }
            return result.dump();
        }

        const bool journal = McpHost::SceneJournal::IsRegistered();
        // A fresh baseline: the cursor, and the snapshot only without the journal.
        const auto baseline = [&](SceneDeltaSnapshot current) -> std::string {
            json result;
            result["baseline"] = true;
            result["objectCount"] = (int)current.size();
            result["journal"] = journal;
            result["currentSeq"] = currentSeq;
            auto stored = journal ? nullptr : std::make_shared<const SceneDeltaSnapshot>(std::move(current));
            {
                std::lock_guard<std::mutex> lock(g_sceneDeltaMutex);
                if (stored) g_sceneDeltaSnapshots[sessionKey] = std::move(stored);
                else g_sceneDeltaSnapshots.erase(sessionKey);
            }
            McpHost::SceneDeltaCursors::Set(sessionKey, {currentSeq, epoch});
            return result.dump();
        };

        McpHost::SceneDeltaCursors::Cursor cursor;
        const bool known = McpHost::SceneDeltaCursors::Get(sessionKey, cursor);
        if (forceCapture || !known || cursor.epoch != epoch) {
            return baseline(CaptureSceneState(ip));
        }

        if (journal) {
            const unsigned long long since = cursor.journalSeq;
            json changes = McpHost::SceneJournal::ChangesSince(since);
            McpHost::SceneDeltaCursors::Set(sessionKey, {currentSeq, epoch});

            json result;
            result["journal"] = true;
            result["since"] = since;
            result["currentSeq"] = currentSeq;
            result["unchanged"] = changes.empty();
            result["changes"] = changes;
            result["counts"] = {{"changed", changes.size()}};
            return result.dump();
        }

        auto current = CaptureSceneState(ip);
        std::shared_ptr<const SceneDeltaSnapshot> stored;
        {
            std::lock_guard<std::mutex> lock(g_sceneDeltaMutex);
            const auto found = g_sceneDeltaSnapshots.find(sessionKey);
            if (found != g_sceneDeltaSnapshots.end()) stored = found->second;
        }
        // The core was reloaded since this session's last call: start over.
        if (!stored) return baseline(std::move(current));
        const SceneDeltaSnapshot& previous = *stored;

        // Diff
        json added = json::array();
        json removed = json::array();
        json modified = json::array();

        // Find added and modified
        for (auto& [name, cur] : current) {
            auto it = previous.find(name);
            if (it == previous.end()) {
                added.push_back({{"name", name}, {"class", cur.className}});
            } else {
                auto& prev = it->second;
                json changes = json::object();

                if (prev.className != cur.className)
                    changes["class"] = {{"from", prev.className}, {"to", cur.className}};

                // Round to 0.1 for position comparison
                if (RoundSceneDeltaPosition(prev.px) != RoundSceneDeltaPosition(cur.px) ||
                    RoundSceneDeltaPosition(prev.py) != RoundSceneDeltaPosition(cur.py) ||
                    RoundSceneDeltaPosition(prev.pz) != RoundSceneDeltaPosition(cur.pz))
                    changes["position"] = {
                        {"from", json::array({prev.px, prev.py, prev.pz})},
                        {"to", json::array({cur.px, cur.py, cur.pz})}
                    };

                if (prev.material != cur.material)
                    changes["material"] = {
                        {"from", prev.material.empty() ? json(nullptr) : json(prev.material)},
                        {"to", cur.material.empty() ? json(nullptr) : json(cur.material)}
                    };

                if (prev.modifierCount != cur.modifierCount)
                    changes["modifierCount"] = {{"from", prev.modifierCount}, {"to", cur.modifierCount}};

                if (prev.hidden != cur.hidden)
                    changes["hidden"] = {{"from", prev.hidden}, {"to", cur.hidden}};

                if (!changes.empty()) {
                    changes["name"] = name;
                    modified.push_back(changes);
                }
            }
        }

        // Find removed
        for (auto& [name, prev] : previous) {
            if (current.find(name) == current.end()) {
                removed.push_back({{"name", name}, {"class", prev.className}});
            }
        }

        json result;
        result["added"] = added;
        result["removed"] = removed;
        result["modified"] = modified;
        result["counts"] = {
            {"added", added.size()},
            {"removed", removed.size()},
            {"modified", modified.size()},
            {"total", (int)current.size()}
        };

        // Update baseline: the new snapshot replaces the old one, moved, not copied.
        auto next = std::make_shared<const SceneDeltaSnapshot>(std::move(current));
        {
            std::lock_guard<std::mutex> lock(g_sceneDeltaMutex);
            g_sceneDeltaSnapshots[sessionKey] = std::move(next);
        }
        McpHost::SceneDeltaCursors::Set(sessionKey, {currentSeq, epoch});
        result["currentSeq"] = currentSeq;
        return result.dump();
    });
}
