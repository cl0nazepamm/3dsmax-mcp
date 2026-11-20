#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/core_bridge.h"

#include <filesystem>
#include <memory>
#include <vector>

#include <modstack.h>
#include <iparamb2.h>

using json = nlohmann::json;
using namespace HandlerHelpers;

namespace {

namespace fs = std::filesystem;

constexpr size_t kMaxNodeParameters = 32;

[[noreturn]] void ThrowMCG(
    const std::string& code,
    const std::string& message,
    const json& hint = json()) {
    throw std::runtime_error(StructuredErrorPayload(code, message, hint));
}

json ParsePayload(const std::string& params) {
    json payload = json::parse(params, nullptr, false);
    if (payload.is_discarded() || !payload.is_object()) {
        ThrowMCG("BAD_PARAM", "MCG native payload must be a JSON object");
    }
    return payload;
}

unsigned long ParseClassPart(const json& value, const char* label) {
    unsigned long long part = 0;
    if (value.is_number_unsigned()) {
        part = value.get<unsigned long long>();
    } else if (value.is_number_integer()) {
        const long long signedPart = value.get<long long>();
        if (signedPart < 0) {
            ThrowMCG("BAD_PARAM", std::string(label) + " must be unsigned");
        }
        part = static_cast<unsigned long long>(signedPart);
    } else {
        ThrowMCG("BAD_PARAM", std::string(label) + " must be an integer");
    }
    if (part > 0xffffffffULL) {
        ThrowMCG("BAD_PARAM", std::string(label) + " is outside the 32-bit class ID range");
    }
    return static_cast<unsigned long>(part);
}

Class_ID ParseClassID(const json& payload) {
    const auto it = payload.find("class_id");
    if (it == payload.end() || it->type() != json::value_t::array || it->size() != 2) {
        ThrowMCG("BAD_PARAM", "class_id must be a two-integer array");
    }
    return Class_ID(
        ParseClassPart((*it)[0], "class_id[0]"),
        ParseClassPart((*it)[1], "class_id[1]"));
}

json ClassIDJson(const Class_ID& classID) {
    return json::array({
        static_cast<unsigned long long>(classID.PartA()),
        static_cast<unsigned long long>(classID.PartB()),
    });
}

std::string DescInternalName(ClassDesc* desc) {
    if (!desc) return {};
    const MCHAR* name = desc->InternalName();
    return name ? WideToUtf8(name) : std::string();
}

std::string DescClassName(ClassDesc* desc) {
    if (!desc) return {};
    const MCHAR* name = desc->ClassName();
    return name ? WideToUtf8(name) : std::string();
}

json ClassDescJson(ClassDesc* desc) {
    return {
        {"class_id", ClassIDJson(desc->ClassID())},
        {"superclass_id", static_cast<unsigned long long>(desc->SuperClassID())},
        {"class_name", DescClassName(desc)},
        {"internal_name", DescInternalName(desc)},
    };
}

bool EqualsNoCase(const std::string& left, const std::string& right) {
    if (left.size() != right.size()) return false;
    return std::equal(
        left.begin(), left.end(), right.begin(),
        [](unsigned char a, unsigned char b) { return std::tolower(a) == std::tolower(b); });
}

ClassDesc* ResolveMCGClass(const json& payload, Class_ID* outClassID = nullptr) {
    const Class_ID classID = ParseClassID(payload);
    ClassDesc* desc = ClassDirectory::GetInstance().FindClass(OSM_CLASS_ID, classID);
    if (!desc) {
        ThrowMCG(
            "MCG_CLASS_NOT_FOUND",
            "No loaded modifier class matches the compiled MCG class ID",
            {{"class_id", ClassIDJson(classID)},
             {"message", "Compile the graph in this 3ds Max session before applying it."}});
    }
    if (desc->SuperClassID() != OSM_CLASS_ID || desc->ClassID() != classID) {
        ThrowMCG(
            "MCG_CLASS_MISMATCH",
            "Resolved class descriptor does not match the requested MCG modifier class",
            {{"expected_class_id", ClassIDJson(classID)}, {"resolved", ClassDescJson(desc)}});
    }

    const std::string expected = payload.value("expected_identifier", "");
    const std::string internalName = DescInternalName(desc);
    if (!expected.empty() && !internalName.empty() && !EqualsNoCase(expected, internalName)) {
        ThrowMCG(
            "MCG_CLASS_MISMATCH",
            "Compiled MCG class internal name does not match the expected generated wrapper",
            {{"expected_identifier", expected}, {"resolved", ClassDescJson(desc)}});
    }

    if (outClassID) *outClassID = classID;
    return desc;
}

std::wstring NormalizedPathKey(const fs::path& input) {
    std::error_code ec;
    fs::path normalized = fs::weakly_canonical(input, ec);
    if (ec) {
        ec.clear();
        normalized = fs::absolute(input, ec);
    }
    if (ec) normalized = input;
    std::wstring key = normalized.lexically_normal().native();
    std::transform(key.begin(), key.end(), key.begin(), towlower);
    return key;
}

fs::path RequiredGraphPath(const json& payload) {
    const auto it = payload.find("graph_path");
    if (it == payload.end() || it->type() != json::value_t::string) {
        ThrowMCG("BAD_PARAM", "graph_path is required");
    }
    const std::string raw = it->get<std::string>();
    if (raw.empty()) ThrowMCG("BAD_PARAM", "graph_path is required");
    fs::path path(Utf8ToWide(raw));
    std::error_code ec;
    if (!fs::is_regular_file(path, ec) || ec) {
        ThrowMCG("MCG_GRAPH_NOT_FOUND", "MCG graph_path is not a readable file", {{"graph_path", raw}});
    }
    std::wstring extension = path.extension().native();
    std::transform(extension.begin(), extension.end(), extension.begin(), towlower);
    if (extension != L".maxtool" && extension != L".maxcompound") {
        ThrowMCG("BAD_PARAM", "graph_path must reference a .maxtool or .maxcompound file");
    }
    return path;
}

struct PB2ParamRef {
    IParamBlock2* block = nullptr;
    ParamID id = 0;
    ParamType2 type = static_cast<ParamType2>(TYPE_INT);
    std::string name;
};

std::vector<PB2ParamRef> FindParams(Animatable* anim, const std::string& requested) {
    std::vector<PB2ParamRef> matches;
    if (!anim) return matches;
    const std::wstring wanted = Utf8ToWide(requested);
    for (int blockIndex = 0; blockIndex < anim->NumParamBlocks(); ++blockIndex) {
        IParamBlock2* block = anim->GetParamBlock(blockIndex);
        if (!block) continue;
        ParamBlockDesc2* desc = block->GetDesc();
        if (!desc) continue;
        for (int paramIndex = 0; paramIndex < desc->count; ++paramIndex) {
            const ParamID id = desc->IndextoID(paramIndex);
            const ParamDef& def = desc->GetParamDef(id);
            if (!def.int_name || _wcsicmp(def.int_name, wanted.c_str()) != 0) continue;
            matches.push_back({block, id, def.type, WideToUtf8(def.int_name)});
        }
    }
    return matches;
}

PB2ParamRef RequireUniqueParam(Animatable* anim, const std::string& name) {
    std::vector<PB2ParamRef> matches = FindParams(anim, name);
    if (matches.empty()) {
        ThrowMCG("MCG_PARAMETER_NOT_FOUND", "MCG modifier parameter not found: " + name);
    }
    if (matches.size() != 1) {
        ThrowMCG("MCG_PARAMETER_AMBIGUOUS", "MCG modifier exposes the parameter more than once: " + name);
    }
    return matches.front();
}

PB2ParamRef RequireNodeParam(Animatable* anim, const std::string& name) {
    PB2ParamRef ref = RequireUniqueParam(anim, name);
    const int baseType = ref.type & ~TYPE_TAB;
    if ((ref.type & TYPE_TAB) || baseType != TYPE_INODE) {
        ThrowMCG(
            "MCG_PARAMETER_TYPE",
            "MCG parameter is not a scalar node reference: " + name,
            {{"parameter", ref.name}, {"type", baseType}, {"is_tab", (ref.type & TYPE_TAB) != 0}});
    }
    return ref;
}

std::string ReadPluginGraph(Modifier* modifier) {
    PB2ParamRef ref = RequireUniqueParam(modifier, "pluginGraph");
    const int baseType = ref.type & ~TYPE_TAB;
    if ((ref.type & TYPE_TAB) || (baseType != TYPE_STRING && baseType != TYPE_FILENAME)) {
        ThrowMCG("MCG_GRAPH_MISMATCH", "Resolved modifier has an invalid pluginGraph parameter type");
    }
    const MCHAR* value = ref.block->GetStr(ref.id, GetCOREInterface()->GetTime());
    if (!value || !*value) {
        ThrowMCG("MCG_GRAPH_MISMATCH", "Resolved modifier does not identify its source MCG graph");
    }
    return WideToUtf8(value);
}

std::string ValidatePluginGraph(Modifier* modifier, const fs::path& expectedPath) {
    const std::string actual = ReadPluginGraph(modifier);
    if (NormalizedPathKey(fs::path(Utf8ToWide(actual))) != NormalizedPathKey(expectedPath)) {
        ThrowMCG(
            "MCG_GRAPH_MISMATCH",
            "Resolved modifier was generated from a different MCG graph",
            {{"expected_graph_path", WideToUtf8(expectedPath.native().c_str())},
             {"actual_graph_path", actual}});
    }
    return actual;
}

Modifier* RequireModifierAt(INode* node, int oneBasedIndex) {
    if (oneBasedIndex <= 0) {
        ThrowMCG("BAD_PARAM", "modifier_index must be a positive 1-based index");
    }
    Object* objectRef = node ? node->GetObjectRef() : nullptr;
    if (!objectRef || objectRef->SuperClassID() != GEN_DERIVOB_CLASS_ID) {
        ThrowMCG("NOT_FOUND", "Target node has no modifier stack");
    }
    IDerivedObject* derived = static_cast<IDerivedObject*>(objectRef);
    const int zeroBased = oneBasedIndex - 1;
    if (zeroBased >= derived->NumModifiers()) {
        ThrowMCG(
            "NOT_FOUND",
            "modifier_index is outside the target modifier stack",
            {{"modifier_index", oneBasedIndex}, {"modifier_count", derived->NumModifiers()}});
    }
    Modifier* modifier = derived->GetModifier(zeroBased);
    if (!modifier) ThrowMCG("NOT_FOUND", "Modifier stack entry is unavailable");
    return modifier;
}

void ValidateExistingModifier(Modifier* modifier, const Class_ID& expectedClassID, const fs::path& graphPath) {
    if (!modifier || modifier->SuperClassID() != OSM_CLASS_ID || modifier->ClassID() != expectedClassID) {
        ThrowMCG(
            "MCG_CLASS_MISMATCH",
            "Modifier stack entry does not match the compiled MCG class ID",
            {{"expected_class_id", ClassIDJson(expectedClassID)},
             {"actual_class_id", modifier ? ClassIDJson(modifier->ClassID()) : json()}});
    }
    ValidatePluginGraph(modifier, graphPath);
}

json NodeParameterJson(Modifier* modifier, TimeValue time) {
    json parameters = json::array();
    if (!modifier) return parameters;
    for (int blockIndex = 0; blockIndex < modifier->NumParamBlocks(); ++blockIndex) {
        IParamBlock2* block = modifier->GetParamBlock(blockIndex);
        if (!block) continue;
        ParamBlockDesc2* desc = block->GetDesc();
        if (!desc) continue;
        for (int paramIndex = 0; paramIndex < desc->count; ++paramIndex) {
            const ParamID id = desc->IndextoID(paramIndex);
            const ParamDef& def = desc->GetParamDef(id);
            const int baseType = def.type & ~TYPE_TAB;
            if (baseType != TYPE_INODE) continue;
            json item = {
                {"name", def.int_name ? WideToUtf8(def.int_name) : ""},
                {"type", "node"},
                {"is_tab", (def.type & TYPE_TAB) != 0},
            };
            if (def.type & TYPE_TAB) {
                item["supported"] = false;
                item["count"] = block->Count(id);
            } else {
                item["supported"] = true;
                INode* value = block->GetINode(id, time);
                item["value"] = value ? NodeIdentityJson(value) : json();
            }
            parameters.push_back(item);
        }
    }
    return parameters;
}

json ModifierProof(
    INode* target,
    Modifier* modifier,
    int modifierIndex,
    const std::string& graphPath,
    TimeValue time) {
    return {
        {"target", NodeIdentityJson(target)},
        {"modifier", {
            {"index", modifierIndex},
            {"name", WideToUtf8(modifier->GetName(false).data())},
            {"class_name", WideToUtf8(modifier->ClassName().data())},
            {"class_id", ClassIDJson(modifier->ClassID())},
            {"graph_path", graphPath},
            {"node_parameters", NodeParameterJson(modifier, time)},
        }},
    };
}

class UnattachedModifierGuard {
public:
    explicit UnattachedModifierGuard(Modifier* modifier) : modifier_(modifier) {}
    ~UnattachedModifierGuard() {
        if (modifier_) {
            try { modifier_->DeleteThis(); } catch (...) {}
        }
    }
    Modifier* get() const { return modifier_; }
    Modifier* release() {
        Modifier* value = modifier_;
        modifier_ = nullptr;
        return value;
    }
private:
    Modifier* modifier_;
};

} // namespace

std::string NativeHandlers::MCGResolveClass(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        const json payload = ParsePayload(params);
        ClassDesc* desc = ResolveMCGClass(payload);
        json result = ClassDescJson(desc);
        result["resolved"] = true;
        return result.dump();
    });
}

std::string NativeHandlers::MCGApplyModifier(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        const json payload = ParsePayload(params);
        const fs::path graphPath = RequiredGraphPath(payload);
        Class_ID classID;
        ClassDesc* desc = ResolveMCGClass(payload, &classID);
        INode* target = ResolveNodeFromPayload(payload);

        const auto nodeParametersIt = payload.find("node_parameters");
        json nodeParameters = json::object();
        if (nodeParametersIt != payload.end() && !nodeParametersIt->is_null()) {
            if (!nodeParametersIt->is_object()) {
                ThrowMCG("BAD_PARAM", "node_parameters must be an object of PB2 names to node selectors");
            }
            nodeParameters = *nodeParametersIt;
        }
        if (nodeParameters.size() > kMaxNodeParameters) {
            ThrowMCG("BAD_PARAM", "Too many MCG node parameters in one request");
        }

        Interface* interfacePtr = GetCOREInterface();
        const TimeValue time = interfacePtr->GetTime();
        Modifier* created = static_cast<Modifier*>(interfacePtr->CreateInstance(OSM_CLASS_ID, classID));
        if (!created) {
            ThrowMCG("MCG_CREATE_FAILED", "3ds Max could not instantiate the compiled MCG modifier");
        }
        UnattachedModifierGuard modifierGuard(created);
        if (created->SuperClassID() != OSM_CLASS_ID || created->ClassID() != classID) {
            ThrowMCG("MCG_CLASS_MISMATCH", "Created modifier instance does not match its class descriptor");
        }
        const std::string actualGraphPath = ValidatePluginGraph(created, graphPath);

        json assigned = json::array();
        for (auto it = nodeParameters.begin(); it != nodeParameters.end(); ++it) {
            if (!it.value().is_object()) {
                ThrowMCG("BAD_PARAM", "Each node_parameters value must be a {name} or {handle} selector");
            }
            PB2ParamRef parameter = RequireNodeParam(created, it.key());
            INode* source = ResolveNodeFromPayload(it.value());
            if (source == target) {
                ThrowMCG(
                    "MCG_REFERENCE_LOOP",
                    "An MCG modifier cannot use its own target node as a source",
                    {{"parameter", parameter.name}, {"target", NodeIdentityJson(target)}});
            }
            if (!parameter.block->SetValue(parameter.id, time, source)) {
                ThrowMCG("MCG_PARAMETER_SET_FAILED", "3ds Max rejected MCG node parameter: " + parameter.name);
            }
            assigned.push_back({{"parameter", parameter.name}, {"source", NodeIdentityJson(source)}});
        }

        Object* objectRef = target->GetObjectRef();
        if (!objectRef) {
            ThrowMCG("MCG_APPLY_FAILED", "Target node has no object reference for a modifier stack");
        }
        IDerivedObject* derived = nullptr;
        bool createdDerived = false;
        if (objectRef && objectRef->SuperClassID() == GEN_DERIVOB_CLASS_ID) {
            derived = static_cast<IDerivedObject*>(objectRef);
        } else {
            derived = CreateDerivedObject(objectRef);
            createdDerived = true;
        }
        if (!derived) ThrowMCG("MCG_APPLY_FAILED", "Could not create a modifier stack for the target node");

        derived->AddModifier(created);
        modifierGuard.release();
        if (createdDerived) target->SetObjectRef(derived);

        int modifierIndex = 0;
        for (int index = 0; index < derived->NumModifiers(); ++index) {
            if (derived->GetModifier(index) == created) {
                modifierIndex = index + 1;
                break;
            }
        }
        if (modifierIndex == 0) {
            ThrowMCG("MCG_APPLY_FAILED", "MCG modifier was not found after stack insertion");
        }

        created->NotifyDependents(FOREVER, PART_ALL, REFMSG_CHANGE);
        interfacePtr->RedrawViews(time);
        json result = ModifierProof(target, created, modifierIndex, actualGraphPath, time);
        result["message"] = "Applied compiled MCG modifier with validated node references";
        result["assigned"] = assigned;
        result["class"] = ClassDescJson(desc);
        return result.dump();
    });
}

std::string NativeHandlers::MCGSetNodeParameter(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        const json payload = ParsePayload(params);
        const fs::path graphPath = RequiredGraphPath(payload);
        const Class_ID classID = ParseClassID(payload);
        INode* target = ResolveNodeFromPayload(payload);
        const int modifierIndex = payload.value("modifier_index", 0);
        const std::string parameterName = payload.value("parameter", "");
        if (parameterName.empty()) ThrowMCG("BAD_PARAM", "parameter is required");
        const auto sourceIt = payload.find("source");
        if (sourceIt == payload.end() || !sourceIt->is_object()) {
            ThrowMCG("BAD_PARAM", "source must be a {name} or {handle} node selector");
        }

        Modifier* modifier = RequireModifierAt(target, modifierIndex);
        ValidateExistingModifier(modifier, classID, graphPath);
        PB2ParamRef parameter = RequireNodeParam(modifier, parameterName);
        INode* source = ResolveNodeFromPayload(*sourceIt);
        if (source == target) {
            ThrowMCG(
                "MCG_REFERENCE_LOOP",
                "An MCG modifier cannot use its own target node as a source",
                {{"parameter", parameter.name}, {"target", NodeIdentityJson(target)}});
        }

        Interface* interfacePtr = GetCOREInterface();
        const TimeValue time = interfacePtr->GetTime();
        if (!parameter.block->SetValue(parameter.id, time, source)) {
            ThrowMCG("MCG_PARAMETER_SET_FAILED", "3ds Max rejected MCG node parameter: " + parameter.name);
        }
        modifier->NotifyDependents(FOREVER, PART_ALL, REFMSG_CHANGE);
        interfacePtr->RedrawViews(time);
        json result = ModifierProof(target, modifier, modifierIndex, ReadPluginGraph(modifier), time);
        result["message"] = "Updated validated MCG node parameter";
        result["updated"] = {{"parameter", parameter.name}, {"source", NodeIdentityJson(source)}};
        return result.dump();
    });
}

std::string NativeHandlers::MCGInspectInstance(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        const json payload = ParsePayload(params);
        const fs::path graphPath = RequiredGraphPath(payload);
        const Class_ID classID = ParseClassID(payload);
        INode* target = ResolveNodeFromPayload(payload);
        const int modifierIndex = payload.value("modifier_index", 0);
        Modifier* modifier = RequireModifierAt(target, modifierIndex);
        ValidateExistingModifier(modifier, classID, graphPath);
        return ModifierProof(
            target,
            modifier,
            modifierIndex,
            ReadPluginGraph(modifier),
            GetCOREInterface()->GetTime()).dump();
    });
}
