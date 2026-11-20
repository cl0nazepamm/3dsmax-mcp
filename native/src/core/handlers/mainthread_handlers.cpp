#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/core_bridge.h"

#include <tlhelp32.h>
#include <map>
#include <vector>
#include <tuple>
#include <algorithm>

using json = nlohmann::json;
using namespace HandlerHelpers;

// Escape a UTF-8 string for embedding inside a MAXScript double-quoted literal.
static std::string MsStringEscape(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 8);
    for (char c : s) {
        if (c == '\\') out += "\\\\";
        else if (c == '"') out += "\\\"";
        else out += c;
    }
    return out;
}

// Build the MAXScript that enumerates main-thread activity and returns a JSON obj.
static std::string BuildListScript() {
    std::string ms;
    ms += "(\n";
    ms += "  local DQ = bit.intAsChar 34 as string\n";
    ms += "  local BS = bit.intAsChar 92 as string\n";
    ms += "  local NL = bit.intAsChar 10 as string\n";
    ms += "  local CR = bit.intAsChar 13 as string\n";
    ms += "  fn __esc s = (s = s as string; s = substituteString s BS (BS + BS); s = substituteString s DQ (BS + DQ); s = substituteString s NL (BS + \"n\"); s = substituteString s CR \"\"; s)\n";

    // ── redraw-views callbacks (only exposed via a Listener-printing fn) ──
    ms += "  local rf = (getDir #temp) + \"/__mcp_mtdiag.txt\"\n";
    ms += "  local rawcb = \"\"\n";
    ms += "  try (openLog rf mode:\"w\" outputOnly:true; showregisteredRedrawViewsCallbacks(); flushLog(); closeLog(); local f = openFile rf; if f != undefined do (while not (eof f) do (rawcb += (readLine f) + NL); close f)) catch (try(closeLog()) catch())\n";
    ms += "  local rlines = filterString rawcb NL\n";
    ms += "  local rcount = 0\n";
    ms += "  for L in rlines do (if (findString L \"function:\") != undefined do rcount += 1)\n";

    // ── live .NET timers held in globals ──
    ms += "  local tj = \"[\"\n";
    ms += "  local tn = 0\n";
    ms += "  local names = (try (globalVars.gather()) catch (#()))\n";
    ms += "  for n in names do (\n";
    ms += "    local v = undefined\n";
    ms += "    try (v = globalVars.get (n as name)) catch()\n";
    ms += "    if v != undefined and (isKindOf v dotNetObject) do (\n";
    ms += "      local cn = \"\"\n";
    ms += "      try (cn = (v.GetType()).FullName) catch()\n";
    ms += "      if (cn != undefined and (matchPattern cn pattern:\"*Timer*\")) do (\n";
    ms += "        local en = (try ((v.Enabled) as string) catch (\"unknown\"))\n";
    ms += "        local iv = (try ((v.Interval) as string) catch (\"-1\"))\n";
    ms += "        if tn > 0 do tj += \",\"\n";
    ms += "        tj += \"{\" + DQ+\"scope\"+DQ+\":\"+DQ+\"global\"+DQ+\",\"\n";
    ms += "        tj += DQ+\"name\"+DQ+\":\"+DQ+(__esc (n as string))+DQ+\",\"\n";
    ms += "        tj += DQ+\"class\"+DQ+\":\"+DQ+(__esc cn)+DQ+\",\"\n";
    ms += "        tj += DQ+\"enabled\"+DQ+\":\"+(if en == \"true\" then \"true\" else \"false\")+\",\"\n";
    ms += "        tj += DQ+\"intervalMs\"+DQ+\":\"+iv+\"}\"\n";
    ms += "        tn += 1\n";
    ms += "      )\n";
    ms += "    )\n";
    ms += "  )\n";
    ms += "  tj += \"]\"\n";

    // ── general callbacks (event-driven) count ──
    ms += "  local gf = (getDir #temp) + \"/__mcp_mtcb.txt\"\n";
    ms += "  local gcount = 0\n";
    ms += "  try (openLog gf mode:\"w\" outputOnly:true; callbacks.show(); flushLog(); closeLog(); local f2 = openFile gf; if f2 != undefined do (while not (eof f2) do (local ln = readLine f2; if (findString ln \"script:\") != undefined do gcount += 1); close f2)) catch (try(closeLog()) catch())\n";

    // ── state ──
    ms += "  local playing = (try ((isAnimPlaying()) as string) catch (\"unknown\"))\n";
    ms += "  local asr = (try ((classof renderers.activeShade) as string) catch (\"unknown\"))\n";
    ms += "  local rdEnabled = (try ((redrawViewsCallbacksEnabled()) as string) catch (\"false\"))\n";

    // ── assemble JSON ──
    ms += "  local out = \"{\"\n";
    ms += "  out += DQ+\"action\"+DQ+\":\"+DQ+\"list\"+DQ+\",\"\n";
    ms += "  out += DQ+\"redrawCallbacks\"+DQ+\":{\"+DQ+\"enabled\"+DQ+\":\"+(if rdEnabled==\"true\" then \"true\" else \"false\")+\",\"+DQ+\"count\"+DQ+\":\"+(rcount as string)+\",\"+DQ+\"raw\"+DQ+\":\"+DQ+(__esc rawcb)+DQ+\"},\"\n";
    ms += "  out += DQ+\"dotNetTimers\"+DQ+\":\"+tj+\",\"\n";
    ms += "  out += DQ+\"generalCallbackHandlers\"+DQ+\":\"+(gcount as string)+\",\"\n";
    ms += "  out += DQ+\"globalsScanned\"+DQ+\":\"+(names.count as string)+\",\"\n";
    ms += "  out += DQ+\"state\"+DQ+\":{\"+DQ+\"isAnimPlaying\"+DQ+\":\"+(if playing==\"true\" then \"true\" else \"false\")+\",\"+DQ+\"activeShadeRenderer\"+DQ+\":\"+DQ+(__esc asr)+DQ+\"}\"\n";
    ms += "  out += \"}\"\n";
    ms += "  out\n";
    ms += ")\n";
    return ms;
}

// ── native thread -> owning module attribution (action="native_threads") ──
// We run INSIDE 3dsmax.exe, so we can read our own threads' Win32 start
// addresses (an external process can't without SeDebugPrivilege) and map each
// to its owning DLL, then sample per-thread CPU over a short window to show
// which native module is actually burning cycles. This is the closest we can
// get to "see the C++ side" — SetTimer/RegisterNotification have no enumeration
// API, but a chugging C++ timer/worker shows up here as CPU on its module.
typedef LONG (NTAPI *PFN_NtQueryInformationThread)(HANDLE, ULONG, PVOID, ULONG, PULONG);

static std::string ModuleForAddr(void* addr) {
    if (!addr) return "(unknown)";
    HMODULE h = nullptr;
    if (GetModuleHandleExW(
            GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
            reinterpret_cast<LPCWSTR>(addr), &h) && h) {
        wchar_t buf[MAX_PATH];
        DWORD n = GetModuleFileNameW(h, buf, MAX_PATH);
        if (n) {
            std::wstring w(buf, n);
            auto pos = w.find_last_of(L"\\/");
            std::wstring base = (pos == std::wstring::npos) ? w : w.substr(pos + 1);
            return WideToUtf8(base.c_str());
        }
    }
    return "(unknown)";
}

static unsigned long long ThreadCpu100ns(HANDLE h) {
    FILETIME c, e, k, u;
    if (!GetThreadTimes(h, &c, &e, &k, &u)) return 0ULL;
    ULARGE_INTEGER ku; ku.LowPart = k.dwLowDateTime; ku.HighPart = k.dwHighDateTime;
    ULARGE_INTEGER uu; uu.LowPart = u.dwLowDateTime; uu.HighPart = u.dwHighDateTime;
    return ku.QuadPart + uu.QuadPart;
}

static HANDLE OpenThreadQuery(DWORD tid) {
    HANDLE h = OpenThread(THREAD_QUERY_INFORMATION, FALSE, tid);
    if (!h) h = OpenThread(THREAD_QUERY_LIMITED_INFORMATION, FALSE, tid);
    return h;
}

static std::string BuildNativeThreads(const json& payload) {
    int sampleMs = payload.value("sampleMs", 500);
    if (sampleMs < 0) sampleMs = 0;
    if (sampleMs > 5000) sampleMs = 5000;

    HMODULE ntdll = GetModuleHandleW(L"ntdll.dll");
    PFN_NtQueryInformationThread NtQIT =
        ntdll ? reinterpret_cast<PFN_NtQueryInformationThread>(
                    GetProcAddress(ntdll, "NtQueryInformationThread"))
              : nullptr;
    const ULONG ThreadQuerySetWin32StartAddress = 9;
    const DWORD pid = GetCurrentProcessId();

    // pass 1: enumerate our threads, attribute to module, snapshot cpu
    struct Rec { std::string module; unsigned long long cpu1; };
    std::map<DWORD, Rec> recs;

    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
    if (snap != INVALID_HANDLE_VALUE) {
        THREADENTRY32 te; te.dwSize = sizeof(te);
        if (Thread32First(snap, &te)) {
            do {
                if (te.th32OwnerProcessID != pid) continue;
                HANDLE h = OpenThreadQuery(te.th32ThreadID);
                if (!h) continue;
                std::string mod = "(unknown)";
                if (NtQIT) {
                    void* a = nullptr;
                    if (NtQIT(h, ThreadQuerySetWin32StartAddress, &a, sizeof(a), nullptr) == 0)
                        mod = ModuleForAddr(a);
                }
                recs[te.th32ThreadID] = Rec{ mod, ThreadCpu100ns(h) };
                CloseHandle(h);
            } while (Thread32Next(snap, &te));
        }
        CloseHandle(snap);
    }

    if (sampleMs > 0) Sleep((DWORD)sampleMs);

    // pass 2: re-read cpu, aggregate by module + collect per-thread deltas
    struct Agg { unsigned long long deltaNs = 0, totalNs = 0; int threads = 0; };
    std::map<std::string, Agg> byMod;
    std::vector<std::tuple<unsigned long long, DWORD, std::string>> perThread;

    for (auto& kv : recs) {
        unsigned long long cpu2 = kv.second.cpu1;
        HANDLE h = OpenThreadQuery(kv.first);
        if (h) { cpu2 = ThreadCpu100ns(h); CloseHandle(h); }
        unsigned long long delta = (cpu2 >= kv.second.cpu1) ? (cpu2 - kv.second.cpu1) : 0ULL;
        Agg& a = byMod[kv.second.module];
        a.deltaNs += delta; a.totalNs += cpu2; a.threads++;
        perThread.emplace_back(delta, kv.first, kv.second.module);
    }

    std::vector<std::pair<std::string, Agg>> mods(byMod.begin(), byMod.end());
    std::sort(mods.begin(), mods.end(), [](const std::pair<std::string, Agg>& a,
                                           const std::pair<std::string, Agg>& b) {
        if (a.second.deltaNs != b.second.deltaNs) return a.second.deltaNs > b.second.deltaNs;
        return a.second.totalNs > b.second.totalNs;
    });
    std::sort(perThread.begin(), perThread.end(),
              [](const std::tuple<unsigned long long, DWORD, std::string>& a,
                 const std::tuple<unsigned long long, DWORD, std::string>& b) {
                  return std::get<0>(a) > std::get<0>(b);
              });

    auto toMs = [](unsigned long long ns100) -> double { return (double)ns100 / 10000.0; };

    json out;
    out["action"] = "native_threads";
    out["sampleMs"] = sampleMs;
    out["threadCount"] = (int)recs.size();

    json jmods = json::array();
    for (auto& m : mods) {
        json e;
        e["module"] = m.first;
        e["threads"] = m.second.threads;
        e["cpuMsDelta"] = toMs(m.second.deltaNs);
        e["cpuMsTotal"] = toMs(m.second.totalNs);
        if (sampleMs > 0)
            e["pctBusy"] = toMs(m.second.deltaNs) / (double)sampleMs * 100.0;
        jmods.push_back(e);
    }
    out["byModule"] = jmods;

    json jtop = json::array();
    int cap = 0;
    for (auto& t : perThread) {
        if (std::get<0>(t) == 0ULL) break;   // only threads that used CPU this window
        if (cap++ >= 15) break;
        json e;
        e["tid"] = (unsigned long)std::get<1>(t);
        e["module"] = std::get<2>(t);
        e["cpuMsDelta"] = toMs(std::get<0>(t));
        jtop.push_back(e);
    }
    out["topThreads"] = jtop;

    return out.dump();
}

// ── native:main_thread ──────────────────────────────────────────
// Single tool for main-thread (UI) hygiene. `action`:
//   "list" (default)     -> enumerate what runs on the main thread:
//        redrawCallbacks (fire every viewport redraw), dotNetTimers held in
//        globals (Enabled=true => firing), generalCallbackHandlers count, state.
//        These are the timer/redraw hooks that callbacks.show() cannot see.
//   "native_threads"     -> C++ side: every process thread attributed to its
//        owning DLL, with per-thread CPU sampled over `sampleMs` (default 500).
//        Shows which native plugin is burning cycles. Runs on the pipe thread.
//   "unregister_redraw"  name=<global fn>   -> unregisterRedrawViewsCallback
//   "disable_all_redraw"                     -> disableRedrawViewsCallbacks
//   "enable_all_redraw"                      -> enableRedrawViewsCallbacks
//   "stop_timer"         name=<global timer> -> .Stop() + .Enabled = false
//   "remove_callback"    name=<callback id>  -> callbacks.removeScripts id:
//
// Must run MAXScript, so it marshals to the main thread via ExecuteSync and is
// kept OUT of the direct-mode list (direct mode would run MAXScript on the pipe
// worker thread). It is also kept OUT of the mutating list: hook toggles are not
// scene edits and should not open a theHold undo transaction.
std::string NativeHandlers::MainThread(const std::string& params, MCPBridgeGUP* gup) {
    json payload = json::parse(params.empty() ? "{}" : params, nullptr, false);
    if (payload.is_discarded() || !payload.is_object()) {
        throw std::runtime_error("Invalid JSON params");
    }

    std::string action = payload.value("action", "list");
    if (action.empty()) action = "list";

    // ── list ──
    if (action == "list") {
        return gup->GetExecutor().ExecuteSync([&]() -> std::string {
            return RunMAXScript(BuildListScript());
        });
    }

    // ── native threads by owning module ──
    // Pure Win32/NT introspection: safe on the pipe worker thread, and must NOT
    // marshal to the main thread (it Sleeps to sample CPU — blocking the main
    // thread would freeze the UI for the sample window).
    if (action == "native_threads") {
        return BuildNativeThreads(payload);
    }

    // ── kill / toggle actions ──
    const std::string name = payload.value("name", "");
    const bool needsName =
        (action == "unregister_redraw" || action == "stop_timer" || action == "remove_callback");
    if (needsName && name.empty()) {
        throw std::runtime_error("name is required for action '" + action + "'");
    }

    const std::string nEsc = MsStringEscape(name);

    std::string ms;
    ms += "(\n";
    ms += "  local nm = \"" + nEsc + "\"\n";
    if (action == "unregister_redraw") {
        ms += "  local fnv = undefined\n";
        ms += "  try (fnv = globalVars.get (nm as name)) catch()\n";
        ms += "  if fnv != undefined and (isKindOf fnv MAXScriptFunction) then (unregisterRedrawViewsCallback fnv; \"OK\") else (\"ERR:global not found or not a function\")\n";
    } else if (action == "disable_all_redraw") {
        ms += "  disableRedrawViewsCallbacks(); \"OK\"\n";
    } else if (action == "enable_all_redraw") {
        ms += "  enableRedrawViewsCallbacks(); \"OK\"\n";
    } else if (action == "stop_timer") {
        ms += "  local t = undefined\n";
        ms += "  try (t = globalVars.get (nm as name)) catch()\n";
        ms += "  if t != undefined and (isKindOf t dotNetObject) then (try(t.Stop()) catch(); try(t.Enabled = false) catch(); \"OK\") else (\"ERR:global timer not found\")\n";
    } else if (action == "remove_callback") {
        ms += "  callbacks.removeScripts id:(nm as name); \"OK\"\n";
    } else {
        throw std::runtime_error(
            "Unknown action '" + action + "' "
            "(list|unregister_redraw|disable_all_redraw|enable_all_redraw|stop_timer|remove_callback)");
    }
    ms += ")\n";

    const std::string r = gup->GetExecutor().ExecuteSync([&]() -> std::string {
        return RunMAXScript(ms);
    });

    json out;
    out["action"] = action;
    if (!name.empty()) out["name"] = name;
    if (r.rfind("ERR:", 0) == 0) {
        out["ok"] = false;
        out["error"] = r.substr(4);
    } else {
        out["ok"] = true;
    }
    return out.dump();
}
