#include "mcp_bridge/scene_journal.h"
#include "mcp_bridge/handler_helpers.h"

#include <ISceneEventManager.h>
#include <deque>
#include <mutex>

using namespace HandlerHelpers;

namespace SceneJournal {

namespace {

constexpr size_t kMaxJournalEntries = 4096;

std::mutex g_mutex;
std::deque<json> g_entries;
unsigned long long g_seq = 0;
unsigned long long g_mutationSeq = 0;
SceneEventNamespace::CallbackKey g_callbackKey = 0;

void AppendEvent(
    const std::string& type,
    INodeEventCallback::NodeKeyTab& nodes,
    bool mutation = true) {
    std::lock_guard<std::mutex> lock(g_mutex);
    for (int i = 0; i < nodes.Count(); ++i) {
        const auto key = nodes[i];
        INode* node = NodeEventNamespace::GetNodeByKey(key);

        json entry;
        entry["seq"] = ++g_seq;
        entry["type"] = type;
        if (mutation) entry["mutationSeq"] = ++g_mutationSeq;
        entry["handle"] = static_cast<unsigned long long>(key);
        if (node) {
            entry["name"] = WideToUtf8(node->GetName());
            entry["class"] = NodeClassName(node);
            entry["layer"] = NodeLayerName(node);
        }

        g_entries.push_back(entry);
        while (g_entries.size() > kMaxJournalEntries) {
            g_entries.pop_front();
        }
    }
}

class JournalNodeCallback final : public INodeEventCallback {
public:
    BOOL VerboseDeleted() override { return TRUE; }

    void Added(NodeKeyTab& nodes) override { AppendEvent("added", nodes); }
    void Deleted(NodeKeyTab& nodes) override { AppendEvent("deleted", nodes); }
    void LinkChanged(NodeKeyTab& nodes) override { AppendEvent("link_changed", nodes); }
    void LayerChanged(NodeKeyTab& nodes) override { AppendEvent("layer_changed", nodes); }
    void GroupChanged(NodeKeyTab& nodes) override { AppendEvent("group_changed", nodes); }
    void HierarchyOtherEvent(NodeKeyTab& nodes) override { AppendEvent("hierarchy_changed", nodes); }
    void ModelStructured(NodeKeyTab& nodes) override { AppendEvent("model_structured", nodes); }
    void GeometryChanged(NodeKeyTab& nodes) override { AppendEvent("geometry_changed", nodes); }
    void TopologyChanged(NodeKeyTab& nodes) override { AppendEvent("topology_changed", nodes); }
    void MappingChanged(NodeKeyTab& nodes) override { AppendEvent("mapping_changed", nodes); }
    void ExtentionChannelChanged(NodeKeyTab& nodes) override { AppendEvent("extension_channel_changed", nodes); }
    void ModelOtherEvent(NodeKeyTab& nodes) override { AppendEvent("model_changed", nodes); }
    void MaterialStructured(NodeKeyTab& nodes) override { AppendEvent("material_structured", nodes); }
    void MaterialOtherEvent(NodeKeyTab& nodes) override { AppendEvent("material_changed", nodes); }
    void ControllerStructured(NodeKeyTab& nodes) override { AppendEvent("controller_structured", nodes); }
    void ControllerOtherEvent(NodeKeyTab& nodes) override { AppendEvent("controller_changed", nodes); }
    void NameChanged(NodeKeyTab& nodes) override { AppendEvent("name_changed", nodes); }
    void WireColorChanged(NodeKeyTab& nodes) override { AppendEvent("wire_color_changed", nodes); }
    void RenderPropertiesChanged(NodeKeyTab& nodes) override { AppendEvent("render_properties_changed", nodes); }
    void DisplayPropertiesChanged(NodeKeyTab& nodes) override { AppendEvent("display_properties_changed", nodes); }
    void UserPropertiesChanged(NodeKeyTab& nodes) override { AppendEvent("user_properties_changed", nodes); }
    void PropertiesOtherEvent(NodeKeyTab& nodes) override { AppendEvent("properties_changed", nodes); }
    void SubobjectSelectionChanged(NodeKeyTab& nodes) override {
        AppendEvent("subobject_selection_changed", nodes, false);
    }
    void SelectionChanged(NodeKeyTab& nodes) override {
        AppendEvent("selection_changed", nodes, false);
    }
    void HideChanged(NodeKeyTab& nodes) override { AppendEvent("hide_changed", nodes); }
    void FreezeChanged(NodeKeyTab& nodes) override { AppendEvent("freeze_changed", nodes); }
    void DisplayOtherEvent(NodeKeyTab& nodes) override { AppendEvent("display_changed", nodes); }
};

JournalNodeCallback g_callback;

} // namespace

void Register() {
    if (g_callbackKey != 0) return;
    ISceneEventManager* manager = GetISceneEventManager();
    if (!manager) return;
    g_callbackKey = manager->RegisterCallback(&g_callback, FALSE, 0, FALSE);
}

void Unregister() {
    if (g_callbackKey == 0) return;
    ISceneEventManager* manager = GetISceneEventManager();
    if (manager) {
        manager->UnRegisterCallback(g_callbackKey);
    }
    g_callbackKey = 0;
}

void Reset() {
    std::lock_guard<std::mutex> lock(g_mutex);
    g_entries.clear();
    ++g_seq;
    ++g_mutationSeq;
}

bool IsRegistered() {
    return g_callbackKey != 0;
}

bool FlushPending() {
    if (g_callbackKey == 0) return false;
    ISceneEventManager* manager = GetISceneEventManager();
    if (!manager) return false;
    manager->TriggerMessages(g_callbackKey);
    return true;
}

unsigned long long CurrentSeq() {
    std::lock_guard<std::mutex> lock(g_mutex);
    return g_seq;
}

unsigned long long CurrentMutationSeq() {
    std::lock_guard<std::mutex> lock(g_mutex);
    return g_mutationSeq;
}

unsigned long long AppendSynthetic(const std::string& type, const json& details) {
    std::lock_guard<std::mutex> lock(g_mutex);
    json entry;
    entry["seq"] = ++g_seq;
    entry["mutationSeq"] = ++g_mutationSeq;
    entry["type"] = type;
    if (details.is_object() && !details.empty()) entry["details"] = details;
    g_entries.push_back(entry);
    while (g_entries.size() > kMaxJournalEntries) g_entries.pop_front();
    return g_mutationSeq;
}

json ChangesSince(unsigned long long since, size_t limit) {
    std::lock_guard<std::mutex> lock(g_mutex);
    json changes = json::array();
    for (const json& entry : g_entries) {
        if (entry.value("seq", 0ULL) <= since) continue;
        changes.push_back(entry);
        if (changes.size() >= limit) break;
    }
    return changes;
}

json MutationChangesSince(unsigned long long since, size_t limit) {
    std::lock_guard<std::mutex> lock(g_mutex);
    json changes = json::array();
    for (const json& entry : g_entries) {
        if (!entry.contains("mutationSeq")) continue;
        if (entry.value("mutationSeq", 0ULL) <= since) continue;
        changes.push_back(entry);
        if (changes.size() >= limit) break;
    }
    return changes;
}

} // namespace SceneJournal
