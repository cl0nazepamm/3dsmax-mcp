// Pure dispatcher policy regression: no Max process, SDK or scene is involved.
#include "mcp_bridge/transaction_policy.h"
#include <iostream>

using json = nlohmann::json;
using CommandDispatcher::Detail::RequestIsDryRunOrPreview;

int main() {
    int checked = 0, failed = 0;
    const auto check = [&](const char* route, const json& payload, bool expected) {
        ++checked;
        const bool actual = RequestIsDryRunOrPreview(route, payload.dump());
        if (actual != expected) {
            ++failed;
            std::cerr << route << " " << payload.dump() << ": expected " << expected << "\n";
        }
    };

    // Ignored flags must not let these mutations join an existing user hold.
    for (const char* route : {"native:create_object", "native:add_modifier",
        "native:set_modifier_property", "native:transform_object", "native:clone_objects",
        "native:set_object_property", "native:assign_material", "native:create_shell_material",
        "native:write_osl_shader", "native:manage_layers", "native:run_macroscript",
        "native:scene_patch", "native:future_mutating_tool"}) {
        check(route, {{"preview", true}}, false);
        check(route, {{"dry_run", true}}, false);
        check(route, {{"preview", true}, {"dry_run", true}}, false);
    }
    for (const char* route : {"native:delete_objects", "native:collapse_modifier_stack",
        "native:scene_qa_fix"}) {
        check(route, json::object(), false);
        check(route, {{"dry_run", true}}, true);
        check(route, {{"preview", true}}, false);
        check(route, {{"dry_run", false}, {"preview", true}}, false);
    }
    check("native:replace_material", json::object(), false);
    check("native:replace_material", {{"preview", true}}, true);
    check("native:replace_material", {{"dry_run", true}}, false);
    check("native:batch_replace_materials", json::object(), false);
    check("native:batch_replace_materials", {{"dry_run", true}}, true);
    check("native:batch_replace_materials", {{"preview", true}}, true);
    check("native:batch_replace_materials", {{"preview", false}, {"dry_run", false}}, false);

    check("native:replicate_material", json::object(), true);
    check("native:replicate_material", {{"preview", false}}, false);
    check("native:replicate_material", {{"preview", false}, {"dry_run", true}}, false);
    check("native:replicate_material", {{"preview", true}, {"mode", "clone"}}, true);
    check("native:replicate_material", {{"preview", false}, {"mode", "PREVIEW"}}, true);
    check("native:replicate_material_apply", json::object(), false);
    check("native:replicate_material_apply", {{"preview", true}, {"dry_run", true}}, false);
    check("native:replicate_material_apply", {{"mode", "preview"}}, true);
    check("native:replicate_material_apply", {{"mode", "PREVIEW"}, {"preview", false}}, true);
    check("native:replicate_material_apply", {{"mode", "clone"}, {"preview", true}}, false);

    std::cout << checked << " transaction policy cases, " << failed << " failures\n";
    return failed ? 1 : 0;
}
