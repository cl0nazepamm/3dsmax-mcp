#include "mcp_bridge/spatial_snapshot.h"
#include "mcp_bridge/handler_helpers.h"

#include <algorithm>
#include <cctype>
#include <units.h>

using namespace HandlerHelpers;

namespace SpatialSnapshot {

namespace {

Point3 SafeNormalizePoint(const Point3& p) {
    const float len = Length(p);
    if (len <= 1.0e-6f) return Point3(0.0f, 0.0f, 0.0f);
    return p / len;
}

std::string ToLowerCopy(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(), ::tolower);
    return s;
}

json MatrixRowsJson(const Matrix3& tm) {
    return json::array({
        PointJson(tm.GetRow(0)),
        PointJson(tm.GetRow(1)),
        PointJson(tm.GetRow(2)),
        PointJson(tm.GetRow(3)),
    });
}

} // namespace

json PointJson(const Point3& p) {
    return json::array({ p.x, p.y, p.z });
}

Box3 WorldBoundingBox(INode* node, TimeValue t) {
    Matrix3 nodeTM = node->GetNodeTM(t);
    // EvalWorldState returns the evaluated pipeline object. Its local bounds
    // require the object-offset transform too (and the post-WSM transform if
    // applicable). NodeTM alone double-translates meshes after Center Pivot.
    const Matrix3 objectTM = node->GetObjTMAfterWSM(t);
    Box3 worldBox;
    worldBox.Init();

    ObjectState os = node->EvalWorldState(t);
    if (!os.obj) {
        worldBox += nodeTM.GetTrans();
        return worldBox;
    }

    Box3 localBox;
    os.obj->GetDeformBBox(t, localBox);
    if (localBox.IsEmpty()) {
        worldBox += nodeTM.GetTrans();
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
        worldBox += (corner * objectTM);
    }
    return worldBox;
}

Point3 BottomCenter(const Box3& bbox) {
    const Point3 mn = bbox.Min();
    const Point3 mx = bbox.Max();
    return Point3((mn.x + mx.x) * 0.5f, (mn.y + mx.y) * 0.5f, mn.z);
}

Point3 BBoxCenter(const Box3& bbox) {
    return (bbox.Min() + bbox.Max()) * 0.5f;
}

PosMode ParsePosMode(const std::string& mode) {
    const std::string lower = ToLowerCopy(mode);
    if (lower == "pivot") return PosMode::Pivot;
    if (lower == "center" || lower == "bbox_center") return PosMode::Center;
    return PosMode::Ground;
}

const char* PosModeToString(PosMode mode) {
    switch (mode) {
    case PosMode::Pivot: return "pivot";
    case PosMode::Center: return "center";
    default: return "ground";
    }
}

json SpaceJson() {
    return {
        { "coordinateSystem", "3ds Max world" },
        { "upAxis", "Z" },
        { "groundPlane", "XY" },
        { "rightHanded", true },
        { "distanceBase", "system units" },
    };
}

static const char* SystemUnitName(int type) {
    switch (type) {
    case UNITS_INCHES: return "inches";
    case UNITS_FEET: return "feet";
    case UNITS_MILES: return "miles";
    case UNITS_MILLIMETERS: return "millimeters";
    case UNITS_CENTIMETERS: return "centimeters";
    case UNITS_METERS: return "meters";
    case UNITS_KILOMETERS: return "kilometers";
    default: return "unknown";
    }
}

static const char* UnitDisplayName(int type) {
    switch (type) {
    case UNITDISP_GENERIC: return "generic";
    case UNITDISP_METRIC: return "metric";
    case UNITDISP_US: return "us";
    case UNITDISP_CUSTOM: return "custom";
    default: return "unknown";
    }
}

json UnitsJson() {
    int systemType = UNITS_INCHES;
    float systemScale = 1.0f;
    GetSystemUnitInfo(&systemType, &systemScale);

    return {
        { "distanceBase", "system units" },
        { "systemType", SystemUnitName(systemType) },
        { "systemTypeId", systemType },
        { "systemScale", systemScale },
        { "displayType", UnitDisplayName(GetUnitDisplayType()) },
    };
}

json TypeAxisHints(const std::string& type) {
    const std::string lower = ToLowerCopy(type);
    json hints;
    hints["primitive"] = type;

    if (lower == "box" || lower == "chamferbox") {
        hints["width"] = "X";
        hints["length"] = "Y";
        hints["height"] = "Z";
        hints["note"] = "Box width=X, length=Y, height=Z. Default pivot is bottom-center.";
        return hints;
    }
    if (lower == "plane") {
        hints["length"] = "Y";
        hints["width"] = "X";
        hints["note"] = "Plane length=Y, width=X. Default pivot is center.";
        return hints;
    }
    if (lower == "cylinder" || lower == "cone" || lower == "tube" || lower == "chamfercyl") {
        hints["radius"] = "XY";
        hints["height"] = "Z";
        hints["note"] = "Radial primitives extend in XY; height is Z.";
        return hints;
    }
    if (lower == "sphere" || lower == "geosphere" || lower == "hedra" || lower == "teapot") {
        hints["radius"] = "XYZ";
        hints["note"] = "Radial primitives are centered on the pivot.";
        return hints;
    }
    if (lower == "pyramid") {
        hints["width"] = "X";
        hints["depth"] = "Y";
        hints["height"] = "Z";
        hints["note"] = "Pyramid width=X, depth=Y, height=Z.";
        return hints;
    }

    hints["note"] = "Use analyze_node_orientation for bbox/pivot after creation.";
    return hints;
}

json NodeOrientationJson(INode* node, TimeValue t) {
    Matrix3 tm = node->GetNodeTM(t);
    const Point3 pivot = tm.GetTrans();
    const Box3 bbox = WorldBoundingBox(node, t);
    const Point3 bboxMin = bbox.Min();
    const Point3 bboxMax = bbox.Max();
    const Point3 center = BBoxCenter(bbox);
    const Point3 dims = bboxMax - bboxMin;
    const Point3 pivotToCenter = center - pivot;

    ObjectState os = node->EvalWorldState(t);
    INode* parent = node->GetParentNode();

    json out;
    out["name"] = WideToUtf8(node->GetName());
    out["handle"] = NodeHandle(node);
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

void ApplyPosMode(INode* node, TimeValue t, const Point3& targetPos, bool hasTargetPos, PosMode mode) {
    if (mode == PosMode::Pivot && !hasTargetPos) {
        return;
    }

    Matrix3 tm = node->GetNodeTM(t);
    const Point3 pivot = tm.GetTrans();
    const Box3 bbox = WorldBoundingBox(node, t);

    Point3 anchor = pivot;
    if (mode == PosMode::Center) {
        anchor = BBoxCenter(bbox);
    } else if (mode == PosMode::Ground) {
        anchor = BottomCenter(bbox);
    }

    const Point3 target = hasTargetPos ? targetPos : Point3(0.0f, 0.0f, 0.0f);
    const Point3 delta = target - anchor;
    if (Length(delta) <= 1.0e-6f) {
        return;
    }

    node->Move(t, Matrix3{}, delta, TRUE, TRUE, PIV_NONE, TRUE);
}

json BuildSpatialSnapshot(INode* node, TimeValue t, const std::string& typeHint) {
    json nodeJson = NodeOrientationJson(node, t);
    ObjectState os = node->EvalWorldState(t);
    const std::string className = os.obj ? WideToUtf8(os.obj->ClassName().data()) : "Unknown";
    const std::string axisType = !typeHint.empty() ? typeHint : className;

    const Box3 bbox = WorldBoundingBox(node, t);

    json result;
    result["name"] = nodeJson["name"];
    result["handle"] = nodeJson["handle"];
    result["class"] = nodeJson["class"];
    result["pivot"] = nodeJson["pivot"];
    result["bbox"] = nodeJson["bbox"];
    result["groundContact"] = PointJson(BottomCenter(bbox));
    result["pivotToBBoxCenter"] = nodeJson["pivotToBBoxCenter"];
    result["localAxesWorld"] = nodeJson["localAxesWorld"];
    result["axes"] = TypeAxisHints(axisType);
    result["space"] = SpaceJson();
    result["units"] = UnitsJson();
    return result;
}

json BuildCreateObjectResult(
    INode* node,
    TimeValue t,
    const std::string& requestedType,
    const Point3& requestedPos,
    bool hasRequestedPos,
    PosMode mode) {

    json result = BuildSpatialSnapshot(node, t, requestedType);
    const Box3 bbox = WorldBoundingBox(node, t);

    result["type"] = requestedType;
    result["placement"] = {
        { "pos", hasRequestedPos ? PointJson(requestedPos) : json(nullptr) },
        { "pos_mode", PosModeToString(mode) },
        { "pivot", result["pivot"] },
        { "ground_contact", PointJson(BottomCenter(bbox)) },
    };
    return result;
}

} // namespace SpatialSnapshot
