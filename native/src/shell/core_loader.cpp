// The MCP_HOT_CORE loader (core_loader.h). Shell code; no Max SDK.
#include "mcp_bridge/core_loader.h"

#include <filesystem>
#include <fstream>
#include <iterator>
#include <vector>

namespace McpCoreLoader {

namespace {

std::string ErrorText(DWORD code) {
    wchar_t* buffer = nullptr;
    const DWORD length = FormatMessageW(
        FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS, nullptr, code,
        0, reinterpret_cast<LPWSTR>(&buffer), 0, nullptr);
    std::wstring text = length && buffer ? std::wstring(buffer, length) : std::wstring();
    if (buffer) LocalFree(buffer);
    while (!text.empty() && (text.back() == L'\r' || text.back() == L'\n' || text.back() == L' ' || text.back() == L'.'))
        text.pop_back();
    return (text.empty() ? std::string("Windows error") : ToUtf8(text)) + " (error " + std::to_string(code) + ")";
}

std::wstring WithExtension(const std::wstring& path, const wchar_t* extension) {
    const size_t slash = path.find_last_of(L"\\/");
    const size_t dot = path.find_last_of(L'.');
    const bool hasExtension = dot != std::wstring::npos && (slash == std::wstring::npos || dot > slash);
    return (hasExtension ? path.substr(0, dot) : path) + extension;
}

bool Exists(const std::wstring& path) {
    const DWORD attributes = GetFileAttributesW(path.c_str());
    return attributes != INVALID_FILE_ATTRIBUTES && !(attributes & FILE_ATTRIBUTE_DIRECTORY);
}

void DeleteQuietly(const std::wstring& path) {
    if (!path.empty()) DeleteFileW(path.c_str());
}

bool Fail(Failure& failure, const char* code, const std::string& message) {
    failure.code = code;
    failure.message = message;
    return false;
}

// While it lives, the loader reports failures on this thread (a missing
// import, a damaged image) as errors instead of a modal system box, which on
// a pipe worker would wait for a click nobody sees.
class QuietLoaderErrors {
public:
    QuietLoaderErrors() { set_ = SetThreadErrorMode(SEM_FAILCRITICALERRORS | SEM_NOOPENFILEERRORBOX, &previous_); }
    ~QuietLoaderErrors() {
        if (set_) SetThreadErrorMode(previous_, nullptr);
    }
    QuietLoaderErrors(const QuietLoaderErrors&) = delete;
    QuietLoaderErrors& operator=(const QuietLoaderErrors&) = delete;

private:
    DWORD previous_ = 0;
    BOOL set_ = FALSE;
};

}  // namespace

std::string ToUtf8(const std::wstring& text) {
    if (text.empty()) return {};
    const int length = WideCharToMultiByte(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), nullptr, 0,
                                           nullptr, nullptr);
    std::string out(static_cast<size_t>(length > 0 ? length : 0), '\0');
    if (length > 0)
        WideCharToMultiByte(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), out.data(), length, nullptr,
                            nullptr);
    return out;
}

std::wstring FromUtf8(const std::string& text) {
    if (text.empty()) return {};
    const int length = MultiByteToWideChar(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), nullptr, 0);
    std::wstring out(static_cast<size_t>(length > 0 ? length : 0), L'\0');
    if (length > 0) MultiByteToWideChar(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), out.data(), length);
    return out;
}

std::wstring DefaultShadowDirectory() {
    wchar_t buffer[MAX_PATH + 1] = {};
    const DWORD length = GetTempPathW(MAX_PATH + 1, buffer);
    std::wstring temp = length && length <= MAX_PATH ? std::wstring(buffer, length) : std::wstring(L".\\");
    return Join(temp, L"3dsmax-mcp\\core");
}

std::wstring ModuleDirectory(const void* address) {
    HMODULE module = nullptr;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                            static_cast<LPCWSTR>(address), &module))
        return {};
    std::wstring path(MAX_PATH, L'\0');
    for (;;) {
        const DWORD length = GetModuleFileNameW(module, path.data(), static_cast<DWORD>(path.size()));
        if (length == 0) return {};
        if (length < path.size()) {
            path.resize(length);
            break;
        }
        path.resize(path.size() * 2);
    }
    const size_t slash = path.find_last_of(L"\\/");
    return slash == std::wstring::npos ? std::wstring() : path.substr(0, slash);
}

std::wstring Environment(const wchar_t* name) {
    const DWORD length = GetEnvironmentVariableW(name, nullptr, 0);
    if (length == 0) return {};
    std::wstring value(length, L'\0');
    const DWORD written = GetEnvironmentVariableW(name, value.data(), length);
    value.resize(written < length ? written : 0);
    return value;
}

std::wstring ReadText(const std::wstring& path) {
    std::ifstream file(std::filesystem::path(path), std::ios::binary);
    if (!file) return {};
    const std::string bytes((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
    if (bytes.size() >= 2 && static_cast<unsigned char>(bytes[0]) == 0xFF && static_cast<unsigned char>(bytes[1]) == 0xFE)
        return std::wstring(reinterpret_cast<const wchar_t*>(bytes.data() + 2), (bytes.size() - 2) / sizeof(wchar_t));
    const bool bom = bytes.size() >= 3 && static_cast<unsigned char>(bytes[0]) == 0xEF &&
                     static_cast<unsigned char>(bytes[1]) == 0xBB && static_cast<unsigned char>(bytes[2]) == 0xBF;
    return FromUtf8(bom ? bytes.substr(3) : bytes);
}

std::wstring FullPath(const std::wstring& path) {
    const DWORD length = GetFullPathNameW(path.c_str(), 0, nullptr, nullptr);
    if (length == 0) return path;
    std::wstring full(length, L'\0');
    const DWORD written = GetFullPathNameW(path.c_str(), length, full.data(), nullptr);
    if (written == 0 || written >= length) return path;
    full.resize(written);
    return full;
}

void SweepShadows(const std::wstring& directory, const std::wstring& keep) {
    const std::wstring keepPdb = keep.empty() ? std::wstring() : WithExtension(keep, L".pdb");
    std::vector<std::wstring> libraries, symbols;
    WIN32_FIND_DATAW entry{};
    HANDLE find = FindFirstFileW(Join(directory, L"mcp_core-*").c_str(), &entry);
    if (find == INVALID_HANDLE_VALUE) return;
    do {
        if (entry.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
        const std::wstring name = entry.cFileName;
        if (!IsShadowName(name)) continue;
        const std::wstring path = Join(directory, name);
        if (_wcsicmp(path.c_str(), keep.c_str()) == 0 || _wcsicmp(path.c_str(), keepPdb.c_str()) == 0) continue;
        const bool library = _wcsicmp(name.substr(name.size() - 4).c_str(), L".dll") == 0;
        (library ? libraries : symbols).push_back(path);
    } while (FindNextFileW(find, &entry));
    FindClose(find);
    // A copy another Max still maps refuses deletion; keep its symbols then.
    for (const auto& path : libraries) DeleteFileW(path.c_str());
    for (const auto& path : symbols)
        if (!Exists(WithExtension(path, L".dll"))) DeleteFileW(path.c_str());
}

bool Load(const std::wstring& source, const std::wstring& directory, const McpCoreExpectation& expected,
          Module& out, Failure& failure) {
    out = Module{};
    const std::string sourceText = ToUtf8(source);
    if (!Exists(source)) return Fail(failure, "CORE_LOAD_FAILED", "the native core was not found at " + sourceText);

    std::error_code created;
    std::filesystem::create_directories(std::filesystem::path(directory), created);

    // A fresh name per load: the previous copy stays mapped until the swap.
    Module module;
    module.source = source;
    const unsigned long pid = GetCurrentProcessId();
    const unsigned long long tick = GetTickCount64();
    DWORD copyError = ERROR_FILE_EXISTS;
    for (unsigned attempt = 0; attempt < 64 && copyError == ERROR_FILE_EXISTS; ++attempt) {
        const std::wstring candidate = Join(directory, ShadowName(pid, tick + attempt, L".dll"));
        if (CopyFileW(source.c_str(), candidate.c_str(), TRUE)) {
            // CopyFile keeps attributes; a read-only copy could never be deleted.
            SetFileAttributesW(candidate.c_str(), FILE_ATTRIBUTE_NORMAL);
            module.shadow = candidate;
            copyError = ERROR_SUCCESS;
        } else {
            copyError = GetLastError();
        }
    }
    if (module.shadow.empty())
        return Fail(failure, "CORE_LOAD_FAILED", "could not copy " + sourceText + " into " + ToUtf8(directory) + ": " +
                                                     ErrorText(copyError));

    // Symbols for debugging the copy after the original's .pdb is rebuilt.
    const std::wstring sourcePdb = WithExtension(source, L".pdb");
    if (Exists(sourcePdb)) {
        const std::wstring shadowPdb = WithExtension(module.shadow, L".pdb");
        if (CopyFileW(sourcePdb.c_str(), shadowPdb.c_str(), FALSE)) {
            SetFileAttributesW(shadowPdb.c_str(), FILE_ATTRIBUTE_NORMAL);
            module.shadowPdb = shadowPdb;
        }
    }

    const auto discard = [&](const char* code, const std::string& message) {
        Unload(module);
        return Fail(failure, code, message);
    };

    // Mapping runs the core's static initializers; the entry returns its table.
    DWORD loadError = ERROR_SUCCESS;
    McpCoreApiFn entry = nullptr;
    {
        QuietLoaderErrors quiet;
        module.handle = LoadLibraryW(module.shadow.c_str());
        if (!module.handle) loadError = GetLastError();
        if (module.handle) entry = reinterpret_cast<McpCoreApiFn>(GetProcAddress(module.handle, MCP_CORE_API_EXPORT));
        if (entry) {
            try {
                module.api = entry();
            } catch (...) {
                module.api = nullptr;
            }
        }
    }
    if (!module.handle)
        return discard("CORE_LOAD_FAILED", "LoadLibrary failed for " + ToUtf8(module.shadow) + " (a copy of " +
                                               sourceText + "): " + ErrorText(loadError));
    if (!entry)
        return discard("CORE_LOAD_FAILED", sourceText + " does not export " MCP_CORE_API_EXPORT
                                                        "; it is not an mcp_core build");
    if (!module.api) return discard("CORE_LOAD_FAILED", sourceText + ": " MCP_CORE_API_EXPORT " returned no table");
    if (const std::string mismatch = McpCoreMismatch(*module.api, expected); !mismatch.empty())
        return discard("CORE_VERSION_MISMATCH", mismatch + " (" + sourceText + ")");
    if (!McpCoreTableComplete(*module.api))
        return discard("CORE_LOAD_FAILED", sourceText + ": the core's entry points are incomplete");

    out = std::move(module);
    module.handle = nullptr;  // owned by `out` now
    return true;
}

void Unload(Module& module) {
    if (module.handle) FreeLibrary(module.handle);
    DeleteQuietly(module.shadow);
    DeleteQuietly(module.shadowPdb);
    module = Module{};
}

}  // namespace McpCoreLoader
