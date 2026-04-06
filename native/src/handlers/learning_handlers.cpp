#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/bridge_gup.h"

#include <modstack.h>
#include <GraphicsWindow.h>
#include <set>
#include <map>
#include <deque>
#include <mutex>
#include <notify.h>
#if MAX_SDK_VERSION >= 2027
#include <notifyParams.h>
#endif

using json = nlohmann::json;
using namespace HandlerHelpers;

// ═════════════════════════════════════════════════════════════════
// 1. REFERENCE GRAPH WALKER
// ═════════════════════════════════════════════════════════════════

static std::string RefTargetClassName(ReferenceTarget* rt) {
    if (!rt) return "null";
    try { return WideToUtf8(rt->ClassName().data()); }
    catch (...) { return "unknown"; }
}

static std::string SuperClassLabel(SClass_ID sid) {
    if (sid == GEOMOBJECT_CLASS_ID) return "geometry";
    if (sid == CAMERA_CLASS_ID) return "camera";
    if (sid == LIGHT_CLASS_ID) return "light";
    if (sid == SHAPE_CLASS_ID) return "shape";
    if (sid == HELPER_CLASS_ID) return "helper";
    if (sid == MATERIAL_CLASS_ID) return "material";
    if (sid == TEXMAP_CLASS_ID) return "texturemap";
    if (sid == OSM_CLASS_ID) return "modifier";
    if (sid == WSM_CLASS_ID) return "wsModifier";
    if (sid == BASENODE_CLASS_ID) return "node";
    return "sid_" + std::to_string(sid);
}

static json WalkRefs(ReferenceTarget* rt, int depth, int maxDepth,
                     std::set<ReferenceTarget*>& visited) {
    if (!rt || depth >= maxDepth) return nullptr;
    if (visited.count(rt)) return json{{"_circular", RefTargetClassName(rt)}};
    visited.insert(rt);

    json node;
    node["class"] = RefTargetClassName(rt);
    node["superclass"] = SuperClassLabel(rt->SuperClassID());

    int numRefs = rt->NumRefs();
    if (numRefs > 0 && depth < maxDepth - 1) {
        json refs = json::array();
        for (int i = 0; i < numRefs && i < 50; i++) {
            ReferenceTarget* child = rt->GetReference(i);
            if (child) {
                json childNode = WalkRefs(child, depth + 1, maxDepth, visited);
                if (!childNode.is_null()) {
                    childNode["refIndex"] = i;
                    refs.push_back(childNode);
                }
            }
        }
        if (!refs.empty()) node["references"] = refs;
    } else if (numRefs > 0) {
        node["refCount"] = numRefs;
    }
    return node;
}

std::string NativeHandlers::WalkReferences(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = params.empty() ? json::object() : json::parse(params, nullptr, false);
        if (!p.is_object() || !p.contains("name"))
            throw std::runtime_error("name is required");

        std::string objName = p["name"].get<std::string>();
        int maxDepth = (std::min)(p.value("max_depth", 4), 8);

        INode* node = FindNodeByName(objName);
        if (!node) throw std::runtime_error("Object not found: " + objName);

        std::set<ReferenceTarget*> visited;
        json result;
        result["name"] = WideToUtf8(node->GetName());
        result["nodeClass"] = NodeClassName(node);
        result["graph"] = WalkRefs(node, 0, maxDepth, visited);
        return result.dump();
    });
}

// ═════════════════════════════════════════════════════════════════
// 2. CLASS RELATIONSHIP MAPPER
// ═════════════════════════════════════════════════════════════════

std::string NativeHandlers::MapClassRelationships(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = params.empty() ? json::object() : json::parse(params, nullptr, false);
        std::string filterPattern = p.value("pattern", "");
        std::string filterSuper = p.value("superclass", "");
        int limit = p.value("limit", 100);

        SClass_ID filterSID = 0;
        if (!filterSuper.empty()) {
            std::string lower = filterSuper;
            for (auto& c : lower) c = (char)tolower((unsigned char)c);
            if (lower == "geometry") filterSID = GEOMOBJECT_CLASS_ID;
            else if (lower == "modifier") filterSID = OSM_CLASS_ID;
            else if (lower == "material") filterSID = MATERIAL_CLASS_ID;
            else if (lower == "texturemap") filterSID = TEXMAP_CLASS_ID;
            else if (lower == "helper") filterSID = HELPER_CLASS_ID;
            else if (lower == "light") filterSID = LIGHT_CLASS_ID;
            else if (lower == "camera") filterSID = CAMERA_CLASS_ID;
        }

        auto& dir = DllDir::GetInstance();
        json relationships = json::array();
        int count = 0;

        for (int d = 0; d < dir.Count() && count < limit; d++) {
            const DllDesc& dll = dir[d];
            for (int c = 0; c < dll.NumberOfClasses() && count < limit; c++) {
                ClassDesc* cd = dll[c];
                if (!cd) continue;
                if (filterSID != 0 && cd->SuperClassID() != filterSID) continue;

                std::string className = cd->ClassName() ? WideToUtf8(cd->ClassName()) : "";
                if (className.empty()) continue;
                if (!filterPattern.empty() && !WildcardMatch(className, filterPattern)) continue;

                ClassDesc2* cd2 = dynamic_cast<ClassDesc2*>(cd);
                if (!cd2) continue;

                json entry;
                entry["class"] = className;
                entry["superclass"] = SuperClassLabel(cd->SuperClassID());

                // Only include user-facing superclasses
                SClass_ID sid = cd->SuperClassID();
                if (sid != GEOMOBJECT_CLASS_ID && sid != OSM_CLASS_ID &&
                    sid != MATERIAL_CLASS_ID && sid != TEXMAP_CLASS_ID &&
                    sid != HELPER_CLASS_ID && sid != LIGHT_CLASS_ID &&
                    sid != CAMERA_CLASS_ID) continue;

                json accepts = json::object();
                for (int pb = 0; pb < cd2->NumParamBlockDescs(); pb++) {
                    ParamBlockDesc2* pbd = cd2->GetParamBlockDesc(pb);
                    if (!pbd) continue;
                    for (int pi = 0; pi < pbd->count; pi++) {
                        ParamID pid = pbd->IndextoID(pi);
                        const ParamDef& pd = pbd->GetParamDef(pid);
                        int base = pd.type & ~TYPE_TAB;
                        std::string paramName = pd.int_name ? WideToUtf8(pd.int_name) : "";

                        const char* refType = nullptr;
                        if (base == TYPE_INODE) refType = "node";
                        else if (base == TYPE_MTL) refType = "material";
                        else if (base == TYPE_TEXMAP) refType = "texturemap";
                        else if (base == TYPE_REFTARG) refType = "refTarget";
                        else if (base == TYPE_OBJECT) refType = "object";

                        if (refType) {
                            if (!accepts.contains(refType)) accepts[refType] = json::array();
                            accepts[refType].push_back(paramName);
                        }
                    }
                }

                if (!accepts.empty()) {
                    entry["accepts"] = accepts;
                    relationships.push_back(entry);
                    count++;
                }
            }
        }

        json result;
        result["count"] = count;
        result["relationships"] = relationships;
        return result.dump();
    });
}

// ═════════════════════════════════════════════════════════════════
// 3. SCENE PATTERN LEARNER
// ═════════════════════════════════════════════════════════════════

std::string NativeHandlers::LearnScenePatterns(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        Interface* ip = GetCOREInterface();
        INode* root = ip->GetRootNode();
        std::vector<INode*> nodes;
        CollectNodes(root, nodes);

        std::map<std::string, int> geometryClasses, materialClasses, modifierClasses, texmapClasses;
        std::map<std::string, int> modifierStacks, materialOnGeometry, modifierOnGeometry, texmapInMaterial;

        for (INode* n : nodes) {
            std::string geoClass = NodeClassName(n);
            geometryClasses[geoClass]++;

            Mtl* mtl = n->GetMtl();
            if (mtl) {
                std::string mtlClass = WideToUtf8(mtl->ClassName().data());
                materialClasses[mtlClass]++;
                materialOnGeometry[mtlClass + " -> " + geoClass]++;
                for (int t = 0; t < mtl->NumSubTexmaps(); t++) {
                    Texmap* tm = mtl->GetSubTexmap(t);
                    if (tm) {
                        std::string tmClass = WideToUtf8(tm->ClassName().data());
                        texmapClasses[tmClass]++;
                        texmapInMaterial[tmClass + " -> " + mtlClass]++;
                    }
                }
                for (int s = 0; s < mtl->NumSubMtls(); s++) {
                    Mtl* subMtl = mtl->GetSubMtl(s);
                    if (subMtl) materialClasses[WideToUtf8(subMtl->ClassName().data())]++;
                }
            }

            Object* objRef = n->GetObjectRef();
            if (objRef && objRef->SuperClassID() == GEN_DERIVOB_CLASS_ID) {
                IDerivedObject* dobj = (IDerivedObject*)objRef;
                std::string stackStr;
                for (int m = 0; m < dobj->NumModifiers(); m++) {
                    Modifier* mod = dobj->GetModifier(m);
                    if (!mod) continue;
                    std::string modClass = WideToUtf8(mod->ClassName().data());
                    modifierClasses[modClass]++;
                    modifierOnGeometry[modClass + " -> " + geoClass]++;
                    if (!stackStr.empty()) stackStr += " | ";
                    stackStr += modClass;
                }
                if (!stackStr.empty()) modifierStacks[stackStr]++;
            }
        }

        auto sortedMap = [](const std::map<std::string, int>& m, int cap) -> json {
            std::vector<std::pair<std::string, int>> sorted(m.begin(), m.end());
            std::sort(sorted.begin(), sorted.end(), [](auto& a, auto& b) { return a.second > b.second; });
            json arr = json::array();
            for (int n = 0; n < (int)sorted.size() && n < cap; n++)
                arr.push_back(json{{"pattern", sorted[n].first}, {"count", sorted[n].second}});
            return arr;
        };

        json result;
        result["totalNodes"] = (int)nodes.size();
        result["geometryClasses"] = sortedMap(geometryClasses, 30);
        result["materialClasses"] = sortedMap(materialClasses, 20);
        result["modifierClasses"] = sortedMap(modifierClasses, 20);
        result["texmapClasses"] = sortedMap(texmapClasses, 20);
        result["modifierStacks"] = sortedMap(modifierStacks, 20);
        result["materialOnGeometry"] = sortedMap(materialOnGeometry, 20);
        result["modifierOnGeometry"] = sortedMap(modifierOnGeometry, 20);
        result["texmapInMaterial"] = sortedMap(texmapInMaterial, 20);
        return result.dump();
    });
}

// ═════════════════════════════════════════════════════════════════
// 4. DEEP SCENE EVENT WATCHER
// ═════════════════════════════════════════════════════════════════

struct SceneEvent {
    std::string type;
    std::string detail;
    DWORD timestamp;
};

static std::mutex g_eventMutex;
static std::deque<SceneEvent> g_eventBuffer;
static const size_t MAX_EVENTS = 500;
static bool g_watcherActive = false;
static bool g_callbacksRegistered = false;

static void PushEvent(const std::string& type, const std::string& detail = "") {
    std::lock_guard<std::mutex> lock(g_eventMutex);
    if (g_eventBuffer.size() >= MAX_EVENTS) g_eventBuffer.pop_front();
    g_eventBuffer.push_back({type, detail, GetTickCount()});
}

// ── Viewport overlay ────────────────────────────────────────────
class MCPWatcherOverlay : public ViewportDisplayCallback {
public:
    void Display(TimeValue t, ViewExp* vpt, int flags) override {
        if (!active) return;
        GraphicsWindow* gw = vpt->getGW();
        if (!gw) return;

        // Green text
        Point3 color(0.0f, 1.0f, 0.3f);
        gw->setColor(TEXT_COLOR, color);

        // Screen-space text via hText
        IPoint3 pos(15, 25, 0);
        gw->hText(&pos, _T("MCP WATCHER LIVE"));
    }

    void GetViewportRect(TimeValue t, ViewExp* vpt, Rect* rect) override {
        rect->left = 10;
        rect->top = 10;
        rect->right = 250;
        rect->bottom = 40;
    }

    BOOL Foreground() override { return TRUE; }

    bool active = false;
};

static MCPWatcherOverlay g_overlay;
static bool g_overlayRegistered = false;

static void RegisterOverlay() {
    if (g_overlayRegistered) return;
    Interface* ip = GetCOREInterface();
    if (ip) {
        g_overlay.active = true;
        ip->RegisterViewportDisplayCallback(FALSE, &g_overlay);
        ip->RedrawViews(ip->GetTime());
        g_overlayRegistered = true;
    }
}

static void UnregisterOverlay() {
    if (!g_overlayRegistered) return;
    Interface* ip = GetCOREInterface();
    if (ip) {
        g_overlay.active = false;
        ip->UnRegisterViewportDisplayCallback(FALSE, &g_overlay);
        ip->RedrawViews(ip->GetTime());
        g_overlayRegistered = false;
    }
}

// ── Deep notification callbacks ─────────────────────────────────

// ── Notify callParam extraction — 2027 SDK uses typed GetNotifyParam<> ──
// For older SDKs, callParam is void* and can be cast directly.
#if MAX_SDK_VERSION >= 2027
#   define GET_NOTIFY_NODE(info, code)  GetNotifyParam<code>(info)
#else
#   define GET_NOTIFY_NODE(info, code)  ((INode*)info->callParam)
#endif

// Node created — callParam is INode*
static void NotifyNodeCreated(void* param, NotifyInfo* info) {
    if (!g_watcherActive || !info) return;
    INode* node = GET_NOTIFY_NODE(info, NOTIFY_NODE_CREATED);
    std::string name = node ? WideToUtf8(node->GetName()) : "";
    std::string cls = "";
    if (node) {
        try {
            ObjectState os = node->EvalWorldState(GetCOREInterface()->GetTime());
            if (os.obj) cls = WideToUtf8(os.obj->ClassName().data());
        } catch (...) {}
    }
    std::string detail = name;
    if (!cls.empty()) detail += " (" + cls + ")";
    PushEvent("node_created", detail);
}

// Node deleted — callParam is INode* (NOTIFY_SCENE_PRE_DELETED_NODE)
static void NotifyNodeDeleted(void* param, NotifyInfo* info) {
    if (!g_watcherActive || !info) return;
#if MAX_SDK_VERSION >= 2027
    INode* node = GetNotifyParam<NOTIFY_SCENE_PRE_DELETED_NODE>(info);
#else
    INode* node = (INode*)info->callParam;
#endif
    std::string name = node ? WideToUtf8(node->GetName()) : "";
    PushEvent("node_deleted", name);
}

// Selection changed
static void NotifySelectionChanged(void* param, NotifyInfo* info) {
    if (!g_watcherActive) return;
    Interface* ip = GetCOREInterface();
    int count = ip->GetSelNodeCount();
    std::string detail;
    if (count == 0) {
        detail = "cleared";
    } else if (count <= 5) {
        for (int i = 0; i < count; i++) {
            if (i > 0) detail += ", ";
            detail += WideToUtf8(ip->GetSelNode(i)->GetName());
        }
    } else {
        detail = std::to_string(count) + " objects";
    }
    PushEvent("selection_changed", detail);
}

// Modifier added/deleted — 2027 uses NotifyModAddDelParam* and PRE/POST codes
static void NotifyModifierAdded(void* param, NotifyInfo* info) {
    if (!g_watcherActive || !info) return;
#if MAX_SDK_VERSION >= 2027
    NotifyModAddDelParam* p = GetNotifyParam<NOTIFY_POST_MODIFIER_ADDED>(info);
#else
    struct ModAddDelParam { INode* node; Modifier* mod; void* mc; };
    if (!info->callParam) return;
    ModAddDelParam* p = (ModAddDelParam*)info->callParam;
#endif
    if (!p) return;
    std::string detail;
    try {
        if (p->mod) detail = WideToUtf8(p->mod->ClassName().data());
        if (p->node) detail += " on " + WideToUtf8(p->node->GetName());
    } catch (...) { detail = "unknown"; }
    PushEvent("modifier_added", detail);
}

static void NotifyModifierDeleted(void* param, NotifyInfo* info) {
    if (!g_watcherActive || !info) return;
#if MAX_SDK_VERSION >= 2027
    NotifyModAddDelParam* p = GetNotifyParam<NOTIFY_POST_MODIFIER_DELETED>(info);
#else
    struct ModAddDelParam { INode* node; Modifier* mod; void* mc; };
    if (!info->callParam) return;
    ModAddDelParam* p = (ModAddDelParam*)info->callParam;
#endif
    if (!p) return;
    std::string detail;
    try {
        if (p->mod) detail = WideToUtf8(p->mod->ClassName().data());
        if (p->node) detail += " from " + WideToUtf8(p->node->GetName());
    } catch (...) { detail = "unknown"; }
    PushEvent("modifier_deleted", detail);
}

// Material assigned — callParam is INode* (NOTIFY_NODE_POST_MTL)
static void NotifyMaterialAssigned(void* param, NotifyInfo* info) {
    if (!g_watcherActive || !info) return;
#if MAX_SDK_VERSION >= 2027
    INode* node = GetNotifyParam<NOTIFY_NODE_POST_MTL>(info);
#else
    INode* node = (INode*)info->callParam;
#endif
    std::string detail;
    if (node) {
        detail = WideToUtf8(node->GetName());
        Mtl* mtl = node->GetMtl();
        if (mtl) detail += " <- " + WideToUtf8(mtl->ClassName().data());
    }
    PushEvent("material_assigned", detail);
}

// Node renamed — callParam is NameChange*
static void NotifyNodeRenamed(void* param, NotifyInfo* info) {
    if (!g_watcherActive || !info) return;
#if MAX_SDK_VERSION >= 2027
    NameChange* nc = GetNotifyParam<NOTIFY_NODE_RENAMED>(info);
#else
    if (!info->callParam) return;
    struct NameChange { const MCHAR* oldname; const MCHAR* newname; };
    NameChange* nc = (NameChange*)info->callParam;
#endif
    std::string detail;
    try {
        if (nc && nc->oldname && nc->newname)
            detail = WideToUtf8(nc->oldname) + " -> " + WideToUtf8(nc->newname);
    } catch (...) {}
    PushEvent("node_renamed", detail);
}

// Node hide/unhide/freeze/unfreeze — callParam is INode*
static void NotifyNodeHide(void* param, NotifyInfo* info) {
    if (!g_watcherActive || !info) return;
    INode* node = GET_NOTIFY_NODE(info, NOTIFY_NODE_HIDE);
    PushEvent("node_hidden", node ? WideToUtf8(node->GetName()) : "");
}
static void NotifyNodeUnhide(void* param, NotifyInfo* info) {
    if (!g_watcherActive || !info) return;
    INode* node = GET_NOTIFY_NODE(info, NOTIFY_NODE_UNHIDE);
    PushEvent("node_unhidden", node ? WideToUtf8(node->GetName()) : "");
}
static void NotifyNodeFreeze(void* param, NotifyInfo* info) {
    if (!g_watcherActive || !info) return;
    INode* node = GET_NOTIFY_NODE(info, NOTIFY_NODE_FREEZE);
    PushEvent("node_frozen", node ? WideToUtf8(node->GetName()) : "");
}
static void NotifyNodeUnfreeze(void* param, NotifyInfo* info) {
    if (!g_watcherActive || !info) return;
    INode* node = GET_NOTIFY_NODE(info, NOTIFY_NODE_UNFREEZE);
    PushEvent("node_unfrozen", node ? WideToUtf8(node->GetName()) : "");
}

// Sub-object level change
static void NotifySubObjectChanged(void* param, NotifyInfo* info) {
    if (!g_watcherActive || !info) return;
#if MAX_SDK_VERSION >= 2027
    NumberChange* nc = GetNotifyParam<NOTIFY_MODPANEL_SUBOBJECTLEVEL_CHANGED>(info);
#else
    if (!info->callParam) return;
    struct NumberChange { int oldNumber; int newNumber; };
    NumberChange* nc = (NumberChange*)info->callParam;
#endif
    if (!nc) return;
    const char* levels[] = {"object", "vertex", "edge", "border", "face", "element"};
    std::string oldLvl = (nc->oldNumber >= 0 && nc->oldNumber <= 5) ? levels[nc->oldNumber] : std::to_string(nc->oldNumber);
    std::string newLvl = (nc->newNumber >= 0 && nc->newNumber <= 5) ? levels[nc->newNumber] : std::to_string(nc->newNumber);
    PushEvent("subobject_changed", oldLvl + " -> " + newLvl);
}

// Undo/redo — callParam is const MCHAR*
static void NotifyUndo(void* param, NotifyInfo* info) {
    if (!g_watcherActive || !info) return;
#if MAX_SDK_VERSION >= 2027
    const MCHAR* desc = GetNotifyParam<NOTIFY_SCENE_UNDO>(info);
#else
    const MCHAR* desc = (const MCHAR*)info->callParam;
#endif
    PushEvent("undo", desc ? WideToUtf8(desc) : "");
}
static void NotifyRedo(void* param, NotifyInfo* info) {
    if (!g_watcherActive || !info) return;
#if MAX_SDK_VERSION >= 2027
    const MCHAR* desc = GetNotifyParam<NOTIFY_SCENE_REDO>(info);
#else
    const MCHAR* desc = (const MCHAR*)info->callParam;
#endif
    PushEvent("redo", desc ? WideToUtf8(desc) : "");
}

// File and scene events
static void NotifyFilePostOpen(void* param, NotifyInfo* info) {
    if (!g_watcherActive) return;
    PushEvent("file_opened", "");
}
static void NotifySceneReset(void* param, NotifyInfo* info) {
    if (!g_watcherActive) return;
    PushEvent("scene_reset", "");
}
static void NotifyRenderStart(void* param, NotifyInfo* info) {
    if (!g_watcherActive) return;
    PushEvent("render_start", "");
}
static void NotifyRenderEnd(void* param, NotifyInfo* info) {
    if (!g_watcherActive) return;
    PushEvent("render_end", "");
}

// Node linked/unlinked — callParam is INode*
static void NotifyNodeLinked(void* param, NotifyInfo* info) {
    if (!g_watcherActive || !info) return;
    INode* node = GET_NOTIFY_NODE(info, NOTIFY_NODE_LINKED);
    std::string detail = node ? WideToUtf8(node->GetName()) : "";
    if (node) {
        INode* parent = node->GetParentNode();
        if (parent && !parent->IsRootNode())
            detail += " -> " + WideToUtf8(parent->GetName());
    }
    PushEvent("node_linked", detail);
}
static void NotifyNodeUnlinked(void* param, NotifyInfo* info) {
    if (!g_watcherActive || !info) return;
    INode* node = GET_NOTIFY_NODE(info, NOTIFY_NODE_UNLINKED);
    PushEvent("node_unlinked", node ? WideToUtf8(node->GetName()) : "");
}

// ── Registration ────────────────────────────────────────────────
static void RegisterWatcherCallbacks() {
    if (g_callbacksRegistered) return;

    RegisterNotification(NotifyNodeCreated, nullptr, NOTIFY_SCENE_ADDED_NODE);
    RegisterNotification(NotifyNodeDeleted, nullptr, NOTIFY_SCENE_PRE_DELETED_NODE);
    RegisterNotification(NotifySelectionChanged, nullptr, NOTIFY_SELECTIONSET_CHANGED);
    RegisterNotification(NotifyModifierAdded, nullptr, NOTIFY_POST_MODIFIER_ADDED);
    RegisterNotification(NotifyModifierDeleted, nullptr, NOTIFY_POST_MODIFIER_DELETED);
    RegisterNotification(NotifyMaterialAssigned, nullptr, NOTIFY_NODE_POST_MTL);
    RegisterNotification(NotifyNodeRenamed, nullptr, NOTIFY_NODE_RENAMED);
    RegisterNotification(NotifyNodeHide, nullptr, NOTIFY_NODE_HIDE);
    RegisterNotification(NotifyNodeUnhide, nullptr, NOTIFY_NODE_UNHIDE);
    RegisterNotification(NotifyNodeFreeze, nullptr, NOTIFY_NODE_FREEZE);
    RegisterNotification(NotifyNodeUnfreeze, nullptr, NOTIFY_NODE_UNFREEZE);
    RegisterNotification(NotifySubObjectChanged, nullptr, NOTIFY_MODPANEL_SUBOBJECTLEVEL_CHANGED);
    RegisterNotification(NotifyUndo, nullptr, NOTIFY_SCENE_UNDO);
    RegisterNotification(NotifyRedo, nullptr, NOTIFY_SCENE_REDO);
    RegisterNotification(NotifyNodeLinked, nullptr, NOTIFY_NODE_LINKED);
    RegisterNotification(NotifyNodeUnlinked, nullptr, NOTIFY_NODE_UNLINKED);
    RegisterNotification(NotifyFilePostOpen, nullptr, NOTIFY_FILE_POST_OPEN);
    RegisterNotification(NotifySceneReset, nullptr, NOTIFY_SYSTEM_PRE_RESET);
    RegisterNotification(NotifyRenderStart, nullptr, NOTIFY_PRE_RENDER);
    RegisterNotification(NotifyRenderEnd, nullptr, NOTIFY_POST_RENDER);

    g_callbacksRegistered = true;
}

static void UnregisterWatcherCallbacks() {
    if (!g_callbacksRegistered) return;

    UnRegisterNotification(NotifyNodeCreated, nullptr, NOTIFY_SCENE_ADDED_NODE);
    UnRegisterNotification(NotifyNodeDeleted, nullptr, NOTIFY_SCENE_PRE_DELETED_NODE);
    UnRegisterNotification(NotifySelectionChanged, nullptr, NOTIFY_SELECTIONSET_CHANGED);
    UnRegisterNotification(NotifyModifierAdded, nullptr, NOTIFY_POST_MODIFIER_ADDED);
    UnRegisterNotification(NotifyModifierDeleted, nullptr, NOTIFY_POST_MODIFIER_DELETED);
    UnRegisterNotification(NotifyMaterialAssigned, nullptr, NOTIFY_NODE_POST_MTL);
    UnRegisterNotification(NotifyNodeRenamed, nullptr, NOTIFY_NODE_RENAMED);
    UnRegisterNotification(NotifyNodeHide, nullptr, NOTIFY_NODE_HIDE);
    UnRegisterNotification(NotifyNodeUnhide, nullptr, NOTIFY_NODE_UNHIDE);
    UnRegisterNotification(NotifyNodeFreeze, nullptr, NOTIFY_NODE_FREEZE);
    UnRegisterNotification(NotifyNodeUnfreeze, nullptr, NOTIFY_NODE_UNFREEZE);
    UnRegisterNotification(NotifySubObjectChanged, nullptr, NOTIFY_MODPANEL_SUBOBJECTLEVEL_CHANGED);
    UnRegisterNotification(NotifyUndo, nullptr, NOTIFY_SCENE_UNDO);
    UnRegisterNotification(NotifyRedo, nullptr, NOTIFY_SCENE_REDO);
    UnRegisterNotification(NotifyNodeLinked, nullptr, NOTIFY_NODE_LINKED);
    UnRegisterNotification(NotifyNodeUnlinked, nullptr, NOTIFY_NODE_UNLINKED);
    UnRegisterNotification(NotifyFilePostOpen, nullptr, NOTIFY_FILE_POST_OPEN);
    UnRegisterNotification(NotifySceneReset, nullptr, NOTIFY_SYSTEM_PRE_RESET);
    UnRegisterNotification(NotifyRenderStart, nullptr, NOTIFY_PRE_RENDER);
    UnRegisterNotification(NotifyRenderEnd, nullptr, NOTIFY_POST_RENDER);

    g_callbacksRegistered = false;
}

// ═════════════════════════════════════════════════════════════════
// native:watch_scene handler
// ═════════════════════════════════════════════════════════════════

std::string NativeHandlers::WatchScene(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = params.empty() ? json::object() : json::parse(params, nullptr, false);
        std::string action = p.value("action", "status");

        if (action == "start") {
            RegisterWatcherCallbacks();
            g_watcherActive = true;
            RegisterOverlay();
            json result;
            result["watching"] = true;
            result["overlay"] = true;
            result["events_tracked"] = json::array({
                "node_created", "node_deleted", "selection_changed",
                "modifier_added", "modifier_deleted", "material_assigned",
                "node_renamed", "node_hidden", "node_unhidden",
                "node_frozen", "node_unfrozen", "subobject_changed",
                "undo", "redo", "node_linked", "node_unlinked",
                "file_opened", "scene_reset", "render_start", "render_end"
            });
            return result.dump();
        }

        if (action == "stop") {
            g_watcherActive = false;
            UnregisterOverlay();
            json result;
            result["watching"] = false;
            result["overlay"] = false;
            return result.dump();
        }

        if (action == "clear") {
            std::lock_guard<std::mutex> lock(g_eventMutex);
            g_eventBuffer.clear();
            return json{{"cleared", true}}.dump();
        }

        if (action == "get") {
            int since = p.value("since", 0);
            int limit = p.value("limit", 100);

            std::lock_guard<std::mutex> lock(g_eventMutex);
            json events = json::array();
            int count = 0;
            for (auto& evt : g_eventBuffer) {
                if ((int)evt.timestamp <= since) continue;
                if (count >= limit) break;
                json e;
                e["type"] = evt.type;
                if (!evt.detail.empty()) e["detail"] = evt.detail;
                e["time"] = (int)evt.timestamp;
                events.push_back(e);
                count++;
            }

            json result;
            result["watching"] = g_watcherActive;
            result["events"] = events;
            result["eventCount"] = events.size();
            result["bufferTotal"] = g_eventBuffer.size();
            return result.dump();
        }

        // status
        json result;
        result["watching"] = g_watcherActive;
        result["overlay"] = g_overlayRegistered;
        result["callbacksRegistered"] = g_callbacksRegistered;
        result["bufferedEvents"] = g_eventBuffer.size();
        result["maxBuffer"] = MAX_EVENTS;
        return result.dump();
    });
}
