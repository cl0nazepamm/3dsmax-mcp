#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/bridge_gup.h"

#include <Materials/MtlLib.h>
#include <ShlObj.h>

#include <cctype>
#include <ctime>
#include <filesystem>
#include <iomanip>
#include <set>
#include <sstream>
#include <system_error>

#pragma comment(lib, "shell32.lib")

using json = nlohmann::json;
using namespace HandlerHelpers;

namespace {

constexpr int kCompactMaterialEditorSlotCount = 24;

std::string LowerTrimmedAscii(std::string value) {
    const auto notSpace = [](unsigned char ch) {
        return std::isspace(ch) == 0;
    };
    value.erase(
        value.begin(),
        std::find_if(value.begin(), value.end(), notSpace));
    value.erase(
        std::find_if(value.rbegin(), value.rend(), notSpace).base(),
        value.end());
    std::transform(
        value.begin(),
        value.end(),
        value.begin(),
        [](unsigned char ch) {
            return static_cast<char>(std::tolower(ch));
        });
    return value;
}

std::string NormalizeMaterialLibrarySource(const std::string& value) {
    const std::string normalized = LowerTrimmedAscii(
        value.empty() ? "all" : value);
    if (normalized == "currentmateriallibrary" ||
        normalized == "current_material_library" ||
        normalized == "library" ||
        normalized == "temporary" ||
        normalized == "temp" ||
        normalized == "scratch" ||
        normalized == "scratchpad") {
        return "current";
    }
    if (normalized == "meditmaterials" ||
        normalized == "medit_materials" ||
        normalized == "material_editor" ||
        normalized == "material-editor" ||
        normalized == "editor" ||
        normalized == "slots") {
        return "medit";
    }
    if (normalized == "both") {
        return "all";
    }
    return normalized;
}

std::filesystem::path ExpandUserPath(const std::string& value) {
    std::filesystem::path path(Utf8ToWide(value));
    const std::wstring native = path.native();
    if (native != L"~" &&
        native.rfind(L"~\\", 0) != 0 &&
        native.rfind(L"~/", 0) != 0) {
        return path;
    }

    wchar_t profile[MAX_PATH]{};
    const DWORD length = GetEnvironmentVariableW(
        L"USERPROFILE",
        profile,
        MAX_PATH);
    if (length == 0 || length >= MAX_PATH) {
        return path;
    }
    if (native == L"~") {
        return std::filesystem::path(profile);
    }
    return std::filesystem::path(profile) / native.substr(2);
}

std::filesystem::path DefaultMaterialBackupDirectory() {
    wchar_t documents[MAX_PATH]{};
    if (SUCCEEDED(SHGetFolderPathW(
            nullptr,
            CSIDL_PERSONAL,
            nullptr,
            SHGFP_TYPE_CURRENT,
            documents))) {
        return std::filesystem::path(documents) /
            L"3dsmax-mcp-material-backups";
    }

    wchar_t profile[MAX_PATH]{};
    const DWORD length = GetEnvironmentVariableW(
        L"USERPROFILE",
        profile,
        MAX_PATH);
    if (length > 0 && length < MAX_PATH) {
        return std::filesystem::path(profile) /
            L"Documents" /
            L"3dsmax-mcp-material-backups";
    }
    throw std::runtime_error(
        "Failed to resolve the default material backup directory");
}

std::string BackupTimestamp() {
    const std::time_t now = std::time(nullptr);
    std::tm local{};
    localtime_s(&local, &now);
    std::ostringstream stream;
    stream << std::put_time(&local, "%Y%m%d_%H%M%S");
    return stream.str();
}

std::string SanitizeBackupPrefix(const std::string& value) {
    const auto notSpace = [](unsigned char ch) {
        return std::isspace(ch) == 0;
    };
    auto begin = std::find_if(value.begin(), value.end(), notSpace);
    auto end = std::find_if(value.rbegin(), value.rend(), notSpace).base();
    if (begin >= end) {
        return {};
    }

    std::string result;
    bool replacing = false;
    for (auto it = begin; it != end; ++it) {
        const unsigned char ch = static_cast<unsigned char>(*it);
        const bool allowed =
            std::isalnum(ch) != 0 ||
            ch == '_' ||
            ch == '.' ||
            ch == '-';
        if (allowed) {
            result.push_back(static_cast<char>(ch));
            replacing = false;
        } else if (!replacing) {
            result.push_back('_');
            replacing = true;
        }
    }
    return result;
}

std::string PathForResult(const std::filesystem::path& path) {
    std::string result = WideToUtf8(path.c_str());
    std::replace(result.begin(), result.end(), '\\', '/');
    return result;
}

struct BackupPaths {
    std::string current;
    std::string medit;
    std::string combined;
};

BackupPaths ResolveBackupPaths(
    const json& payload,
    const std::string& source) {
    BackupPaths paths{
        payload.value("current_path", ""),
        payload.value("medit_path", ""),
        payload.value("combined_path", ""),
    };

    const std::string filePath = payload.value("file_path", "");
    if (!filePath.empty()) {
        if (source == "all") {
            throw std::runtime_error(
                "file_path can only be used with a single source");
        }
        const std::string exact = PathForResult(ExpandUserPath(filePath));
        if (source == "current") paths.current = exact;
        if (source == "medit") paths.medit = exact;
        if (source == "combined") paths.combined = exact;
    }

    const bool missingRequired =
        ((source == "all" || source == "current") && paths.current.empty()) ||
        ((source == "all" || source == "medit") && paths.medit.empty()) ||
        ((source == "all" || source == "combined") && paths.combined.empty());
    if (missingRequired) {
        const std::string requestedRoot = payload.value("backup_dir", "");
        const std::filesystem::path root = requestedRoot.empty()
            ? DefaultMaterialBackupDirectory()
            : ExpandUserPath(requestedRoot);
        const std::string prefix =
            SanitizeBackupPrefix(payload.value("prefix", ""));
        const std::string stamp = BackupTimestamp();
        if (paths.current.empty()) {
            paths.current = PathForResult(
                root /
                Utf8ToWide(prefix + "currentMaterialLibrary_" + stamp + ".mat"));
        }
        if (paths.medit.empty()) {
            paths.medit = PathForResult(
                root /
                Utf8ToWide(prefix + "meditMaterials_" + stamp + ".mat"));
        }
        if (paths.combined.empty()) {
            paths.combined = PathForResult(
                root /
                Utf8ToWide(
                    prefix + "combined_material_scratchpad_" +
                    stamp + ".mat"));
        }
    }
    return paths;
}

class RemapDirOwner {
public:
    RemapDirOwner() : remap_(NewRemapDir()) {
        if (!remap_) {
            throw std::runtime_error("Failed to create material clone remap directory");
        }
    }

    ~RemapDirOwner() {
        if (remap_) {
            remap_->DeleteThis();
        }
    }

    RemapDir& Get() const {
        return *remap_;
    }

private:
    RemapDir* remap_;
};

std::string MaterialSuperClassName(SClass_ID superClassId) {
    if (superClassId == MATERIAL_CLASS_ID) return "material";
    if (superClassId == TEXMAP_CLASS_ID) return "textureMap";
    return "unknown";
}

std::string MaterialLibraryFileName(Interface* ip) {
#if MAX_SDK_VERSION >= 2025
    return WideToUtf8(ip->GetMatLibFileName().data());
#else
    const MCHAR* file = ip->GetMatLibFileName();
    return file ? WideToUtf8(file) : std::string();
#endif
}

bool IsMtlBaseUsedInSceneRecursive(
    ReferenceTarget* target,
    std::set<ReferenceTarget*>& visited) {

    if (!target || !visited.insert(target).second) {
        return false;
    }

    DependentIterator dependents(target);
    ReferenceMaker* dependent = nullptr;
    while ((dependent = dependents.Next()) != nullptr) {
        const SClass_ID superClassId = dependent->SuperClassID();
        if (superClassId == BASENODE_CLASS_ID) {
            INode* node = static_cast<INode*>(dependent);
            if (node->TestAFlag(A_IS_DELETED)) {
                continue;
            }
            if (node->GetMtl() == target && !node->IsSceneXRefNode()) {
                return true;
            }
            continue;
        }

        // Match isMtlUsedInSceneMtl's default skipCustAttributes:true behavior.
        if (superClassId == REF_MAKER_CLASS_ID &&
            dependent->ClassID() == CUSTATTRIB_CONTAINER_CLASS_ID) {
            continue;
        }

        if (dependent->IsRefTarget() &&
            IsMtlBaseUsedInSceneRecursive(
                static_cast<ReferenceTarget*>(dependent), visited)) {
            return true;
        }
    }
    return false;
}

bool IsMtlBaseUsedInScene(MtlBase* material) {
    std::set<ReferenceTarget*> visited;
    return IsMtlBaseUsedInSceneRecursive(material, visited);
}

json MaterialEntry(MtlBase* material, int oneBasedIndex, const char* source) {
    if (!material) {
        return {
            {"index", oneBasedIndex},
            {"source", source},
            {"empty", true},
        };
    }

    int subMaterialCount = 0;
    int subTexmapCount = 0;
    try {
        if (material->SuperClassID() == MATERIAL_CLASS_ID) {
            subMaterialCount = static_cast<Mtl*>(material)->NumSubMtls();
        }
    } catch (...) {
        subMaterialCount = 0;
    }
    try {
        subTexmapCount = material->NumSubTexmaps();
    } catch (...) {
        subTexmapCount = 0;
    }

    const auto handle = static_cast<unsigned long long>(
        Animatable::GetHandleByAnim(material));

    return {
        {"index", oneBasedIndex},
        {"source", source},
        {"empty", false},
        {"name", WideToUtf8(material->GetName().data())},
        {"class", MaxScriptVisibleClassName(material)},
        {"superClass", MaterialSuperClassName(material->SuperClassID())},
        {"handle", std::to_string(handle) + "P"},
        {"usedInScene", IsMtlBaseUsedInScene(material)},
        {"subMaterialCount", subMaterialCount},
        {"subTexmapCount", subTexmapCount},
    };
}

json CurrentLibrarySummary(Interface* ip, bool includeEmptySlots) {
    MtlBaseLib& library = ip->GetMaterialLibrary();
    json materials = json::array();
    for (int i = 0; i < library.Count(); ++i) {
        MtlBase* material = library[i];
        if (material || includeEmptySlots) {
            materials.push_back(MaterialEntry(material, i + 1, "current"));
        }
    }

    const std::string file = MaterialLibraryFileName(ip);
    return {
        {"source", "current"},
        {"label", "currentMaterialLibrary"},
        {"file", file},
        {"isTemporary", file.empty()},
        {"count", materials.size()},
        {"materials", materials},
    };
}

json MeditLibrarySummary(Interface* ip, bool includeEmptySlots) {
    json materials = json::array();
    for (int i = 0; i < kCompactMaterialEditorSlotCount; ++i) {
        MtlBase* material = ip->GetMtlSlot(i);
        if (material || includeEmptySlots) {
            materials.push_back(MaterialEntry(material, i + 1, "medit"));
        }
    }

    return {
        {"source", "medit"},
        {"label", "meditMaterials"},
        {"slotCount", kCompactMaterialEditorSlotCount},
        {"count", materials.size()},
        {"materials", materials},
    };
}

json CombinedLibrarySummary(Interface* ip, bool includeEmptySlots) {
    json materials = json::array();

    MtlBaseLib& current = ip->GetMaterialLibrary();
    for (int i = 0; i < current.Count(); ++i) {
        MtlBase* material = current[i];
        if (material || includeEmptySlots) {
            materials.push_back(MaterialEntry(material, i + 1, "current"));
        }
    }

    for (int i = 0; i < kCompactMaterialEditorSlotCount; ++i) {
        MtlBase* material = ip->GetMtlSlot(i);
        if (material || includeEmptySlots) {
            materials.push_back(MaterialEntry(material, i + 1, "medit"));
        }
    }

    return {
        {"source", "combined"},
        {"label", "currentMaterialLibrary+meditMaterials"},
        {"count", materials.size()},
        {"materials", materials},
    };
}

void AddMaterialClone(
    MtlBaseLib& library,
    MtlBase* source) {
    if (!source) {
        return;
    }

    RemapDirOwner remap;
    MtlBase* clone = static_cast<MtlBase*>(remap.Get().CloneRef(source));
    if (!clone) {
        throw std::runtime_error(
            "Failed to clone material \"" +
            WideToUtf8(source->GetName().data()) + "\"");
    }
    remap.Get().Backpatch();
    library.Add(clone);
}

void EnsureParentDirectory(const std::string& path) {
    const std::filesystem::path nativePath(Utf8ToWide(path));
    const std::filesystem::path parent = nativePath.parent_path();
    if (parent.empty()) {
        return;
    }

    std::error_code error;
    std::filesystem::create_directories(parent, error);
    if (error) {
        throw std::runtime_error(
            "Failed to create backup directory: " + error.message());
    }
}

bool FileExists(const std::string& path) {
    std::error_code error;
    const bool exists = std::filesystem::exists(
        std::filesystem::path(Utf8ToWide(path)), error);
    return !error && exists;
}

json SaveLibrary(
    Interface* ip,
    const char* source,
    const std::string& path,
    MtlBaseLib* library,
    int count,
    const std::string& emptyMessage) {

    json result = {
        {"source", source},
        {"path", path},
        {"count", count},
        {"saved", false},
        {"skipped", false},
        {"error", ""},
    };

    if (count <= 0) {
        result["skipped"] = true;
        result["error"] = emptyMessage;
        return result;
    }

    try {
        EnsureParentDirectory(path);
        const std::wstring widePath = Utf8ToWide(path);
        const bool sdkSaved = ip->SaveMaterialLib(widePath.c_str(), library) != 0;
        result["saved"] = sdkSaved && FileExists(path);
    } catch (const std::exception& error) {
        result["error"] = error.what();
    } catch (...) {
        result["error"] = "Native SaveMaterialLib exception";
    }

    return result;
}

json SaveCurrentLibrary(
    Interface* ip,
    const std::string& path) {

    MtlBaseLib& current = ip->GetMaterialLibrary();
    return SaveLibrary(
        ip,
        "current",
        path,
        &current,
        current.Count(),
        "currentMaterialLibrary is empty");
}

json SaveBuiltLibrary(
    Interface* ip,
    const char* source,
    const std::string& path,
    bool includeCurrent,
    bool includeMedit) {

    TypedSingleRefMaker<MtlBaseLib> library(new MtlBaseLib);

    if (includeCurrent) {
        MtlBaseLib& current = ip->GetMaterialLibrary();
        for (int i = 0; i < current.Count(); ++i) {
            AddMaterialClone(*library, current[i]);
        }
    }

    if (includeMedit) {
        for (int i = 0; i < kCompactMaterialEditorSlotCount; ++i) {
            AddMaterialClone(*library, ip->GetMtlSlot(i));
        }
    }

    const int count = library->Count();
    return SaveLibrary(
        ip,
        source,
        path,
        library.Get(),
        count,
        std::string(source) + " material library is empty");
}

} // namespace

namespace NativeHandlers {

// ── native:get_material_library (Pure SDK) ──────────────────────
std::string GetMaterialLibrary(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        const json payload = json::parse(params, nullptr, false);
        if (payload.is_discarded() || !payload.is_object()) {
            throw std::runtime_error("Invalid JSON params");
        }

        const std::string source = NormalizeMaterialLibrarySource(
            payload.value("source", "all"));
        const bool includeEmptySlots =
            payload.value("include_empty_slots", false);
        if (source != "current" && source != "medit" &&
            source != "combined" && source != "all") {
            throw std::runtime_error(
                "source must be one of: current, medit, combined, all");
        }

        Interface* ip = GetCOREInterface();
        if (!ip) {
            throw std::runtime_error("3ds Max core interface is unavailable");
        }

        if (source == "current") {
            return CurrentLibrarySummary(ip, includeEmptySlots).dump();
        }
        if (source == "medit") {
            return MeditLibrarySummary(ip, includeEmptySlots).dump();
        }
        if (source == "combined") {
            return CombinedLibrarySummary(ip, includeEmptySlots).dump();
        }

        json result = {
            {"source", "all"},
            {"currentMaterialLibrary",
                CurrentLibrarySummary(ip, includeEmptySlots)},
            {"meditMaterials",
                MeditLibrarySummary(ip, includeEmptySlots)},
            {"warnings", json::array()},
        };

        MtlBaseLib& current = ip->GetMaterialLibrary();
        const std::string file = MaterialLibraryFileName(ip);
        if (current.Count() > 0 && file.empty()) {
            result["warnings"].push_back(
                "currentMaterialLibrary has no backing .mat file; "
                "use backup_material_library before restarting 3ds Max");
        }
        return result.dump();
    });
}

// ── native:backup_material_library (Pure SDK) ───────────────────
std::string BackupMaterialLibrary(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        const json payload = json::parse(params, nullptr, false);
        if (payload.is_discarded() || !payload.is_object()) {
            throw std::runtime_error("Invalid JSON params");
        }

        const std::string source = NormalizeMaterialLibrarySource(
            payload.value("source", "all"));
        if (source != "current" && source != "medit" &&
            source != "combined" && source != "all") {
            throw std::runtime_error(
                "source must be one of: current, medit, combined, all");
        }

        const BackupPaths paths = ResolveBackupPaths(payload, source);
        const std::string& currentPath = paths.current;
        const std::string& meditPath = paths.medit;
        const std::string& combinedPath = paths.combined;
        if ((source == "all" || source == "current") && currentPath.empty()) {
            throw std::runtime_error("current_path is required");
        }
        if ((source == "all" || source == "medit") && meditPath.empty()) {
            throw std::runtime_error("medit_path is required");
        }
        if ((source == "all" || source == "combined") &&
            combinedPath.empty()) {
            throw std::runtime_error("combined_path is required");
        }

        Interface* ip = GetCOREInterface();
        if (!ip) {
            throw std::runtime_error("3ds Max core interface is unavailable");
        }

        json saved = json::array();
        if (source == "all" || source == "current") {
            saved.push_back(SaveCurrentLibrary(ip, currentPath));
        }
        if (source == "all" || source == "medit") {
            saved.push_back(
                SaveBuiltLibrary(ip, "medit", meditPath, false, true));
        }
        if (source == "all" || source == "combined") {
            saved.push_back(
                SaveBuiltLibrary(ip, "combined", combinedPath, true, true));
        }

        json result = {
            {"source", source},
            {"saved", saved},
        };
        for (const auto& item : saved) {
            if (!item.value("saved", false) &&
                !item.value("skipped", false)) {
                result["status"] = "error";
                result["error"] =
                    "one or more material library backups failed";
                break;
            }
        }
        return result.dump();
    });
}

} // namespace NativeHandlers
