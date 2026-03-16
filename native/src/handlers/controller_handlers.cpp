#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/bridge_gup.h"

#include <control.h>

using json = nlohmann::json;
using namespace HandlerHelpers;

// ── Helper: get compact value string from a SubAnim ─────────
static std::string CompactValue(Animatable* sa, TimeValue t) {
    if (!sa) return "";
    try {
        Control* ctrl = (Control*)sa->GetInterface(I_CONTROL);
        if (!ctrl) return "";

        SClass_ID scid = ctrl->SuperClassID();
        if (scid == CTRL_FLOAT_CLASS_ID) {
            float fVal = 0;
            Interval valid = FOREVER;
            ctrl->GetValue(t, &fVal, valid, CTRL_ABSOLUTE);
            char buf[64];
            snprintf(buf, sizeof(buf), "%.6g", fVal);
            return buf;
        }
        if (scid == CTRL_POINT3_CLASS_ID || scid == CTRL_POSITION_CLASS_ID) {
            Point3 pt(0, 0, 0);
            Interval valid = FOREVER;
            ctrl->GetValue(t, &pt, valid, CTRL_ABSOLUTE);
            char buf[128];
            snprintf(buf, sizeof(buf), "[%.4g,%.4g,%.4g]", pt.x, pt.y, pt.z);
            return buf;
        }
    }
    catch (...) {}
    return "";
}

// ── Helper: recursive SubAnim tree walk ─────────────────────
static json WalkSubAnims(Animatable* anim, const std::string& path,
                         const std::string& trackName, int depthLeft,
                         const std::string& filter, bool includeValues,
                         TimeValue t) {
    if (!anim || depthLeft < 0) return nullptr;

    // Controller info
    Animatable* ctrlAnim = anim->SubAnim(anim->NumSubs() > 0 ? 0 : -1); // dummy
    Control* ctrl = nullptr;
    std::string ctrlClass, ctrlSuper;

    // Check if this animatable has a controller interface
    ctrl = (Control*)anim->GetInterface(I_CONTROL);
    if (ctrl) {
        ctrlClass = WideToUtf8(ctrl->ClassName().data());
        SClass_ID scid = ctrl->SuperClassID();
        if (scid == CTRL_FLOAT_CLASS_ID) ctrlSuper = "float";
        else if (scid == CTRL_POINT3_CLASS_ID) ctrlSuper = "point3";
        else if (scid == CTRL_POSITION_CLASS_ID) ctrlSuper = "position";
        else if (scid == CTRL_ROTATION_CLASS_ID) ctrlSuper = "rotation";
        else if (scid == CTRL_SCALE_CLASS_ID) ctrlSuper = "scale";
        else if (scid == CTRL_MATRIX3_CLASS_ID) ctrlSuper = "matrix3";
        else ctrlSuper = "controller";
    }

    // Build children
    json children = json::array();
    int childCount = 0;

    if (depthLeft > 0) {
        int numSubs = anim->NumSubs();
        for (int i = 0; i < numSubs; i++) {
            Animatable* child = anim->SubAnim(i);
            if (!child) continue;

            MSTR childNameM = anim->SubAnimName(i, false);
            if (childNameM.isNull() || childNameM.Length() == 0) continue;

            std::string childName = WideToUtf8(childNameM.data());
            std::string childPath = path + "[#" + childName + "]";

            json childJson = WalkSubAnims(child, childPath, childName,
                                          depthLeft - 1, filter, includeValues, t);
            if (!childJson.is_null()) {
                children.push_back(childJson);
                childCount++;
            }
        }
    }

    // Filter check
    if (!filter.empty()) {
        std::string haystack = trackName + " " + path + " " + ctrlClass;
        std::transform(haystack.begin(), haystack.end(), haystack.begin(), ::tolower);
        if (haystack.find(filter) == std::string::npos && childCount == 0) {
            return nullptr;
        }
    }

    // Build result node
    json node;
    node["name"] = trackName;
    node["path"] = path;
    if (!ctrlClass.empty()) node["controller"] = ctrlClass;
    if (!ctrlSuper.empty()) node["controllerSuperclass"] = ctrlSuper;
    if (includeValues) {
        std::string val = CompactValue(anim, t);
        if (!val.empty()) node["value"] = val;
    }
    node["childCount"] = childCount;
    node["children"] = children;

    return node;
}

// ── native:inspect_track_view ───────────────────────────────
std::string NativeHandlers::InspectTrackView(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string name = p.value("name", "");
        int depth = p.value("depth", 4);
        std::string filter = p.value("filter", "");
        bool includeValues = p.value("include_values", true);

        if (name.empty()) throw std::runtime_error("name is required");
        depth = std::max(1, std::min(depth, 6));

        INode* node = FindNodeByName(name);
        if (!node) throw std::runtime_error("Object not found: " + name);

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        // Lowercase filter for comparison
        std::string lowerFilter = filter;
        std::transform(lowerFilter.begin(), lowerFilter.end(), lowerFilter.begin(), ::tolower);

        // Walk root sub-anims
        json tracks = json::array();
        int rootCount = 0;
        int numSubs = node->NumSubs();

        for (int i = 0; i < numSubs; i++) {
            Animatable* child = node->SubAnim(i);
            if (!child) continue;

            MSTR childNameM = node->SubAnimName(i, false);
            if (childNameM.isNull() || childNameM.Length() == 0) continue;

            std::string childName = WideToUtf8(childNameM.data());
            std::string childPath = "[#" + childName + "]";

            json childJson = WalkSubAnims(child, childPath, childName,
                                          depth - 1, lowerFilter, includeValues, t);
            if (!childJson.is_null()) {
                tracks.push_back(childJson);
                rootCount++;
            }
        }

        json result;
        result["object"] = WideToUtf8(node->GetName());
        result["class"] = NodeClassName(node);
        result["depth"] = depth;
        result["filter"] = filter;
        result["rootTrackCount"] = rootCount;
        result["tracks"] = tracks;
        return result.dump();
    });
}

// ── native:list_wireable_params ─────────────────────────────
std::string NativeHandlers::ListWireableParams(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string name = p.value("name", "");
        std::string filter = p.value("filter", "");
        int depth = p.value("depth", 3);

        if (name.empty()) throw std::runtime_error("name is required");
        depth = std::max(1, std::min(depth, 5));

        INode* node = FindNodeByName(name);
        if (!node) throw std::runtime_error("Object not found: " + name);

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        std::string lowerFilter = filter;
        std::transform(lowerFilter.begin(), lowerFilter.end(), lowerFilter.begin(), ::tolower);

        json results = json::array();

        // Recursive lambda to walk sub-anims
        std::function<void(Animatable*, const std::string&, int)> walkParams =
            [&](Animatable* anim, const std::string& path, int depthLeft) {
            if (!anim || depthLeft <= 0) return;

            int numSubs = anim->NumSubs();
            for (int i = 0; i < numSubs; i++) {
                Animatable* child = anim->SubAnim(i);
                if (!child) continue;

                MSTR childNameM = anim->SubAnimName(i, false);
                if (childNameM.isNull() || childNameM.Length() == 0) continue;

                std::string childName = WideToUtf8(childNameM.data());
                std::string childPath = path + "[#" + childName + "]";

                // Check if this has a controller (wireable)
                Control* ctrl = (Control*)child->GetInterface(I_CONTROL);
                bool isWireable = ctrl != nullptr;
                int childSubs = child->NumSubs();

                if (childSubs == 0 || isWireable) {
                    // Filter check
                    if (!lowerFilter.empty()) {
                        std::string lower = childPath;
                        std::transform(lower.begin(), lower.end(), lower.begin(), ::tolower);
                        if (lower.find(lowerFilter) == std::string::npos) {
                            if (childSubs > 0) walkParams(child, childPath, depthLeft - 1);
                            continue;
                        }
                    }

                    json entry;
                    entry["path"] = childPath;
                    entry["is_wireable"] = isWireable;
                    entry["type"] = ctrl ? WideToUtf8(ctrl->ClassName().data()) : "none";

                    // Compact value
                    std::string val = CompactValue(child, t);
                    entry["value"] = val.empty() ? "?" : val;

                    results.push_back(entry);
                }

                if (childSubs > 0) {
                    walkParams(child, childPath, depthLeft - 1);
                }
            }
        };

        walkParams(node, "", depth);

        return results.dump();
    });
}
