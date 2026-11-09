#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/spatial_snapshot.h"
#include "mcp_bridge/core_bridge.h"

#include <algorithm>
#include <set>

using json = nlohmann::json;
using namespace HandlerHelpers;
using namespace SpatialSnapshot;

namespace {

void AddNodeAndChildren(INode* node, std::vector<INode*>& out) {
    if (!node) return;
    out.push_back(node);
    for (int i = 0; i < node->NumberOfChildren(); ++i) {
        AddNodeAndChildren(node->GetChildNode(i), out);
    }
}

void PushUnique(std::vector<INode*>& nodes, INode* node) {
    if (!node) return;
    if (std::find(nodes.begin(), nodes.end(), node) == nodes.end()) {
        nodes.push_back(node);
    }
}

std::vector<std::string> ReadNames(const json& p) {
    std::vector<std::string> names;
    if (!p.contains("names")) return names;

    const json& raw = p["names"];
    if (raw.type() == json::value_t::string) {
        names.push_back(raw.get<std::string>());
    } else if (raw.type() == json::value_t::array) {
        for (const auto& item : raw) {
            if (item.type() == json::value_t::string) names.push_back(item.get<std::string>());
        }
    }
    return names;
}

// Named targets fill resolved; the caller reports its unresolved names.
std::vector<INode*> ResolveTargets(const json& p, Interface* ip, NamedNodes& resolved) {
    std::vector<INode*> targets;
    const std::vector<std::string> names = ReadNames(p);
    const std::string pattern = p.value("pattern", "");

    if (!names.empty()) {
        resolved = ResolveNodesByNames(names);
        if (resolved.nodes.empty()) ThrowUnresolvedNames(resolved, "no node was analyzed.");
        targets = resolved.nodes;
    } else if (!pattern.empty()) {
        std::vector<INode*> matched = CollectNodesByPattern(pattern);
        for (INode* node : matched) PushUnique(targets, node);
    } else {
        const int count = ip->GetSelNodeCount();
        for (int i = 0; i < count; ++i) PushUnique(targets, ip->GetSelNode(i));
    }

    if (p.value("include_children", false)) {
        std::vector<INode*> expanded;
        for (INode* node : targets) AddNodeAndChildren(node, expanded);
        targets.clear();
        for (INode* node : expanded) PushUnique(targets, node);
    }
    return targets;
}

} // namespace

std::string NativeHandlers::AnalyzeNodeOrientation(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = params.empty() ? json::object() : json::parse(params, nullptr, false);
        if (!p.is_object()) p = json::object();

        Interface* ip = GetCOREInterface();
        const TimeValue t = ip->GetTime();
        NamedNodes resolved;
        std::vector<INode*> targets = ResolveTargets(p, ip, resolved);

        int maxNodes = p.value("max_nodes", 20);
        maxNodes = (std::max)(1, (std::min)(maxNodes, 100));
        const int count = (std::min)(static_cast<int>(targets.size()), maxNodes);

        json nodes = json::array();
        for (int i = 0; i < count; ++i) {
            nodes.push_back(NodeOrientationJson(targets[i], t));
        }

        json result;
        result["space"] = SpaceJson();
        result["query"] = {
            { "pattern", p.value("pattern", "") },
            { "includeChildren", p.value("include_children", false) },
            { "maxNodes", maxNodes },
        };
        result["nodes"] = nodes;
        result["truncated"] = static_cast<int>(targets.size()) > count;
        if (!resolved.notFound.empty()) {
            result["notFound"] = resolved.notFound;
            result["status"] = "partial";
            result["warnings"].push_back(std::to_string(resolved.notFound.size()) +
                " requested name(s) matched no object and were skipped: " + JoinNames(resolved.notFound));
        }
        AddAmbiguousNamesWarning(result, resolved.ambiguous);
        return result.dump();
    });
}
