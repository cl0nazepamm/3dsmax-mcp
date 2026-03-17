#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/bridge_gup.h"

#include <triobj.h>
#include <objbase.h>
#include <propidl.h>
#include <propkey.h>
#include <future>
#include <set>

using json = nlohmann::json;
using namespace HandlerHelpers;

// ── OLE Structured Storage metadata reader ──────────────────
// .max files are OLE compound documents. We can read file properties
// (title, author, comments, dates) without loading the scene.
static json ReadOLEMetadata(const std::wstring& filePath) {
    json meta;

    // Get file size
    WIN32_FILE_ATTRIBUTE_DATA fad;
    if (GetFileAttributesExW(filePath.c_str(), GetFileExInfoStandard, &fad)) {
        ULARGE_INTEGER sz;
        sz.HighPart = fad.nFileSizeHigh;
        sz.LowPart = fad.nFileSizeLow;
        meta["fileSizeBytes"] = sz.QuadPart;
        double mb = sz.QuadPart / (1024.0 * 1024.0);
        char buf[32];
        snprintf(buf, sizeof(buf), "%.1f MB", mb);
        meta["fileSize"] = buf;

        // File dates
        SYSTEMTIME st;
        FileTimeToSystemTime(&fad.ftCreationTime, &st);
        char dateBuf[64];
        snprintf(dateBuf, sizeof(dateBuf), "%04d-%02d-%02d %02d:%02d:%02d",
                 st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond);
        meta["created"] = dateBuf;

        FileTimeToSystemTime(&fad.ftLastWriteTime, &st);
        snprintf(dateBuf, sizeof(dateBuf), "%04d-%02d-%02d %02d:%02d:%02d",
                 st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond);
        meta["modified"] = dateBuf;
    }

    // Open as OLE structured storage
    IStorage* pStorage = nullptr;
    HRESULT hr = StgOpenStorage(filePath.c_str(), nullptr,
                                STGM_READ | STGM_SHARE_DENY_WRITE,
                                nullptr, 0, &pStorage);
    if (FAILED(hr) || !pStorage) {
        meta["oleError"] = "Could not open as structured storage";
        return meta;
    }

    // Read summary info properties
    IPropertySetStorage* pPropSetStg = nullptr;
    hr = pStorage->QueryInterface(IID_IPropertySetStorage, (void**)&pPropSetStg);
    if (SUCCEEDED(hr) && pPropSetStg) {
        IPropertyStorage* pPropStg = nullptr;
        hr = pPropSetStg->Open(FMTID_SummaryInformation, STGM_READ | STGM_SHARE_EXCLUSIVE, &pPropStg);
        if (SUCCEEDED(hr) && pPropStg) {
            // Read title, subject, author, comments
            PROPSPEC propSpec[4];
            PROPVARIANT propVar[4];
            for (int i = 0; i < 4; i++) {
                propSpec[i].ulKind = PRSPEC_PROPID;
                PropVariantInit(&propVar[i]);
            }
            propSpec[0].propid = PIDSI_TITLE;
            propSpec[1].propid = PIDSI_SUBJECT;
            propSpec[2].propid = PIDSI_AUTHOR;
            propSpec[3].propid = PIDSI_COMMENTS;

            hr = pPropStg->ReadMultiple(4, propSpec, propVar);
            if (SUCCEEDED(hr)) {
                auto readStr = [](const PROPVARIANT& pv) -> std::string {
                    if (pv.vt == VT_LPSTR && pv.pszVal) return pv.pszVal;
                    if (pv.vt == VT_LPWSTR && pv.pwszVal) return WideToUtf8(pv.pwszVal);
                    return "";
                };
                std::string title = readStr(propVar[0]);
                std::string subject = readStr(propVar[1]);
                std::string author = readStr(propVar[2]);
                std::string comments = readStr(propVar[3]);
                if (!title.empty()) meta["title"] = title;
                if (!subject.empty()) meta["subject"] = subject;
                if (!author.empty()) meta["author"] = author;
                if (!comments.empty()) meta["comments"] = comments;
            }
            for (int i = 0; i < 4; i++) PropVariantClear(&propVar[i]);
            pPropStg->Release();
        }

        // Read doc summary (custom properties like "Object Count")
        pPropStg = nullptr;
        hr = pPropSetStg->Open(FMTID_DocSummaryInformation, STGM_READ | STGM_SHARE_EXCLUSIVE, &pPropStg);
        if (SUCCEEDED(hr) && pPropStg) {
            // Enumerate all properties
            IEnumSTATPROPSTG* pEnum = nullptr;
            hr = pPropStg->Enum(&pEnum);
            if (SUCCEEDED(hr) && pEnum) {
                STATPROPSTG stat;
                while (pEnum->Next(1, &stat, nullptr) == S_OK) {
                    if (stat.lpwstrName) {
                        PROPSPEC ps;
                        ps.ulKind = PRSPEC_LPWSTR;
                        ps.lpwstr = stat.lpwstrName;
                        PROPVARIANT pv;
                        PropVariantInit(&pv);
                        if (SUCCEEDED(pPropStg->ReadMultiple(1, &ps, &pv))) {
                            std::string key = WideToUtf8(stat.lpwstrName);
                            if (pv.vt == VT_LPSTR && pv.pszVal)
                                meta["docProperties"][key] = std::string(pv.pszVal);
                            else if (pv.vt == VT_LPWSTR && pv.pwszVal)
                                meta["docProperties"][key] = WideToUtf8(pv.pwszVal);
                            else if (pv.vt == VT_I4)
                                meta["docProperties"][key] = pv.lVal;
                            else if (pv.vt == VT_R8)
                                meta["docProperties"][key] = pv.dblVal;
                            PropVariantClear(&pv);
                        }
                        CoTaskMemFree(stat.lpwstrName);
                    }
                }
                pEnum->Release();
            }
            pPropStg->Release();
        }
        pPropSetStg->Release();
    }

    pStorage->Release();
    return meta;
}

// ── OLE ClassDirectory3 reader ───────────────────────────────
// .max files use a chunk-based binary format inside OLE streams.
// Each chunk: uint16 type + int32 size (6 byte header).
// ClassDirectory3 contains container chunks, each with sub-chunks:
//   0x2042 = class name (UTF-16 string)
//   0x2060 = DllIndex(uint32) + ClassID(uint64) + SuperClassID(uint32)
// May be gzip-compressed (starts with 0x1F8B).

static std::vector<uint8_t> ReadStreamBytes(IStream* pStream) {
    std::vector<uint8_t> data;
    STATSTG stat;
    pStream->Stat(&stat, STATFLAG_NONAME);
    ULONG totalSize = (ULONG)stat.cbSize.QuadPart;
    data.resize(totalSize);
    ULONG bytesRead = 0;
    pStream->Read(data.data(), totalSize, &bytesRead);
    data.resize(bytesRead);
    return data;
}

// Skip decompression for now — Max 2026 files are not gzip-compressed.
// Older files (pre-2015) may be, but we target Max 2026.

struct MaxClassEntry {
    uint32_t dllIndex;
    uint64_t classID;
    uint32_t superClassID;
    std::wstring name;
};

static std::vector<MaxClassEntry> ParseClassChunks(const uint8_t* data, size_t size) {
    std::vector<MaxClassEntry> entries;

    // Parse top-level chunks — each is a container for one class entry
    size_t offset = 0;
    while (offset + 6 <= size) {
        uint16_t chunkType = *(uint16_t*)(data + offset);
        int32_t chunkSizeRaw = *(int32_t*)(data + offset + 2);
        size_t headerSize = 6;
        size_t chunkSize;

        if (chunkSizeRaw == 0 && offset + 14 <= size) {
            // Extended size: 8 bytes after the initial header
            int64_t extSize = *(int64_t*)(data + offset + 6);
            chunkSize = (size_t)(extSize & 0x7FFFFFFFFFFFFFFF);
            headerSize = 14;
        } else if (chunkSizeRaw < 0) {
            // Container chunk — size is absolute (includes children)
            chunkSize = (size_t)(chunkSizeRaw & 0x7FFFFFFF);
        } else {
            chunkSize = (size_t)chunkSizeRaw;
        }

        if (offset + headerSize + chunkSize > size + headerSize) break;

        // Parse sub-chunks inside this container
        const uint8_t* containerData;
        size_t containerSize;
        if (chunkSizeRaw < 0) {
            // Container: children start right after header
            containerData = data + offset + headerSize;
            containerSize = chunkSize - headerSize;
        } else {
            containerData = data + offset + headerSize;
            containerSize = chunkSize;
        }

        MaxClassEntry entry = {};
        size_t subOff = 0;
        while (subOff + 6 <= containerSize) {
            uint16_t subType = *(uint16_t*)(containerData + subOff);
            int32_t subSizeRaw = *(int32_t*)(containerData + subOff + 2);
            size_t subHeaderSize = 6;
            size_t subSize = (size_t)(subSizeRaw & 0x7FFFFFFF);

            if (subOff + subHeaderSize + subSize > containerSize + subHeaderSize) break;

            const uint8_t* subData = containerData + subOff + subHeaderSize;

            if (subType == 0x2042 && subSize >= 2) {
                // Class name — UTF-16LE string
                size_t charCount = subSize / 2;
                entry.name.assign((const wchar_t*)subData, charCount);
                // Strip null terminators
                while (!entry.name.empty() && entry.name.back() == L'\0')
                    entry.name.pop_back();
            } else if (subType == 0x2060 && subSize >= 16) {
                // DllIndex(4) + ClassID(8) + SuperClassID(4)
                entry.dllIndex = *(uint32_t*)subData;
                entry.classID = *(uint64_t*)(subData + 4);
                entry.superClassID = *(uint32_t*)(subData + 12);
            }

            subOff += subHeaderSize + subSize;
        }

        if (!entry.name.empty() || entry.classID != 0) {
            entries.push_back(entry);
        }

        offset += headerSize + (chunkSizeRaw < 0 ? (chunkSize - headerSize) : chunkSize);
        if (chunkSizeRaw < 0) {
            offset = offset; // already advanced correctly
        }
    }

    return entries;
}

static json ReadClassDirectory(const std::wstring& filePath) {
    json classes = json::array();

    IStorage* pStorage = nullptr;
    HRESULT hr = StgOpenStorage(filePath.c_str(), nullptr,
                                STGM_READ | STGM_SHARE_DENY_WRITE,
                                nullptr, 0, &pStorage);
    if (FAILED(hr) || !pStorage) return classes;

    // Open ClassDirectory3 or ClassDirectory stream
    IStream* pStream = nullptr;
    hr = pStorage->OpenStream(L"ClassDirectory3", nullptr, STGM_READ | STGM_SHARE_EXCLUSIVE, 0, &pStream);
    if (FAILED(hr)) {
        hr = pStorage->OpenStream(L"ClassDirectory", nullptr, STGM_READ | STGM_SHARE_EXCLUSIVE, 0, &pStream);
    }
    if (FAILED(hr) || !pStream) {
        pStorage->Release();
        return classes;
    }

    // Read entire stream
    auto rawData = ReadStreamBytes(pStream);
    pStream->Release();
    pStorage->Release();

    if (rawData.empty()) return classes;

    auto& data = rawData;

    // Parse chunk-based format
    auto entries = ParseClassChunks(data.data(), data.size());

    for (auto& entry : entries) {
        std::string category;
        uint32_t sid = entry.superClassID;
        if (sid == 0x10) category = "geometry";
        else if (sid == 0x20) category = "camera";
        else if (sid == 0x30) category = "light";
        else if (sid == 0x40) category = "shape";
        else if (sid == 0x50) category = "helper";
        else if (sid == 0x60) category = "system";
        else if (sid == 0xC00) category = "material";
        else if (sid == 0xC10) category = "texturemap";
        else if (sid == 0xC20) category = "modifier";
        else if (sid == 0xC30) category = "wsModifier";
        else if ((sid & 0xFFF000) == 0x9000) category = "controller";
        else category = "internal";

        if (category != "internal" && category != "system" && !entry.name.empty()) {
            json e;
            e["name"] = WideToUtf8(entry.name.c_str());
            e["category"] = category;
            e["classID"] = entry.classID;
            classes.push_back(e);
        }
    }

    return classes;
}

// Summarize class directory into compact categories
static json SummarizeClassDirectory(const json& classes) {
    json summary;
    std::map<std::string, json> byCategory;

    for (const auto& cls : classes) {
        std::string cat = cls.value("category", "other");
        std::string name = cls.value("name", "");
        if (!byCategory.count(cat)) {
            byCategory[cat] = json::array();
        }
        byCategory[cat].push_back(name);
    }

    for (auto& [cat, names] : byCategory) {
        summary[cat] = {
            {"count", names.size()},
            {"classes", names},
        };
    }
    return summary;
}

// ── native:inspect_max_file ─────────────────────────────────
std::string NativeHandlers::InspectMaxFile(const std::string& params, MCPBridgeGUP* gup) {
    json p = json::parse(params, nullptr, false);
    std::string filePath = p.value("file_path", "");
    bool listObjects = p.value("list_objects", false);
    bool listClasses = p.value("list_classes", false);

    if (filePath.empty()) throw std::runtime_error("file_path is required");

    std::wstring wpath = Utf8ToWide(filePath);

    // Check file exists
    DWORD attrib = GetFileAttributesW(wpath.c_str());
    if (attrib == INVALID_FILE_ATTRIBUTES) {
        throw std::runtime_error("File not found: " + filePath);
    }

    // Step 1: Read OLE metadata (no main thread needed)
    json meta = ReadOLEMetadata(wpath);

    // Step 2: Optionally read class directory (no main thread needed)
    json classInfo = nullptr;
    json rawClasses = nullptr;
    if (listClasses) {
        rawClasses = ReadClassDirectory(wpath);
        if (!rawClasses.empty()) {
            // Check if first entry is debug info
            if (rawClasses[0].contains("_debug")) {
                classInfo = rawClasses[0]; // Return debug info directly
            } else {
                classInfo = SummarizeClassDirectory(rawClasses);
            }
        }
    }

    // Step 3: Optionally list objects using MERGE_LIST_NAMES
    if (listObjects) {
        return gup->GetExecutor().ExecuteSync([&]() -> std::string {
            Interface* ip = GetCOREInterface();

            NameTab nameList;
            int result = ip->MergeFromFile(wpath.c_str(),
                TRUE,           // mergeAll = TRUE (required for MERGE_LIST_NAMES)
                FALSE,          // selMerged
                FALSE,          // refresh
                MERGE_LIST_NAMES, // dupAction = list names only
                &nameList);

            json objects = json::array();
            for (int i = 0; i < nameList.Count(); i++) {
                if (nameList[i]) {
                    objects.push_back(WideToUtf8(nameList[i]));
                }
            }

            json fileInfo;
            fileInfo["filePath"] = filePath;
            fileInfo["metadata"] = meta;
            fileInfo["objectCount"] = objects.size();
            fileInfo["objects"] = objects;
            if (!classInfo.is_null()) fileInfo["classes"] = classInfo;
            return fileInfo.dump();
        });
    }

    // Quick mode — metadata + optional classes (no main thread needed)
    json fileInfo;
    fileInfo["filePath"] = filePath;
    fileInfo["metadata"] = meta;
    if (listClasses) {
        if (!classInfo.is_null()) {
            fileInfo["classes"] = classInfo;
        } else {
            fileInfo["classes"] = json::object();
            fileInfo["classesError"] = "Could not read class directory from file";
        }
    }
    return fileInfo.dump();
}

// ── native:merge_from_file ──────────────────────────────────
std::string NativeHandlers::MergeFromFile(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string filePath = p.value("file_path", "");
        auto objectNames = p.value("object_names", std::vector<std::string>{});
        bool selectMerged = p.value("select_merged", true);
        std::string dupAction = p.value("duplicate_action", "rename");

        if (filePath.empty()) throw std::runtime_error("file_path is required");

        std::wstring wpath = Utf8ToWide(filePath);
        Interface* ip = GetCOREInterface();

        // Determine dup action
        int dupFlag = MERGE_DUPS_RENAME;
        if (dupAction == "skip") dupFlag = MERGE_DUPS_SKIP;
        else if (dupAction == "merge") dupFlag = MERGE_DUPS_MERGE;
        else if (dupAction == "delete_old") dupFlag = MERGE_DUPS_DELOLD;

        // Capture existing node names for diffing
        INode* root = ip->GetRootNode();
        std::vector<INode*> existingNodes;
        CollectNodes(root, existingNodes);
        std::set<std::string> existingNames;
        for (INode* n : existingNodes) {
            existingNames.insert(WideToUtf8(n->GetName()));
        }

        int result;
        if (objectNames.empty()) {
            // Merge all
            result = ip->MergeFromFile(wpath.c_str(),
                TRUE,           // mergeAll
                selectMerged ? TRUE : FALSE,
                TRUE,           // refresh
                dupFlag);
        } else {
            // Selective merge — build NameTab
            NameTab mrgList;
            for (const auto& name : objectNames) {
                std::wstring wname = Utf8ToWide(name);
                mrgList.AddName(wname.c_str());
            }
            result = ip->MergeFromFile(wpath.c_str(),
                TRUE,           // mergeAll must be TRUE when mrgList provided
                selectMerged ? TRUE : FALSE,
                TRUE,           // refresh
                dupFlag,
                &mrgList);
        }

        if (!result) {
            throw std::runtime_error("MergeFromFile failed for: " + filePath);
        }

        // Diff to find newly merged nodes
        std::vector<INode*> afterNodes;
        CollectNodes(ip->GetRootNode(), afterNodes);
        json mergedNames = json::array();
        for (INode* n : afterNodes) {
            std::string nm = WideToUtf8(n->GetName());
            if (existingNames.find(nm) == existingNames.end()) {
                mergedNames.push_back(nm);
            }
        }

        ip->RedrawViews(ip->GetTime());

        json res;
        res["filePath"] = filePath;
        res["mergedCount"] = mergedNames.size();
        res["merged"] = mergedNames;
        res["message"] = "Merged " + std::to_string(mergedNames.size()) + " objects from " + filePath;
        return res.dump();
    });
}

// ── native:batch_file_info ──────────────────────────────────
// Reads OLE metadata from multiple .max files in parallel (no main thread needed)
std::string NativeHandlers::BatchFileInfo(const std::string& params, MCPBridgeGUP* gup) {
    json p = json::parse(params, nullptr, false);
    auto filePaths = p.value("file_paths", std::vector<std::string>{});
    bool listObjects = p.value("list_objects", false);

    if (filePaths.empty()) throw std::runtime_error("file_paths is required");

    // For metadata-only mode, we can parallelize on worker threads
    if (!listObjects) {
        // Launch async tasks for each file
        std::vector<std::future<json>> futures;
        for (const auto& fp : filePaths) {
            futures.push_back(std::async(std::launch::async, [fp]() -> json {
                json fileInfo;
                fileInfo["filePath"] = fp;

                std::wstring wpath = Utf8ToWide(fp);
                DWORD attrib = GetFileAttributesW(wpath.c_str());
                if (attrib == INVALID_FILE_ATTRIBUTES) {
                    fileInfo["error"] = "File not found";
                    return fileInfo;
                }

                // COM init for this thread
                CoInitializeEx(nullptr, COINIT_MULTITHREADED);
                fileInfo["metadata"] = ReadOLEMetadata(wpath);
                CoUninitialize();
                return fileInfo;
            }));
        }

        // Collect results
        json results = json::array();
        for (auto& f : futures) {
            results.push_back(f.get());
        }

        json response;
        response["fileCount"] = results.size();
        response["files"] = results;
        return response.dump();
    }

    // With object listing, we need the main thread for MergeFromFile(MERGE_LIST_NAMES)
    return gup->GetExecutor().ExecuteSync([&filePaths]() -> std::string {
        Interface* ip = GetCOREInterface();
        json results = json::array();

        for (const auto& fp : filePaths) {
            json fileInfo;
            fileInfo["filePath"] = fp;

            std::wstring wpath = Utf8ToWide(fp);
            DWORD attrib = GetFileAttributesW(wpath.c_str());
            if (attrib == INVALID_FILE_ATTRIBUTES) {
                fileInfo["error"] = "File not found";
                results.push_back(fileInfo);
                continue;
            }

            fileInfo["metadata"] = ReadOLEMetadata(wpath);

            // List objects
            NameTab nameList;
            ip->MergeFromFile(wpath.c_str(), TRUE, FALSE, FALSE, MERGE_LIST_NAMES, &nameList);

            json objects = json::array();
            for (int i = 0; i < nameList.Count(); i++) {
                if (nameList[i]) objects.push_back(WideToUtf8(nameList[i]));
            }
            fileInfo["objectCount"] = objects.size();
            fileInfo["objects"] = objects;
            results.push_back(fileInfo);
        }

        json response;
        response["fileCount"] = results.size();
        response["files"] = results;
        return response.dump();
    });
}
