#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/bridge_gup.h"

#include <algorithm>
#include <set>

using json = nlohmann::json;
using namespace HandlerHelpers;

namespace {

json PointJson(const Point3& p) {
    return json::array({ p.x, p.y, p.z });
}

Point3 SafeNormalizePoint(const Point3& p) {
    const float len = Length(p);
    if (len <= 1.0e-6f) return Point3(0.0f, 0.0f, 0.0f);
    return p / len;
}

json MatrixRowsJson(const Matrix3& tm) {
    return json::array({
        PointJson(tm.GetRow(0)),
        PointJson(tm.GetRow(1)),
        PointJson(tm.GetRow(2)),
        PointJson(tm.GetRow(3)),
    });
}

Box3 WorldBoundingBox(INode* node, TimeValue t, const Matrix3& nodeTM) {
    Box3 worldBox;
    worldBox.Init();

    ObjectState os = node->EvalWorldState(t);
    if (!os.obj) {
        const Point3 pivot = nodeTM.GetTrans();
        worldBox += pivot;
        return worldBox;
    }

    Box3 localBox;
    os.obj->GetDeformBBox(t, localBox);
    if (localBox.IsEmpty()) {
        const Point3 pivot = nodeTM.GetTrans();
        worldBox += pivot;
        return worldBox;
    }

    const Point3 mn = localBox.Min();
    const Point3 mx = localBox.Max();
    const Point3 corners[8] = {
        Point3(mn.x, mn.y, mn.z),
        Point3(mx.x, mn.y, mn.z),
        Point3(mn.x, mx.y, mn.z),
        Point3(mx.x, mx.y, mn.z),
        Point3(mn.x, mn.y, mx.z),
        Point3(mx.x, mn.y, mx.z),
        Point3(mn.x, mx.y, mx.z),
        Point3(mx.x, mx.y, mx.z),
    };
    for (const Point3& corner : corners) {
        worldBox += (corner * nodeTM);
    }
    return worldBox;
}

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

std::vector<INode*> ResolveTargets(const json& p, Interface* ip) {
    std::vector<INode*> targets;
    const std::vector<std::string> names = ReadNames(p);
    const std::string pattern = p.value("pattern", "");

    if (!names.empty()) {
        for (const std::string& name : names) {
            PushUnique(targets, FindNodeByName(name));
        }
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

json NodeOrientationJson(INode* node, TimeValue t) {
    Matrix3 tm = node->GetNodeTM(t);
    const Point3 pivot = tm.GetTrans();
    const Box3 bbox = WorldBoundingBox(node, t, tm);
    const Point3 bboxMin = bbox.Min();
    const Point3 bboxMax = bbox.Max();
    const Point3 center = (bboxMin + bboxMax) * 0.5f;
    const Point3 dims = bboxMax - bboxMin;
    const Point3 pivotToCenter = center - pivot;

    ObjectState os = node->EvalWorldState(t);
    INode* parent = node->GetParentNode();

    json out;
    out["name"] = WideToUtf8(node->GetName());
    out["class"] = os.obj ? WideToUtf8(os.obj->ClassName().data()) : "Unknown";
    out["parent"] = (parent && !parent->IsRootNode()) ? json(WideToUtf8(parent->GetName())) : json(nullptr);
    out["pivot"] = PointJson(pivot);
    out["position"] = PointJson(pivot);
    out["bbox"] = {
        { "min", PointJson(bboxMin) },
        { "max", PointJson(bboxMax) },
        { "center", PointJson(center) },
        { "dimensions", PointJson(dims) },
    };
    out["pivotToBBoxCenter"] = PointJson(pivotToCenter);
    out["localAxesWorld"] = {
        { "x", PointJson(SafeNormalizePoint(tm.GetRow(0))) },
        { "y", PointJson(SafeNormalizePoint(tm.GetRow(1))) },
        { "z", PointJson(SafeNormalizePoint(tm.GetRow(2))) },
    };
    out["worldMatrixRows"] = MatrixRowsJson(tm);
    return out;
}

} // namespace

std::string NativeHandlers::AnalyzeNodeOrientation(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = params.empty() ? json::object() : json::parse(params, nullptr, false);
        if (!p.is_object()) p = json::object();

        Interface* ip = GetCOREInterface();
        const TimeValue t = ip->GetTime();
        std::vector<INode*> targets = ResolveTargets(p, ip);

        int maxNodes = p.value("max_nodes", 20);
        maxNodes = (std::max)(1, (std::min)(maxNodes, 100));
        const int count = (std::min)(static_cast<int>(targets.size()), maxNodes);

        json nodes = json::array();
        for (int i = 0; i < count; ++i) {
            nodes.push_back(NodeOrientationJson(targets[i], t));
        }

        json result;
        result["space"] = {
            { "coordinateSystem", "3ds Max world" },
            { "upAxis", "Z" },
            { "groundPlane", "XY" },
            { "rightHanded", true },
        };
        result["query"] = {
            { "pattern", p.value("pattern", "") },
            { "includeChildren", p.value("include_children", false) },
            { "maxNodes", maxNodes },
        };
        result["nodes"] = nodes;
        result["truncated"] = static_cast<int>(targets.size()) > count;
        return result.dump();
    });
}
