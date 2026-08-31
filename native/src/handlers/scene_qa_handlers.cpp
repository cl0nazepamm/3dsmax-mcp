#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/node_ref.h"
#include "mcp_bridge/scene_journal.h"

#include <max.h>
#include <units.h>

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cwctype>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

using namespace HandlerHelpers;

namespace NativeHandlers {
namespace {

const std::set<std::string> kDefaultChecks = {
    "name_collisions",
    "empty_names",
    "invalid_transforms",
    "degenerate_transforms",
    "hierarchy_cycles",
    "group_integrity",
    "timeline",
};

const std::set<std::string> kKnownChecks = {
    "name_collisions",
    "empty_names",
    "invalid_transforms",
    "degenerate_transforms",
    "transform_risks",
    "far_from_origin",
    "hierarchy_cycles",
    "group_integrity",
    "timeline",
};

const std::set<std::string> kKnownFixes = {
    "name_collisions",
    "empty_names",
};

std::set<std::string> ParseTokens(
    const json& payload,
    const char* key,
    const std::set<std::string>& fallback,
    const std::set<std::string>& allowed) {
    auto it = payload.find(key);
    if (it == payload.end() || it->is_null()) return fallback;
    if (it->type() != json::value_t::array) {
        throw std::runtime_error(std::string(key) + " must be an array");
    }
    std::set<std::string> result;
    for (const json& value : *it) {
        if (value.type() != json::value_t::string) {
            throw std::runtime_error(std::string(key) + " entries must be strings");
        }
        std::string token = value.get<std::string>();
        std::transform(token.begin(), token.end(), token.begin(),
            [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
        if (allowed.count(token) == 0) {
            throw std::runtime_error(
                "Unknown " + std::string(key) + " token: " + token);
        }
        result.insert(token);
    }
    return result;
}

bool IsBlankName(const MCHAR* name) {
    if (!name || !*name) return true;
    for (const MCHAR* p = name; *p; ++p) {
        if (!std::iswspace(*p)) return false;
    }
    return true;
}

std::string FoldName(const MCHAR* name) {
    if (!name) return {};
    std::wstring folded(name);
    std::transform(folded.begin(), folded.end(), folded.begin(),
        [](wchar_t c) { return static_cast<wchar_t>(std::towlower(c)); });
    return WideToUtf8(folded.c_str());
}

void AppendUnique(std::vector<INode*>& nodes, INode* node) {
    if (!node || node->IsRootNode()) return;
    if (std::find(nodes.begin(), nodes.end(), node) == nodes.end()) {
        nodes.push_back(node);
    }
}

std::vector<INode*> ResolveScope(const json& payload) {
    Interface* ip = GetCOREInterface();
    const std::string scope = payload.value("scope", "scene");
    std::vector<INode*> nodes;

    if (scope == "scene") {
        CollectNodes(ip->GetRootNode(), nodes);
    } else if (scope == "selection") {
        for (int i = 0; i < ip->GetSelNodeCount(); ++i) {
            AppendUnique(nodes, ip->GetSelNode(i));
        }
    } else if (scope == "targets") {
        auto refsIt = payload.find("refs");
        if (refsIt != payload.end()) {
            if (refsIt->type() != json::value_t::array) {
                throw std::runtime_error("refs must be an array");
            }
            for (const json& ref : *refsIt) {
                AppendUnique(nodes, NodeRefs::Resolve(ref));
            }
        }
        auto handlesIt = payload.find("handles");
        if (handlesIt != payload.end()) {
            if (handlesIt->type() != json::value_t::array) {
                throw std::runtime_error("handles must be an array");
            }
            for (const json& handleValue : *handlesIt) {
                json ref = {{"handle", handleValue}};
                AppendUnique(nodes, NodeRefs::Resolve(ref));
            }
        }
        auto namesIt = payload.find("names");
        if (namesIt != payload.end()) {
            if (namesIt->type() != json::value_t::array) {
                throw std::runtime_error("names must be an array");
            }
            for (const json& nameValue : *namesIt) {
                if (nameValue.type() != json::value_t::string) {
                    throw std::runtime_error("names entries must be strings");
                }
                json ref = {{"name", nameValue}};
                AppendUnique(nodes, NodeRefs::Resolve(ref));
            }
        }
        if (nodes.empty()) {
            throw std::runtime_error("scope=targets requires refs, names, or handles");
        }
    } else {
        throw std::runtime_error("scope must be scene, selection, or targets");
    }

    std::sort(nodes.begin(), nodes.end(), [](INode* a, INode* b) {
        return NodeHandle(a) < NodeHandle(b);
    });
    return nodes;
}

struct IssueCollector {
    explicit IssueCollector(size_t limit) : limit(limit) {}

    void Add(
        const std::string& code,
        const std::string& severity,
        const std::string& message,
        INode* node = nullptr,
        const json& details = json()) {
        ++total;
        ++byCode[code];
        ++bySeverity[severity];
        if (issues.size() >= limit) {
            truncated = true;
            return;
        }
        json issue = {
            {"code", code},
            {"severity", severity},
            {"message", message},
        };
        if (node) issue["node"] = NodeIdentityJson(node);
        if (!details.is_null() && !details.empty()) issue["details"] = details;
        issues.push_back(issue);
    }

    json Summary() const {
        json codes = json::object();
        for (const auto& [name, count] : byCode) codes[name] = count;
        json severities = json::object();
        for (const auto& [name, count] : bySeverity) severities[name] = count;
        return {
            {"issue_count", total},
            {"by_code", codes},
            {"by_severity", severities},
        };
    }

    size_t limit;
    size_t total = 0;
    bool truncated = false;
    json issues = json::array();
    std::map<std::string, size_t> byCode;
    std::map<std::string, size_t> bySeverity;
};

bool IsFinitePoint(const Point3& point) {
    return std::isfinite(point.x) &&
           std::isfinite(point.y) &&
           std::isfinite(point.z);
}

json PointToJson(const Point3& point) {
    return json::array({point.x, point.y, point.z});
}

json NodeArray(const std::vector<INode*>& nodes) {
    json out = json::array();
    for (INode* node : nodes) out.push_back(NodeIdentityJson(node));
    return out;
}

json ScanSceneGraph(
    const std::vector<INode*>& nodes,
    const std::set<std::string>& checks,
    size_t maxIssues,
    double transformEpsilon,
    double farOriginThreshold,
    unsigned long long sceneSeq) {
    Interface* ip = GetCOREInterface();
    const TimeValue time = ip->GetTime();
    IssueCollector collector(maxIssues);

    std::unordered_map<std::string, std::vector<INode*>> foldedNames;
    if (checks.count("name_collisions") || checks.count("empty_names")) {
        for (INode* node : nodes) {
            if (checks.count("empty_names") && IsBlankName(node->GetName())) {
                collector.Add(
                    "empty_name", "error", "Node name is empty or whitespace-only.", node,
                    {{"repairable", true}, {"fix", "empty_names"}});
            }
            if (checks.count("name_collisions") && !IsBlankName(node->GetName())) {
                foldedNames[FoldName(node->GetName())].push_back(node);
            }
        }
        if (checks.count("name_collisions")) {
            for (auto& [folded, group] : foldedNames) {
                if (group.size() < 2) continue;
                std::sort(group.begin(), group.end(), [](INode* a, INode* b) {
                    return NodeHandle(a) < NodeHandle(b);
                });
                collector.Add(
                    "name_collision", "error",
                    "Multiple nodes have the same case-insensitive name.", nullptr,
                    {
                        {"folded_name", folded},
                        {"nodes", NodeArray(group)},
                        {"repairable", true},
                        {"fix", "name_collisions"},
                    });
            }
        }
    }

    if (checks.count("invalid_transforms") ||
        checks.count("degenerate_transforms") ||
        checks.count("transform_risks") ||
        checks.count("far_from_origin")) {
        for (INode* node : nodes) {
            Matrix3 tm = node->GetNodeTM(time);
            const Point3 x = tm.GetRow(0);
            const Point3 y = tm.GetRow(1);
            const Point3 z = tm.GetRow(2);
            const Point3 position = tm.GetRow(3);
            const bool finite = IsFinitePoint(x) && IsFinitePoint(y) &&
                                IsFinitePoint(z) && IsFinitePoint(position);
            if (!finite) {
                if (checks.count("invalid_transforms")) {
                    collector.Add(
                        "invalid_transform", "error",
                        "Node transform contains NaN or infinite values.", node,
                        {{"repairable", false}});
                }
                continue;
            }

            const double sx = static_cast<double>(Length(x));
            const double sy = static_cast<double>(Length(y));
            const double sz = static_cast<double>(Length(z));
            const double determinant = static_cast<double>(DotProd(CrossProd(x, y), z));

            if (checks.count("degenerate_transforms") &&
                (sx <= transformEpsilon || sy <= transformEpsilon ||
                 sz <= transformEpsilon || std::abs(determinant) <= transformEpsilon)) {
                collector.Add(
                    "degenerate_transform", "error",
                    "Node transform has a zero axis or singular basis.", node,
                    {
                        {"axis_lengths", json::array({sx, sy, sz})},
                        {"determinant", determinant},
                        {"repairable", false},
                    });
            }

            if (checks.count("transform_risks")) {
                const double maxScale = std::max({sx, sy, sz});
                const double minScale = std::min({sx, sy, sz});
                if (maxScale - minScale > transformEpsilon * std::max(1.0, maxScale)) {
                    collector.Add(
                        "non_uniform_scale", "warning",
                        "Node has non-uniform scale; this can be intentional.", node,
                        {{"axis_lengths", json::array({sx, sy, sz})}, {"repairable", false}});
                }
                if (determinant < -transformEpsilon) {
                    collector.Add(
                        "reflected_transform", "warning",
                        "Node transform has negative parity; this can be intentional.", node,
                        {{"determinant", determinant}, {"repairable", false}});
                }
            }

            if (checks.count("far_from_origin") && farOriginThreshold > 0.0 &&
                static_cast<double>(Length(position)) > farOriginThreshold) {
                collector.Add(
                    "far_from_origin", "warning",
                    "Node is beyond the requested origin-distance threshold.", node,
                    {
                        {"position", PointToJson(position)},
                        {"threshold", farOriginThreshold},
                        {"repairable", false},
                    });
            }
        }
    }

    if (checks.count("hierarchy_cycles")) {
        std::unordered_set<unsigned long long> reportedCycles;
        for (INode* start : nodes) {
            std::unordered_map<INode*, size_t> pathIndex;
            std::vector<INode*> path;
            INode* current = start;
            while (current && !current->IsRootNode()) {
                const auto seen = pathIndex.find(current);
                if (seen != pathIndex.end()) {
                    std::vector<INode*> cycle(path.begin() + seen->second, path.end());
                    unsigned long long key = NodeHandle(current);
                    for (INode* item : cycle) key = std::min(key, NodeHandle(item));
                    if (reportedCycles.insert(key).second) {
                        collector.Add(
                            "hierarchy_cycle", "error",
                            "A parent cycle was detected in the node hierarchy.", nullptr,
                            {{"nodes", NodeArray(cycle)}, {"repairable", false}});
                    }
                    break;
                }
                pathIndex[current] = path.size();
                path.push_back(current);
                current = current->GetParentNode();
            }
        }
    }

    if (checks.count("group_integrity")) {
        for (INode* node : nodes) {
            if (node->IsGroupHead()) {
                if (node->NumberOfChildren() == 0) {
                    collector.Add(
                        "empty_group", "warning",
                        "Group head has no children.", node,
                        {{"repairable", false}});
                }
                for (int i = 0; i < node->NumberOfChildren(); ++i) {
                    INode* child = node->GetChildNode(i);
                    if (child && !child->IsGroupMember()) {
                        collector.Add(
                            "group_child_flag_missing", "error",
                            "A group-head child is not marked as a group member.", child,
                            {
                                {"group", NodeIdentityJson(node)},
                                {"repairable", false},
                            });
                    }
                }
            }
            if (node->IsGroupMember()) {
                INode* parent = node->GetParentNode();
                if (!parent || !parent->IsGroupHead()) {
                    collector.Add(
                        "orphan_group_member", "error",
                        "Node is marked as a group member but has no group-head parent.", node,
                        {{"repairable", false}});
                }
            }
        }
    }

    json timeline;
    if (checks.count("timeline")) {
        const int fps = GetFrameRate();
        const int ticksPerFrame = GetTicksPerFrame();
        const Interval range = ip->GetAnimRange();
        timeline = {
            {"frame_rate", fps},
            {"ticks_per_frame", ticksPerFrame},
            {"current_tick", time},
            {"start_tick", range.Start()},
            {"end_tick", range.End()},
        };
        if (ticksPerFrame > 0) {
            timeline["current_frame"] = static_cast<double>(time) / ticksPerFrame;
            timeline["start_frame"] = static_cast<double>(range.Start()) / ticksPerFrame;
            timeline["end_frame"] = static_cast<double>(range.End()) / ticksPerFrame;
        }
        if (fps <= 0 || ticksPerFrame <= 0) {
            collector.Add(
                "invalid_frame_rate", "error",
                "Frame rate or ticks-per-frame is not positive.", nullptr,
                {{"frame_rate", fps}, {"ticks_per_frame", ticksPerFrame}, {"repairable", false}});
        }
        if (range.End() < range.Start()) {
            collector.Add(
                "invalid_animation_range", "error",
                "Animation range ends before it starts.", nullptr,
                {{"start_tick", range.Start()}, {"end_tick", range.End()}, {"repairable", false}});
        }
    }

    json checkList = json::array();
    for (const std::string& check : checks) checkList.push_back(check);
    json out = {
        {"scene_seq", sceneSeq},
        {"scanned_nodes", nodes.size()},
        {"checks", checkList},
        {"mesh_checks_included", false},
        {"summary", collector.Summary()},
        {"issues", collector.issues},
        {"truncated", collector.truncated},
    };
    if (!timeline.is_null()) out["timeline"] = timeline;
    return out;
}

std::vector<std::vector<INode*>> NameCollisionGroups(const std::vector<INode*>& nodes) {
    std::unordered_map<std::string, std::vector<INode*>> grouped;
    for (INode* node : nodes) {
        if (!IsBlankName(node->GetName())) grouped[FoldName(node->GetName())].push_back(node);
    }
    std::vector<std::vector<INode*>> collisions;
    for (auto& [_, group] : grouped) {
        if (group.size() < 2) continue;
        std::sort(group.begin(), group.end(), [](INode* a, INode* b) {
            return NodeHandle(a) < NodeHandle(b);
        });
        collisions.push_back(group);
    }
    std::sort(collisions.begin(), collisions.end(), [](const auto& a, const auto& b) {
        return NodeHandle(a.front()) < NodeHandle(b.front());
    });
    return collisions;
}

json PlannedRepairs(
    const std::vector<INode*>& nodes,
    const std::set<std::string>& fixes) {
    json planned = json::array();
    if (fixes.count("empty_names")) {
        for (INode* node : nodes) {
            if (IsBlankName(node->GetName())) {
                planned.push_back({
                    {"fix", "empty_names"},
                    {"node", NodeIdentityJson(node)},
                });
            }
        }
    }
    if (fixes.count("name_collisions")) {
        for (const auto& group : NameCollisionGroups(nodes)) {
            for (size_t i = 1; i < group.size(); ++i) {
                planned.push_back({
                    {"fix", "name_collisions"},
                    {"node", NodeIdentityJson(group[i])},
                    {"preserved", NodeIdentityJson(group.front())},
                });
            }
        }
    }
    return planned;
}

json ApplyRepairs(
    const std::vector<INode*>& nodes,
    const std::set<std::string>& fixes) {
    Interface* ip = GetCOREInterface();
    json applied = json::array();

    if (fixes.count("empty_names")) {
        for (INode* node : nodes) {
            if (!IsBlankName(node->GetName())) continue;
            const json before = NodeIdentityJson(node);
            MSTR uniqueName(L"Object");
            ip->MakeNameUnique(uniqueName);
            node->SetName(uniqueName.data());
            applied.push_back({
                {"fix", "empty_names"},
                {"before", before},
                {"after", NodeIdentityJson(node)},
            });
        }
    }

    if (fixes.count("name_collisions")) {
        for (const auto& group : NameCollisionGroups(nodes)) {
            for (size_t i = 1; i < group.size(); ++i) {
                INode* node = group[i];
                const json before = NodeIdentityJson(node);
                MSTR uniqueName(node->GetName());
                ip->MakeNameUnique(uniqueName);
                if (WideToUtf8(uniqueName.data()) == WideToUtf8(node->GetName())) {
                    std::wstring fallback = std::wstring(node->GetName()) + L"_mcp";
                    uniqueName = MSTR(fallback.c_str());
                    ip->MakeNameUnique(uniqueName);
                }
                node->SetName(uniqueName.data());
                applied.push_back({
                    {"fix", "name_collisions"},
                    {"before", before},
                    {"after", NodeIdentityJson(node)},
                    {"preserved", NodeIdentityJson(group.front())},
                });
            }
        }
    }

    if (!applied.empty()) {
        ip->RedrawViews(ip->GetTime());
    }
    return applied;
}

std::string SceneQAImpl(const std::string& params, bool allowMutation) {
    json payload = json::parse(params.empty() ? "{}" : params);
    const std::string action = payload.value("action", "scan");
    const bool requestedFix = action == "fix";
    const bool dryRun = payload.value("dry_run", false) || !allowMutation;

    if (action != "scan" && action != "fix") {
        throw std::runtime_error("action must be scan or fix");
    }
    if (allowMutation && !requestedFix) {
        throw std::runtime_error("native:scene_qa_fix requires action=fix");
    }

    const size_t maxIssues = static_cast<size_t>(
        std::clamp(payload.value("max_issues", 1000), 1, 100000));
    const double transformEpsilon = payload.value("transform_epsilon", 1.0e-6);
    if (!(transformEpsilon > 0.0) || !std::isfinite(transformEpsilon)) {
        throw std::runtime_error("transform_epsilon must be finite and greater than zero");
    }
    const double farOriginThreshold = payload.value("far_origin_threshold", 0.0);
    if (farOriginThreshold < 0.0 || !std::isfinite(farOriginThreshold)) {
        throw std::runtime_error("far_origin_threshold must be finite and non-negative");
    }

    std::set<std::string> checks = ParseTokens(
        payload, "checks", kDefaultChecks, kKnownChecks);
    if (farOriginThreshold > 0.0) checks.insert("far_from_origin");

    std::set<std::string> fixes;
    if (requestedFix) {
        fixes = ParseTokens(payload, "fixes", {}, kKnownFixes);
        if (fixes.empty()) {
            throw std::runtime_error("action=fix requires at least one fixes token");
        }
        checks.insert(fixes.begin(), fixes.end());
    }

    SceneJournal::FlushPending();
    const unsigned long long seqBefore = SceneJournal::CurrentMutationSeq();
    auto expectedIt = payload.find("expected_scene_seq");
    if (requestedFix && !dryRun && expectedIt != payload.end() && !expectedIt->is_null()) {
        const unsigned long long expected = expectedIt->get<unsigned long long>();
        if (expected != seqBefore) {
            throw std::runtime_error(StructuredErrorPayload(
                "SCENE_CONFLICT",
                "Scene changed after the caller's QA baseline.",
                {
                    {"expected_scene_seq", expected},
                    {"current_scene_seq", seqBefore},
                    {"suggested_tools", json::array({"scene_qa"})},
                }));
        }
    }

    const std::vector<INode*> nodes = ResolveScope(payload);
    json before = ScanSceneGraph(
        nodes, checks, maxIssues, transformEpsilon, farOriginThreshold, seqBefore);
    before["scope"] = payload.value("scope", "scene");
    before["action"] = "scan";

    if (!requestedFix) return before.dump();

    json fixList = json::array();
    for (const std::string& fix : fixes) fixList.push_back(fix);
    if (dryRun) {
        return json({
            {"action", "fix"},
            {"dry_run", true},
            {"scene_seq_before", seqBefore},
            {"scene_seq_after", seqBefore},
            {"fixes", fixList},
            {"before", before},
            {"planned", PlannedRepairs(nodes, fixes)},
            {"mesh_checks_included", false},
        }).dump();
    }

    json applied = ApplyRepairs(nodes, fixes);
    SceneJournal::FlushPending();
    const unsigned long long seqAfter = SceneJournal::CurrentMutationSeq();
    json after = ScanSceneGraph(
        nodes, checks, maxIssues, transformEpsilon, farOriginThreshold, seqAfter);
    after["scope"] = payload.value("scope", "scene");
    after["action"] = "scan";

    return json({
        {"action", "fix"},
        {"dry_run", false},
        {"scene_seq_before", seqBefore},
        {"scene_seq_after", seqAfter},
        {"fixes", fixList},
        {"applied_count", applied.size()},
        {"applied", applied},
        {"before", before},
        {"after", after},
        {"mesh_checks_included", false},
    }).dump();
}

} // namespace

std::string SceneQAScan(const std::string& params, MCPBridgeGUP*) {
    return SceneQAImpl(params, false);
}

std::string SceneQAFix(const std::string& params, MCPBridgeGUP*) {
    return SceneQAImpl(params, true);
}

} // namespace NativeHandlers
