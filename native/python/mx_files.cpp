// Scene files: the open scene (info, hold and fetch, reset, save) and other
// files (metadata, search, merge, import, batch import onto a grid).
//
// Nothing here discards unsaved work or waits on a prompt: reset() and fetch()
// check what they would drop and refuse instead of asking, and save() writes
// only the scene's own file. Merges and imports run without dialogs; a failed
// one removes the nodes it added. Paths are str or os.PathLike.
#include "mx_common.h"

#include <nanobind/stl/filesystem.h>
#include <nanobind/stl/variant.h>

#include <cwctype>
#include <filesystem>
#include <fstream>
#include <map>
#include <unordered_set>
#include <variant>

#include <nametab.h>
#include <notify.h>

namespace mx {
inline namespace MX_ABI_TAG {

namespace fs = std::filesystem;
using json = nlohmann::json;

// ── Text and paths ───────────────────────────────────────────────

static std::string Text(const std::wstring& s) { return WideToUtf8(s.c_str()); }
static std::string PathText(const fs::path& p) { return WideToUtf8(p.c_str()); }

static std::wstring LowerW(std::wstring s) {
    for (wchar_t& c : s) c = (wchar_t)std::towlower(c);
    return s;
}

static std::vector<std::string> Utf8(const std::vector<std::wstring>& names) {
    std::vector<std::string> out;
    for (const std::wstring& name : names) out.push_back(Text(name));
    return out;
}

static fs::path Absolute(const fs::path& p) {
    std::error_code ec;
    fs::path full = fs::absolute(p, ec);
    return ec ? p : full;
}

static fs::path RequireFile(const fs::path& p) {
    const fs::path file = Absolute(p);
    std::error_code ec;
    if (!fs::is_regular_file(file, ec)) throw NotFound("no file '" + PathText(file) + "'");
    return file;
}

static fs::path RequireFolder(const fs::path& p, const char* what) {
    const fs::path folder = Absolute(p);
    std::error_code ec;
    if (!fs::is_directory(folder, ec)) throw NotFound(std::string("no ") + what + " '" + PathText(folder) + "'");
    return folder;
}

// .max files are OLE compound documents.
static bool IsSceneFile(const fs::path& file) {
    static const unsigned char kOleSignature[8] = {0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1};
    unsigned char head[8] = {};
    std::ifstream in(file, std::ios::binary);
    return in.read(reinterpret_cast<char*>(head), sizeof head) && std::memcmp(head, kOleSignature, sizeof head) == 0;
}

static fs::path RequireSceneFile(const fs::path& p) {
    const fs::path file = RequireFile(p);
    if (!IsSceneFile(file)) throw BadParam("'" + PathText(file) + "' is not a 3ds Max scene file");
    return file;
}

using Extensions = std::vector<std::wstring>;

static std::wstring Extension(const fs::path& p) { return LowerW(p.extension().wstring()); }

static bool HasExtension(const fs::path& p, const Extensions& extensions) {
    return std::find(extensions.begin(), extensions.end(), Extension(p)) != extensions.end();
}

// Files under root with one of the extensions, sorted by path ignoring case.
static std::vector<fs::path> Scan(const fs::path& root, bool recursive, const Extensions& extensions) {
    std::vector<fs::path> files;
    auto keep = [&](const fs::directory_entry& entry) {
        std::error_code ec;
        if (entry.is_regular_file(ec) && HasExtension(entry.path(), extensions)) files.push_back(entry.path());
    };
    std::error_code ec;
    const auto options = fs::directory_options::skip_permission_denied;
    if (recursive) {
        for (fs::recursive_directory_iterator it(root, options, ec), end; !ec && it != end; it.increment(ec)) keep(*it);
    } else {
        for (fs::directory_iterator it(root, options, ec), end; !ec && it != end; it.increment(ec)) keep(*it);
    }
    std::sort(files.begin(), files.end(),
              [](const fs::path& a, const fs::path& b) { return LowerW(a.wstring()) < LowerW(b.wstring()); });
    return files;
}

// fnmatch-style wildcards, ignoring case: * any run, ? one character, [abc], [a-z], [!abc].
static bool MatchOne(const std::wstring& pattern, size_t& p, wchar_t c) {
    const wchar_t ch = (wchar_t)std::towlower(c);
    if (pattern[p] == L'?') {
        ++p;
        return true;
    }
    if (pattern[p] == L'[') {
        size_t first = p + 1;
        const bool negate = first < pattern.size() && pattern[first] == L'!';
        if (negate) ++first;
        // A ']' right after the opening bracket is a member, not the end.
        const size_t close = pattern.find(L']', first + 1);
        if (close != std::wstring::npos) {
            bool member = false;
            for (size_t j = first; j < close; ++j) {
                if (j + 2 < close && pattern[j + 1] == L'-') {
                    member = member || (std::towlower(pattern[j]) <= ch && ch <= std::towlower(pattern[j + 2]));
                    j += 2;
                } else {
                    member = member || std::towlower(pattern[j]) == ch;
                }
            }
            p = close + 1;
            return member != negate;
        }
    }
    return (wchar_t)std::towlower(pattern[p++]) == ch;
}

static bool Glob(const std::wstring& name, const std::wstring& pattern) {
    size_t n = 0, p = 0, star = std::wstring::npos, resume = 0;
    while (n < name.size()) {
        if (p < pattern.size() && pattern[p] == L'*') {
            star = p++;
            resume = n;
            continue;
        }
        size_t next = p;
        if (p < pattern.size() && MatchOne(pattern, next, name[n])) {
            p = next;
            ++n;
            continue;
        }
        if (star == std::wstring::npos) return false;
        p = star + 1;
        n = ++resume;
    }
    while (p < pattern.size() && pattern[p] == L'*') ++p;
    return p == pattern.size();
}

// ── Scene nodes ──────────────────────────────────────────────────

static std::unordered_set<AnimHandle> Handles() {
    std::unordered_set<AnimHandle> out;
    for (INode* node : AllNodes()) out.insert(Animatable::GetHandleByAnim(node));
    return out;
}

// Nodes that are not in `before`, in scene order.
static std::vector<Node> NewNodes(const std::unordered_set<AnimHandle>& before) {
    std::vector<Node> out;
    for (INode* node : AllNodes())
        if (!before.count(Animatable::GetHandleByAnim(node))) out.push_back(Wrap(node));
    return out;
}

static void DeleteNodes(const std::vector<Node>& nodes) {
    for (const Node& n : nodes)
        if (INode* node = HandlerHelpers::FindNodeByHandle(n.handle); InScene(node)) GetCOREInterface()->DeleteNode(node, FALSE);
}

// ── The open scene ───────────────────────────────────────────────

static std::wstring SceneFileW() {
    const MCHAR* path = GetCOREInterface()->GetCurFilePath().data();
    return path ? path : L"";
}

static std::string SceneLabel(const std::wstring& file) {
    return file.empty() ? "the untitled scene" : "'" + Text(file) + "'";
}

static std::optional<std::string> SceneFile() {
    RequireMain();
    const std::wstring file = SceneFileW();
    if (file.empty()) return std::nullopt;
    return Text(file);
}

static nb::dict SceneInfo() {
    const json counts = NativeJson("native:manage_scene", {{"action", "info"}});
    const std::optional<std::string> file = SceneFile();
    nb::dict out;
    out["file"] = file ? nb::object(nb::str(file->c_str())) : nb::none();
    out["unsaved"] = IsSaveRequired() != 0;
    out["objects"] = counts.value("objectCount", 0);
    out["faces"] = counts.value("polyCount", 0);
    return out;
}

// Replacing the scene inside an open undo operation would pull the scene out
// from under whoever owns that operation.
static void RequireNoUndoOperation(const char* call) {
    if (theHold.Holding())
        throw MaxError(std::string("3ds Max has an open undo operation; finish or cancel it, then call ") + call + " again");
}

struct FileStamp {
    ULONGLONG written = 0;
    ULONGLONG size = 0;
    bool operator==(const FileStamp&) const = default;
};

static std::optional<FileStamp> Stamp(const std::wstring& file) {
    WIN32_FILE_ATTRIBUTE_DATA data;
    if (!GetFileAttributesExW(file.c_str(), GetFileExInfoStandard, &data)) return std::nullopt;
    return FileStamp{(ULONGLONG(data.ftLastWriteTime.dwHighDateTime) << 32) | data.ftLastWriteTime.dwLowDateTime,
                     (ULONGLONG(data.nFileSizeHigh) << 32) | data.nFileSizeLow};
}

// What hold() held, so that fetch() restores only that.
struct Held {
    std::wstring file;   // the hold buffer
    FileStamp stamp;     // as hold() left it
    std::wstring scene;  // the scene file open at the time
};

static std::optional<Held>& LastHold() {
    static std::optional<Held> held;
    return held;
}

// The hold buffer a hold writes or a fetch reads, from 3ds Max's file-process
// notifications. Success is judged by the file and by open failures, not by
// the notifications' success flag.
struct HoldFetchWatch {
    std::wstring file;                // the hold buffer, once 3ds Max names it
    std::optional<FileStamp> before;  // the hold buffer before a hold wrote it
    bool finished = false;            // the hold or fetch process ran to its end
    bool openFailed = false;

    HoldFetchWatch() {
        for (NotifyCode code : kCodes) RegisterNotification(&Record, this, code);
    }
    ~HoldFetchWatch() { UnRegisterNotification(&Record, this); }
    HoldFetchWatch(const HoldFetchWatch&) = delete;
    HoldFetchWatch& operator=(const HoldFetchWatch&) = delete;

    static constexpr NotifyCode kCodes[] = {NOTIFY_FILE_PRE_SAVE_PROCESS, NOTIFY_FILE_POST_SAVE_PROCESS,
                                            NOTIFY_FILE_POST_OPEN_PROCESS, NOTIFY_FILE_OPEN_FAILED};

    static void Record(void* self, NotifyInfo* info) {
        auto* watch = static_cast<HoldFetchWatch*>(self);
        if (!info) return;
        if (info->intcode == NOTIFY_FILE_OPEN_FAILED) {
            watch->openFailed = true;
            return;
        }
        const NotifyFileProcessParam* process = GetNotifyParam<NOTIFY_FILE_PRE_SAVE_PROCESS,
            NOTIFY_FILE_POST_SAVE_PROCESS, NOTIFY_FILE_POST_OPEN_PROCESS>(info);
        if (!process || process->iType != FILE_PROCESS_HOLD_FETCH || !process->fname) return;
        watch->file = process->fname;
        if (info->intcode == NOTIFY_FILE_PRE_SAVE_PROCESS) watch->before = Stamp(watch->file);
        else watch->finished = true;
    }
};

static void HoldScene() {
    RequireMain();
    HoldFetchWatch watch;
    GetCOREInterface()->FileHold();
    const std::optional<FileStamp> stamp = watch.file.empty() ? std::nullopt : Stamp(watch.file);
    if (!watch.finished || !stamp || stamp == watch.before) throw MaxError("3ds Max did not write the hold buffer");
    LastHold() = Held{watch.file, *stamp, SceneFileW()};
}

static void FetchScene() {
    RequireMain();
    RequireNoUndoOperation("fetch()");
    const std::optional<Held>& held = LastHold();
    if (!held)
        throw MaxError("nothing to fetch: fetch() restores only what hold() held in this 3ds Max session; call hold() first");
    if (Stamp(held->file) != held->stamp)
        throw MaxError("the hold buffer changed after hold() (Edit > Hold or another tool held since), so fetch() "
                       "would not restore what hold() held; call hold() again first");
    const std::wstring scene = SceneFileW();
    if (_wcsicmp(scene.c_str(), held->scene.c_str()) != 0)
        throw MaxError("hold() held " + SceneLabel(held->scene) + " but " + SceneLabel(scene) +
                       " is open now; fetch() would replace it");
    HoldFetchWatch watch;
    {
        TempQuietMode quiet;  // Max's fetch confirmation: the checks above stand in for it
        GetCOREInterface()->FileFetch();
    }
    if (!watch.finished || watch.openFailed) throw MaxError("3ds Max did not fetch the hold buffer");
}

static void ResetScene() {
    RequireMain();
    RequireNoUndoOperation("reset()");
    const std::wstring file = SceneFileW();
    if (IsSaveRequired())
        throw MaxError(file.empty()
            ? "the untitled scene has unsaved changes and reset() never discards work; ask the user to save or discard them"
            : "the scene has unsaved changes and reset() never discards work; save() them first, or ask the user");
    if (file.empty() && !AllNodes().empty())
        throw MaxError("the untitled scene holds objects that were never saved and reset() never discards work; "
                       "ask the user to save or discard them");
    GetCOREInterface()->FileReset(TRUE);  // nothing is left to lose, so no prompt
}

static std::string SaveScene() {
    RequireMain();
    const std::wstring file = SceneFileW();
    if (file.empty())
        throw MaxError("the scene is untitled and save() writes only the scene's own file; "
                       "save it once in 3ds Max (File > Save As)");
    if (!GetCOREInterface()->SaveToFile(file.c_str())) throw MaxError("3ds Max could not save '" + Text(file) + "'");
    return Text(file);
}

// ── Other .max files ─────────────────────────────────────────────

// Object names in a .max file, read without merging anything.
static std::vector<std::wstring> ObjectNames(const fs::path& file) {
    NameTab names;
    {
        TempQuietMode quiet;
        GetCOREInterface()->MergeFromFile(file.c_str(), TRUE, FALSE, FALSE, MERGE_LIST_NAMES, &names);
    }
    std::vector<std::wstring> out;
    for (int i = 0; i < names.Count(); ++i)
        if (names[i]) out.emplace_back(names[i]);
    return out;
}

// The bridge's class-directory categories, as snake_case kinds.
static const char* ClassKind(const std::string& category) {
    static const std::map<std::string, const char*> kinds = {
        {"geometry", "geometry"}, {"camera", "camera"}, {"light", "light"}, {"shape", "shape"},
        {"helper", "helper"}, {"material", "material"}, {"texturemap", "texture_map"}, {"modifier", "modifier"},
        {"wsModifier", "spacewarp_modifier"}, {"controller", "controller"},
    };
    const auto it = kinds.find(category);
    return it == kinds.end() ? "other" : it->second;
}

// Plugin classes the file uses, by kind.
static nb::dict FileClasses(const fs::path& file) {
    const json reply = NativeJson("native:inspect_max_file", {{"file_path", PathText(file)}, {"list_classes", true}});
    if (reply.contains("classesError") || !reply.contains("classes") || !reply["classes"].is_object())
        throw MaxError("could not read the class directory of '" + PathText(file) + "'");
    nb::dict out;
    for (auto it = reply["classes"].begin(); it != reply["classes"].end(); ++it)
        out[ClassKind(it.key())] = ToPython(it.value().value("classes", json::array()));
    return out;
}

static nb::dict MetadataReport(const fs::path& file, const json& info) {
    if (info.contains("error")) {
        const json& error = info["error"];
        throw MaxError(PathText(file) + ": " + ((error.is_string)() ? error.get<std::string>() : error.dump()));
    }
    const json meta = info.value("metadata", json::object());
    if (meta.contains("oleError")) throw BadParam("'" + PathText(file) + "' is not a 3ds Max scene file");
    nb::dict out;
    out["path"] = PathText(file);
    out["size"] = meta.value("fileSizeBytes", 0ULL);
    for (const char* key : {"created", "modified", "title", "subject", "author", "comments"})
        if (meta.contains(key)) out[key] = ToPython(meta[key]);
    if (meta.contains("docProperties")) out["properties"] = ToPython(meta["docProperties"]);
    return out;
}

static nb::dict FailedReport(const fs::path& file, const std::string& error) {
    nb::dict out;
    out["path"] = PathText(file);
    out["error"] = error;
    return out;
}

// One report per path, in order; a file that cannot be read reports its 'error'.
static std::vector<nb::dict> FileReports(const std::vector<fs::path>& paths, bool objects, bool classes) {
    std::vector<nb::dict> out(paths.size());
    std::vector<fs::path> files(paths.size());
    json readable = json::array();
    for (size_t i = 0; i < paths.size(); ++i) {
        try {
            files[i] = RequireSceneFile(paths[i]);
            readable.push_back(PathText(files[i]));
        } catch (const MaxError& e) {
            out[i] = FailedReport(Absolute(paths[i]), e.what());
        }
    }
    if (readable.empty()) return out;
    // Metadata of every readable file in one call: the bridge reads them in parallel.
    const json reply = NativeJson("native:batch_file_info", {{"file_paths", readable}, {"list_objects", false}});
    const json infos = reply.value("files", json::array());
    size_t next = 0;
    for (size_t i = 0; i < paths.size(); ++i) {
        if (files[i].empty()) continue;
        const json info = next < infos.size() ? infos[next] : json::object();
        ++next;
        try {
            nb::dict report = MetadataReport(files[i], info);
            if (classes) report["classes"] = FileClasses(files[i]);
            if (objects) report["objects"] = Utf8(ObjectNames(files[i]));
            out[i] = report;
        } catch (const MaxError& e) {
            out[i] = FailedReport(files[i], e.what());
        }
    }
    return out;
}

static nb::object FileInfoOf(const std::variant<fs::path, std::vector<fs::path>>& path, bool objects, bool classes) {
    RequireMain();
    if (const fs::path* one = std::get_if<fs::path>(&path)) {
        const fs::path file = RequireSceneFile(*one);
        nb::dict report = FileReports({file}, objects, classes).front();
        if (report.contains("error")) throw MaxError(nb::cast<std::string>(report["error"]));
        return report;
    }
    const auto& paths = std::get<std::vector<fs::path>>(path);
    if (paths.empty()) throw BadParam("file_info() takes a path or a non-empty list of paths");
    nb::list out;
    for (nb::dict& report : FileReports(paths, objects, classes)) out.append(report);
    return out;
}

// A file pattern ("*.max") would match no object name.
static void RequireNamePattern(const std::string& pattern) {
    const std::string p = Lower(pattern);
    const bool fileLike = p.find('/') != std::string::npos || p.find('\\') != std::string::npos ||
                          p.ends_with(".max") || p.ends_with(".3ds") || p.ends_with(".fbx") || p.ends_with(".obj");
    if (fileLike)
        throw BadParam("pattern matches object names inside the files, not file names: pass '*' for every object, "
                       "or a name pattern such as 'Chair*'");
}

static nb::list SearchMaxFiles(const fs::path& folder, const std::string& pattern, bool recursive) {
    RequireMain();
    const fs::path root = RequireFolder(folder, "folder");
    RequireNamePattern(pattern);
    const std::wstring wanted = Utf8ToWide(pattern);
    nb::list out;
    for (const fs::path& file : Scan(root, recursive, {L".max"})) {
        if (!IsSceneFile(file)) continue;
        const std::vector<std::wstring> names = ObjectNames(file);
        std::vector<std::string> matched;
        for (const std::wstring& name : names)
            if (Glob(name, wanted)) matched.push_back(Text(name));
        if (matched.empty()) continue;
        nb::dict hit;
        hit["path"] = PathText(file);
        hit["objects"] = names.size();
        hit["names"] = matched;
        out.append(hit);
    }
    return out;
}

// ── Merge and import ─────────────────────────────────────────────

static int DuplicateAction(const std::string& duplicates) {
    if (duplicates == "rename") return MERGE_DUPS_RENAME;
    if (duplicates == "skip") return MERGE_DUPS_SKIP;
    if (duplicates == "merge") return MERGE_DUPS_MERGE;
    if (duplicates == "delete_old") return MERGE_DUPS_DELOLD;
    throw BadParam("duplicates must be 'rename', 'skip', 'merge' (keep both names) or 'delete_old', not '" +
                   duplicates + "'");
}

static int MaterialAction(const std::string& materials) {
    if (materials == "rename") return MERGE_DUP_MTL_RENAME;
    if (materials == "scene") return MERGE_DUP_MTL_USE_SCENE;
    if (materials == "merged") return MERGE_DUP_MTL_USE_MERGED;
    throw BadParam("materials must be 'rename', 'scene' (use the scene's material of that name) or 'merged' "
                   "(replace the scene's), not '" + materials + "'");
}

// Merge without dialogs, hierarchies kept; a failed merge removes what it added.
static std::vector<Node> MergeNodes(const fs::path& file, const std::vector<std::wstring>& names, int duplicates,
                                    int materials) {
    NameTab list;
    for (const std::wstring& name : names) list.AddName(name.c_str());
    const auto before = Handles();
    int merged = 0;
    {
        TempQuietMode quiet;
        merged = GetCOREInterface()->MergeFromFile(file.c_str(), TRUE, FALSE, FALSE, duplicates,
                                                   names.empty() ? nullptr : &list, materials, MERGE_REPARENT_ALWAYS);
    }
    std::vector<Node> added = NewNodes(before);
    if (!merged) {
        DeleteNodes(added);
        throw MaxError("3ds Max could not merge '" + PathText(file) + "'");
    }
    return added;
}

// Every requested name must be an object of the file before anything merges.
static void RequireObjects(const fs::path& file, const std::vector<std::wstring>& names) {
    const std::vector<std::wstring> inFile = ObjectNames(file);
    std::vector<std::string> missing, quoted;
    for (const std::wstring& name : names) {
        if (std::find(inFile.begin(), inFile.end(), name) != inFile.end()) continue;
        missing.push_back(Text(name));
        quoted.push_back("'" + missing.back() + "'");
    }
    if (missing.empty()) return;
    const std::vector<std::string> available = Utf8(inFile);
    throw NotFound("'" + PathText(file) + "' has no object named " + Join(quoted) + " (names are case-sensitive)." +
                   Suggest(missing.front(), available) +
                   (available.empty() ? std::string(" It has no objects.") : " Its objects: " + Join(available)));
}

using NamesArg = std::variant<std::string, std::vector<std::string>>;

static std::vector<Node> MergeFile(const fs::path& path, const std::optional<NamesArg>& names,
                                   const std::string& duplicates, const std::string& materials) {
    RequireMain();
    const fs::path file = RequireSceneFile(path);
    const int duplicateAction = DuplicateAction(duplicates);
    const int materialAction = MaterialAction(materials);
    std::vector<std::wstring> wanted;
    if (names) {
        if (const std::string* one = std::get_if<std::string>(&*names)) wanted.push_back(Utf8ToWide(*one));
        else for (const std::string& name : std::get<std::vector<std::string>>(*names)) wanted.push_back(Utf8ToWide(name));
        if (wanted.empty()) throw BadParam("names is empty; pass None to merge every object");
        RequireObjects(file, wanted);
    }
    return MergeNodes(file, wanted, duplicateAction, materialAction);
}

// Import with the importer's defaults; a failed import removes what it added.
static std::vector<Node> ImportNodes(const fs::path& file) {
    const auto before = Handles();
    const BOOL imported = GetCOREInterface()->ImportFromFile(file.c_str(), TRUE);
    std::vector<Node> added = NewNodes(before);
    if (!imported) {
        DeleteNodes(added);
        throw MaxError("3ds Max could not import '" + PathText(file) +
                       "' (no importer for this file type, or the importer failed)");
    }
    return added;
}

static std::vector<Node> ImportFile(const fs::path& path) {
    RequireMain();
    const fs::path file = RequireFile(path);
    if (Extension(file) == L".max") throw BadParam("'" + PathText(file) + "' is a 3ds Max scene: merge() it instead");
    return ImportNodes(file);
}

// ── Texture channels ─────────────────────────────────────────────
// Which PBR channel a texture file feeds and which material it belongs to,
// from its name alone (the material tools' detection).

static std::vector<std::wstring> Tokens(const std::wstring& text) {
    std::vector<std::wstring> tokens;
    std::wstring current;
    for (wchar_t raw : text) {
        const wchar_t c = (wchar_t)std::towlower(raw);
        if ((c >= L'a' && c <= L'z') || (c >= L'0' && c <= L'9')) {
            current += c;
        } else if (!current.empty()) {
            tokens.push_back(current);
            current.clear();
        }
    }
    if (!current.empty()) tokens.push_back(current);
    return tokens;
}

static std::wstring JoinW(const std::vector<std::wstring>& parts, const std::wstring& separator) {
    std::wstring out;
    for (size_t i = 0; i < parts.size(); ++i) out += (i ? separator : L"") + parts[i];
    return out;
}

struct ChannelAlias {
    std::vector<std::wstring> tokens;
    std::wstring compact;
};

struct ChannelPatterns {
    const char* channel;
    std::vector<ChannelAlias> aliases;
};

// Priority order: on equal scores the earlier channel wins.
static const std::vector<ChannelPatterns>& PbrChannels() {
    static const std::vector<std::pair<const char*, std::vector<const wchar_t*>>> table = {
        {"diffuse", {L"_basecolor", L"_base_color", L"basecolor", L"base color", L"_albedo", L"albedo", L"_diffuse",
                     L"diffuse", L"_diff", L"diff", L"_color", L"color", L"_col", L"col", L"_rgb", L"rgb", L"_clr",
                     L"clr", L"_alb", L"alb", L"_dif", L"dif", L"_d"}},
        {"orm", {L"_occlusionroughnessmetallic", L"occlusion roughness metallic",
                 L"_ambientocclusionroughnessmetallic", L"ambient occlusion roughness metallic", L"_orm", L"orm",
                 L"_arm", L"arm"}},
        {"ao", {L"_ambientocclusion", L"ambient occlusion", L"_ambient_occlusion", L"_occlusion", L"occlusion",
                L"_amb_occ", L"amb occ", L"_ao", L"ao"}},
        {"roughness", {L"_roughness", L"roughness", L"_rough", L"rough", L"_rgh", L"rgh", L"_r"}},
        {"glossiness", {L"_glossiness", L"glossiness", L"_smoothness", L"smoothness", L"_gloss", L"gloss", L"_smooth",
                        L"smooth", L"_gls", L"gls", L"_g"}},
        {"metallic", {L"_metallic", L"metallic", L"_metalness", L"metalness", L"_metal", L"metal", L"_met", L"met",
                      L"_mtl", L"mtl", L"_m"}},
        {"normal", {L"_normalgl", L"normalgl", L"normal gl", L"_normaldx", L"normaldx", L"normal dx", L"_normal",
                    L"normal", L"_norm", L"norm", L"_nrm", L"nrm", L"_nor", L"nor", L"_n"}},
        {"displacement", {L"_displacement", L"displacement", L"_displace", L"displace", L"_height", L"height",
                          L"_depth", L"depth", L"_hght", L"hght", L"_hgt", L"hgt", L"_disp", L"disp", L"_dis", L"dis",
                          L"_h"}},
        {"bump", {L"_bump", L"bump", L"_bmp", L"bmp", L"_b"}},
        {"opacity", {L"_opacity", L"opacity", L"_alpha", L"alpha", L"_alphamasked", L"alphamasked", L"_opa", L"opa",
                     L"_alph", L"alph", L"_o"}},
        {"emission", {L"_emissive", L"emissive", L"_emission", L"emission", L"_emisive", L"emisive", L"_illumination",
                      L"illumination", L"_illum", L"illum", L"_emit", L"emit", L"_light", L"light", L"_emi", L"emi",
                      L"_ill", L"ill", L"_lght", L"lght", L"_e"}},
        {"translucency", {L"_translucency", L"translucency", L"_translucent", L"translucent", L"_transmission",
                          L"transmission", L"_transparency", L"transparency", L"_transparancy", L"transparancy",
                          L"_trans", L"trans", L"_trns", L"trns", L"_t"}},
        {"ior", {L"_ior", L"ior", L"_i"}},
        {"specular", {L"_specular", L"specular", L"_spec", L"spec", L"_spc", L"spc", L"_reflection", L"reflection",
                      L"_reflect", L"reflect", L"_refl", L"refl", L"_ref", L"ref", L"_s"}},
    };
    static const std::vector<ChannelPatterns> compiled = [] {
        std::vector<ChannelPatterns> out;
        for (const auto& [channel, aliases] : table) {
            ChannelPatterns patterns{channel, {}};
            for (const wchar_t* alias : aliases) {
                const std::vector<std::wstring> tokens = Tokens(alias);
                patterns.aliases.push_back({tokens, JoinW(tokens, L"")});
            }
            out.push_back(std::move(patterns));
        }
        return out;
    }();
    return compiled;
}

struct ChannelScore {
    int value = 0;
    std::optional<std::pair<size_t, size_t>> span;  // the alias's tokens in the stem; none for a suffix match
};

// Whole-token matches score by length and a final position; a compact suffix
// ("woodBaseColor") scores lower; single letters rarely win.
static std::optional<ChannelScore> AliasScore(const std::vector<std::wstring>& tokens, const ChannelAlias& alias) {
    if (tokens.empty() || alias.tokens.empty()) return std::nullopt;
    const size_t count = alias.tokens.size();
    for (size_t start = 0; start + count <= tokens.size(); ++start) {
        if (!std::equal(alias.tokens.begin(), alias.tokens.end(), tokens.begin() + start)) continue;
        const bool atEnd = start + count == tokens.size();
        const int penalty = alias.compact.size() == 1 ? 80 : 0;
        return ChannelScore{int(count * 100 + alias.compact.size()) + (atEnd ? 25 : 0) - penalty,
                            std::make_pair(start, start + count)};
    }
    const std::wstring stem = JoinW(tokens, L"");
    if (alias.compact.size() >= 4 && stem.ends_with(alias.compact)) return ChannelScore{int(alias.compact.size())};
    return std::nullopt;
}

struct TextureChannel {
    std::string channel;
    std::wstring key;  // the material the texture belongs to
};

static bool IsAnyOf(const std::wstring& token, std::initializer_list<const wchar_t*> words) {
    return std::any_of(words.begin(), words.end(), [&](const wchar_t* word) { return token == word; });
}

static std::optional<TextureChannel> DetectChannel(const fs::path& texture) {
    const std::wstring stem = texture.stem().wstring();
    const std::vector<std::wstring> tokens = Tokens(stem);
    const ChannelPatterns* best = nullptr;
    ChannelScore bestScore;
    for (const ChannelPatterns& candidate : PbrChannels()) {
        for (const ChannelAlias& alias : candidate.aliases) {
            const std::optional<ChannelScore> score = AliasScore(tokens, alias);
            if (!score) continue;
            const bool beats = !best || score->value > bestScore.value ||
                               (score->value == bestScore.value && &candidate == best && score->span > bestScore.span);
            if (beats) {
                best = &candidate;
                bestScore = *score;
            }
        }
    }
    if (!best) return std::nullopt;

    std::vector<std::wstring> keyTokens = tokens;
    if (bestScore.span) {
        auto [start, end] = *bestScore.span;
        // A normal map's GL/DX flavour is part of the channel, not of the material name.
        const bool normal = std::strcmp(best->channel, "normal") == 0 || std::strcmp(best->channel, "bump") == 0;
        auto flavour = [](const std::wstring& t) {
            return IsAnyOf(t, {L"gl", L"ogl", L"opengl", L"open", L"dx", L"directx", L"normalmap", L"normalbump"});
        };
        while (normal && end < tokens.size() && flavour(tokens[end])) ++end;
        while (normal && start > 0 && flavour(tokens[start - 1])) --start;
        keyTokens.assign(tokens.begin(), tokens.begin() + start);
        keyTokens.insert(keyTokens.end(), tokens.begin() + end, tokens.end());
    }
    std::erase_if(keyTokens, [](const std::wstring& t) {
        return IsAnyOf(t, {L"2k", L"4k", L"8k", L"16k", L"1k", L"512", L"1024", L"2048", L"4096", L"8192", L"png",
                           L"jpg", L"jpeg", L"tif", L"tiff", L"exr", L"tga", L"hdr", L"bmp", L"dds", L"tx", L"map",
                           L"tex", L"texture", L"textures"});
    });
    std::wstring key = JoinW(keyTokens, L"_");
    if (key.empty()) key = LowerW(texture.parent_path().filename().wstring());
    if (key.empty()) key = LowerW(stem);
    return TextureChannel{best->channel, key};
}

static int ResolutionScore(const fs::path& texture) {
    static const std::map<std::wstring, int> scores = {
        {L"16k", 16000}, {L"8192", 8192}, {L"8k", 8000}, {L"4096", 4096}, {L"4k", 4000},
        {L"2048", 2048}, {L"2k", 2000}, {L"1024", 1024}, {L"1k", 1000}, {L"512", 512},
    };
    int best = 0;
    for (const std::wstring& token : Tokens(texture.stem().wstring()))
        if (const auto it = scores.find(token); it != scores.end()) best = std::max(best, it->second);
    return best;
}

// One material's maps by channel, in the order they were found.
struct TextureGroup {
    std::wstring name;
    std::vector<std::pair<std::string, fs::path>> channels;

    // The higher-resolution map wins a channel.
    void Add(const std::string& channel, const fs::path& texture) {
        for (auto& [existing, path] : channels) {
            if (existing != channel) continue;
            if (ResolutionScore(texture) > ResolutionScore(path)) path = texture;
            return;
        }
        channels.emplace_back(channel, texture);
    }
    void Merge(const TextureGroup& other) {
        for (const auto& [channel, path] : other.channels) Add(channel, path);
    }
};

// ── Pairing meshes with textures ─────────────────────────────────
// A mesh's key is its stem's tokens ("WoodFloor" -> "wood_floor"), or the
// asset id it shares with its maps: "a1b2c3_LOD0" -> "a1b2c3", or the id that
// ends the name of an asset folder holding a texture folder.

static bool IsAlnumW(const std::wstring& s) {
    return !s.empty() && std::all_of(s.begin(), s.end(), [](wchar_t c) { return std::iswalnum(c) != 0; });
}

static bool IsAsciiAlnum(wchar_t c) { return (c >= L'a' && c <= L'z') || (c >= L'A' && c <= L'Z') || (c >= L'0' && c <= L'9'); }

// "<id>_LOD<n>" with an alphanumeric id of 6 or more characters.
static std::optional<std::wstring> LodAssetId(const std::wstring& stem) {
    const size_t underscore = stem.find(L'_');
    if (underscore == std::wstring::npos || underscore < 6) return std::nullopt;
    if (!std::all_of(stem.begin(), stem.begin() + underscore, IsAsciiAlnum)) return std::nullopt;
    const std::wstring rest = stem.substr(underscore + 1);
    if (rest.size() < 4 || _wcsnicmp(rest.c_str(), L"lod", 3) != 0) return std::nullopt;
    if (!std::all_of(rest.begin() + 3, rest.end(), [](wchar_t c) { return c >= L'0' && c <= L'9'; })) return std::nullopt;
    return LowerW(stem.substr(0, underscore));
}

// The id ending a bundle folder's name: "category label xikkdhjja" or "category_label_xikkdhjja".
static std::optional<std::wstring> FolderAssetId(const std::wstring& folderName) {
    std::wstring name = LowerW(folderName);
    const auto notSpace = [](wchar_t c) { return !std::iswspace(c); };
    name.erase(name.begin(), std::find_if(name.begin(), name.end(), notSpace));
    name.erase(std::find_if(name.rbegin(), name.rend(), notSpace).base(), name.end());
    const bool temporary = name.size() > 3 && name.starts_with(L"tmp") &&
        std::all_of(name.begin() + 3, name.end(), [](wchar_t c) { return (c >= L'a' && c <= L'z') || (c >= L'0' && c <= L'9'); });
    if (temporary) return std::nullopt;
    if (name.find(L' ') != std::wstring::npos) {
        // The name is trimmed, so the last word follows its last whitespace.
        const auto lastSpace = std::find_if(name.rbegin(), name.rend(), [](wchar_t c) { return std::iswspace(c) != 0; });
        const std::wstring tail(lastSpace.base(), name.end());
        if (IsAlnumW(tail) && tail.size() >= 6) return tail;
    }
    std::vector<std::wstring> parts;
    for (size_t start = 0; start <= name.size();) {
        const size_t end = std::min(name.find(L'_', start), name.size());
        if (end > start) parts.push_back(name.substr(start, end - start));
        start = end + 1;
    }
    if (parts.size() >= 2 && IsAlnumW(parts.back()) && parts.back().size() >= 6) return parts.back();
    return std::nullopt;
}

static bool IsTextureFolderName(const std::wstring& name) {
    return IsAnyOf(LowerW(name), {L"tex", L"textures", L"maps", L"map", L"materials"});
}

static bool HasTextureFolder(const fs::path& folder) {
    std::error_code ec;
    for (const wchar_t* sub : {L"tex", L"textures", L"maps", L"map", L"materials"})
        if (fs::is_directory(folder / sub, ec)) return true;
    return false;
}

static std::optional<std::wstring> SharedAssetId(const fs::path& mesh) {
    if (auto id = LodAssetId(mesh.stem().wstring())) return id;
    for (const fs::path& parent : {mesh.parent_path(), mesh.parent_path().parent_path()}) {
        const std::optional<std::wstring> id = FolderAssetId(parent.filename().wstring());
        if (id && HasTextureFolder(parent)) return id;
    }
    return std::nullopt;
}

static std::wstring MeshKey(const fs::path& mesh) {
    if (auto id = SharedAssetId(mesh)) return *id;
    return JoinW(Tokens(mesh.stem().wstring()), L"_");
}

// The material's name: the asset id, the folder of a generic "mesh.fbx", else the stem.
static std::wstring MaterialName(const fs::path& mesh) {
    if (auto id = SharedAssetId(mesh)) return *id;
    const std::wstring stem = mesh.stem().wstring();
    if (IsAnyOf(LowerW(stem), {L"mesh", L"model", L"geo", L"geometry", L"asset", L"low", L"high"}))
        return mesh.parent_path().filename().wstring();
    return stem;
}

// A texture's material key belongs to a mesh key when one extends the other by "_...".
static bool StrictMatch(const std::wstring& mesh, const std::wstring& material) {
    if (mesh.empty() || material.empty()) return false;
    return material == mesh || material.starts_with(mesh + L"_") || mesh.starts_with(material + L"_");
}

// Else when one, without underscores and at least 4 long, lies inside the other.
static bool FuzzyMatch(const std::wstring& mesh, const std::wstring& material) {
    if (StrictMatch(mesh, material)) return false;
    std::wstring shorter = mesh, longer = material;
    std::erase(shorter, L'_');
    std::erase(longer, L'_');
    if (longer.size() < shorter.size()) std::swap(shorter, longer);
    return shorter.size() >= 4 && longer.find(shorter) != std::wstring::npos;
}

// The folder that owns a mesh: its own, or the one above a texture folder.
static fs::path AssetRoot(const fs::path& mesh) {
    const fs::path parent = mesh.parent_path();
    return IsTextureFolderName(parent.filename().wstring()) ? parent.parent_path() : parent;
}

static bool IsUnder(const fs::path& path, const fs::path& root) {
    auto p = path.begin();
    for (auto r = root.begin(); r != root.end(); ++r, ++p)
        if (p == path.end() || _wcsicmp(r->c_str(), p->c_str()) != 0) return false;
    return true;
}

// A mesh alone in its asset folder takes the maps in that folder, whatever their names.
static std::vector<std::pair<size_t, TextureGroup>> ColocatedGroups(
    const std::vector<fs::path>& meshes, const std::vector<fs::path>& textures,
    const std::vector<std::optional<TextureChannel>>& detected) {
    std::vector<fs::path> roots;
    std::vector<std::vector<size_t>> members;
    for (size_t m = 0; m < meshes.size(); ++m) {
        const fs::path root = AssetRoot(meshes[m]);
        const auto it = std::find_if(roots.begin(), roots.end(),
                                     [&](const fs::path& r) { return _wcsicmp(r.c_str(), root.c_str()) == 0; });
        if (it == roots.end()) {
            roots.push_back(root);
            members.push_back({m});
        } else {
            members[it - roots.begin()].push_back(m);
        }
    }
    std::vector<std::pair<size_t, TextureGroup>> out;
    for (size_t r = 0; r < roots.size(); ++r) {
        if (members[r].size() != 1) continue;
        const size_t m = members[r].front();
        TextureGroup group{MaterialName(meshes[m]), {}};
        for (size_t t = 0; t < textures.size(); ++t)
            if (detected[t] && IsUnder(textures[t], roots[r])) group.Add(detected[t]->channel, textures[t]);
        if (!group.channels.empty()) out.emplace_back(m, std::move(group));
    }
    return out;
}

// One texture group per mesh, or none: each texture goes to the longest mesh
// key it strictly matches (else fuzzily), sole meshes add the maps of their
// asset folder, and meshes with the same key share the first group of that key.
static std::vector<std::optional<TextureGroup>> MatchTextures(const std::vector<fs::path>& meshes,
                                                              const std::vector<fs::path>& textures) {
    std::vector<std::wstring> keys;
    for (const fs::path& mesh : meshes) keys.push_back(MeshKey(mesh));
    std::vector<std::optional<TextureChannel>> detected;
    for (const fs::path& texture : textures) detected.push_back(DetectChannel(texture));

    std::vector<std::optional<TextureGroup>> groups(meshes.size());
    std::vector<size_t> order;  // meshes in the order their groups were made
    auto groupOf = [&](size_t m) -> TextureGroup& {
        if (!groups[m]) {
            groups[m] = TextureGroup{MaterialName(meshes[m]), {}};
            order.push_back(m);
        }
        return *groups[m];
    };
    for (size_t t = 0; t < textures.size(); ++t) {
        if (!detected[t]) continue;
        std::optional<size_t> strict, fuzzy;
        size_t strictLength = 0, fuzzyLength = 0;
        for (size_t m = 0; m < meshes.size(); ++m) {
            if (StrictMatch(keys[m], detected[t]->key)) {
                if (keys[m].size() > strictLength) {
                    strict = m;
                    strictLength = keys[m].size();
                }
            } else if (FuzzyMatch(keys[m], detected[t]->key) && keys[m].size() > fuzzyLength) {
                fuzzy = m;
                fuzzyLength = keys[m].size();
            }
        }
        if (const std::optional<size_t> mesh = strict ? strict : fuzzy) groupOf(*mesh).Add(detected[t]->channel, textures[t]);
    }
    for (auto& [m, group] : ColocatedGroups(meshes, textures, detected)) {
        if (groups[m]) groups[m]->Merge(group);
        else groupOf(m) = std::move(group);
    }

    std::map<std::wstring, TextureGroup> byKey;
    for (size_t m : order) {
        if (groups[m]->channels.empty()) continue;
        if (auto it = byKey.find(keys[m]); it != byKey.end()) it->second.Merge(*groups[m]);
        else byKey.emplace(keys[m], *groups[m]);
    }
    std::vector<std::optional<TextureGroup>> shared(meshes.size());
    for (size_t m = 0; m < meshes.size(); ++m)
        if (const auto it = byKey.find(keys[m]); it != byKey.end()) shared[m] = it->second;
    return shared;
}

// ── smart_import ─────────────────────────────────────────────────

static const Extensions kMeshExtensions = {
    L".max", L".fbx", L".obj", L".3ds", L".dae", L".dwg", L".dxf", L".stl", L".ply", L".abc", L".usd",
    L".usda", L".usdc", L".usdz", L".glb", L".gltf", L".skp", L".ifc", L".step", L".stp", L".iges", L".igs",
};
static const Extensions kUsdExtensions = {L".usd", L".usda", L".usdc", L".usdz"};
static const Extensions kImageExtensions = {
    L".png", L".jpg", L".jpeg", L".tif", L".tiff", L".exr", L".tga", L".hdr", L".bmp", L".dds", L".tx",
};

// Whether a "name_LOD<n>" stem is LOD0; none without a LOD suffix.
static std::optional<bool> IsLod0(const std::wstring& stem) {
    size_t digits = stem.size();
    while (digits > 0 && stem[digits - 1] >= L'0' && stem[digits - 1] <= L'9') --digits;
    if (digits == stem.size() || digits < 4 || _wcsnicmp(stem.c_str() + digits - 4, L"_lod", 4) != 0)
        return std::nullopt;
    return std::all_of(stem.begin() + digits, stem.end(), [](wchar_t c) { return c == L'0'; });
}

// Comma-separated globs; blanks and a bare "*" (everything) drop out.
static std::vector<std::wstring> Patterns(const std::string& text) {
    std::vector<std::wstring> out;
    const std::wstring all = Utf8ToWide(text);
    for (size_t start = 0; start <= all.size();) {
        const size_t end = std::min(all.find(L',', start), all.size());
        std::wstring part = all.substr(start, end - start);
        const auto notSpace = [](wchar_t c) { return !std::iswspace(c); };
        part.erase(part.begin(), std::find_if(part.begin(), part.end(), notSpace));
        part.erase(std::find_if(part.rbegin(), part.rend(), notSpace).base(), part.end());
        if (!part.empty() && part != L"*") out.push_back(part);
        start = end + 1;
    }
    return out;
}

static bool MatchesAny(const std::wstring& name, const std::vector<std::wstring>& patterns) {
    return std::any_of(patterns.begin(), patterns.end(), [&](const std::wstring& p) { return Glob(name, p); });
}

static bool SceneHasNamePrefix(const std::wstring& prefix) {
    for (INode* node : AllNodes()) {
        const MCHAR* name = node->GetName();
        if (wcslen(name) >= prefix.size() && _wcsnicmp(name, prefix.c_str(), prefix.size()) == 0) return true;
    }
    return false;
}

// Moves the new top-level nodes so their pivots' average lands on (x, y).
static void PlaceOnGrid(const std::vector<Node>& nodes, double x, double y) {
    std::vector<INode*> roots;
    for (const Node& n : nodes) {
        INode* node = HandlerHelpers::FindNodeByHandle(n.handle);
        if (InScene(node) && node->GetParentNode()->IsRootNode()) roots.push_back(node);
    }
    if (roots.empty()) return;
    const TimeValue t = Now();
    Point3 center(0, 0, 0);
    for (INode* root : roots) center += root->GetNodeTM(t).GetTrans();
    center /= float(roots.size());
    const Point3 shift(float(x) - center.x, float(y) - center.y, 0.0f);
    Matrix3 world;
    world.IdentityMatrix();
    NoAnimate noAnimate;
    for (INode* root : roots) root->Move(t, world, shift, FALSE, TRUE, PIV_NONE, TRUE);
}

static nb::dict AssetEntry(const fs::path& file, const std::string& reason) {
    nb::dict entry;
    entry["path"] = PathText(file);
    entry["name"] = Text(file.stem().wstring());
    if (!reason.empty()) entry["reason"] = reason;
    return entry;
}

static nb::dict Textures(const std::optional<TextureGroup>& group) {
    nb::dict out;
    if (group)
        for (const auto& [channel, path] : group->channels) out[channel.c_str()] = PathText(path);
    return out;
}

// Mesh files, filtered by LOD and name; NotFound when none is left.
static std::vector<fs::path> MeshFiles(const fs::path& root, bool recursive, bool lod0, const std::string& include,
                                       const std::string& exclude, size_t& lodSkipped, size_t& filteredOut) {
    std::vector<fs::path> meshes = Scan(root, recursive, kMeshExtensions);
    if (meshes.empty())
        throw NotFound("no mesh files in '" + PathText(root) + "' (.max, .fbx, .obj, .3ds, .dae, .dwg, .dxf, .stl, .ply, "
                       ".abc, .usd*, .glb, .gltf, .skp, .ifc, .step, .stp, .iges, .igs)");
    if (lod0) {
        lodSkipped = std::erase_if(meshes, [](const fs::path& p) { return IsLod0(p.stem().wstring()) == false; });
        if (meshes.empty())
            throw NotFound("no mesh files left after lod_filter='lod0' (" + std::to_string(lodSkipped) +
                           " LOD1+ files skipped); pass lod_filter='all'");
    }
    const std::vector<std::wstring> includes = Patterns(include), excludes = Patterns(exclude);
    filteredOut = std::erase_if(meshes, [&](const fs::path& p) {
        const std::wstring stem = p.stem().wstring();
        return (!includes.empty() && !MatchesAny(stem, includes)) || MatchesAny(stem, excludes);
    });
    if (meshes.empty())
        throw NotFound("no mesh file stem matches name_pattern='" + include + "' / exclude_pattern='" + exclude + "' (" +
                       std::to_string(filteredOut) + " filtered out)");
    return meshes;
}

static nb::dict SmartImport(const fs::path& folder, const std::optional<fs::path>& textureFolder, bool recursive,
                            double gridSpacing, const std::string& lodFilter, const std::string& namePattern,
                            const std::string& excludePattern) {
    RequireMain();
    const fs::path root = RequireFolder(folder, "folder");
    const std::string lod = Lower(lodFilter);
    if (lod != "lod0" && lod != "all")
        throw BadParam("lod_filter must be 'lod0' (only *_LOD0 and untagged meshes) or 'all', not '" + lodFilter + "'");
    const fs::path textureRoot = textureFolder ? RequireFolder(*textureFolder, "texture folder") : root;
    size_t lodSkipped = 0, filteredOut = 0;
    const std::vector<fs::path> meshes =
        MeshFiles(root, recursive, lod == "lod0", namePattern, excludePattern, lodSkipped, filteredOut);

    // Only meshes that bring no materials of their own get textures: .max and USD keep theirs.
    std::vector<fs::path> eligible;
    std::vector<size_t> eligibleIndex;
    for (size_t i = 0; i < meshes.size(); ++i) {
        if (Extension(meshes[i]) == L".max" || HasExtension(meshes[i], kUsdExtensions)) continue;
        eligible.push_back(meshes[i]);
        eligibleIndex.push_back(i);
    }
    const std::vector<std::optional<TextureGroup>> matched =
        MatchTextures(eligible, Scan(textureRoot, recursive, kImageExtensions));
    std::vector<std::optional<TextureGroup>> groups(meshes.size());
    for (size_t k = 0; k < eligible.size(); ++k) groups[eligibleIndex[k]] = matched[k];

    const size_t columns = std::max<size_t>(1, size_t(std::ceil(std::sqrt(double(meshes.size())))));
    nb::list imported, skipped, failed;
    for (size_t i = 0; i < meshes.size(); ++i) {
        const fs::path& mesh = meshes[i];
        if (SceneHasNamePrefix(mesh.stem().wstring())) {
            skipped.append(AssetEntry(mesh, "an object named " + Text(mesh.stem().wstring()) + "* is in the scene"));
            continue;
        }
        std::vector<Node> nodes;
        try {
            nodes = Extension(mesh) == L".max" ? MergeNodes(mesh, {}, MERGE_DUPS_RENAME, MERGE_DUP_MTL_USE_SCENE)
                                               : ImportNodes(mesh);
        } catch (const std::exception& e) {
            failed.append(AssetEntry(mesh, e.what()));
            continue;
        }
        // USD brings its MaterialX materials from a sibling .mtlx.
        std::optional<fs::path> mtlx;
        if (HasExtension(mesh, kUsdExtensions)) {
            fs::path candidate = fs::path(mesh).replace_extension(L".mtlx");
            std::error_code ec;
            if (fs::is_regular_file(candidate, ec)) {
                try {
                    const std::vector<Node> more = ImportNodes(candidate);
                    nodes.insert(nodes.end(), more.begin(), more.end());
                    mtlx = candidate;
                } catch (const std::exception& e) {
                    failed.append(AssetEntry(candidate, e.what()));
                }
            }
        }
        PlaceOnGrid(nodes, double(i % columns) * gridSpacing, double(i / columns) * gridSpacing);
        nb::dict entry = AssetEntry(mesh, "");
        entry["nodes"] = nodes;
        entry["textures"] = Textures(groups[i]);
        entry["material_name"] = groups[i] ? nb::object(nb::str(Text(groups[i]->name).c_str())) : nb::none();
        entry["mtlx"] = mtlx ? nb::object(nb::str(PathText(*mtlx).c_str())) : nb::none();
        imported.append(entry);
    }
    nb::dict report;
    report["imported"] = imported;
    report["skipped"] = skipped;
    report["failed"] = failed;
    report["lod_skipped"] = lodSkipped;
    report["filtered_out"] = filteredOut;
    return report;
}

// ── Binding ──────────────────────────────────────────────────────

void BindFiles(nb::module_& m) {
    m.def("scene_file", &SceneFile, "Path of the open .max file, or None while the scene is untitled.");
    m.def("scene_info", &SceneInfo,
          "The open scene: {'file', 'unsaved', 'objects', 'faces'}, faces of the evaluated meshes, triangulated.");
    m.def("hold", &HoldScene, "Save the scene to the hold buffer (Edit > Hold) for fetch().");
    m.def("fetch", &FetchScene,
          "Restore what hold() held (Edit > Fetch), dropping the changes since; refuses a hold made elsewhere or "
          "for another scene.");
    m.def("reset", &ResetScene,
          "Reset to an empty scene (File > Reset); refuses while the scene has work that was never saved.");
    m.def("save", &SaveScene,
          "Save the scene to its own file and return the path; an untitled scene raises (never Save As).");
    m.def("file_info", &FileInfoOf, "path"_a, "objects"_a = false, "classes"_a = false,
          "A .max file's metadata without opening it: {'path', 'size', 'created', 'modified' (UTC), 'title', "
          "'author', ...}, plus 'objects' (names) and 'classes' (plugin classes by kind) when asked. A list of paths "
          "gives a list of reports; an unreadable file reports its 'error'.");
    m.def("search_max_files", &SearchMaxFiles, "folder"_a, "pattern"_a = "*", "recursive"_a = true,
          "The .max files under folder with objects named like pattern: [{'path', 'objects', 'names'}], "
          "search_max_files('D:/lib', 'chair*'); then merge(hit['path'], hit['names']).");
    m.def("merge", &MergeFile, "path"_a, "names"_a = nb::none(), "duplicates"_a = "rename", "materials"_a = "rename",
          "Merge a .max file's objects (all, or names) and return the new nodes. Name clashes: duplicates='rename', "
          "'skip', 'merge' (keep both) or 'delete_old'; materials='rename', 'scene' or 'merged'.");
    m.def("import_file", &ImportFile, "path"_a,
          "Import a file (FBX, OBJ, USD, ...) with its importer's defaults, no dialogs, and return the new nodes.");
    m.def("smart_import", &SmartImport, "folder"_a, "texture_folder"_a = nb::none(), "recursive"_a = true,
          "grid_spacing"_a = 200.0, "lod_filter"_a = "lod0", "name_pattern"_a = "", "exclude_pattern"_a = "",
          "Import the mesh files under folder onto an XY grid, pairing each with its textures by name: {'imported': "
          "[{'path', 'name', 'nodes', 'textures': {channel: path}, 'material_name', 'mtlx'}], 'skipped', 'failed', "
          "'lod_skipped', 'filtered_out'}. Skips a file when an object named like its stem is in the scene.");
}

} // namespace MX_ABI_TAG
} // namespace mx
