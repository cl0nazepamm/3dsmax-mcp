#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/core_bridge.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/node_ref.h"

#include <hold.h>

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cwctype>
#include <limits>
#include <set>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

using json = nlohmann::json;
using namespace HandlerHelpers;

namespace {

constexpr size_t kMaxPatchOperations = 256;

struct NodeFlags {
    bool hidden = false;
    bool frozen = false;
    bool renderable = true;
    bool castShadows = true;
    bool receiveShadows = true;
};

struct PlannedOperation {
    size_t index = 0;
    std::string id;
    std::string op;
    INode* target = nullptr;
    INode* parent = nullptr;
    json args = json::object();
    json proof = json::object();
    bool changed = false;
};

struct ExpectedSequence {
    bool present = false;
    unsigned long long value = 0;
};

// Raised when every operation was ignored; the transaction is already
// cancelled and the payload already structured.
struct ScenePatchIgnored : std::runtime_error {
    using std::runtime_error::runtime_error;
};

class ScenePatchTransaction {
public:
    explicit ScenePatchTransaction(const std::string& label) {
        if (theHold.Holding()) {
            throw std::runtime_error(StructuredErrorPayload(
                "TRANSACTION_BUSY",
                "Cannot start scene_patch while another 3ds Max hold is open.",
                {{"message", "Finish or cancel the active Max operation, then retry."}}));
        }
        label_ = MSTR(Utf8ToWide(label).c_str());
        theHold.Begin();
        active_ = true;
    }

    ~ScenePatchTransaction() {
        if (!active_) return;
        try {
            theHold.Cancel();
        } catch (...) {
        }
    }

    void Accept() {
        if (!active_) return;
        theHold.Accept(label_);
        active_ = false;
    }

    void Cancel() {
        if (!active_) return;
        theHold.Cancel();
        active_ = false;
    }

private:
    bool active_ = false;
    MSTR label_;
};

std::string Lower(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(),
        [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
    return value;
}

std::string FoldName(const std::string& value) {
    std::wstring folded = Utf8ToWide(value);
    std::transform(folded.begin(), folded.end(), folded.begin(),
        [](wchar_t ch) { return static_cast<wchar_t>(std::towlower(ch)); });
    return WideToUtf8(folded.c_str());
}

[[noreturn]] void ThrowPatchError(
    const std::string& code,
    const std::string& message,
    size_t index,
    const json& extra = json::object()) {
    json hint = extra.is_object() ? extra : json::object();
    hint["operationIndex"] = index;
    throw std::runtime_error(StructuredErrorPayload(code, message, hint));
}

[[noreturn]] void ThrowSceneConflict(
    unsigned long long expected,
    unsigned long long current,
    const std::string& message) {
    json payload;
    payload["type"] = "NativeError";
    payload["message"] = message;
    payload["code"] = "SCENE_CONFLICT";
    payload["retryable"] = true;
    payload["hint"] = {
        {"expectedSceneSeq", expected},
        {"currentSceneSeq", current},
        {"changes", expected < current
            ? McpHost::SceneJournal::MutationChangesSince(expected, 32)
            : json::array()},
        {"next", "Refresh scene state and retry with currentSceneSeq."},
    };
    throw std::runtime_error(payload.dump());
}

ExpectedSequence ParseExpectedSequence(const json& payload) {
    ExpectedSequence result;
    if (!payload.contains("expected_scene_seq") || payload["expected_scene_seq"].is_null()) {
        return result;
    }
    result.present = true;
    const json& value = payload["expected_scene_seq"];
    try {
        if (value.is_number_unsigned()) {
            result.value = value.get<unsigned long long>();
            return result;
        }
        if (value.is_number_integer()) {
            const long long parsed = value.get<long long>();
            if (parsed >= 0) {
                result.value = static_cast<unsigned long long>(parsed);
                return result;
            }
        }
        if (value.type() == json::value_t::string) {
            const std::string text = value.get<std::string>();
            if (text.empty() || !std::all_of(text.begin(), text.end(), [](unsigned char ch) {
                    return std::isdigit(ch) != 0;
                })) {
                throw std::invalid_argument("not a decimal sequence");
            }
            size_t consumed = 0;
            result.value = std::stoull(text, &consumed);
            if (consumed == text.size()) return result;
        }
    } catch (...) {
    }
    throw std::runtime_error(StructuredErrorPayload(
        "BAD_PARAM", "expected_scene_seq must be a non-negative integer."));
}

json PointJson(const Point3& point) {
    return json::array({point.x, point.y, point.z});
}

bool IsFiniteNumber(const json& value) {
    if (!value.is_number()) return false;
    try {
        return std::isfinite(value.get<double>());
    } catch (...) {
        return false;
    }
}

Point3 ParseVector3(const json& value, const std::string& field, size_t index) {
    if (value.type() != json::value_t::array || value.size() != 3 ||
        !IsFiniteNumber(value[0]) || !IsFiniteNumber(value[1]) || !IsFiniteNumber(value[2])) {
        ThrowPatchError(
            "BAD_PARAM", field + " must be an array of three finite numbers.", index);
    }
    return Point3(
        value[0].get<float>(),
        value[1].get<float>(),
        value[2].get<float>());
}

Point3 ParseScale(const json& value, size_t index) {
    if (IsFiniteNumber(value)) {
        const float scalar = value.get<float>();
        return Point3(scalar, scalar, scalar);
    }
    if (value.type() == json::value_t::array && value.size() == 1 && IsFiniteNumber(value[0])) {
        const float scalar = value[0].get<float>();
        return Point3(scalar, scalar, scalar);
    }
    return ParseVector3(value, "scale", index);
}

bool Near(float lhs, float rhs) {
    return std::fabs(lhs - rhs) <= 1.0e-6f;
}

bool IsZero(const Point3& value) {
    return Near(value.x, 0.0f) && Near(value.y, 0.0f) && Near(value.z, 0.0f);
}

bool IsIdentityScale(const Point3& value) {
    return Near(value.x, 1.0f) && Near(value.y, 1.0f) && Near(value.z, 1.0f);
}

NodeFlags ReadFlags(INode* node) {
    NodeFlags flags;
    flags.hidden = node->IsHidden() != 0;
    flags.frozen = node->IsFrozen() != 0;
    flags.renderable = node->Renderable() != 0;
    flags.castShadows = node->CastShadows() != 0;
    flags.receiveShadows = node->RcvShadows() != 0;
    return flags;
}

// The undo record lives in the shell (undo_records.cpp): Max keeps it after
// the request, so its vtable must not point into the core.
unsigned FlagBits(const NodeFlags& flags) {
    return (flags.hidden ? kMcpNodeHidden : 0u) | (flags.frozen ? kMcpNodeFrozen : 0u) |
        (flags.renderable ? kMcpNodeRenderable : 0u) | (flags.castShadows ? kMcpNodeCastShadows : 0u) |
        (flags.receiveShadows ? kMcpNodeReceiveShadows : 0u);
}

json NullableNodeRef(INode* node) {
    return node && !node->IsRootNode() ? NodeRefs::ToJson(node) : json(nullptr);
}

void ValidateNoParentCycle(
    INode* target,
    const std::unordered_map<INode*, INode*>& simulatedParents,
    size_t index) {
    std::unordered_set<INode*> visited;
    INode* cursor = target;
    while (cursor && !cursor->IsRootNode()) {
        if (!visited.insert(cursor).second) {
            ThrowPatchError(
                "HIERARCHY_CYCLE",
                "set_parent would create a hierarchy cycle.",
                index,
                {{"target", NodeRefs::ToJson(target, true)}});
        }
        const auto found = simulatedParents.find(cursor);
        cursor = found == simulatedParents.end() ? cursor->GetParentNode() : found->second;
    }
}

json ParseJsonObject(const std::string& params) {
    json payload = params.empty() ? json::object() : json::parse(params, nullptr, false);
    if (payload.is_discarded() || !payload.is_object()) {
        throw std::runtime_error(StructuredErrorPayload(
            "BAD_PARAM", "scene_patch payload must be a JSON object."));
    }
    return payload;
}

std::vector<PlannedOperation> BuildPlan(
    const json& operations,
    Interface* ip,
    TimeValue time,
    std::vector<INode*>& allNodes) {
    (void)time;
    CollectNodes(ip->GetRootNode(), allNodes);

    std::unordered_map<INode*, std::string> simulatedNames;
    std::unordered_map<INode*, INode*> simulatedParents;
    std::unordered_map<INode*, NodeFlags> simulatedFlags;
    for (INode* node : allNodes) {
        simulatedNames[node] = WideToUtf8(node->GetName());
        simulatedParents[node] = node->GetParentNode();
        simulatedFlags[node] = ReadFlags(node);
    }

    std::unordered_set<INode*> renamedTargets;
    std::vector<PlannedOperation> plan;
    plan.reserve(operations.size());

    for (size_t index = 0; index < operations.size(); ++index) {
        const json& raw = operations[index];
        if (!raw.is_object()) {
            ThrowPatchError("BAD_PARAM", "Each scene_patch operation must be an object.", index);
        }
        if (!raw.contains("target")) {
            ThrowPatchError("BAD_NODE_REF", "Operation target NodeRef is required.", index);
        }

        PlannedOperation item;
        item.index = index;
        if (raw.contains("id") && raw["id"].type() != json::value_t::string) {
            ThrowPatchError("BAD_PARAM", "Operation id must be a string.", index);
        }
        item.id = raw.value("id", "");
        if (item.id.size() > 128) {
            ThrowPatchError("BAD_PARAM", "Operation id cannot exceed 128 UTF-8 bytes.", index);
        }
        if (!raw.contains("op") || raw["op"].type() != json::value_t::string) {
            ThrowPatchError("BAD_PARAM", "Operation op must be a string.", index);
        }
        item.op = Lower(raw["op"].get<std::string>());
        if (item.op.empty()) ThrowPatchError("BAD_PARAM", "Operation op is required.", index);

        try {
            item.target = NodeRefs::Resolve(raw["target"]);
        } catch (const std::exception& error) {
            json parsed = json::parse(error.what(), nullptr, false);
            if (!parsed.is_discarded() && parsed.is_object()) {
                json hint = parsed.value("hint", json::object());
                hint["operationIndex"] = index;
                parsed["hint"] = hint;
                throw std::runtime_error(parsed.dump());
            }
            throw;
        }
        if (!item.target || item.target->IsRootNode()) {
            ThrowPatchError("BAD_NODE_REF", "Scene root cannot be a patch target.", index);
        }

        item.proof["index"] = index;
        if (!item.id.empty()) item.proof["id"] = item.id;
        item.proof["op"] = item.op;
        item.proof["target"] = {{"before", NodeRefs::ToJson(item.target)}};

        if (item.op == "rename") {
            if (!raw.contains("name") || raw["name"].type() != json::value_t::string) {
                ThrowPatchError("BAD_PARAM", "rename requires a string name.", index);
            }
            const std::string newName = raw["name"].get<std::string>();
            if (newName.empty() || newName.size() > 1024) {
                ThrowPatchError(
                    "BAD_PARAM", "rename name must contain 1 to 1024 UTF-8 bytes.", index);
            }
            const std::string oldName = simulatedNames[item.target];
            item.args["name"] = newName;
            item.changed = oldName != newName;
            item.proof["change"] = {{"name", {{"from", oldName}, {"to", newName}}}};
            simulatedNames[item.target] = newName;
            if (item.changed) renamedTargets.insert(item.target);
        } else if (item.op == "transform") {
            if (raw.contains("coordinate_system") &&
                raw["coordinate_system"].type() != json::value_t::string) {
                ThrowPatchError(
                    "BAD_PARAM", "coordinate_system must be world or local.", index);
            }
            const std::string coordinateSystem = Lower(raw.value("coordinate_system", "world"));
            if (coordinateSystem != "world" && coordinateSystem != "local") {
                ThrowPatchError(
                    "BAD_PARAM", "coordinate_system must be world or local.", index);
            }
            item.args["coordinate_system"] = coordinateSystem;

            if (raw.contains("move")) {
                const Point3 move = ParseVector3(raw["move"], "move", index);
                item.args["move"] = PointJson(move);
                item.changed = item.changed || !IsZero(move);
            }
            if (raw.contains("rotate")) {
                const Point3 rotate = ParseVector3(raw["rotate"], "rotate", index);
                item.args["rotate"] = PointJson(rotate);
                item.changed = item.changed || !IsZero(rotate);
            }
            if (raw.contains("scale")) {
                const Point3 scale = ParseScale(raw["scale"], index);
                if (Near(scale.x, 0.0f) || Near(scale.y, 0.0f) || Near(scale.z, 0.0f)) {
                    ThrowPatchError(
                        "BAD_PARAM", "scale components must be non-zero.", index);
                }
                item.args["scale"] = PointJson(scale);
                item.changed = item.changed || !IsIdentityScale(scale);
            }
            if (!item.args.contains("move") && !item.args.contains("rotate") &&
                !item.args.contains("scale")) {
                ThrowPatchError(
                    "BAD_PARAM", "transform requires move, rotate, or scale.", index);
            }
            item.proof["change"] = {
                {"request", item.args},
                {"beforePosition", NodePosition(item.target, ip->GetTime())},
            };
        } else if (item.op == "set_flags") {
            NodeFlags& simulated = simulatedFlags[item.target];
            json changes = json::object();
            const auto addFlag = [&](const char* key, bool& state) {
                if (!raw.contains(key)) return;
                if (!raw[key].is_boolean()) {
                    ThrowPatchError(
                        "BAD_PARAM", std::string(key) + " must be boolean.", index);
                }
                const bool next = raw[key].get<bool>();
                item.args[key] = next;
                changes[key] = {{"from", state}, {"to", next}};
                item.changed = item.changed || state != next;
                state = next;
            };
            addFlag("hidden", simulated.hidden);
            addFlag("frozen", simulated.frozen);
            addFlag("renderable", simulated.renderable);
            addFlag("cast_shadows", simulated.castShadows);
            addFlag("receive_shadows", simulated.receiveShadows);
            if (item.args.empty()) {
                ThrowPatchError(
                    "BAD_PARAM",
                    "set_flags requires hidden, frozen, renderable, cast_shadows, or receive_shadows.",
                    index);
            }
            item.proof["change"] = changes;
        } else if (item.op == "set_parent") {
            if (!raw.contains("parent")) {
                ThrowPatchError(
                    "BAD_PARAM",
                    "set_parent requires parent; pass null to detach to scene root.",
                    index);
            }
            if (raw.contains("parent") && !raw["parent"].is_null()) {
                try {
                    item.parent = NodeRefs::Resolve(raw["parent"]);
                } catch (const std::exception& error) {
                    json parsed = json::parse(error.what(), nullptr, false);
                    if (!parsed.is_discarded() && parsed.is_object()) {
                        json hint = parsed.value("hint", json::object());
                        hint["operationIndex"] = index;
                        hint["field"] = "parent";
                        parsed["hint"] = hint;
                        throw std::runtime_error(parsed.dump());
                    }
                    throw;
                }
                if (item.parent == item.target) {
                    ThrowPatchError(
                        "HIERARCHY_CYCLE", "A node cannot be its own parent.", index);
                }
            }
            if (raw.contains("keep_transform") && !raw["keep_transform"].is_boolean()) {
                ThrowPatchError("BAD_PARAM", "keep_transform must be boolean.", index);
            }
            const bool keepTransform = raw.value("keep_transform", true);
            INode* oldParent = simulatedParents[item.target];
            item.args["keep_transform"] = keepTransform;
            item.changed = oldParent != (item.parent ? item.parent : ip->GetRootNode());
            item.proof["change"] = {
                {"parent", {
                    {"from", NullableNodeRef(oldParent)},
                    {"to", NullableNodeRef(item.parent)},
                }},
                {"keepTransform", keepTransform},
            };
            simulatedParents[item.target] = item.parent ? item.parent : ip->GetRootNode();
            ValidateNoParentCycle(item.target, simulatedParents, index);
        } else {
            ThrowPatchError(
                "BAD_PARAM",
                "Unsupported scene_patch op: " + item.op,
                index,
                {{"supported", {"rename", "transform", "set_flags", "set_parent"}}});
        }

        item.proof["changed"] = item.changed;
        plan.push_back(std::move(item));
    }

    if (!renamedTargets.empty()) {
        std::unordered_map<std::string, std::vector<INode*>> nodesByFinalName;
        for (INode* node : allNodes) {
            nodesByFinalName[FoldName(simulatedNames[node])].push_back(node);
        }
        for (INode* target : renamedTargets) {
            const std::string& finalName = simulatedNames[target];
            const auto found = nodesByFinalName.find(FoldName(finalName));
            if (found != nodesByFinalName.end() && found->second.size() > 1) {
                json handles = json::array();
                for (INode* node : found->second) handles.push_back(NodeHandle(node));
                throw std::runtime_error(StructuredErrorPayload(
                    "DUPLICATE_NAME",
                    "scene_patch would create duplicate object name: " + finalName,
                    {{"name", finalName}, {"handles", handles}}));
            }
        }
    }

    return plan;
}

// Constraint, script and expression controllers ignore Move, Rotate and Scale
// without an error. Each requested part is compared with the node transform
// before it; the parts that left it unchanged are returned.
json ApplyTransform(const PlannedOperation& item, TimeValue time) {
    Matrix3 axis(TRUE);
    if (item.args.value("coordinate_system", "world") == "local") {
        axis = item.target->GetNodeTM(time);
        axis.NoTrans();
    }

    json ignored = json::array();
    const auto check = [&](const char* part, const Matrix3& before) {
        if (SameTransform(before, item.target->GetNodeTM(time)))
            ignored.push_back(IgnoredTransformPart(item.target, part));
    };
    if (item.args.contains("move")) {
        const Point3 value = ParseVector3(item.args["move"], "move", item.index);
        if (!IsZero(value)) {
            const Matrix3 before = item.target->GetNodeTM(time);
            item.target->Move(time, axis, value, TRUE, TRUE, PIV_NONE, TRUE);
            check("move", before);
        }
    }
    if (item.args.contains("rotate")) {
        const Point3 degrees = ParseVector3(item.args["rotate"], "rotate", item.index);
        if (!IsZero(degrees)) {
            const Matrix3 before = item.target->GetNodeTM(time);
            item.target->Rotate(time, axis, EulerDegreesToQuat(degrees.x, degrees.y, degrees.z), TRUE, TRUE,
                                PIV_NONE, TRUE);
            check("rotate", before);
        }
    }
    if (item.args.contains("scale")) {
        const Point3 value = ParseScale(item.args["scale"], item.index);
        if (!IsIdentityScale(value)) {
            const Matrix3 before = item.target->GetNodeTM(time);
            item.target->Scale(time, axis, value, TRUE, TRUE, PIV_NONE, TRUE);
            check("scale", before);
        }
    }
    return ignored;
}

// Requested transform parts per operation, to tell a wholly ignored transform
// from a partly applied one.
size_t RequestedTransformParts(const PlannedOperation& item) {
    size_t parts = 0;
    if (item.args.contains("move") && !IsZero(ParseVector3(item.args["move"], "move", item.index))) ++parts;
    if (item.args.contains("rotate") && !IsZero(ParseVector3(item.args["rotate"], "rotate", item.index))) ++parts;
    if (item.args.contains("scale") && !IsIdentityScale(ParseScale(item.args["scale"], item.index))) ++parts;
    return parts;
}

json ApplyOperation(const PlannedOperation& item, Interface* ip, TimeValue time) {
    if (!item.changed) return json::array();
    if (item.op == "transform") return ApplyTransform(item, time);
    if (item.op == "rename") {
        item.target->SetName(Utf8ToWide(item.args["name"].get<std::string>()).c_str());
        return json::array();
    }
    if (item.op == "set_flags") {
        const NodeFlags before = ReadFlags(item.target);
        NodeFlags after = before;
        if (item.args.contains("hidden")) after.hidden = item.args["hidden"].get<bool>();
        if (item.args.contains("frozen")) after.frozen = item.args["frozen"].get<bool>();
        if (item.args.contains("renderable")) after.renderable = item.args["renderable"].get<bool>();
        if (item.args.contains("cast_shadows")) after.castShadows = item.args["cast_shadows"].get<bool>();
        if (item.args.contains("receive_shadows")) after.receiveShadows = item.args["receive_shadows"].get<bool>();
        McpHost::UndoRecords::PutNodeFlagsRestore(item.target, FlagBits(before), FlagBits(after));
        if (item.args.contains("hidden")) item.target->Hide(item.args["hidden"].get<bool>());
        if (item.args.contains("frozen")) item.target->Freeze(item.args["frozen"].get<bool>());
        if (item.args.contains("renderable")) item.target->SetRenderable(item.args["renderable"].get<bool>());
        if (item.args.contains("cast_shadows")) item.target->SetCastShadows(item.args["cast_shadows"].get<bool>());
        if (item.args.contains("receive_shadows")) item.target->SetRcvShadows(item.args["receive_shadows"].get<bool>());
        return json::array();
    }
    if (item.op == "set_parent") {
        INode* parent = item.parent ? item.parent : ip->GetRootNode();
        parent->AttachChild(item.target, item.args.value("keep_transform", true) ? TRUE : FALSE);
        return json::array();
    }
    throw std::runtime_error("Unhandled preflighted operation: " + item.op);
}

json BuildProofArray(
    const std::vector<PlannedOperation>& plan,
    TimeValue time,
    bool includeAfter) {
    json proof = json::array();
    for (const auto& item : plan) {
        json row = item.proof;
        if (includeAfter) {
            row["target"]["after"] = NodeRefs::ToJson(item.target);
            if (item.op == "transform") {
                row["change"]["afterPosition"] = NodePosition(item.target, time);
            }
        }
        proof.push_back(std::move(row));
    }
    return proof;
}

json UniqueTouchedRefs(const std::vector<PlannedOperation>& plan) {
    json touched = json::array();
    std::unordered_set<unsigned long long> seen;
    for (const auto& item : plan) {
        const unsigned long long handle = NodeHandle(item.target);
        if (seen.insert(handle).second) touched.push_back(NodeRefs::ToJson(item.target));
    }
    return touched;
}

size_t ChangedCount(const std::vector<PlannedOperation>& plan) {
    return static_cast<size_t>(std::count_if(
        plan.begin(), plan.end(), [](const PlannedOperation& item) { return item.changed; }));
}

} // namespace

std::string NativeHandlers::ResolveNodeRefs(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        const json payload = ParseJsonObject(params);
        if (!payload.contains("refs") || payload["refs"].type() != json::value_t::array || payload["refs"].empty()) {
            throw std::runtime_error(StructuredErrorPayload(
                "BAD_PARAM", "refs must be a non-empty array of NodeRef objects."));
        }
        if (payload["refs"].size() > kMaxPatchOperations) {
            throw std::runtime_error(StructuredErrorPayload(
                "BAD_PARAM", "refs exceeds the 256-item limit."));
        }

        McpHost::SceneJournal::FlushPending();
        json resolved = json::array();
        for (size_t index = 0; index < payload["refs"].size(); ++index) {
            try {
                json row = NodeRefs::ToJson(NodeRefs::Resolve(payload["refs"][index]), true);
                row["index"] = index;
                resolved.push_back(std::move(row));
            } catch (const std::exception& error) {
                json parsed = json::parse(error.what(), nullptr, false);
                if (!parsed.is_discarded() && parsed.is_object()) {
                    json hint = parsed.value("hint", json::object());
                    hint["inputIndex"] = index;
                    parsed["hint"] = hint;
                    throw std::runtime_error(parsed.dump());
                }
                throw;
            }
        }

        json result;
        result["refs"] = std::move(resolved);
        result["sceneSeq"] = McpHost::SceneJournal::CurrentMutationSeq();
        result["activitySeq"] = McpHost::SceneJournal::CurrentSeq();
        result["journal"] = McpHost::SceneJournal::IsRegistered();
        return result.dump();
    });
}

std::string NativeHandlers::ScenePatch(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        const json payload = ParseJsonObject(params);
        if (!payload.contains("operations") || payload["operations"].type() != json::value_t::array ||
            payload["operations"].empty()) {
            throw std::runtime_error(StructuredErrorPayload(
                "BAD_PARAM", "operations must be a non-empty array."));
        }
        if (payload["operations"].size() > kMaxPatchOperations) {
            throw std::runtime_error(StructuredErrorPayload(
                "BAD_PARAM", "operations exceeds the 256-operation limit."));
        }

        if (payload.contains("dry_run") && !payload["dry_run"].is_boolean()) {
            throw std::runtime_error(StructuredErrorPayload(
                "BAD_PARAM", "dry_run must be boolean."));
        }
        const bool dryRun = payload.value("dry_run", false);
        if (payload.contains("label") && payload["label"].type() != json::value_t::string) {
            throw std::runtime_error(StructuredErrorPayload(
                "BAD_PARAM", "label must be a string."));
        }
        const std::string label = payload.value("label", "MCP Scene Patch");
        if (label.empty() || label.size() > 128) {
            throw std::runtime_error(StructuredErrorPayload(
                "BAD_PARAM", "label must contain 1 to 128 UTF-8 bytes."));
        }

        const ExpectedSequence expected = ParseExpectedSequence(payload);
        if (expected.present && !McpHost::SceneJournal::IsRegistered()) {
            throw std::runtime_error(StructuredErrorPayload(
                "SCENE_JOURNAL_UNAVAILABLE",
                "expected_scene_seq requires the native scene journal."));
        }

        McpHost::SceneJournal::FlushPending();
        const unsigned long long beforeActivitySeq = McpHost::SceneJournal::CurrentSeq();
        const unsigned long long beforeSeq = McpHost::SceneJournal::CurrentMutationSeq();
        if (expected.present && expected.value != beforeSeq) {
            ThrowSceneConflict(
                expected.value, beforeSeq, "Scene changed since expected_scene_seq.");
        }

        Interface* ip = GetCOREInterface();
        const TimeValue time = ip->GetTime();
        std::vector<INode*> allNodes;
        std::vector<PlannedOperation> plan = BuildPlan(
            payload["operations"], ip, time, allNodes);

        McpHost::SceneJournal::FlushPending();
        const unsigned long long readyActivitySeq = McpHost::SceneJournal::CurrentSeq();
        const unsigned long long readySeq = McpHost::SceneJournal::CurrentMutationSeq();
        if (readySeq != beforeSeq) {
            ThrowSceneConflict(
                beforeSeq, readySeq, "Scene changed while scene_patch was preflighting.");
        }

        size_t changedCount = ChangedCount(plan);
        json result;
        result["dryRun"] = dryRun;
        result["status"] = dryRun ? "preflight" : (changedCount ? "applied" : "noop");
        result["sceneSeq"] = {
            {"before", beforeSeq},
            {"after", beforeSeq},
        };
        result["activitySeq"] = {
            {"before", beforeActivitySeq},
            {"after", readyActivitySeq},
        };
        if (expected.present) result["sceneSeq"]["expected"] = expected.value;
        result["journal"] = McpHost::SceneJournal::IsRegistered();
        result["counts"] = {
            {"operations", plan.size()},
            {"changed", changedCount},
            {"targets", UniqueTouchedRefs(plan).size()},
        };
        result["operations"] = BuildProofArray(plan, time, false);
        result["transaction"] = dryRun || changedCount == 0 ? "none" : "one_native_hold";
        if (!dryRun && changedCount == 0) {
            result["warnings"] = json::array(
                {"No operation changed the scene: every target already matched the request."});
        }

        if (dryRun || changedCount == 0) {
            result["touched"] = UniqueTouchedRefs(plan);
            return result.dump();
        }

        ScenePatchTransaction transaction(label);
        size_t applyingIndex = 0;
        try {
            std::unordered_map<size_t, json> ignored;
            for (auto& item : plan) {
                applyingIndex = item.index;
                json parts = ApplyOperation(item, ip, time);
                if (parts.empty()) continue;
                if (parts.size() == RequestedTransformParts(item)) item.changed = false;
                ignored[item.index] = std::move(parts);
            }
            McpHost::DialogWatch::ThrowIfDismissed();
            changedCount = ChangedCount(plan);
            json ignoredRows = json::array();
            for (const auto& item : plan) {
                const auto found = ignored.find(item.index);
                if (found == ignored.end()) continue;
                ignoredRows.push_back({{"index", item.index}, {"target", NodeRefs::ToJson(item.target)},
                                       {"ignored", found->second}});
            }
            if (changedCount == 0) {
                transaction.Cancel();
                McpHost::SceneJournal::FlushPending();
                throw ScenePatchIgnored(StructuredErrorPayload(
                    "PATCH_APPLY_FAILED",
                    "No operation took effect: the targets' transform controllers ignored the edit "
                    "(constraint, script or expression controllers drive them). The scene is unchanged.",
                    {{"ignored", ignoredRows},
                     {"message", kTransformIgnoredAdvice}}));
            }
            ip->RedrawViews(time);
            result["operations"] = BuildProofArray(plan, time, true);
            for (size_t i = 0; i < plan.size(); ++i) {
                const auto found = ignored.find(plan[i].index);
                if (found == ignored.end()) continue;
                result["operations"][i]["changed"] = plan[i].changed;
                result["operations"][i]["ignored"] = found->second;
            }
            if (!ignoredRows.empty()) {
                result["status"] = "partial";
                result["counts"]["changed"] = changedCount;
                result["ignored"] = ignoredRows;
                result["warnings"] = json::array({std::to_string(ignoredRows.size()) +
                    " transform operation(s) were ignored in whole or part by the targets' transform "
                    "controllers (constraint, script or expression); see ignored."});
            }
            result["touched"] = UniqueTouchedRefs(plan);
            transaction.Accept();
        } catch (const ScenePatchIgnored&) {
            throw;
        } catch (const std::exception& error) {
            try {
                transaction.Cancel();
            } catch (...) {
            }
            McpHost::SceneJournal::FlushPending();
            json cause = json::parse(error.what(), nullptr, false);
            if (cause.is_discarded()) cause = error.what();
            throw std::runtime_error(StructuredErrorPayload(
                "PATCH_APPLY_FAILED",
                "scene_patch failed and its native hold was cancelled.",
                {
                    {"operationIndex", applyingIndex},
                    {"rollback", "cancelled"},
                    {"cause", cause},
                }));
        } catch (...) {
            try {
                transaction.Cancel();
            } catch (...) {
            }
            McpHost::SceneJournal::FlushPending();
            throw std::runtime_error(StructuredErrorPayload(
                "PATCH_APPLY_FAILED",
                "scene_patch failed and its native hold was cancelled.",
                {{"operationIndex", applyingIndex}, {"rollback", "cancelled"}}));
        }

        McpHost::SceneJournal::FlushPending();
        json handles = json::array();
        std::unordered_set<unsigned long long> seenHandles;
        for (const auto& item : plan) {
            const unsigned long long handle = NodeHandle(item.target);
            if (seenHandles.insert(handle).second) handles.push_back(handle);
        }
        const unsigned long long afterSeq = McpHost::SceneJournal::AppendSynthetic(
            "scene_patch",
            {{"operationCount", plan.size()}, {"changedCount", changedCount}, {"handles", handles}});
        result["sceneSeq"]["after"] = afterSeq;
        result["activitySeq"]["after"] = McpHost::SceneJournal::CurrentSeq();
        result["journalEventCount"] = McpHost::SceneJournal::ChangesSince(beforeActivitySeq, 512).size();
        result["undoLabel"] = label;
        return result.dump();
    });
}
