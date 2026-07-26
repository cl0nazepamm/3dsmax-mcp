#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/bridge_gup.h"

#include <plugin.h>
#include <Rendering/Renderer.h>

#include <algorithm>
#include <cctype>
#include <initializer_list>
#include <string>
#include <vector>

using json = nlohmann::json;
using namespace HandlerHelpers;

namespace {

std::string NormalizeClassName(const MCHAR* value) {
    std::string normalized;
    const std::string utf8 = WideToUtf8(value);
    normalized.reserve(utf8.size());
    for (unsigned char ch : utf8) {
        if (std::isalnum(ch)) {
            normalized.push_back(static_cast<char>(std::tolower(ch)));
        }
    }
    return normalized;
}

bool MatchesAny(
    const std::string& value,
    std::initializer_list<const char*> expected) {
    for (const char* candidate : expected) {
        if (value == candidate) return true;
    }
    return false;
}

std::vector<std::string> MaxScriptClassNames(SClass_ID superClassId) {
    struct ScriptClassEntry {
        Class_ID classId;
        std::string name;
    };

    std::vector<ScriptClassEntry> entries;
    ClassDirectory& classDirectory =
        DllDir::GetInstance().ClassDir();
    SubClassList* classList =
        classDirectory.GetClassList(superClassId);
    if (!classList) return {};

    // MAXScript superclass registries contain public and private classes.
    // SubClassList's filtered iterator returns the real (zero-based) indices;
    // walking 1..Count reaches past the list on some Max versions.
    for (int accessType : {ACC_PUBLIC, ACC_PRIVATE}) {
        for (int index = classList->GetFirst(accessType);
             index >= 0;
             index = classList->GetNext(accessType)) {
            ClassEntry& entry = (*classList)[index];
            const Class_ID classId = entry.ClassID();
            std::string name =
                MaxScriptVisibleClassName(superClassId, classId);
            if (!name.empty()) {
                entries.push_back({classId, std::move(name)});
            }
        }
    }

    // MAXScript's `.classes` registry is ordered by the unsigned Class_ID.
    // This was verified live against RendererClass.classes, including private
    // classes and the Missing_Renderer sentinel.
    std::sort(
        entries.begin(),
        entries.end(),
        [](const ScriptClassEntry& left,
           const ScriptClassEntry& right) {
            if (left.classId.PartA() != right.classId.PartA()) {
                return left.classId.PartA() < right.classId.PartA();
            }
            return left.classId.PartB() < right.classId.PartB();
        });

    std::vector<std::string> names;
    names.reserve(entries.size());
    Class_ID previous;
    bool hasPrevious = false;
    for (const ScriptClassEntry& entry : entries) {
        if (hasPrevious && entry.classId == previous) {
            continue;
        }
        names.push_back(entry.name);
        previous = entry.classId;
        hasPrevious = true;
    }
    return names;
}

void DetectKnownPlugin(
    ClassDesc* descriptor,
    bool& forestPack,
    bool& forestLite,
    bool& tyFlow,
    bool& railClone,
    bool& phoenixFD) {
    if (!descriptor) return;

    const MCHAR* names[] = {
        descriptor->InternalName(),
        descriptor->NonLocalizedClassName(),
        descriptor->ClassName(),
    };

    for (const MCHAR* rawName : names) {
        if (!rawName || !*rawName) continue;
        const std::string name = NormalizeClassName(rawName);

        if (MatchesAny(name, {"forestpro", "forestpackpro", "forestpack"})) {
            forestPack = true;
        } else if (MatchesAny(name, {"forestlite", "forestpacklite"})) {
            forestLite = true;
        }

        if (name == "tyflow") tyFlow = true;
        if (MatchesAny(name, {"railclonepro", "railclone"})) railClone = true;
        if (name == "phoenixfdliquid") phoenixFD = true;
    }
}

} // namespace

// ── native:get_plugin_capabilities ─────────────────────────────
// Pure SDK replacement for the MAXScript capability probe.
std::string NativeHandlers::GetPluginCapabilities(
    const std::string&,
    MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([]() -> std::string {
        Interface* ip = GetCOREInterface();
        if (!ip) {
            throw std::runtime_error("3ds Max core interface is unavailable");
        }

        DllDir& dllDirectory = DllDir::GetInstance();
        ClassDirectory& classDirectory = dllDirectory.ClassDir();

        json result;
        const DWORD version = Get3DSMAXVersion();
        result["maxVersion"] = 1998 + (HIWORD(version) / 1000);

        Renderer* currentRenderer = ip->GetCurrentRenderer(false);
        std::string currentRendererName;
        if (currentRenderer) {
            ClassDesc* descriptor = classDirectory.FindClass(
                RENDERER_CLASS_ID,
                currentRenderer->ClassID());
            try {
                currentRendererName = MaxScriptVisibleClassName(
                    RENDERER_CLASS_ID,
                    currentRenderer->ClassID());
            } catch (...) {
                throw std::runtime_error(
                    "Capabilities failed reading the current renderer MAXClass");
            }
            if (currentRendererName.empty()) {
                currentRendererName = WideToUtf8(
                    currentRenderer->ClassName().data());
            }
        }
        result["renderer"] = currentRendererName;

        std::vector<std::string> rendererNames;
        try {
            rendererNames = MaxScriptClassNames(RENDERER_CLASS_ID);
        } catch (...) {
            throw std::runtime_error(
                "Capabilities failed enumerating RendererClass.classes");
        }
        json renderers = rendererNames;
        result["renderers"] = renderers;

        bool forestPack = false;
        bool forestLite = false;
        bool tyFlow = false;
        bool railClone = false;
        bool phoenixFD = false;

        for (int dllIndex = 0; dllIndex < dllDirectory.Count(); ++dllIndex) {
            const DllDesc& dll = dllDirectory[dllIndex];
            for (int classIndex = 0;
                 classIndex < dll.NumberOfClasses();
                 ++classIndex) {
                DetectKnownPlugin(
                    dll[classIndex],
                    forestPack,
                    forestLite,
                    tyFlow,
                    railClone,
                    phoenixFD);
            }
        }

        result["plugins"] = {
            {"forestPack", forestPack},
            {"forestLite", forestLite},
            {"tyFlow", tyFlow},
            {"railClone", railClone},
            {"phoenixFD", phoenixFD},
        };
        try {
            result["materialClasses"] =
                MaxScriptClassNames(MATERIAL_CLASS_ID).size();
            result["geometryClasses"] =
                MaxScriptClassNames(GEOMOBJECT_CLASS_ID).size();
            result["modifierClasses"] =
                MaxScriptClassNames(OSM_CLASS_ID).size();
        } catch (...) {
            throw std::runtime_error(
                "Capabilities failed counting MAXScript class registries");
        }

        return result.dump();
    });
}
