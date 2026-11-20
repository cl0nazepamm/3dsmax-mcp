// native:parameters — describe, read and set the parameters of anything a node
// carries (base object, modifiers, materials and their maps, controllers), or
// describe a class before any instance exists. The values go through the
// shared ParamCodec, the same model _mx's params use.
#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/core_bridge.h"
#include "mcp_bridge/param_codec.h"

using namespace HandlerHelpers;
using json = nlohmann::json;

namespace {

struct KindEntry { const char* name; SClass_ID sid; };

const KindEntry kKinds[] = {
    {"geometry", GEOMOBJECT_CLASS_ID}, {"shape", SHAPE_CLASS_ID}, {"helper", HELPER_CLASS_ID},
    {"light", LIGHT_CLASS_ID}, {"camera", CAMERA_CLASS_ID}, {"spacewarp", WSM_OBJECT_CLASS_ID},
    {"modifier", OSM_CLASS_ID}, {"wsmodifier", WSM_CLASS_ID}, {"material", MATERIAL_CLASS_ID},
    {"texmap", TEXMAP_CLASS_ID}, {"map", TEXMAP_CLASS_ID}, {"renderer", RENDERER_CLASS_ID},
    {"atmospheric", ATMOSPHERIC_CLASS_ID}, {"effect", RENDER_EFFECT_CLASS_ID},
    {"controller", CTRL_FLOAT_CLASS_ID}, {"float_controller", CTRL_FLOAT_CLASS_ID},
    {"position_controller", CTRL_POSITION_CLASS_ID}, {"rotation_controller", CTRL_ROTATION_CLASS_ID},
    {"scale_controller", CTRL_SCALE_CLASS_ID}, {"point3_controller", CTRL_POINT3_CLASS_ID},
    {"transform_controller", CTRL_MATRIX3_CLASS_ID},
};

ClassDesc* ClassByKind(const std::string& name, const std::string& kind) {
    if (kind.empty()) {
        if (ClassDesc* cd = FindClassDescByName(name)) return cd;
        if (ClassDesc* cd = ParamCodec::ScriptClass(name, {})) return cd;
        throw ParamCodec::Error("NOT_FOUND", "No class named '" + name + "'; check the MAXScript class name");
    }
    const std::string k = ParamCodec::Folded(kind);
    std::vector<SClass_ID> sids;
    std::vector<std::string> names;
    for (const KindEntry& entry : kKinds) {
        names.push_back(entry.name);
        if (ParamCodec::Folded(entry.name) == k) sids.push_back(entry.sid);
    }
    // "controller" covers every controller superclass.
    if (k == "controller")
        sids = {CTRL_FLOAT_CLASS_ID, CTRL_POSITION_CLASS_ID, CTRL_ROTATION_CLASS_ID, CTRL_SCALE_CLASS_ID,
                CTRL_POINT3_CLASS_ID, CTRL_MATRIX3_CLASS_ID};
    if (sids.empty()) ParamCodec::Fail("Unknown kind '" + kind + "'; kinds: " + ParamCodec::JoinNames(names));
    for (SClass_ID sid : sids)
        if (ClassDesc* cd = FindClassDescByName(name, sid)) return cd;
    if (ClassDesc* cd = ParamCodec::ScriptClass(name, sids)) return cd;
    throw ParamCodec::Error("NOT_FOUND", "No " + kind + " class named '" + name + "'");
}

ParamCodec::DescribeOptions Options(const json& p) {
    ParamCodec::DescribeOptions o;
    o.query = p.value("query", "");
    o.offset = (size_t)std::max(0, p.value("offset", 0));
    o.limit = (size_t)std::clamp(p.value("limit", 400), 1, 2000);
    return o;
}

} // namespace

std::string NativeHandlers::Parameters(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        if (!p.is_object()) throw std::runtime_error("Invalid JSON params");
        std::string action = ParamCodec::Lower(p.value("action", "describe"));
        const TimeValue t = GetCOREInterface()->GetTime();
        const std::string className = p.value("class_name", "");

        if (!className.empty()) {
            if (action != "describe")
                ParamCodec::Fail("class_name describes a class; " + action + " needs an object (name or handle) and a path");
            ClassDesc* cd = ClassByKind(className, p.value("kind", ""));
            json out = ParamCodec::DescribeClass(cd, t, Options(p));
            MAXSuperClass* super = nullptr;
            {
                ScopedMaxScriptEvaluationContext context;
                super = MAXSuperClass::lookup_superclass(cd->SuperClassID());
                if (super && super->name) out["superclass"] = WideToUtf8(super->name->to_string());
            }
            return out.dump();
        }

        INode* node = ResolveNodeFromPayload(p);
        const std::string path = p.value("path", "object");
        Animatable* owner = ParamCodec::ResolvePath(node, path, t);

        json out;
        if (action == "describe") {
            out = ParamCodec::Describe(owner, t, Options(p));
        } else if (action == "get") {
            const json names = p.value("names", json::array());
            if (!ParamCodec::IsArray(names) || names.empty()) ParamCodec::Fail("get needs names: a list of parameter names");
            json values = json::object();
            for (const json& name : names) {
                if (!ParamCodec::IsString(name)) ParamCodec::Fail("names must be strings");
                values[name.get<std::string>()] = ParamCodec::Get(owner, name.get<std::string>(), t);
            }
            out = {{"class", MaxScriptVisibleClassName(owner)}, {"owner", ParamCodec::OwnerLabel(owner)}, {"values", values}};
        } else if (action == "set") {
            const json values = p.value("values", json::object());
            if (!values.is_object() || values.empty()) ParamCodec::Fail("set needs values: {name: value, ...}");
            json readback = json::object();
            json warnings = json::array();
            ParamCodec::Apply(owner, values, t, &readback, &warnings);
            GetCOREInterface()->RedrawViews(t);
            out = {{"class", MaxScriptVisibleClassName(owner)}, {"owner", ParamCodec::OwnerLabel(owner)},
                   {"values", readback}, {"set", values.size()}};
            if (!warnings.empty()) out["warnings"] = warnings;
        } else {
            ParamCodec::Fail("action must be describe, get or set");
        }
        out["node"] = NodeIdentityJson(node);
        out["path"] = path;
        return out.dump();
    });
}
