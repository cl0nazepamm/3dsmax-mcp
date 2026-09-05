#pragma once
#include "mcp_bridge/handler_helpers.h"
#include <maxscript/foundation/numbers.h>
#include <maxscript/foundation/3dmath.h>
#include <cmath>
#include <limits>

namespace ModifierHelpers {

inline bool IsModifierClass(SClass_ID superClassId) {
    return superClassId == OSM_CLASS_ID || superClassId == WSM_CLASS_ID;
}

// Main thread only. The DLL list can omit deferred classes such as Surface and
// CrossSection. Search the complete, superclass-scoped registry instead.
// Script tokens take precedence over UI/internal aliases across both scopes.
inline ClassDesc* FindModifierClass(const std::string& name) {
    using namespace HandlerHelpers;
    const std::wstring wanted = Utf8ToWide(name);
    ScopedMaxScriptEvaluationContext evaluationContext;
    auto& directory = ClassDirectory::GetInstance();
    for (bool scriptNames : {true, false}) {
        for (SClass_ID superClassId : {OSM_CLASS_ID, WSM_CLASS_ID}) {
            SubClassList* list = directory.GetClassList(superClassId);
            if (!list) continue;
            for (int access : {ACC_PUBLIC, ACC_PRIVATE}) {
                for (int index = list->GetFirst(access); index >= 0;
                     index = list->GetNext(access)) {
                    ClassEntry& entry = (*list)[index];
                    const Class_ID id = entry.ClassID();
                    bool matches = false;
                    if (scriptNames) {
                        Class_ID lookupId = id; // Max 2023 takes a non-const pointer.
                        MAXClass* scriptClass = MAXClass::lookup_class(&lookupId, superClassId, false);
                        matches = scriptClass && scriptClass->name &&
                            _wcsicmp(scriptClass->name->to_string(), wanted.c_str()) == 0;
                    } else {
                        ClassDesc* descriptor = entry.CD();
                        if (!descriptor) continue;
                        for (const MCHAR* alias : {descriptor->InternalName(),
                                 descriptor->NonLocalizedClassName(), descriptor->ClassName()}) {
                            if (alias && _wcsicmp(alias, wanted.c_str()) == 0) {
                                matches = true;
                                break;
                            }
                        }
                    }
                    if (!matches) continue;
                    // Load only the matched plugin, then recheck its identity.
                    ClassDesc* descriptor = entry.FullCD();
                    if (!descriptor || descriptor->SuperClassID() != superClassId ||
                        descriptor->ClassID() != id)
                        throw std::runtime_error("Modifier descriptor identity mismatch: " + name);
                    return descriptor;
                }
            }
        }
    }
    return nullptr;
}

// No unscoped fallback: a position controller is also named "Surface".
// Casting that controller to Modifier* invokes the wrong vtable in modifier
// notifications (including MassFX's preModifierAdded callback).
inline Modifier* CreateModifier(const std::string& name) {
    ClassDesc* descriptor = FindModifierClass(name);
    if (!descriptor || !IsModifierClass(descriptor->SuperClassID()))
        throw std::runtime_error(HandlerHelpers::StructuredErrorPayload(
            "BAD_PARAM", "Unknown or non-modifier class: " + name,
            {{"message", "Use a registered modifier class name; other plugin types cannot be added to a modifier stack."}}));
    const SClass_ID superClassId = descriptor->SuperClassID();
    const Class_ID classId = descriptor->ClassID();
    auto* instance = static_cast<Animatable*>(
        GetCOREInterface()->CreateInstance(superClassId, classId));
    if (!instance) throw std::runtime_error("Failed to create modifier: " + name);
    if (instance->SuperClassID() != superClassId || instance->ClassID() != classId) {
        instance->DeleteThis();
        throw std::runtime_error("Created modifier identity mismatch: " + name);
    }
    return static_cast<Modifier*>(instance);
}

// PB1 modifiers (Surface, Normal, etc.) expose their parameters through the
// MAXScript wrapper, not NumParamBlocks()/IParamBlock2. Use that property API on
// the existing, typed modifier. Values are literals; no script is executed.
inline bool SetParameter(Modifier* modifier, const std::string& name,
                         const std::string& value, TimeValue time) {
    using namespace HandlerHelpers;
    if (SetParamByName(modifier, name, value, time)) return true;
    const auto first = value.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) return false;
    const std::string literal = value.substr(first, value.find_last_not_of(" \t\r\n") - first + 1);
    json parsed = json::parse(literal, nullptr, false);
    ScopedMaxScriptEvaluationContext evaluationContext;
    three_value_locals(wrapper, property, input);
    try {
        if (literal == "on" || literal == "true") vl.input = &true_value;
        else if (literal == "off" || literal == "false") vl.input = &false_value;
        else if (parsed.is_number_integer() && parsed.get<double>() >= INT_MIN && parsed.get<double>() <= INT_MAX)
            vl.input = Integer::intern(parsed.get<int>());
        else if ((parsed.is_number)() && std::isfinite(parsed.get<double>()) &&
                 std::abs(parsed.get<double>()) <= std::numeric_limits<float>::max())
            vl.input = Float::intern(parsed.get<float>());
        else if (parsed.type() == json::value_t::string)
            vl.input = new String(Utf8ToWide(parsed.get<std::string>()).c_str());
        else if (parsed.type() == json::value_t::array && parsed.size() == 3 &&
                 std::all_of(parsed.begin(), parsed.end(), [](const json& part) {
                     return (part.is_number)() && std::isfinite(part.get<double>()) &&
                         std::abs(part.get<double>()) <= std::numeric_limits<float>::max();
                 }))
            vl.input = new Point3Value(Point3(parsed[0].get<float>(), parsed[1].get<float>(), parsed[2].get<float>()));
        else if (literal.front() == '#' && literal.size() > 1 &&
                 literal.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_", 1) == std::string::npos)
            vl.input = Name::intern(Utf8ToWide(literal.substr(1)).c_str());
        else {
            return false;
        }
        vl.wrapper = MAXModifier::intern(modifier);
        vl.property = Name::intern(Utf8ToWide(name).c_str());
        vl.wrapper->_set_property(vl.property, vl.input);
    } catch (...) {
        throw std::runtime_error("Cannot set modifier parameter: " + name);
    }
    return true;
}

inline nlohmann::json InspectLegacyParameters(Modifier* modifier) {
    using namespace HandlerHelpers;
    json properties = json::array();
    if (!modifier) return properties;
    ScopedMaxScriptEvaluationContext evaluationContext;
    Class_ID id = modifier->ClassID();
    MAXClass* scriptClass = MAXClass::lookup_class(&id, modifier->SuperClassID(), true);
    if (!scriptClass) return properties;
    scriptClass->build_parm_descs(modifier);
    four_value_locals(wrapper, output, type, printed);
    vl.wrapper = MAXModifier::intern(modifier);
    for (int index = 0; index < scriptClass->n_parms && index < 500; ++index) {
        const parm_desc& parameter = scriptClass->parms[index];
        if (!parameter.name) continue;
        json entry = {{"name", WideToUtf8(parameter.name->to_string())},
            {"source", "MAXScript property metadata"}};
        try {
            vl.output = vl.wrapper->_get_property(parameter.name);
            vl.printed = new StringStream();
            vl.output->sprin1(static_cast<CharStream*>(vl.printed));
            entry["value"] = WideToUtf8(vl.printed->to_string());
            vl.type = vl.output->classOf_vf(nullptr, 0);
            vl.printed = new StringStream();
            vl.type->sprin1(static_cast<CharStream*>(vl.printed));
            entry["runtimeType"] = WideToUtf8(vl.printed->to_string());
            entry["declaredTypeCode"] = static_cast<int>(parameter.type);
        } catch (...) { entry["value"] = "<unavailable>"; }
        properties.push_back(std::move(entry));
    }
    return properties;
}

} // namespace ModifierHelpers
