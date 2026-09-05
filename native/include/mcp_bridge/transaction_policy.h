#pragma once
#include <nlohmann/json.hpp>
#include <algorithm>
#include <cctype>
#include <string>

namespace CommandDispatcher::Detail {

// Exempt only flags actually honored by the destination handler. An unrelated
// preview/dry_run field must never disable a write's undo hold or USER_BUSY gate.
inline bool RequestIsDryRunOrPreview(const std::string& cmd_type, const std::string& command) {
    const bool dryRunOnly = cmd_type == "native:delete_objects" ||
        cmd_type == "native:collapse_modifier_stack" || cmd_type == "native:scene_qa_fix";
    const bool replace = cmd_type == "native:replace_material";
    const bool batchReplace = cmd_type == "native:batch_replace_materials";
    const bool replicate = cmd_type == "native:replicate_material";
    const bool replicateApply = cmd_type == "native:replicate_material_apply";
    if (!dryRunOnly && !replace && !batchReplace && !replicate && !replicateApply)
        return false;
    auto payload = nlohmann::json::parse(command, nullptr, false);
    if (!payload.is_object()) return false;
    if (dryRunOnly) return payload.value("dry_run", false);
    if (replace) return payload.value("preview", false);
    if (batchReplace) return payload.value("preview", false) || payload.value("dry_run", false);

    // ReplicateMaterial defaults to preview; the explicit Apply route ignores
    // preview and dry_run, but both routes honor mode="preview" (case-insensitive).
    std::string mode = payload.value("mode", "clone_and_remap");
    std::transform(mode.begin(), mode.end(), mode.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return replicate ? payload.value("preview", true) || mode == "preview" : mode == "preview";
}

} // namespace CommandDispatcher::Detail
