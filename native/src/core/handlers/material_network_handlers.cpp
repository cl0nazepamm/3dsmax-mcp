#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/core_bridge.h"

#include <iparamb2.h>
#include <pbbitmap.h>
#include <AssetEnumCallback.h>
#include <FileEnumConstants.h>
#include <AssetManagement/AssetUser.h>
#include <Materials/Mtl.h>
// BitmapInfo (TYPE_BITMAP paths) lives in bmm.lib, which the target does not list.
#pragma comment(lib, "bmm.lib")
#include <Materials/MtlLib.h>
#include <Materials/Texmap.h>

#include <algorithm>
#include <cctype>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <map>
#include <set>
#include <unordered_map>
#include <vector>

using json = nlohmann::json;
using namespace HandlerHelpers;

namespace {

struct MaterialResolve {
    Mtl* material = nullptr;
    std::string resolvedVia;
    std::string ownerName;
};

struct InspectContext {
    TimeValue time = 0;
    bool includeValues = true;
    bool verifyFiles = true;
    bool readAssetFiles = true;
    std::string scope = "wired";
    int maxDepth = 3;
    int maxNodes = 80;
    int nodesOmitted = 0;
    int edgesOmitted = 0;
    int maxEdges = 6400;
    json edges = json::array();
    std::set<std::string> edgeKeys, depthLimited;
    std::unordered_map<MtlBase*, int> expandedDepth;
    std::unordered_map<MtlBase*, std::string> ids;
    json nodes = json::array();
    json wiredSlots = json::array();
    std::set<std::string> wiredSlotKeys;
    json issues = json::array();
};

static std::string Lower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(),
        [](unsigned char c) { return (char)std::tolower(c); });
    return s;
}

static bool ContainsInsensitive(const std::string& text, const std::string& needle) {
    return Lower(text).find(Lower(needle)) != std::string::npos;
}

static bool SameName(const std::string& a, const std::string& b) {
    return Lower(a) == Lower(b);
}

static int BaseParamType(ParamType2 t) {
    return (int)t & ~TYPE_TAB;
}

static bool IsTabParam(ParamType2 t) {
    return (((int)t & TYPE_TAB) != 0);
}

static bool HasScriptName(const ParamDef& pd) {
    return pd.int_name && pd.int_name[0] != 0;
}

static std::string ParamName(const ParamDef& pd, ParamID pid) {
    return HasScriptName(pd) ? WideToUtf8(pd.int_name) : ("param_" + std::to_string(pid));
}

static std::string ClassName(MtlBase* base) {
    return base ? SanitizeScriptName(WideToUtf8(base->ClassName().data())) : "";
}

static std::string BaseName(MtlBase* base) {
    return base ? WideToUtf8(base->GetName().data()) : "";
}

static std::string KindName(MtlBase* base) {
    if (!base) return "unknown";
    if (base->SuperClassID() == MATERIAL_CLASS_ID) return "material";
    if (base->SuperClassID() == TEXMAP_CLASS_ID) return "texture";
    return "mtlbase";
}

static bool IsPathLike(const std::string& value) {
    if (value.empty()) return false;
    if (value.size() > 1024) return false;
    if (value.find('\n') != std::string::npos || value.find('\r') != std::string::npos) return false;
    std::string lower = Lower(value);
    if (lower.find("\\") != std::string::npos || lower.find("/") != std::string::npos) return true;
    static const char* exts[] = {
        ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".bmp",
        ".tga", ".hdr", ".tx", ".webp", ".dds"
    };
    for (const char* ext : exts) {
        if (lower.size() >= strlen(ext) &&
            lower.compare(lower.size() - strlen(ext), strlen(ext), ext) == 0) {
            return true;
        }
    }
    return false;
}

static bool IsTextureDependency(const std::string& param, const std::string& path) {
    std::string lowerParam = Lower(param);
    if (lowerParam == "oslpath") return false;
    if (lowerParam.find("shader") != std::string::npos) return false;

    std::string lowerPath = Lower(path);
    static const char* textureExts[] = {
        ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".bmp",
        ".tga", ".hdr", ".tx", ".webp", ".dds"
    };
    for (const char* ext : textureExts) {
        if (lowerPath.size() >= strlen(ext) &&
            lowerPath.compare(lowerPath.size() - strlen(ext), strlen(ext), ext) == 0) {
            return true;
        }
    }
    return false;
}

static void AddWiredSlot(InspectContext& ctx, const std::string& slotName, int inputType,
                         const std::string& role, const std::string& nodeId) {
    std::string key = nodeId + "|" + slotName + "|" + std::to_string(inputType);
    if (!ctx.wiredSlotKeys.insert(key).second) return;

    json wired;
    wired["slot"] = slotName;
    wired["inputType"] = inputType;
    wired["role"] = role;
    wired["nodeId"] = nodeId;
    ctx.wiredSlots.push_back(wired);
}

static std::string NormalizeBackslashes(std::string path) {
    std::replace(path.begin(), path.end(), '/', '\\');
    return path;
}

static std::filesystem::path FsPath(const std::string& path) {
#ifdef _WIN32
    // Texture paths reach us as UTF-8 (WideToUtf8 of the Max string). Building a
    // filesystem::path from a narrow string decodes it with the active code page,
    // not UTF-8, so any path with non-ASCII characters (Greek, accents, Cyrillic)
    // resolves to the wrong name and the texture is reported as missing even
    // though it is there. Convert back to wide explicitly instead.
    return std::filesystem::path(Utf8ToWide(path));
#elif (defined(_MSVC_LANG) && _MSVC_LANG >= 202002L) || __cplusplus >= 202002L
    return std::filesystem::path(path);
#else
    return std::filesystem::u8path(path);
#endif
}

static std::string FsPathUtf8(const std::filesystem::path& path) {
    const auto text = path.u8string();
    return std::string(reinterpret_cast<const char*>(text.data()), text.size());
}

static std::string FilenameOnly(const std::string& path) {
    return FsPathUtf8(FsPath(path).filename());
}

static std::string ParentFolder(const std::string& path) {
    return FsPathUtf8(FsPath(path).parent_path());
}

static int ExtractUdim(const std::string& path) {
    std::string file = FilenameOnly(path);
    for (size_t i = 0; i + 4 <= file.size(); ++i) {
        if (!std::isdigit((unsigned char)file[i]) ||
            !std::isdigit((unsigned char)file[i + 1]) ||
            !std::isdigit((unsigned char)file[i + 2]) ||
            !std::isdigit((unsigned char)file[i + 3])) {
            continue;
        }
        int value = std::stoi(file.substr(i, 4));
        if (value >= 1001 && value <= 1999) return value;
    }
    return 0;
}

static uintmax_t FileSizeOrZero(const std::string& path, bool* existsOut) {
    std::error_code ec;
    bool exists = std::filesystem::exists(FsPath(path), ec);
    if (existsOut) *existsOut = exists && !ec;
    if (!exists || ec) return 0;
    uintmax_t size = std::filesystem::file_size(FsPath(path), ec);
    return ec ? 0 : size;
}

static std::string ExtensionMismatchCode(const std::string& path) {
    std::ifstream f(FsPath(path), std::ios::binary);
    if (!f) return "";
    unsigned char bytes[8] = {};
    f.read((char*)bytes, sizeof(bytes));
    std::streamsize got = f.gcount();
    std::string lower = Lower(path);
    bool extPng = lower.size() >= 4 && lower.compare(lower.size() - 4, 4, ".png") == 0;
    bool extJpg = (lower.size() >= 4 && lower.compare(lower.size() - 4, 4, ".jpg") == 0) ||
                  (lower.size() >= 5 && lower.compare(lower.size() - 5, 5, ".jpeg") == 0);
    bool sigPng = got >= 8 && bytes[0] == 0x89 && bytes[1] == 'P' && bytes[2] == 'N' && bytes[3] == 'G';
    bool sigJpg = got >= 2 && bytes[0] == 0xff && bytes[1] == 0xd8;
    if ((extPng && sigJpg) || (extJpg && sigPng)) return "EXTENSION_MISMATCH";
    return "";
}

static void AddIssue(json& issues, const std::string& code, const std::string& nodeId,
                     const std::string& message) {
    json issue;
    issue["code"] = code;
    if (!nodeId.empty()) issue["nodeId"] = nodeId;
    issue["message"] = message;
    issues.push_back(issue);
}

static std::string NormalizeSlotToken(std::string slot) {
    slot = Lower(slot);
    std::replace(slot.begin(), slot.end(), ' ', '_');
    return slot;
}

static std::string InferRole(const std::string& edge) {
    std::string e = NormalizeSlotToken(edge);
    if (e == "basecolor" || e == "basecolor_tex" || e == "diffuse" || e == "albedo") return "base_color";
    if (e == "roughness" || e == "roughness_tex") return "roughness";
    if (e == "metallic" || e == "metallic_tex") return "metallic";
    if (e == "normal" || e == "normal_tex" || e == "bump" || e == "bump_tex") return "normal";
    if (e == "displacement" || e == "displacement_tex") return "displacement";
    if (e == "opacity" || e == "opacity_tex" || e == "alpha") return "opacity";
    if (e == "subsurfacecolor" || e == "subsurfacecolor_tex" || e == "subsurface") return "subsurface";
    if (e == "radius" || e == "radius_tex") return "subsurface_radius";
    if (e.find("rough") != std::string::npos) return "roughness";
    if (e.find("metal") != std::string::npos) return "metallic";
    if (e.find("normal") != std::string::npos || e.find("bump") != std::string::npos) return "normal";
    if (e.find("disp") != std::string::npos || e.find("height") != std::string::npos) return "displacement";
    if (e.find("opacity") != std::string::npos || e.find("alpha") != std::string::npos) return "opacity";
    if (e.find("radius") != std::string::npos) return "subsurface_radius";
    if (e.find("subsurface") != std::string::npos || e.find("sss") != std::string::npos) return "subsurface";
    if (e.find("diffuse") != std::string::npos || e.find("albedo") != std::string::npos) return "base_color";
    if (e.find("base") != std::string::npos) return "base_color";
    if (e.find("ao") != std::string::npos || e.find("ambient") != std::string::npos) return "ambient_occlusion";
    return "map";
}

static bool ShouldSkipMirrorChild(MtlBase* parent, MtlBase* child) {
    if (!parent || !child) return true;
    std::string pCls = Lower(ClassName(parent));
    std::string cCls = Lower(ClassName(child));
    if (pCls.find("image tiles") != std::string::npos || pCls.find("image_tiles") != std::string::npos) {
        if (cCls.find("multitile") != std::string::npos || cCls == "bitmap") return true;
    }
    if (cCls.find("multitile") != std::string::npos &&
        (ContainsInsensitive(ClassName(parent), "MultiTile") ||
         ContainsInsensitive(ClassName(parent), "Image tiles"))) {
        return true;
    }
    return false;
}

static bool SplitUdimFilename(const std::string& filename, std::string& prefix, int& udim, std::string& ext) {
    udim = ExtractUdim(filename);
    if (udim <= 0) return false;
    std::string token = "." + std::to_string(udim);
    size_t pos = Lower(filename).find(Lower(token));
    if (pos == std::string::npos) return false;
    prefix = filename.substr(0, pos);
    ext = filename.substr(pos + token.length());
    return true;
}

struct TextureFolderCatalog {
    std::unordered_map<std::string, std::string> byFilenameLower;
    std::map<std::string, std::map<int, std::string>> byPrefixUdim;
};

static TextureFolderCatalog BuildTextureFolderCatalog(const std::string& folder) {
    TextureFolderCatalog cat;
    if (folder.empty()) return cat;
    std::error_code ec;
    std::filesystem::path root = FsPath(folder);
    if (!std::filesystem::is_directory(root, ec) || ec) return cat;

    for (const auto& entry : std::filesystem::directory_iterator(root, ec)) {
        if (ec || !entry.is_regular_file()) continue;
        std::string path = NormalizeBackslashes(FsPathUtf8(entry.path()));
        std::string fname = FilenameOnly(path);
        cat.byFilenameLower[Lower(fname)] = path;

        std::string prefix, ext;
        int udim = 0;
        if (SplitUdimFilename(fname, prefix, udim, ext)) {
            cat.byPrefixUdim[Lower(prefix + ext)][udim] = path;
        }
    }
    return cat;
}

static std::string LookupCatalogPath(const TextureFolderCatalog* catalog, const std::string& filename) {
    if (!catalog || filename.empty()) return "";
    auto exact = catalog->byFilenameLower.find(Lower(filename));
    if (exact != catalog->byFilenameLower.end()) return exact->second;

    std::string prefix, ext;
    int udim = 0;
    if (!SplitUdimFilename(filename, prefix, udim, ext)) return "";
    auto group = catalog->byPrefixUdim.find(Lower(prefix + ext));
    if (group != catalog->byPrefixUdim.end()) {
        auto tile = group->second.find(udim);
        if (tile != group->second.end()) return tile->second;
    }

    static const char* altExts[] = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".exr"};
    for (const char* alt : altExts) {
        std::string altName = prefix + "." + std::to_string(udim) + alt;
        auto it = catalog->byFilenameLower.find(Lower(altName));
        if (it != catalog->byFilenameLower.end()) return it->second;
    }
    return "";
}

static bool IsBlockingIssue(const std::string& code) {
    return code == "EMPTY_TILE_LIST" || code == "FILE_MISSING" || code == "UDIM_GRID_MISMATCH" ||
           code == "STUB_TEXTURE" || code == "EXTENSION_MISMATCH";
}

static bool IsWarningIssue(const std::string& code) {
    return code == "CIRCULAR_REF" || code == "PATH_FORMAT" || code == "AUX_FILES_AMBIGUOUS";
}

static std::string DetectProfile(MtlBase* root) {
    std::string cls = ClassName(root);
    std::string lower = Lower(cls);
    if (lower.find("corona") != std::string::npos) return "corona";
    if (lower.find("octane") != std::string::npos ||
        lower.find("std_surface") != std::string::npos ||
        lower.find("std surface") != std::string::npos ||
        lower.find("open_pbr_surf") != std::string::npos) return "octane_standard";
    if (lower.find("openpbr") != std::string::npos || lower.find("open pbr") != std::string::npos) return "openpbr";
    if (lower.find("physical") != std::string::npos) return "physical";
    if (lower.find("ai_standard") != std::string::npos || lower.find("arnold") != std::string::npos) return "arnold";
    return "generic";
}

static std::string ReadScalarValue(IParamBlock2* pb, ParamID pid, ParamType2 type, TimeValue t, int tabIndex = 0) {
    int baseType = BaseParamType(type);
    try {
        switch (baseType) {
        case TYPE_FLOAT:
        case TYPE_ANGLE:
        case TYPE_PCNT_FRAC:
        case TYPE_WORLD:
        case TYPE_COLOR_CHANNEL:
            return std::to_string(pb->GetFloat(pid, t, tabIndex));
        case TYPE_INT:
        case TYPE_BOOL:
        case TYPE_TIMEVALUE:
        case TYPE_RADIOBTN_INDEX:
        case TYPE_INDEX:
            return std::to_string(pb->GetInt(pid, t, tabIndex));
        case TYPE_POINT2: {
            Point2 pt = pb->GetPoint2(pid, t, tabIndex);
            return "[" + std::to_string(pt.x) + "," + std::to_string(pt.y) + "]";
        }
        case TYPE_POINT3:
        case TYPE_RGBA: {
            Point3 pt = pb->GetPoint3(pid, t, tabIndex);
            return "[" + std::to_string(pt.x) + "," + std::to_string(pt.y) + "," + std::to_string(pt.z) + "]";
        }
        case TYPE_POINT4:
        case TYPE_FRGBA: {
            Point4 pt = pb->GetPoint4(pid, t, tabIndex);
            return "[" + std::to_string(pt.x) + "," + std::to_string(pt.y) + "," +
                   std::to_string(pt.z) + "," + std::to_string(pt.w) + "]";
        }
        case TYPE_STRING:
        case TYPE_FILENAME: {
            const MCHAR* s = pb->GetStr(pid, t, tabIndex);
            return s ? WideToUtf8(s) : "";
        }
        default:
            return "";
        }
    } catch (...) {
        return "";
    }
}

static void AppendFileMeta(json& files, const std::string& path, const std::string& param,
                           const std::string& nodeId, bool verifyFiles, json& issues) {
    json f;
    f["path"] = path;
    f["param"] = param;
    int udim = ExtractUdim(path);
    if (udim > 0) f["udim"] = udim;

    if (verifyFiles) {
        bool exists = false;
        uintmax_t bytes = FileSizeOrZero(path, &exists);
        f["exists"] = exists;
        f["bytes"] = bytes;
        if (!exists) {
            AddIssue(issues, "FILE_MISSING", nodeId, "Texture file is missing: " + path);
        } else {
            if (bytes > 0 && bytes < 4096) {
                AddIssue(issues, "STUB_TEXTURE", nodeId, "Texture file is suspiciously small: " + path);
            }
            std::string mismatch = ExtensionMismatchCode(path);
            if (!mismatch.empty()) {
                AddIssue(issues, mismatch, nodeId, "Texture magic bytes do not match extension: " + path);
            }
        }
    }
    files.push_back(f);
}

using MaxSDK::AssetManagement::AssetUser;

// An asset-backed file source; the search-path lookup is deferred until it is needed.
struct AssetCandidate {
    AssetUser asset;
    std::string raw, param;
};

static std::string AssetRawPath(const AssetUser& asset) {
    try {
        const MSTR& raw = asset.GetFileName();
        return raw.isNull() ? std::string() : WideToUtf8(raw.data());
    } catch (...) {
        return "";
    }
}

// A search-path lookup probes every map path for a missing file, so each raw path is
// resolved once per request (main thread only), not once per material sharing it.
static std::unordered_map<std::string, std::string>* g_resolvedPaths = nullptr;

struct ResolvedPathCacheScope {
    std::unordered_map<std::string, std::string> cache;
    const bool owner = g_resolvedPaths == nullptr;
    ResolvedPathCacheScope() { if (owner) g_resolvedPaths = &cache; }
    ~ResolvedPathCacheScope() { if (owner) g_resolvedPaths = nullptr; }
};

static std::string AssetResolvedPath(const AssetUser& asset, const std::string& raw) {
    std::string key;
    try { key = std::to_string((int)asset.GetType()) + "|" + Lower(NormalizeBackslashes(raw)); } catch (...) { return ""; }
    if (g_resolvedPaths) {
        auto it = g_resolvedPaths->find(key);
        if (it != g_resolvedPaths->end()) return it->second;
    }
    std::string resolved;
    bool exists = false;
    if (FsPath(raw).is_absolute() && (FileSizeOrZero(raw, &exists), exists)) {
        resolved = raw;  // Already a readable absolute path; no search needed.
    } else {
        try {
            MSTR full;
            if (asset.GetFullFilePath(full) && !full.isNull()) resolved = WideToUtf8(full.data());
        } catch (...) {}
    }
    if (g_resolvedPaths) (*g_resolvedPaths)[key] = resolved;
    return resolved;
}

static bool HasFilePath(const json& files, const std::string& path) {
    const std::string key = Lower(NormalizeBackslashes(path));
    for (const auto& f : files)
        if (Lower(NormalizeBackslashes(f.value("path", ""))) == key) return true;
    return false;
}

// Asset-backed sources are reported only for nodes without a plain string/filename
// path, so a Bitmap's "bitmap" asset never duplicates its "fileName" string. Relative
// paths (common in Revit/ATF imports) resolve through the Max search paths.
static void AppendAssetFile(json& files, const AssetCandidate& c, const std::string& nodeId, InspectContext& ctx) {
    if (c.raw.empty() || HasFilePath(files, c.raw)) return;
    const std::string resolved = AssetResolvedPath(c.asset, c.raw);
    if (!resolved.empty() && HasFilePath(files, resolved)) return;
    const std::string& path = resolved.empty() ? c.raw : resolved;
    if (IsPathLike(path)) AppendFileMeta(files, path, c.param, nodeId, ctx.verifyFiles, ctx.issues);
}

class BitmapAssetCollector : public AssetEnumCallback {
public:
    struct Found { AssetCandidate candidate; int level; };
    std::vector<Found> found;
    int level = 0;
    void RecordAsset(const AssetUser& asset) override {
        if (found.size() >= 64) return;
        try {
            if (asset.GetType() != MaxSDK::AssetManagement::kBitmapAsset) return;
        } catch (...) {
            return;
        }
        std::string raw = AssetRawPath(asset);
        if (!raw.empty()) found.push_back({{asset, std::move(raw), "auxFiles"}, level});
    }
};

static std::vector<std::pair<MtlBase*, std::string>> PB2Children(MtlBase* base, TimeValue t);

// Fallback for leaf maps that keep their image outside readable paramblock values
// (e.g. Autodesk Bitmap). Each object enumerates only its own files; we descend at
// most two levels into helper references, never into materials, nodes or graph maps,
// so a node never reports a child's files and the cost stays bounded. A leaf's own
// texmap references are hidden helpers the graph never visits, so they are read.
// Only the shallowest level that has files counts; several distinct files there are
// ambiguous (likely a shared helper) and are reported as a warning, not attributed.
// Returns whether any bitmap asset was found.
static bool CollectOwnAuxFiles(MtlBase* base, const std::string& nodeId, InspectContext& ctx, json& files) {
    if (!base || base->SuperClassID() != TEXMAP_CLASS_ID || !files.empty()) return false;
    for (int i = 0; i < base->NumSubTexmaps(); ++i)
        if (base->GetSubTexmap(i)) return false;
    if (!PB2Children(base, ctx.time).empty()) return false;
    DWORD flags = FILE_ENUM_ALL | FILE_ENUM_DONT_RECURSE;
#ifdef FILE_ENUM_SKIP_SUB_FILES
    flags |= FILE_ENUM_SKIP_SUB_FILES;
#endif
    BitmapAssetCollector collector;
    std::vector<std::pair<ReferenceMaker*, int>> pending{{base, 0}};
    std::set<ReferenceMaker*> visited;
    while (!pending.empty() && visited.size() < 32) {
        const auto [maker, level] = pending.back();
        pending.pop_back();
        if (!maker || !visited.insert(maker).second) continue;
        // EnumAuxFiles marks A_WORK1; leave the scratch flag as we found it.
        const bool hadWork = maker->TestAFlag(A_WORK1);
        collector.level = level;
        try { maker->EnumAuxFiles(collector, flags); } catch (...) {}
        if (!hadWork) maker->ClearAFlag(A_WORK1);
        if (level >= 2) continue;
        const int numRefs = maker->NumRefs();
        for (int r = 0; r < numRefs && r < 64; ++r) {
            ReferenceTarget* ref = maker->GetReference(r);
            if (!ref) continue;
            const SClass_ID sc = ref->SuperClassID();
            if (sc == MATERIAL_CLASS_ID || sc == BASENODE_CLASS_ID) continue;
            if (sc == TEXMAP_CLASS_ID && level > 0) continue;
            pending.push_back({ref, level + 1});
        }
    }
    if (collector.found.empty()) return false;
    int best = 3;
    for (const auto& f : collector.found) best = std::min(best, f.level);
    std::vector<const AssetCandidate*> chosen;
    std::set<std::string> distinct;
    for (const auto& f : collector.found) {
        if (f.level != best) continue;
        if (distinct.insert(Lower(NormalizeBackslashes(f.candidate.raw))).second) chosen.push_back(&f.candidate);
    }
    if (chosen.size() > 1) {
        AddIssue(ctx.issues, "AUX_FILES_AMBIGUOUS", nodeId, std::to_string(chosen.size()) +
                 " image files were found through helper references; none is attributed to this map.");
        return true;
    }
    AppendAssetFile(files, *chosen.front(), nodeId, ctx);
    return true;
}

// Returns true when the node has file parameters that are all empty (no file
// assigned), as opposed to a source whose path could not be read.
static bool CollectPB2Values(MtlBase* base, const std::string& nodeId, InspectContext& ctx,
                             json& values, json& files) {
    // Asset-backed values (TYPE_BITMAP, TYPE_FILENAME without a readable string).
    std::vector<AssetCandidate> assets;
    bool fileParams = false, fileValues = false;
    auto fileAsset = [&](IParamBlock2* pb, ParamID pid, int i, const std::string& name) {
        if (!ctx.readAssetFiles) return;
        try {
            AssetUser asset = pb->GetAssetUser(pid, ctx.time, i);
            std::string raw = AssetRawPath(asset);
            if (raw.empty()) return;
            fileValues = true;
            // Images only: an IES, shader or proxy name must not become a texture source.
            if (asset.GetType() == MaxSDK::AssetManagement::kBitmapAsset || IsPathLike(raw))
                assets.push_back({asset, std::move(raw), name});
        } catch (...) {}
    };
    int numPB = base->NumParamBlocks();
    for (int pbIdx = 0; pbIdx < numPB; ++pbIdx) {
        IParamBlock2* pb = base->GetParamBlock(pbIdx);
        if (!pb) continue;
        ParamBlockDesc2* desc = pb->GetDesc();
        if (!desc) continue;

        for (int pi = 0; pi < desc->count; ++pi) {
            ParamID pid = desc->IndextoID(pi);
            const ParamDef& pd = desc->GetParamDef(pid);
            std::string name = ParamName(pd, pid);
            int baseType = BaseParamType(pd.type);
            int count = IsTabParam(pd.type) ? std::max(0, pb->Count(pid)) : 1;

            // Unnamed params are internal and hidden from MAXScript; never treat them
            // as file sources. (Cosmos VRayBitmaps keep a package-relative copy of the
            // texture path in one, which surfaced as a bogus FILE_MISSING row.)
            if (!HasScriptName(pd) &&
                (baseType == TYPE_BITMAP || baseType == TYPE_STRING || baseType == TYPE_FILENAME)) {
                if (ctx.includeValues && !IsTabParam(pd.type) && baseType != TYPE_BITMAP)
                    values[name] = ReadScalarValue(pb, pid, pd.type, ctx.time);
                continue;
            }

            if (baseType == TYPE_BITMAP) {
                fileParams = true;
                for (int i = 0; ctx.readAssetFiles && i < count; ++i) {
                    PBBitmap* bmp = nullptr;
                    try { bmp = pb->GetBitmap(pid, ctx.time, i); } catch (...) {}
                    if (!bmp) continue;
                    const AssetUser& asset = bmp->bi.GetAsset();
                    std::string raw = AssetRawPath(asset);
                    if (raw.empty() && bmp->bi.Name()) raw = WideToUtf8(bmp->bi.Name());
                    if (raw.empty()) continue;
                    fileValues = true;
                    assets.push_back({asset, std::move(raw), name});
                }
                continue;
            }

            if (baseType == TYPE_STRING || baseType == TYPE_FILENAME) {
                fileParams = fileParams || baseType == TYPE_FILENAME;
                if (IsTabParam(pd.type)) {
                    json arr = json::array();
                    for (int i = 0; i < count; ++i) {
                        std::string val = ReadScalarValue(pb, pid, pd.type, ctx.time, i);
                        arr.push_back(val);
                        fileValues = fileValues || (baseType == TYPE_FILENAME && !val.empty());
                        if (IsPathLike(val)) {
                            AppendFileMeta(files, val, name, nodeId, ctx.verifyFiles, ctx.issues);
                        } else if (baseType == TYPE_FILENAME) {
                            fileAsset(pb, pid, i, name);
                        }
                    }
                    if (ctx.includeValues) values[name] = arr;
                    if (count == 0 && ContainsInsensitive(ClassName(base), "Image_tiles")) {
                        AddIssue(ctx.issues, "EMPTY_TILE_LIST", nodeId, name + " is empty.");
                    }
                    continue;
                }

                std::string val = ReadScalarValue(pb, pid, pd.type, ctx.time);
                if (ctx.includeValues) values[name] = val;
                fileValues = fileValues || (baseType == TYPE_FILENAME && !val.empty());
                if (IsPathLike(val)) {
                    AppendFileMeta(files, val, name, nodeId, ctx.verifyFiles, ctx.issues);
                } else if (baseType == TYPE_FILENAME) {
                    fileAsset(pb, pid, 0, name);
                }
                continue;
            }

            if (!ctx.includeValues) continue;
            if (baseType == TYPE_TEXMAP || baseType == TYPE_MTL || baseType == TYPE_REFTARG ||
                baseType == TYPE_INODE || baseType == TYPE_PBLOCK2) {
                continue;
            }

            if (IsTabParam(pd.type)) {
                json arr = json::array();
                for (int i = 0; i < count; ++i) {
                    std::string val = ReadScalarValue(pb, pid, pd.type, ctx.time, i);
                    if (!val.empty()) arr.push_back(val);
                }
                if (!arr.empty()) values[name] = arr;
            } else {
                std::string val = ReadScalarValue(pb, pid, pd.type, ctx.time);
                if (!val.empty()) values[name] = val;
            }
        }
    }
    // Asset-backed sources are a fallback for nodes without a plain path: a VRayBitmap
    // reports HDRIMapName, and its internal bitmap asset (a Cosmos package-relative
    // path that never resolves) must not add a second, bogus FILE_MISSING row.
    if (files.empty())
        for (const auto& c : assets) AppendAssetFile(files, c, nodeId, ctx);
    if (!ctx.readAssetFiles) return false;
    const bool auxFound = CollectOwnAuxFiles(base, nodeId, ctx, files);
    return fileParams && !fileValues && !auxFound && files.empty();
}

static std::vector<std::pair<MtlBase*, std::string>> PB2Children(MtlBase* base, TimeValue t) {
    std::vector<std::pair<MtlBase*, std::string>> out;
    int numPB = base->NumParamBlocks();
    for (int pbIdx = 0; pbIdx < numPB; ++pbIdx) {
        IParamBlock2* pb = base->GetParamBlock(pbIdx);
        if (!pb) continue;
        ParamBlockDesc2* desc = pb->GetDesc();
        if (!desc) continue;

        for (int pi = 0; pi < desc->count; ++pi) {
            ParamID pid = desc->IndextoID(pi);
            const ParamDef& pd = desc->GetParamDef(pid);
            int baseType = BaseParamType(pd.type);
            if (baseType != TYPE_TEXMAP && baseType != TYPE_MTL) continue;

            std::string name = ParamName(pd, pid);
            int count = IsTabParam(pd.type) ? std::max(0, pb->Count(pid)) : 1;
            for (int i = 0; i < count; ++i) {
                MtlBase* child = nullptr;
                if (baseType == TYPE_TEXMAP) child = pb->GetTexmap(pid, t, i);
                if (baseType == TYPE_MTL) child = pb->GetMtl(pid, t, i);
                if (child) {
                    out.push_back({child, IsTabParam(pd.type) ? name + "[" + std::to_string(i) + "]" : name});
                }
            }
        }
    }
    return out;
}

// A connection is identified by its owning slot, never by its target map.
struct MaterialLink {
    MtlBase* target;
    std::string key, slot, kind;
    int inputType;
    std::vector<std::string> aliases;
};

static std::vector<MaterialLink> MaterialLinks(MtlBase* base, TimeValue t) {
    std::vector<MaterialLink> links;
    auto add = [&](MtlBase* child, const std::string& key, const std::string& slot,
                   const std::string& kind, int inputType) {
        if (!child || ShouldSkipMirrorChild(base, child)) return;
        for (auto& link : links) if (link.key == key && link.target == child) {
            if (slot != link.slot) {
                const auto alias = inputType == 2 ? link.slot : slot;
                if (std::find(link.aliases.begin(), link.aliases.end(), alias) == link.aliases.end()) link.aliases.push_back(alias);
                if (inputType == 2) { link.slot = slot; link.inputType = 2; }
            }
            return;
        }
        links.push_back({child, key, slot, kind, inputType, {}});
    };
    if (base->SuperClassID() == MATERIAL_CLASS_ID) {
        auto* mtl = static_cast<Mtl*>(base);
        for (int i = 0; i < mtl->NumSubMtls(); ++i) {
            MSTR label = mtl->GetSubMtlSlotName(i, false);
            add(mtl->GetSubMtl(i), "material:" + std::to_string(i),
                label.data() && *label.data() ? WideToUtf8(label.data()) : "subMaterial[" + std::to_string(i + 1) + "]",
                "material", 0);
        }
    }
    for (int i = 0; i < base->NumSubTexmaps(); ++i) {
        MSTR label = base->GetSubTexmapSlotName(i, false);
        add(base->GetSubTexmap(i), "map:" + std::to_string(i),
            label.data() && *label.data() ? WideToUtf8(label.data()) : "map[" + std::to_string(i) + "]",
            "map", base->MapSlotType(i));
    }
    for (int bi = 0; bi < base->NumParamBlocks(); ++bi) {
        auto* pb = base->GetParamBlock(bi);
        auto* desc = pb ? pb->GetDesc() : nullptr;
        if (!desc) continue;
        for (int pi = 0; pi < desc->count; ++pi) {
            const auto pid = desc->IndextoID(pi);
            const auto& pd = desc->GetParamDef(pid);
            const auto type = BaseParamType(pd.type);
            if (type != TYPE_TEXMAP && type != TYPE_MTL) continue;
            const bool tab = IsTabParam(pd.type);
            const int count = tab ? std::max(0, pb->Count(pid)) : 1;
            for (int i = 0; i < count; ++i) {
                MtlBase* child = type == TYPE_TEXMAP ? static_cast<MtlBase*>(pb->GetTexmap(pid, t, i))
                                                    : static_cast<MtlBase*>(pb->GetMtl(pid, t, i));
                if (!child) continue;
                const std::string kind = type == TYPE_TEXMAP ? "map" : "material";
                std::string key = "parameter:" + std::to_string(pb->ID()) + ":" + std::to_string(pid) + ":" + std::to_string(i);
                int slot = type == TYPE_TEXMAP ? desc->GetSubTexNo(pid) : desc->GetSubMtlNo(pid);
                if (slot >= 0) {
                    slot += tab ? i : 0;
                    if (type == TYPE_TEXMAP && slot < base->NumSubTexmaps() && base->GetSubTexmap(slot) == child)
                        key = "map:" + std::to_string(slot);
                    if (type == TYPE_MTL && base->SuperClassID() == MATERIAL_CLASS_ID) {
                        auto* mtl = static_cast<Mtl*>(base);
                        if (slot < mtl->NumSubMtls() && mtl->GetSubMtl(slot) == child) key = "material:" + std::to_string(slot);
                    }
                }
                add(child, key, ParamName(pd, pid) + (tab ? "[" + std::to_string(i) + "]" : ""), kind, 2);
            }
        }
    }
    return links;
}

static void TraverseMaterialGraph(MtlBase* base, const std::string& parentId,
                                  const std::string& edge, int depth, InspectContext& ctx,
                                  std::set<MtlBase*>& stack) {
    if (!base) return;
    auto found = ctx.ids.find(base);
    if (found != ctx.ids.end() && stack.count(base)) {
        AddIssue(ctx.issues, "CIRCULAR_REF", found->second, "Circular material/map reference at edge " + edge);
        return;
    }
    if (found != ctx.ids.end() && ctx.expandedDepth[base] >= depth) return;
    if (found == ctx.ids.end()) {
        if ((int)ctx.nodes.size() >= ctx.maxNodes) { ctx.nodesOmitted++; return; }
        std::string id = "n" + std::to_string(ctx.ids.size());
        ctx.ids[base] = id;
        json values = json::object(), files = json::array();
        const bool fileUnset = CollectPB2Values(base, id, ctx, values, files);
        json node = {{"id", id}, {"kind", KindName(base)}, {"class", ClassName(base)},
                     {"name", BaseName(base)}, {"handle", std::to_string((uint64_t)Animatable::GetHandleByAnim(base))}};
        // "class" is the localized UI label; consumers matching class names need the script token.
        const std::string internalClass = SanitizeScriptName(WideToUtf8(base->ClassName(false).data()));
        if (internalClass != node["class"].get<std::string>()) node["classInternal"] = internalClass;
        if (fileUnset) node["fileUnset"] = true;
        if (!parentId.empty()) node["parentId"] = parentId;
        if (!edge.empty()) node["edge"] = edge;
        if (!files.empty()) node["files"] = files;
        if (ctx.includeValues && !values.empty()) node["values"] = values;
        if (ContainsInsensitive(ClassName(base), "Image_tiles")) {
            node["udim"] = {{"tileCount", files.size()}, {"valid", !files.empty()}};
            if (files.empty()) AddIssue(ctx.issues, "EMPTY_TILE_LIST", id, "Image_tiles has no usable file paths.");
            for (const auto& f : files) if (f.value("path", "").find('/') != std::string::npos)
                AddIssue(ctx.issues, "PATH_FORMAT", id, "Image_tiles path should use Windows backslashes: " + f.value("path", ""));
        }
        ctx.nodes.push_back(node);
    }
    const std::string id = ctx.ids.at(base);
    ctx.expandedDepth[base] = depth;
    auto links = MaterialLinks(base, ctx.time);
    if (depth <= 0) {
        if (!links.empty()) ctx.depthLimited.insert(id);
        return;
    }
    ctx.depthLimited.erase(id);
    stack.insert(base);
    for (const auto& link : links) {
        const auto key = id + "|" + link.key;
        if (!ctx.edgeKeys.count(key) && ctx.edges.size() >= (size_t)ctx.maxEdges) { ctx.edgesOmitted++; continue; }
        TraverseMaterialGraph(link.target, id, link.slot, depth - 1, ctx, stack);
        auto target = ctx.ids.find(link.target);
        if (target == ctx.ids.end()) continue;
        if (!ctx.edgeKeys.count(key) && ctx.edges.size() >= (size_t)ctx.maxEdges) { ctx.edgesOmitted++; continue; }
        if (ctx.edgeKeys.insert(key).second) {
            ctx.edges.push_back({{"parentId", id}, {"nodeId", target->second}, {"slot", link.slot},
                {"slotKey", link.key}, {"kind", link.kind}, {"inputType", link.inputType},
                {"role", InferRole(link.slot)}, {"aliases", link.aliases}});
        }
        if (parentId.empty()) AddWiredSlot(ctx, link.slot, link.inputType, InferRole(link.slot), target->second);
    }
    stack.erase(base);
}


static Mtl* FindMaterialByNameRecursive(Mtl* mtl, const std::string& name, std::set<Mtl*>& seen) {
    if (!mtl || seen.count(mtl)) return nullptr;
    seen.insert(mtl);
    if (SameName(BaseName(mtl), name)) return mtl;
    for (int i = 0; i < mtl->NumSubMtls(); ++i) {
        if (Mtl* found = FindMaterialByNameRecursive(mtl->GetSubMtl(i), name, seen)) return found;
    }
    return nullptr;
}

static Mtl* FindInSceneMtlLib(const std::string& name) {
    Interface* ip = GetCOREInterface();
    MtlBaseLib* lib = ip ? ip->GetSceneMtls() : nullptr;
    if (!lib) return nullptr;

    for (int i = 0; i < lib->NumSubs(); ++i) {
        Animatable* anim = lib->SubAnim(i);
        if (!anim) continue;
        MtlBase* base = dynamic_cast<MtlBase*>(anim);
        if (!base || base->SuperClassID() != MATERIAL_CLASS_ID) continue;
        Mtl* mtl = static_cast<Mtl*>(base);
        if (SameName(BaseName(mtl), name)) return mtl;
        std::set<Mtl*> seen;
        if (Mtl* found = FindMaterialByNameRecursive(mtl, name, seen)) return found;
    }
    return nullptr;
}

static MaterialResolve ResolveMaterial(const std::string& name, int subMaterialIndex) {
    if (name.empty()) throw std::runtime_error("name is required");

    const std::vector<INode*> owners = CollectNodesByName(name);
    if (owners.size() > 1) {
        // Objects that share a name but carry one material still name that
        // material; with different materials the name cannot choose one.
        Mtl* shared = owners.front()->GetMtl();
        bool sameMaterial = true;
        for (INode* owner : owners) sameMaterial = sameMaterial && owner->GetMtl() == shared;
        if (!sameMaterial) {
            json candidates = json::array();
            for (INode* owner : owners) {
                json candidate = NodeIdentityJson(owner);
                Mtl* mtl = owner->GetMtl();
                candidate["material"] = mtl ? json(BaseName(mtl)) : json(nullptr);
                candidates.push_back(candidate);
            }
            throw std::runtime_error(StructuredErrorPayload(
                "AMBIGUOUS",
                "Ambiguous object name: " + name,
                {{"message", "Several objects with this name carry different materials. "
                             "Pass the material name instead, or give the objects unique names."},
                 {"candidates", candidates}}));
        }
    }

    if (INode* node = owners.empty() ? nullptr : owners.front()) {
        Mtl* mtl = node->GetMtl();
        if (!mtl) throw std::runtime_error("No material assigned to " + name);
        if (subMaterialIndex > 0) {
            int idx = subMaterialIndex - 1;
            if (idx >= mtl->NumSubMtls()) {
                throw std::runtime_error("Sub-material index " + std::to_string(subMaterialIndex) +
                    " out of range (has " + std::to_string(mtl->NumSubMtls()) + ")");
            }
            mtl = mtl->GetSubMtl(idx);
            if (!mtl) throw std::runtime_error("Sub-material index " + std::to_string(subMaterialIndex) + " is empty");
        }
        return {mtl, "object", WideToUtf8(node->GetName())};
    }

    Interface* ip = GetCOREInterface();
    std::vector<INode*> nodes;
    CollectNodes(ip->GetRootNode(), nodes);
    for (INode* n : nodes) {
        Mtl* mtl = n->GetMtl();
        if (!mtl) continue;
        std::set<Mtl*> seen;
        if (Mtl* found = FindMaterialByNameRecursive(mtl, name, seen)) {
            return {found, "material", WideToUtf8(n->GetName())};
        }
    }

    if (Mtl* libMtl = FindInSceneMtlLib(name)) {
        return {libMtl, "scene_library", ""};
    }

    throw std::runtime_error("Not found as object, assigned material, or scene library material: " + name);
}

static json FilterNodesForScope(const json& nodes, const std::string& scope) {
    if (scope != "files_only") return nodes;
    json filtered = json::array();
    for (const auto& node : nodes) {
        if (node.value("id", "") == "n0") {
            filtered.push_back(node);
            continue;
        }
        if (node.contains("files") && node["files"].type() == json::value_t::array && !node["files"].empty()) {
            filtered.push_back(node);
        }
    }
    return filtered;
}

static json SplitIssues(const json& issues) {
    json blocking = json::array();
    json warnings = json::array();
    for (const auto& issue : issues) {
        std::string code = issue.value("code", "");
        if (IsBlockingIssue(code)) blocking.push_back(issue);
        else if (IsWarningIssue(code)) warnings.push_back(issue);
        else blocking.push_back(issue);
    }
    return {{"blocking", blocking}, {"warnings", warnings}};
}

static json InspectMaterialGraph(Mtl* root, const std::string& requestedName,
                                 const std::string& resolvedVia, int subMaterialIndex,
                                 int depth, const std::string& scope,
                                 bool includeValues, bool verifyFiles, int maxNodes,
                                 bool readAssetFiles = true) {
    ResolvedPathCacheScope resolvedPaths;
    InspectContext ctx;
    ctx.time = GetCOREInterface()->GetTime();
    ctx.includeValues = includeValues;
    ctx.verifyFiles = verifyFiles;
    ctx.readAssetFiles = readAssetFiles;
    ctx.scope = scope.empty() ? "wired" : Lower(scope);
    ctx.maxDepth = std::min(16, std::max(0, depth));
    ctx.maxNodes = std::min(2000, std::max(1, maxNodes));
    ctx.maxEdges = std::min(64000, ctx.maxNodes * 32);

    std::set<MtlBase*> stack;
    TraverseMaterialGraph(root, "", "", ctx.maxDepth, ctx, stack);

    json result;
    result["ok"] = true;
    result["resolvedVia"] = resolvedVia;
    result["query"] = requestedName;
    result["root"] = {
        {"id", "n0"},
        {"name", BaseName(root)},
        {"class", ClassName(root)},
        {"subMaterialIndex", subMaterialIndex},
        {"rendererProfile", DetectProfile(root)}
    };
    result["graphVersion"] = 2;
    result["edges"] = ctx.edges;
    result["wiredSlots"] = ctx.wiredSlots;
    result["nodes"] = FilterNodesForScope(ctx.nodes, ctx.scope);
    json issueSplit = SplitIssues(ctx.issues);
    result["issues"] = issueSplit["blocking"];
    result["warnings"] = issueSplit["warnings"];
    result["truncated"] = {{"nodesOmitted", ctx.nodesOmitted}, {"edgesOmitted", ctx.edgesOmitted}, {"depthLimited", ctx.depthLimited}};
    result["complete"] = ctx.nodesOmitted == 0 && ctx.edgesOmitted == 0 && ctx.depthLimited.empty();

    std::string folderGuess;
    std::set<std::string> uniquePaths;
    std::set<std::string> uniqueTexturePaths;
    for (const auto& node : result["nodes"]) {
        if (!node.contains("files")) continue;
        for (const auto& f : node["files"]) {
            std::string path = f.value("path", "");
            if (path.empty() || !uniquePaths.insert(NormalizeBackslashes(path)).second) continue;
            if (IsTextureDependency(f.value("param", ""), path)) {
                uniqueTexturePaths.insert(NormalizeBackslashes(path));
                if (folderGuess.empty()) folderGuess = ParentFolder(path);
            }
        }
    }

    bool replicateReady = result["complete"].get<bool>();
    for (const auto& issue : result["issues"]) {
        if (IsBlockingIssue(issue.value("code", ""))) {
            replicateReady = false;
            break;
        }
    }

    json fileManifest = json::array();
    for (const auto& node : result["nodes"]) {
        if (!node.contains("files")) continue;
        for (const auto& f : node["files"]) {
            std::string path = f.value("path", "");
            if (path.empty()) continue;
            json entry = f;
            entry["nodeId"] = node.value("id", "");
            entry["nodeClass"] = node.value("class", "");
            fileManifest.push_back(entry);
        }
    }

    json hints;
    hints["replicateReady"] = replicateReady;
    if (!folderGuess.empty()) hints["textureFolderGuess"] = NormalizeBackslashes(folderGuess);
    if (DetectProfile(root) == "octane_standard") hints["preset"] = "cc_octane_skin";
    hints["uniqueFileCount"] = uniquePaths.size();
    hints["uniqueTextureFileCount"] = uniqueTexturePaths.size();
    result["hints"] = hints;
    result["fileManifest"] = fileManifest;
    return result;
}

static void GatherMaterials(Mtl* material, std::vector<Mtl*>& out, std::set<Mtl*>& seen) {
    if (!material || !seen.insert(material).second) return;
    out.push_back(material);
    for (int i = 0; i < material->NumSubMtls(); ++i) GatherMaterials(material->GetSubMtl(i), out, seen);
}

// A batch is read in one main-thread invocation, using pointers resolved in this
// scene. In particular, scene scans must not round-trip through material names.
static json InspectRoleGraphs(const json& p) {
    ResolvedPathCacheScope resolvedPaths;  // Shared by every material in the batch.
    const bool scan = p.value("scan_scene", false);
    const auto names = p.value("names", std::vector<std::string>{});
    const int limit = p.value("limit", 25), offset = p.value("offset", 0);
    const int depth = p.value("depth", 8), maxNodes = p.value("max_nodes", 400);
    if (depth < 1 || depth > 16 || maxNodes < 1 || maxNodes > 2000)
        throw std::runtime_error("depth must be 1..16 and max_nodes 1..2000.");
    if (limit < 1 || limit > 200 || offset < 0 || names.size() > 200 || scan == !names.empty())
        throw std::runtime_error("Supply names or scan_scene, limit 1..200, and a nonnegative offset.");
    std::vector<INode*> nodes;
    CollectNodes(GetCOREInterface()->GetRootNode(), nodes);
    std::vector<Mtl*> roots, materials;
    std::set<Mtl*> rootSet, seen;
    for (auto* node : nodes) if (auto* mtl = node->GetMtl()) {
        if (rootSet.insert(mtl).second) roots.push_back(mtl);
        if (!scan) GatherMaterials(mtl, materials, seen);
    }
    if (!scan) if (auto* lib = GetCOREInterface()->GetSceneMtls()) for (int i = 0; i < lib->NumSubs(); ++i) {
        auto* mtl = dynamic_cast<Mtl*>(lib->SubAnim(i));
        if (mtl) GatherMaterials(mtl, materials, seen);
    }
    std::sort(roots.begin(), roots.end(), [](Mtl* a, Mtl* b) {
        auto an = Lower(BaseName(a)), bn = Lower(BaseName(b));
        return an != bn ? an < bn : Animatable::GetHandleByAnim(a) < Animatable::GetHandleByAnim(b);
    });
    const size_t total = scan ? roots.size() : names.size();
    const size_t start = std::min(total, (size_t)offset);
    const size_t end = std::min(total, start + (size_t)limit);
    json graphs = json::array(), failed = json::array();
    for (size_t i = start; i < end; ++i) {
        const std::string query = scan ? BaseName(roots[i]) : names[i];
        try {
            Mtl* root = scan ? roots[i] : nullptr;
            std::string owner;
            if (!scan) {
                std::set<Mtl*> matches;
                int matchingNodes = 0;
                for (auto* node : nodes) if (SameName(WideToUtf8(node->GetName()), query)) {
                    matchingNodes++;
                    if (auto* mtl = node->GetMtl()) { matches.insert(mtl); owner = WideToUtf8(node->GetName()); }
                }
                for (auto* mtl : materials) if (SameName(BaseName(mtl), query)) matches.insert(mtl);
                if (matchingNodes > 1 || matches.size() > 1) {
                    failed.push_back({{"material", query}, {"code", "AMBIGUOUS_TARGET"}, {"retryable", false},
                        {"error", "Name is ambiguous; use a uniquely named object with the intended material."}});
                    continue;
                }
                if (matches.empty()) {
                    failed.push_back({{"material", query}, {"code", "NOT_FOUND"}, {"retryable", false},
                        {"error", "No material resolves from this object or material name."}});
                    continue;
                }
                root = *matches.begin();
            }
            auto graph = InspectMaterialGraph(root, query, scan ? "scene" : "unique_name", 0,
                depth, "wired", false, true, maxNodes);
            graph["root"]["handle"] = std::to_string((uint64_t)Animatable::GetHandleByAnim(root));
            graph["owner"] = owner;
            graphs.push_back(std::move(graph));
        } catch (const std::exception& e) {
            failed.push_back({{"material", query}, {"code", "INSPECTION_FAILED"}, {"retryable", false}, {"error", e.what()}});
        }
    }
    return {{"graphVersion", 2}, {"graphs", graphs}, {"failed", failed}, {"total", total},
            {"checked", end - start}, {"offset", start}, {"next_offset", end < total ? json(end) : json(nullptr)}};
}


static std::string RemappedPath(const std::string& oldPath,
                                const std::unordered_map<std::string, std::string>& pathMap,
                                const std::string& textureFolder,
                                const TextureFolderCatalog* catalog) {
    auto exact = pathMap.find(oldPath);
    if (exact != pathMap.end()) return NormalizeBackslashes(exact->second);
    auto normalized = pathMap.find(NormalizeBackslashes(oldPath));
    if (normalized != pathMap.end()) return NormalizeBackslashes(normalized->second);
    std::string filename = FilenameOnly(oldPath);
    auto byFile = pathMap.find(filename);
    if (byFile != pathMap.end()) return NormalizeBackslashes(byFile->second);

    if (catalog) {
        std::string catalogPath = LookupCatalogPath(catalog, filename);
        if (!catalogPath.empty()) return catalogPath;
    }

    if (!textureFolder.empty() && !filename.empty()) {
        std::filesystem::path p = FsPath(textureFolder) / FsPath(filename);
        std::error_code ec;
        if (std::filesystem::exists(p, ec) && !ec) return NormalizeBackslashes(FsPathUtf8(p));
        if (catalog) {
            std::string catalogPath = LookupCatalogPath(catalog, filename);
            if (!catalogPath.empty()) return catalogPath;
        }
        return NormalizeBackslashes(FsPathUtf8(p));
    }
    return oldPath;
}

static json DeduplicatePlannedFiles(const json& plannedFiles) {
    json out = json::array();
    std::set<std::string> seen;
    for (const auto& entry : plannedFiles) {
        std::string key = entry.value("oldPath", "") + "|" + entry.value("newPath", "") + "|" +
                          entry.value("param", "");
        if (!seen.insert(key).second) continue;
        out.push_back(entry);
    }
    return out;
}

static json BuildReplicationPlan(const json& request, Mtl* source, const std::string& sourceName,
                                 const std::vector<std::string>& targets);

static std::unordered_map<std::string, std::string> ParsePathMap(const json& p) {
    std::unordered_map<std::string, std::string> out;
    if (!p.contains("path_map") || p["path_map"].type() != json::value_t::object) return out;
    for (auto it = p["path_map"].begin(); it != p["path_map"].end(); ++it) {
        std::string key = it.key();
        std::string value = it.value().type() == json::value_t::string
            ? it.value().get<std::string>()
            : it.value().dump();
        out[key] = value;
        out[NormalizeBackslashes(key)] = value;
        out[FilenameOnly(key)] = value;
    }
    return out;
}

static bool ExtendImageTilesFromFolder(MtlBase* base, TimeValue t, const TextureFolderCatalog& catalog,
                                       json& changed, std::set<MtlBase*>& seen) {
    if (!base || seen.count(base)) return false;
    seen.insert(base);

    bool any = false;
    if (ContainsInsensitive(ClassName(base), "Image_tiles") && !catalog.byPrefixUdim.empty()) {
        int numPB = base->NumParamBlocks();
        for (int pbIdx = 0; pbIdx < numPB; ++pbIdx) {
            IParamBlock2* pb = base->GetParamBlock(pbIdx);
            if (!pb) continue;
            ParamBlockDesc2* desc = pb->GetDesc();
            if (!desc) continue;
            for (int pi = 0; pi < desc->count; ++pi) {
                ParamID pid = desc->IndextoID(pi);
                const ParamDef& pd = desc->GetParamDef(pid);
                if (BaseParamType(pd.type) != TYPE_STRING && BaseParamType(pd.type) != TYPE_FILENAME) continue;
                if (!IsTabParam(pd.type)) continue;
                std::string paramName = ParamName(pd, pid);
                if (!ContainsInsensitive(paramName, "ImageFilenames_list")) continue;

                std::string first = ReadScalarValue(pb, pid, pd.type, t, 0);
                std::string prefix, ext;
                int firstUdim = 0;
                if (!SplitUdimFilename(FilenameOnly(first), prefix, firstUdim, ext)) continue;

                auto group = catalog.byPrefixUdim.find(Lower(prefix + ext));
                if (group == catalog.byPrefixUdim.end()) continue;

                std::vector<std::pair<int, std::string>> tiles(group->second.begin(), group->second.end());
                if (tiles.size() <= 1) continue;

                std::vector<std::wstring> paths;
                std::vector<int> ofsU;
                for (size_t idx = 0; idx < tiles.size(); ++idx) {
                    paths.push_back(Utf8ToWide(NormalizeBackslashes(tiles[idx].second)));
                    ofsU.push_back((int)idx);
                }

                ParamID ofsPid = -1;
                for (int pj = 0; pj < desc->count; ++pj) {
                    ParamID candidate = desc->IndextoID(pj);
                    std::string candidateName = ParamName(desc->GetParamDef(candidate), candidate);
                    if (ContainsInsensitive(candidateName, "ImageFilenames_ofsU")) {
                        ofsPid = candidate;
                        break;
                    }
                }

                pb->SetCount(pid, (int)paths.size());
                for (int i = 0; i < (int)paths.size(); ++i) {
                    pb->SetValue(pid, t, paths[i].c_str(), i);
                }
                if (ofsPid >= 0) {
                    pb->SetCount(ofsPid, (int)ofsU.size());
                    for (int i = 0; i < (int)ofsU.size(); ++i) {
                        pb->SetValue(ofsPid, t, ofsU[i], i);
                    }
                }
                for (int pj = 0; pj < desc->count; ++pj) {
                    ParamID gridPid = desc->IndextoID(pj);
                    std::string gridName = ParamName(desc->GetParamDef(gridPid), gridPid);
                    if (!ContainsInsensitive(gridName, "gridSize")) continue;
                    const ParamDef& gridDef = desc->GetParamDef(gridPid);
                    if (BaseParamType(gridDef.type) == TYPE_INT && IsTabParam(gridDef.type)) {
                        pb->SetCount(gridPid, 2);
                        pb->SetValue(gridPid, t, (int)tiles.size(), 0);
                        pb->SetValue(gridPid, t, 1, 1);
                    }
                    break;
                }

                json entry;
                entry["node"] = BaseName(base);
                entry["class"] = ClassName(base);
                entry["param"] = paramName;
                entry["tileCount"] = paths.size();
                entry["firstPath"] = tiles.front().second;
                entry["lastPath"] = tiles.back().second;
                changed.push_back(entry);
                any = true;
            }
        }
    }

    if (base->SuperClassID() == MATERIAL_CLASS_ID) {
        Mtl* mtl = static_cast<Mtl*>(base);
        for (int i = 0; i < mtl->NumSubMtls(); ++i) {
            any = ExtendImageTilesFromFolder(mtl->GetSubMtl(i), t, catalog, changed, seen) || any;
        }
    }
    for (int i = 0; i < base->NumSubTexmaps(); ++i) {
        if (ShouldSkipMirrorChild(base, base->GetSubTexmap(i))) continue;
        any = ExtendImageTilesFromFolder(base->GetSubTexmap(i), t, catalog, changed, seen) || any;
    }
    for (const auto& child : PB2Children(base, t)) {
        if (ShouldSkipMirrorChild(base, child.first)) continue;
        any = ExtendImageTilesFromFolder(child.first, t, catalog, changed, seen) || any;
    }
    return any;
}

static json BuildReplicationPlan(const json& request, Mtl* source, const std::string& sourceName,
                                 const std::vector<std::string>& targets) {
    std::string textureFolder = NormalizeBackslashes(request.value("texture_folder", ""));
    auto pathMap = ParsePathMap(request);
    bool allowMissing = request.value("allow_missing", false);
    std::string mode = Lower(request.value("mode", "clone_and_remap"));
    TextureFolderCatalog catalog = BuildTextureFolderCatalog(textureFolder);
    const TextureFolderCatalog* catalogPtr = textureFolder.empty() ? nullptr : &catalog;

    json graph = InspectMaterialGraph(
        source,
        sourceName,
        "material",
        request.value("source_sub_material_index", 0),
        request.value("depth", 6),
        "wired",
        request.value("include_values", false),
        request.value("verify", true),
        request.value("max_nodes", 160),
        false  // Replication remaps string paths only; keep its plan to those.
    );

    json plannedFiles = json::array();
    bool allFilesExist = true;
    for (const auto& node : graph["nodes"]) {
        if (!node.contains("files")) continue;
        for (const auto& file : node["files"]) {
            std::string oldPath = file.value("path", "");
            if (oldPath.empty()) continue;
            if (!IsTextureDependency(file.value("param", ""), oldPath)) continue;
            std::string newPath = (mode == "clone") ? oldPath
                                                  : RemappedPath(oldPath, pathMap, textureFolder, catalogPtr);
            bool remapped = NormalizeBackslashes(oldPath) != NormalizeBackslashes(newPath);

            bool exists = false;
            uintmax_t bytes = FileSizeOrZero(newPath, &exists);
            allFilesExist = allFilesExist && exists;

            json entry;
            entry["nodeId"] = node.value("id", "");
            entry["param"] = file.value("param", "");
            entry["oldPath"] = oldPath;
            entry["newPath"] = NormalizeBackslashes(newPath);
            entry["remapped"] = remapped;
            entry["exists"] = exists;
            entry["bytes"] = bytes;
            int udim = ExtractUdim(newPath);
            if (udim > 0) entry["udim"] = udim;
            plannedFiles.push_back(entry);
        }
    }

    plannedFiles = DeduplicatePlannedFiles(plannedFiles);
    int remappedCount = 0;
    std::set<std::string> missingPlannedPaths;
    for (const auto& f : plannedFiles) {
        if (f.value("remapped", false)) remappedCount++;
        if (!f.value("exists", true)) {
            missingPlannedPaths.insert(NormalizeBackslashes(f.value("newPath", "")));
        }
    }

    // A target name several objects share cannot be assigned: apply would
    // skip it like a missing target.
    json targetInfo = json::array();
    for (const auto& target : targets) {
        const std::vector<INode*> matches = CollectNodesByName(target);
        json t;
        t["name"] = target;
        t["exists"] = !matches.empty();
        if (matches.size() > 1) {
            t["ambiguous"] = true;
            t["candidates"] = NameCandidatesJson(matches);
        }
        targetInfo.push_back(t);
    }

    json errors = json::array();
    if (!allowMissing) {
        for (const auto& f : plannedFiles) {
            if (!f.value("exists", true)) {
                std::string prefix = (mode == "clone") ? "Missing source texture: " : "Missing remapped texture: ";
                errors.push_back(prefix + f.value("newPath", ""));
            }
        }
        if (graph.contains("issues") && graph["issues"].type() == json::value_t::array) {
            for (const auto& issue : graph["issues"]) {
                std::string code = issue.value("code", "");
                std::string message = issue.value("message", "");
                if (code == "FILE_MISSING") {
                    bool alreadyReported = false;
                    for (const auto& missingPath : missingPlannedPaths) {
                        if (!missingPath.empty() && message.find(missingPath) != std::string::npos) {
                            alreadyReported = true;
                            break;
                        }
                    }
                    if (alreadyReported) continue;
                    allFilesExist = false;
                }
                errors.push_back(message.empty() ? ("Material graph issue: " + code) : message);
            }
        }
    }
    for (const auto& t : targetInfo) {
        if (!t.value("exists", false)) errors.push_back("Target object not found: " + t.value("name", ""));
        else if (t.value("ambiguous", false))
            errors.push_back("Target object name is shared by several objects: " + t.value("name", ""));
    }

    json plan;
    plan["ok"] = errors.empty();
    plan["preview"] = true;
    plan["mode"] = mode;
    plan["source"] = sourceName;
    plan["sourceMaterial"] = BaseName(source);
    plan["rootClass"] = ClassName(source);
    plan["targets"] = targetInfo;
    plan["plannedFiles"] = plannedFiles;
    plan["remappedFiles"] = remappedCount;
    plan["allFilesExist"] = allFilesExist;
    plan["errors"] = errors;
    plan["graph"] = graph;
    plan["policy"] = {
        {"assignRequiredForApply", mode == "remap_in_place"},
        {"allowMissing", allowMissing},
        {"catalogFileCount", catalog.byFilenameLower.size()}
    };
    return plan;
}

static std::vector<std::string> ParseTargets(const json& p) {
    std::vector<std::string> targets;
    if (p.contains("targets") && p["targets"].type() == json::value_t::array) {
        for (const auto& v : p["targets"]) targets.push_back(v.get<std::string>());
    } else if (p.contains("target") && p["target"].type() == json::value_t::array) {
        for (const auto& v : p["target"]) targets.push_back(v.get<std::string>());
    } else if (p.contains("target") && p["target"].type() == json::value_t::string) {
        targets.push_back(p["target"].get<std::string>());
    }
    return targets;
}

static bool SetMappedStringParams(MtlBase* base, TimeValue t,
                                  const std::unordered_map<std::string, std::string>& pathMap,
                                  const std::string& textureFolder,
                                  const TextureFolderCatalog* catalog, json& changed) {
    bool any = false;
    int numPB = base->NumParamBlocks();
    for (int pbIdx = 0; pbIdx < numPB; ++pbIdx) {
        IParamBlock2* pb = base->GetParamBlock(pbIdx);
        if (!pb) continue;
        ParamBlockDesc2* desc = pb->GetDesc();
        if (!desc) continue;
        for (int pi = 0; pi < desc->count; ++pi) {
            ParamID pid = desc->IndextoID(pi);
            const ParamDef& pd = desc->GetParamDef(pid);
            int baseType = BaseParamType(pd.type);
            if (baseType != TYPE_STRING && baseType != TYPE_FILENAME) continue;

            std::string paramName = ParamName(pd, pid);
            int count = IsTabParam(pd.type) ? std::max(0, pb->Count(pid)) : 1;
            for (int i = 0; i < count; ++i) {
                std::string oldPath = ReadScalarValue(pb, pid, pd.type, t, i);
                if (!IsPathLike(oldPath)) continue;
                if (!IsTextureDependency(paramName, oldPath)) continue;
                std::string newPath = RemappedPath(oldPath, pathMap, textureFolder, catalog);
                newPath = NormalizeBackslashes(newPath);
                if (NormalizeBackslashes(oldPath) == newPath) continue;
                std::wstring w = Utf8ToWide(newPath);
                if (pb->SetValue(pid, t, w.c_str(), i)) {
                    any = true;
                    json entry;
                    entry["node"] = BaseName(base);
                    entry["class"] = ClassName(base);
                    entry["param"] = paramName;
                    entry["oldPath"] = oldPath;
                    entry["newPath"] = newPath;
                    changed.push_back(entry);
                }
            }
        }
    }
    return any;
}

static void RemapGraphFiles(MtlBase* root, TimeValue t,
                            const std::unordered_map<std::string, std::string>& pathMap,
                            const std::string& textureFolder, const TextureFolderCatalog* catalog,
                            json& changed, std::set<MtlBase*>& seen) {
    if (!root || seen.count(root)) return;
    seen.insert(root);
    SetMappedStringParams(root, t, pathMap, textureFolder, catalog, changed);

    if (root->SuperClassID() == MATERIAL_CLASS_ID) {
        Mtl* mtl = static_cast<Mtl*>(root);
        for (int i = 0; i < mtl->NumSubMtls(); ++i) {
            RemapGraphFiles(mtl->GetSubMtl(i), t, pathMap, textureFolder, catalog, changed, seen);
        }
    }
    for (int i = 0; i < root->NumSubTexmaps(); ++i) {
        Texmap* tex = root->GetSubTexmap(i);
        if (ShouldSkipMirrorChild(root, tex)) continue;
        RemapGraphFiles(tex, t, pathMap, textureFolder, catalog, changed, seen);
    }
    for (const auto& child : PB2Children(root, t)) {
        if (ShouldSkipMirrorChild(root, child.first)) continue;
        RemapGraphFiles(child.first, t, pathMap, textureFolder, catalog, changed, seen);
    }
}

} // namespace

std::string NativeHandlers::InspectMaterialNetwork(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        if (p.is_discarded()) throw std::runtime_error("Invalid JSON params");

        if (p.value("action", "inspect") == "roles") return InspectRoleGraphs(p).dump();

        std::string name = p.value("name", "");
        int subIdx = p.value("sub_material_index", 0);
        int depth = p.value("depth", 6);
        std::string scope = p.value("scope", "wired");
        bool includeValues = p.value("include_values", true);
        bool verifyFiles = p.value("verify_files", true);
        int maxNodes = p.value("max_nodes", 80);

        MaterialResolve resolved = ResolveMaterial(name, subIdx);
        json result = InspectMaterialGraph(
            resolved.material,
            name,
            resolved.resolvedVia,
            subIdx,
            depth,
            scope,
            includeValues,
            verifyFiles,
            maxNodes
        );
        result["owner"] = resolved.ownerName;
        return result.dump();
    });
}

std::string NativeHandlers::ReplicateMaterialPreview(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        if (p.is_discarded()) throw std::runtime_error("Invalid JSON params");
        std::string source = p.value("source", "");
        int subIdx = p.value("source_sub_material_index", 0);
        MaterialResolve resolved = ResolveMaterial(source, subIdx);
        return BuildReplicationPlan(p, resolved.material, source, ParseTargets(p)).dump();
    });
}

std::string NativeHandlers::ReplicateMaterial(const std::string& params, MCPBridgeGUP* gup) {
    json p = json::parse(params, nullptr, false);
    if (p.is_discarded()) throw std::runtime_error("Invalid JSON params");

    std::string mode = Lower(p.value("mode", "clone_and_remap"));
    bool preview = p.value("preview", true) || mode == "preview";
    return preview
        ? NativeHandlers::ReplicateMaterialPreview(params, gup)
        : NativeHandlers::ReplicateMaterialApply(params, gup);
}

std::string NativeHandlers::ReplicateMaterialApply(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        if (p.is_discarded()) throw std::runtime_error("Invalid JSON params");

        std::string source = p.value("source", "");
        int subIdx = p.value("source_sub_material_index", 0);
        bool assign = p.value("assign", true);
        bool verify = p.value("verify", true);
        bool allowMissing = p.value("allow_missing", false);
        std::string mode = Lower(p.value("mode", "clone_and_remap"));
        std::string materialName = p.value("material_name", "");
        std::string textureFolder = NormalizeBackslashes(p.value("texture_folder", ""));
        auto pathMap = ParsePathMap(p);
        std::vector<std::string> targets = ParseTargets(p);
        bool extendUdimTiles = p.value("extend_udim_tiles", true);

        if (mode == "preview") {
            MaterialResolve resolved = ResolveMaterial(source, subIdx);
            return BuildReplicationPlan(p, resolved.material, source, targets).dump();
        }
        if (assign && targets.empty()) {
            throw std::runtime_error("target is required when assign=true");
        }
        if (mode == "remap_in_place" && !p.value("confirm", false)) {
            throw std::runtime_error("remap_in_place requires confirm=true");
        }
        if ((mode == "clone_and_remap" || mode == "remap_in_place") &&
            textureFolder.empty() && pathMap.empty()) {
            throw std::runtime_error("texture_folder or path_map is required for remap modes");
        }

        MaterialResolve resolved = ResolveMaterial(source, subIdx);
        json plan = BuildReplicationPlan(p, resolved.material, source, targets);
        if (!plan.value("ok", false) && !allowMissing) {
            json result;
            result["ok"] = false;
            result["preview"] = false;
            result["status"] = "blocked";
            // "error" makes the envelope report the refusal instead of success;
            // the plan's errors travel in details.
            result["error"] = "replicate_material was blocked by its preflight plan; nothing was changed. "
                "Fix the listed errors or pass allow_missing=true.";
            result["details"] = {{"errors", plan["errors"]}};
            result["errors"] = plan["errors"];
            result["plan"] = plan;
            return result.dump();
        }

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();
        Mtl* targetMaterial = resolved.material;
        bool cloned = false;

        if (mode != "remap_in_place") {
            RemapDir* remap = NewRemapDir();
            if (!remap) throw std::runtime_error("Could not create SDK remap directory");
            RefTargetHandle clonedRef = resolved.material->Clone(*remap);
            remap->Backpatch();
            remap->DeleteThis();
            targetMaterial = dynamic_cast<Mtl*>(clonedRef);
            if (!targetMaterial) throw std::runtime_error("Material clone failed");
            cloned = true;
            if (materialName.empty()) materialName = BaseName(resolved.material) + "_copy";
            std::wstring wname = Utf8ToWide(materialName);
            targetMaterial->SetName(wname.c_str());
        }

        json changed = json::array();
        TextureFolderCatalog catalog = BuildTextureFolderCatalog(textureFolder);
        const TextureFolderCatalog* catalogPtr = textureFolder.empty() ? nullptr : &catalog;
        if (mode != "clone") {
            std::set<MtlBase*> seen;
            RemapGraphFiles(targetMaterial, t, pathMap, textureFolder, catalogPtr, changed, seen);
            if (extendUdimTiles && catalogPtr) {
                std::set<MtlBase*> extendSeen;
                ExtendImageTilesFromFolder(targetMaterial, t, catalog, changed, extendSeen);
            }
        }

        json assigned = json::array();
        NamedNodes resolvedTargets;
        const json& missingTargets = resolvedTargets.notFound;
        if (assign) {
            resolvedTargets = ResolveNodesByNames(targets);
            for (INode* node : resolvedTargets.nodes) {
                node->SetMtl(targetMaterial);
                assigned.push_back(WideToUtf8(node->GetName()));
            }
            // With every target unresolved the new material would have no owner;
            // the throw cancels the dispatcher's hold and leaves the scene as it was.
            if (assigned.empty()) {
                json hint = UnresolvedNamesHint(resolvedTargets);
                hint["missingTargets"] = missingTargets;
                throw std::runtime_error(StructuredErrorPayload(
                    UnresolvedNamesCode(resolvedTargets),
                    resolvedTargets.ambiguous.empty()
                        ? "None of the target objects exist; no material was assigned."
                        : "No target name matched exactly one object; no material was assigned.",
                    hint));
            }
        } else if (cloned && ip->GetSceneMtls()) {
            ip->GetSceneMtls()->Add(targetMaterial);
        }

        targetMaterial->NotifyDependents(FOREVER, PART_ALL, REFMSG_CHANGE);
        ip->RedrawViews(t);

        json verification = json::object();
        if (verify) {
            json graph = InspectMaterialGraph(
                targetMaterial,
                materialName.empty() ? BaseName(targetMaterial) : materialName,
                cloned ? "clone" : "material",
                0,
                p.value("depth", 6),
                "wired",
                p.value("include_values", false),
                true,
                p.value("max_nodes", 160),
                false  // Verify the same string-param files the plan remapped.
            );
            verification["graph"] = graph;
            bool allFilesExist = true;
            for (const auto& node : graph["nodes"]) {
                if (!node.contains("files")) continue;
                for (const auto& f : node["files"]) {
                    allFilesExist = allFilesExist && f.value("exists", true);
                }
            }
            verification["allFilesExist"] = allFilesExist;
        }

        json result;
        result["ok"] = !resolvedTargets.Unresolved();
        result["preview"] = false;
        result["status"] = resolvedTargets.Unresolved() ? "partial" : "applied";
        result["mode"] = mode;
        result["source"] = source;
        result["materialName"] = BaseName(targetMaterial);
        result["cloned"] = cloned;
        result["assignedTo"] = assigned;
        if (!missingTargets.empty()) {
            result["missingTargets"] = missingTargets;
            result["warnings"].push_back(std::to_string(missingTargets.size()) +
                " target object(s) do not exist and were not assigned: " + JoinNames(missingTargets));
        }
        AddAmbiguousNamesWarning(result, resolvedTargets.ambiguous);
        result["remappedFiles"] = changed.size();
        result["remapped"] = changed;
        result["verification"] = verification;
        result["hint"] = "Save the scene to persist material graph/path changes.";
        return result.dump();
    });
}
