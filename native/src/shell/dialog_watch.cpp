#include "mcp_bridge/dialog_watch.h"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <deque>
#include <map>
#include <mutex>
#include <random>
#include <set>
#include <stdexcept>
#include <thread>
#include <vector>

namespace DialogWatch {
using json = nlohmann::json;
using Clock = std::chrono::steady_clock;

struct Session {
    std::string request, command;
    DWORD thread = GetCurrentThreadId();
    std::set<HWND> baseline;
    std::set<std::string> attempted;
    std::mutex mutex;
    std::condition_variable idle;
    int pending = 0;  // acknowledgments being posted for this operation
    bool stop = false;
    json events = json::array();
};

namespace {
constexpr wchar_t kTokenProperty[] = L"MCPBridge.DialogWatch.Identity";
constexpr wchar_t kLaneClass[] = L"MCPBridgeDialogLane";
constexpr wchar_t kScriptControllerException[] = L"MAXScript Script Controller Exception";
constexpr UINT kLaneSend = WM_APP + 0x4D44, kLanePost = WM_APP + 0x4D45;
constexpr size_t kHistoryLimit = 32, kMaxDialogs = 16, kMaxChildren = 256, kMaxText = 16384, kMaxField = 4096;
constexpr int kAutoLimit = 8;
// A window first seen this long before its owner was disabled is a modeless
// window that a later modal dialog happened to block, not the dialog itself.
constexpr auto kModalGrace = std::chrono::milliseconds(1000);

// Longest ThrowIfDismissed waits for an acknowledgment in flight to publish.
constexpr auto kPublishWait = std::chrono::milliseconds(3000);

thread_local std::string request_id, command_type;
thread_local std::shared_ptr<Session> current;
thread_local int defer_depth = 0;

struct Request { std::string command, state; DWORD thread; Clock::time_point since; };
struct Open {
    ULONG_PTR token = 0;
    std::string id, title, cls;
    DWORD thread = 0;
    Clock::time_point appeared;
    json during = json::array();
    bool checked = false;
};

// state_mutex guards the request registry, sessions, open dialogs and history.
std::mutex state_mutex;
std::map<std::string, Request> requests;
std::vector<std::weak_ptr<Session>> active;
std::map<HWND, Open> open;
std::deque<json> history;
std::atomic<ULONG_PTR> next_token{1};

// scan_mutex serializes window scans; guards the first-seen/disabled maps.
std::mutex scan_mutex;
std::map<HWND, Clock::time_point> seen, disabled_since;

DWORD main_thread = 0;
HWND lane = nullptr;
WPARAM lane_cookie = 0;
QtBackend qt;
std::mutex pumping_mutex;
std::function<bool()> main_pumping;
std::mutex monitor_mutex;
std::condition_variable monitor_wake;
bool monitor_stop = false;
std::thread monitor;
std::atomic<bool> monitor_running{false};
std::atomic<unsigned> controller_closed{0};

std::mutex text_mutex;
// A timed-out in-process WM_GETTEXT may still execute later. Keep its buffer
// alive rather than letting a late window procedure write into freed memory,
// until its window is destroyed: a message to a destroyed window is never
// delivered. Capped per window, so one hung field cannot disable the others.
struct PendingText { HWND hwnd; std::unique_ptr<wchar_t[]> buffer; };
std::vector<PendingText> pending_text;
constexpr size_t kPendingPerWindow = 4, kPendingTotal = 256;

// Structured errors keep their code through the dispatcher.
[[noreturn]] void Fail(const char* code, const std::string& message, bool retryable = false) {
    throw std::runtime_error(json{{"type", "NativeError"}, {"code", code}, {"message", message},
        {"retryable", retryable}}.dump());
}
std::string Message(const std::exception& error) {
    const json structured = json::parse(error.what(), nullptr, false);
    return structured.is_object() ? structured.value("message", error.what()) : error.what();
}

long long EpochMs() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
}
long long AgeMs(Clock::time_point since) {
    return std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - since).count();
}

std::string Utf8(const wchar_t* text) {
    int size = WideCharToMultiByte(CP_UTF8, 0, text, -1, nullptr, 0, nullptr, nullptr);
    if (size <= 1) return {};
    std::string result(size, '\0');
    WideCharToMultiByte(CP_UTF8, 0, text, -1, result.data(), size, nullptr, nullptr);
    result.pop_back();
    return result;
}

std::string ClassOf(HWND hwnd) {
    wchar_t buffer[256]{};
    GetClassNameW(hwnd, buffer, 256);
    return Utf8(buffer);
}

// Window-manager text; never sends a message to the window's thread.
std::string InternalText(HWND hwnd, bool* truncated = nullptr) {
    std::vector<wchar_t> buffer(8192);
    const int count = InternalGetWindowText(hwnd, buffer.data(), static_cast<int>(buffer.size()));
    if (truncated && count >= static_cast<int>(buffer.size()) - 1) *truncated = true;
    return Utf8(buffer.data());
}

bool IsQt(const std::string& cls) {
    return cls.rfind("Qt", 0) == 0 && cls.find("QWindow") != std::string::npos;
}

// Modeless tool windows that a modal dialog elsewhere can leave enabled above a
// disabled owner. They are never blocking dialogs, and some must never be
// messaged: a Chaos Cosmos browser may belong to a deadlocked importer thread,
// and closing or activating the Material Editor around a Cosmos import
// deadlocked Max. Window-manager title only; no message is sent.
bool ToolWindow(HWND hwnd) {
    const std::string title = InternalText(hwnd);
    auto starts = [&title](const char* prefix) { return title.rfind(prefix, 0) == 0; };
    return title == "Chaos Cosmos Browser" || title == "Material Editor" || starts("Material Editor - ") ||
        starts("Slate Material Editor") || starts("AGENT VIEWPORT") || starts("Floating Viewport");
}

// Only dialogs on Max's main thread are read or pressed. Another thread's
// window may belong to a thread that is itself hung (the Cosmos importer's),
// and a Qt widget may only be touched from the thread that owns it.
bool OnMainThread(HWND hwnd) {
    return main_thread != 0 && GetWindowThreadProcessId(hwnd, nullptr) == main_thread;
}
constexpr char kNotMainThread[] = "not on Max's main thread, so it is not read or pressed (its thread may be hung); "
                                  "the user can answer it in Max";
constexpr char kNotPumping[] = "Max's main thread is not processing messages";

bool MainThreadPumping() {
    std::function<bool()> pumping;
    {
        std::lock_guard<std::mutex> lock(pumping_mutex);
        pumping = main_pumping;
    }
    try { return !pumping || pumping(); } catch (...) { return true; }
}

std::string Lower(std::string text) {
    std::transform(text.begin(), text.end(), text.begin(), [](unsigned char c) { return static_cast<char>(tolower(c)); });
    return text;
}

std::string WithoutMnemonic(const std::string& text) {
    std::string out;
    for (size_t i = 0; i < text.size(); ++i) {
        if (text[i] == '&' && i + 1 < text.size()) ++i;
        out += text[i];
    }
    return out;
}

std::string Clip(std::string text, size_t limit, bool& truncated) {
    if (text.size() > limit) {
        text.resize(limit);
        truncated = true;
    }
    return text;
}

// Identity of one exact dialog state. Any change in its text, buttons or
// fields invalidates a response planned against an earlier inspection.
std::string Token(const std::string& id, const json& snapshot) {
    // Win32 moves the default-button style with keyboard focus, so it is
    // reported but never part of the identity.
    json identity = snapshot;
    if (identity.contains("buttons"))
        for (auto& button : identity["buttons"]) button.erase("default");
    const std::string text = id + "\n" + identity.dump();
    unsigned long long hash = 1469598103934665603ull;
    for (unsigned char c : text) {
        hash ^= c;
        hash *= 1099511628211ull;
    }
    char buffer[17];
    std::snprintf(buffer, sizeof(buffer), "%016llx", hash);
    return buffer;
}

// GetWindowText can block indefinitely on a window in our own process. Every
// cross-thread message read has a timeout.
bool MessageText(HWND hwnd, std::string& text) {
    std::lock_guard<std::mutex> lock(text_mutex);
    if (GetWindowThreadProcessId(hwnd, nullptr) == GetCurrentThreadId()) return false;
    pending_text.erase(std::remove_if(pending_text.begin(), pending_text.end(),
        [](const PendingText& entry) { return !IsWindow(entry.hwnd); }), pending_text.end());
    const auto for_window = std::count_if(pending_text.begin(), pending_text.end(),
        [hwnd](const PendingText& entry) { return entry.hwnd == hwnd; });
    if (static_cast<size_t>(for_window) >= kPendingPerWindow || pending_text.size() >= kPendingTotal) return false;
    auto buffer = std::make_unique<wchar_t[]>(4096);
    DWORD_PTR count = 0;
    if (!SendMessageTimeoutW(hwnd, WM_GETTEXT, 4096, reinterpret_cast<LPARAM>(buffer.get()),
            SMTO_ABORTIFHUNG | SMTO_BLOCK | SMTO_ERRORONEXIT, 40, &count)) {
        pending_text.push_back({hwnd, std::move(buffer)});
        return false;
    }
    text = Utf8(buffer.get());
    return count < 4095;
}

BOOL CALLBACK Collect(HWND hwnd, LPARAM data) {
    DWORD pid = 0;
    GetWindowThreadProcessId(hwnd, &pid);
    if (pid == GetCurrentProcessId() && IsWindowVisible(hwnd))
        reinterpret_cast<std::vector<HWND>*>(data)->push_back(hwnd);
    return TRUE;
}
std::vector<HWND> Windows() {
    std::vector<HWND> result;
    EnumWindows(Collect, reinterpret_cast<LPARAM>(&result));
    return result;
}

// ── Main-thread lane ─────────────────────────────────────────────
// Qt widgets are only readable on Max's main thread. A modal dialog runs a
// nested message loop there, so a message-only window receives work even
// while an MCP operation is blocked inside that dialog.
// A sent task that its caller gave up on is withdrawn unless the lane already
// started it, so a late delivery does nothing (a press reported as not made
// never clicks afterwards).
enum LaneState : int { kLaneQueued, kLaneStarted, kLaneWithdrawn };
struct LaneTask {
    std::function<json()> work;
    json result;
    std::string error;
    std::atomic<int> state{kLaneQueued};
};

LRESULT CALLBACK LaneProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    if ((msg == kLaneSend || msg == kLanePost) && wp == lane_cookie && lp) {
        auto* raw = reinterpret_cast<std::shared_ptr<LaneTask>*>(lp);
        std::shared_ptr<LaneTask> task = std::move(*raw);
        delete raw;
        int queued = kLaneQueued;
        if (!task->state.compare_exchange_strong(queued, kLaneStarted)) return 1;  // withdrawn
        try { task->result = task->work(); }
        catch (const std::exception& e) { task->error = e.what(); }
        catch (...) { task->error = "Unknown dialog lane error"; }
        return 1;
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

json RunOnMain(std::function<json()> work, DWORD timeout_ms) {
    if (!lane) Fail("DIALOG_LANE_UNAVAILABLE", "the dialog monitor is not running");
    if (GetCurrentThreadId() == main_thread) return work();
    // A main thread that stops retrieving posted messages is not in a dialog's
    // loop; a sent message would only run inside whatever call blocks it.
    // The heartbeat notices that only after a few periods (up to ~3 s), so in
    // that window a read can still run inside such a call. Reads only read,
    // and presses are posted (PostOnMain), so neither ever acts there.
    if (!MainThreadPumping()) Fail("MAIN_THREAD_BUSY", kNotPumping, true);
    auto task = std::make_shared<LaneTask>();
    task->work = std::move(work);
    auto* raw = new std::shared_ptr<LaneTask>(task);
    DWORD_PTR ignored = 0;
    // On timeout the message may still be delivered later; the lane then frees
    // raw. Withdraw it so it does nothing then; only a task the lane already
    // started can still act, and its caller is told the outcome is unknown.
    if (!SendMessageTimeoutW(lane, kLaneSend, lane_cookie, reinterpret_cast<LPARAM>(raw),
            SMTO_ABORTIFHUNG | SMTO_BLOCK, timeout_ms, &ignored)) {
        int queued = kLaneQueued;
        if (task->state.compare_exchange_strong(queued, kLaneWithdrawn)) Fail("MAIN_THREAD_BUSY", kNotPumping, true);
        Fail("DIALOG_OUTCOME_UNKNOWN", "Max's main thread started this dialog action but did not finish it in time; "
             "it may still take effect (a press may still click). Inspect again before responding");
    }
    if (!task->error.empty()) throw std::runtime_error(task->error);
    return task->result;
}

// Runs after the current message returns, so the dialog's own handlers never
// execute inside a reader's synchronous call.
void PostOnMain(std::function<void()> work) {
    auto task = std::make_shared<LaneTask>();
    task->work = [work = std::move(work)]() -> json { work(); return nullptr; };
    auto* raw = new std::shared_ptr<LaneTask>(task);
    if (!lane || !PostMessageW(lane, kLanePost, lane_cookie, reinterpret_cast<LPARAM>(raw))) {
        delete raw;
        Fail("DIALOG_PRESS_FAILED", "the click could not be scheduled");
    }
}

// ── Dialog snapshots ─────────────────────────────────────────────
json Unreadable(HWND hwnd, const std::string& cls, const std::string& reason) {
    return {{"kind", IsQt(cls) ? "qt" : "win32"}, {"title", InternalText(hwnd)}, {"text", ""},
        {"buttons", json::array()}, {"fields", json::array()}, {"complete", false}, {"unavailable", reason}};
}

struct Child { HWND hwnd; RECT rect; };
BOOL CALLBACK CollectChild(HWND hwnd, LPARAM data) {
    auto& children = *reinterpret_cast<std::vector<Child>*>(data);
    if (children.size() >= kMaxChildren) return FALSE;
    if (IsWindowVisible(hwnd)) {
        RECT rect{};
        GetWindowRect(hwnd, &rect);
        children.push_back({hwnd, rect});
    }
    return TRUE;
}

// Reads controls through the window manager; only edit/combo values and
// check states need bounded cross-thread messages.
json Win32Snapshot(HWND hwnd, std::vector<HWND>* button_windows = nullptr) {
    if (!IsWindow(hwnd)) return Unreadable(hwnd, "", "closed");
    std::vector<Child> children;
    EnumChildWindows(hwnd, CollectChild, reinterpret_cast<LPARAM>(&children));
    bool truncated = children.size() >= kMaxChildren, complete = true;
    std::stable_sort(children.begin(), children.end(), [](const Child& a, const Child& b) {
        return a.rect.top != b.rect.top ? a.rect.top < b.rect.top : a.rect.left < b.rect.left;
    });
    std::string text;
    json buttons = json::array(), fields = json::array(), other = json::array();
    for (const auto& child : children) {
        const std::string cls = ClassOf(child.hwnd);
        const LONG style = GetWindowLongW(child.hwnd, GWL_STYLE);
        if (cls == "Button") {
            const LONG type = style & BS_TYPEMASK;
            if (type == BS_GROUPBOX) {
                const std::string label = WithoutMnemonic(InternalText(child.hwnd, &truncated));
                if (!label.empty()) text += label + "\n";
                continue;
            }
            const bool check = type == BS_CHECKBOX || type == BS_AUTOCHECKBOX || type == BS_3STATE || type == BS_AUTO3STATE;
            const bool radio = type == BS_RADIOBUTTON || type == BS_AUTORADIOBUTTON;
            json item = {{"index", buttons.size()}, {"label", WithoutMnemonic(InternalText(child.hwnd, &truncated))},
                {"enabled", IsWindowEnabled(child.hwnd) != FALSE}, {"kind", check ? "check" : radio ? "radio" : "push"},
                {"id", GetDlgCtrlID(child.hwnd)}};
            // 0xD and 0xF: CommCtrl's BS_DEFSPLITBUTTON and BS_DEFCOMMANDLINK.
            if (type == BS_DEFPUSHBUTTON || type == 0xD || type == 0xF) item["default"] = true;
            if (check || radio) {
                DWORD_PTR state = 0;
                if (SendMessageTimeoutW(child.hwnd, BM_GETCHECK, 0, 0, SMTO_ABORTIFHUNG | SMTO_BLOCK, 40, &state))
                    item["checked"] = state == BST_CHECKED;
                else complete = false;
            }
            buttons.push_back(std::move(item));
            if (button_windows) button_windows->push_back(child.hwnd);
        } else if (cls == "Static" || cls == "SysLink") {
            const LONG type = style & SS_TYPEMASK;
            if (cls == "Static" && (type == SS_ICON || type == SS_BITMAP || type == SS_ENHMETAFILE ||
                    type == SS_ETCHEDHORZ || type == SS_ETCHEDVERT || type == SS_ETCHEDFRAME)) continue;
            std::string label = InternalText(child.hwnd, &truncated);
            if (cls == "SysLink") {
                std::string plain;
                for (size_t i = 0; i < label.size(); ++i) {
                    if (label[i] == '<') { const size_t end = label.find('>', i); if (end == std::string::npos) break; i = end; }
                    else plain += label[i];
                }
                label = plain;
            }
            if (!label.empty() && text.size() < kMaxText) text += label + "\n";
            else if (!label.empty()) truncated = true;
        } else if (cls == "Edit" || cls.rfind("RichEdit", 0) == 0 || cls.rfind("RICHEDIT", 0) == 0 || cls == "ComboBox") {
            if (fields.size() >= 32) { truncated = true; continue; }
            json field = {{"kind", cls == "ComboBox" ? "combo" : (style & ES_MULTILINE) ? "text" : "edit"}};
            if (cls != "ComboBox") field["read_only"] = (style & ES_READONLY) != 0;
            std::string value;
            if (cls != "ComboBox" && (style & ES_PASSWORD)) {
                // Never expose a password field's value.
            } else if (MessageText(child.hwnd, value)) {
                field["value"] = Clip(value, kMaxField, truncated);
            } else {
                complete = false;
            }
            fields.push_back(std::move(field));
        } else if (cls == "msctls_progress32" || cls == "ScrollBar") {
            continue;
        } else if ((style & WS_TABSTOP) || cls == "DirectUIHWND") {
            // Interactive controls this reader does not understand.
            if (other.size() < 16 && std::find(other.begin(), other.end(), cls) == other.end()) other.push_back(cls);
        }
    }
    if (!text.empty()) text.pop_back();
    bool title_truncated = false;
    json result = {{"kind", "win32"}, {"title", InternalText(hwnd, &title_truncated)},
        {"text", Clip(text, kMaxText, truncated)}, {"buttons", buttons}, {"fields", fields},
        {"complete", complete && !truncated && !title_truncated && other.empty()}};
    if (!other.empty()) result["other_controls"] = other;
    return result;
}

json Snapshot(HWND hwnd, const std::string& cls, DWORD timeout_ms = 1500) {
    if (!OnMainThread(hwnd)) return Unreadable(hwnd, cls, kNotMainThread);
    if (!IsQt(cls)) return Win32Snapshot(hwnd);
    if (!qt.snapshot) return Unreadable(hwnd, cls, "Qt dialog reader unavailable in this build");
    try {
        json snapshot = RunOnMain([hwnd]() -> json { return qt.snapshot(hwnd); }, timeout_ms);
        return snapshot.is_null() ? Unreadable(hwnd, cls, "not a Qt widget dialog") : snapshot;
    } catch (const std::exception& e) {
        return Unreadable(hwnd, cls, Message(e));
    }
}

int SelectButton(const json& snapshot, const json& spec) {
    const json& buttons = snapshot.at("buttons");
    int index = -1;
    if (spec.is_number_integer()) {
        index = spec.get<int>();
        if (index < 0 || index >= static_cast<int>(buttons.size()))
            Fail("BUTTON_NOT_FOUND", "no button at index " + std::to_string(index));
    } else if (spec.is_string()) {
        const std::string wanted = Lower(WithoutMnemonic(spec.get<std::string>()));
        std::vector<std::string> labels;
        for (size_t i = 0; i < buttons.size(); ++i) {
            const std::string label = buttons[i].value("label", "");
            labels.push_back(label);
            if (Lower(label) != wanted) continue;
            if (index >= 0) Fail("AMBIGUOUS_BUTTON", "several buttons are labeled '" + label + "'; pass its index");
            index = static_cast<int>(i);
        }
        // MCP clients may deliver an index as text; labels still win.
        const std::string text = spec.get<std::string>();
        if (index < 0 && !text.empty() && text.size() < 4 &&
            std::all_of(text.begin(), text.end(), [](unsigned char c) { return isdigit(c) != 0; })) {
            index = std::stoi(text);
            if (index >= static_cast<int>(buttons.size())) Fail("BUTTON_NOT_FOUND", "no button at index " + text);
        }
        if (index < 0) Fail("BUTTON_NOT_FOUND", "no button labeled '" + text + "'; available: " + json(labels).dump());
    } else {
        throw std::runtime_error("button must be a label or an index from inspect");
    }
    if (!buttons[index].value("enabled", false)) Fail("BUTTON_DISABLED", "that button is disabled");
    return index;
}

struct Pressed { json snapshot, button; };

// Re-reads the dialog, requires the inspected state, then posts the click.
Pressed PressButton(HWND hwnd, const std::string& id, const std::string& cls, const std::string& expected, const json& spec) {
    if (!OnMainThread(hwnd)) Fail("DIALOG_NOT_ON_MAIN_THREAD", std::string("this dialog is ") + kNotMainThread);
    if (IsQt(cls)) {
        if (!qt.snapshot || !qt.click) Fail("DIALOG_UNREADABLE", "Qt dialog support is unavailable in this build");
        json out = RunOnMain([hwnd, id, expected, spec]() -> json {
            json snapshot = qt.snapshot(hwnd);
            if (snapshot.is_null() || Token(id, snapshot) != expected)
                Fail("STALE_DIALOG", "the dialog changed or closed; inspect again");
            const int index = SelectButton(snapshot, spec);
            PostOnMain([hwnd, id, expected, index] {
                json again = qt.snapshot(hwnd);
                if (!again.is_null() && Token(id, again) == expected) qt.click(hwnd, index);
            });
            return {{"snapshot", snapshot}, {"button", snapshot["buttons"][index]}};
        }, 1500);
        return {out["snapshot"], out["button"]};
    }
    // Like the Qt path: never queue a click into a main thread that is not
    // pumping (it would fire whenever Max recovers, maybe minutes later).
    if (!MainThreadPumping()) Fail("MAIN_THREAD_BUSY", kNotPumping, true);
    std::vector<HWND> controls;
    json snapshot = Win32Snapshot(hwnd, &controls);
    if (Token(id, snapshot) != expected) Fail("STALE_DIALOG", "the dialog changed or closed; inspect again");
    const int index = SelectButton(snapshot, spec);
    const json& button = snapshot["buttons"][index];
    const int control_id = button.value("id", 0);
    const HWND control = controls[index];
    const bool command = button.value("kind", "") == "push" && control_id > 0;
    const ULONG_PTR identity = reinterpret_cast<ULONG_PTR>(GetPropW(hwnd, kTokenProperty));
    // Posted through the lane and checked again when the main thread takes it:
    // the same dialog instance, the button still enabled inside it.
    PostOnMain([hwnd, control, control_id, command, identity] {
        if (!IsWindow(hwnd) || reinterpret_cast<ULONG_PTR>(GetPropW(hwnd, kTokenProperty)) != identity ||
            !IsWindow(control) || !IsChild(hwnd, control) || !IsWindowEnabled(control)) return;
        // A button's WM_COMMAND goes to its own parent, which is a nested pane
        // (e.g. a file dialog's template) rather than the dialog itself.
        const HWND parent = GetParent(control);
        if (command && parent)
            PostMessageW(parent, WM_COMMAND, MAKEWPARAM(control_id, BN_CLICKED), reinterpret_cast<LPARAM>(control));
        else
            PostMessageW(control, BM_CLICK, 0, 0);
    });
    return {snapshot, button};
}

bool KnownTitle(const std::string& title) {
    // Deliberately exact, English Max error dialogs. Extend only from observed
    // dialog evidence. A title containing "error" is not sufficient.
    static const std::set<std::string> titles = {
        "MAXScript Controller Exception", "MAXScript Callback script Exception",
        "MAXScript Rollout Handler Exception", "MAXScript Scripted Object Exception",
        "MAXScript FileIn Exception", "MAXScript Runtime Error", "MAXScript Compile Error"
    };
    return titles.count(title) != 0;
}

// An acknowledgment-only error: a recognized title, a fully read body with an
// error marker, and a sole enabled OK button.
bool KnownError(const json& dialog) {
    if (!dialog.value("complete", false) || !KnownTitle(dialog.value("title", ""))) return false;
    const auto& buttons = dialog.at("buttons");
    if (buttons.size() != 1 || !buttons[0].value("enabled", false) || buttons[0].value("kind", "") != "push" ||
        Lower(buttons[0].value("label", "")) != "ok") return false;
    if (dialog.value("kind", "") == "win32" && buttons[0].value("id", 0) != IDOK) return false;
    const std::string text = dialog.value("text", "");
    return text.find("--") != std::string::npos || text.find("Runtime error:") != std::string::npos ||
        text.find("Type error:") != std::string::npos || text.find("Syntax error:") != std::string::npos;
}

json Event(const std::string& action, const Open& dialog, const json& snapshot) {
    bool truncated = false;
    return {{"action", action}, {"dialog_id", dialog.id}, {"title", dialog.title}, {"class", dialog.cls},
        {"kind", IsQt(dialog.cls) ? "qt" : "win32"}, {"text", Clip(snapshot.value("text", ""), 2000, truncated)},
        {"time_ms", EpochMs()}, {"during_requests", dialog.during}};
}

void RecordHistoryLocked(json event) {
    history.push_back(std::move(event));
    while (history.size() > kHistoryLimit) history.pop_front();
}
void RecordHistory(json event) {
    std::lock_guard<std::mutex> lock(state_mutex);
    RecordHistoryLocked(std::move(event));
}

// ── Monitor ──────────────────────────────────────────────────────
// Window-manager state only: never sends a message to a possibly blocked
// thread. A blocking dialog is an enabled window whose owner (or, for an
// unowned dialog, a window on its thread) is disabled, and which appeared no
// earlier than that disablement.
void Refresh() {
    std::lock_guard<std::mutex> scan_lock(scan_mutex);
    const auto now = Clock::now();
    const auto windows = Windows();
    const std::set<HWND> visible(windows.begin(), windows.end());
    for (auto it = seen.begin(); it != seen.end();)
        it = visible.count(it->first) ? std::next(it) : seen.erase(it);
    for (HWND hwnd : windows) seen.emplace(hwnd, now);
    std::map<DWORD, HWND> disabled_on_thread;
    for (HWND hwnd : windows)
        if (!IsWindowEnabled(hwnd)) disabled_on_thread.emplace(GetWindowThreadProcessId(hwnd, nullptr), hwnd);

    std::set<HWND> blocked, blockers;
    for (HWND hwnd : windows) {
        if (hwnd == lane || !IsWindowEnabled(hwnd) || ToolWindow(hwnd)) continue;
        HWND blocker = GetWindow(hwnd, GW_OWNER);
        if (blocker) {
            DWORD pid = 0;
            GetWindowThreadProcessId(blocker, &pid);
            if (pid != GetCurrentProcessId() || IsWindowEnabled(blocker)) continue;
        } else {
            auto found = disabled_on_thread.find(GetWindowThreadProcessId(hwnd, nullptr));
            if (found == disabled_on_thread.end() || ClassOf(hwnd) != "#32770") continue;
            blocker = found->second;
        }
        blockers.insert(blocker);
        const auto since = disabled_since.emplace(blocker, now).first->second;
        if (seen[hwnd] + kModalGrace >= since) blocked.insert(hwnd);
    }
    for (auto it = disabled_since.begin(); it != disabled_since.end();)
        it = blockers.count(it->first) ? std::next(it) : disabled_since.erase(it);

    std::lock_guard<std::mutex> lock(state_mutex);
    for (auto it = open.begin(); it != open.end();) {
        const bool same = blocked.count(it->first) && IsWindow(it->first) &&
            reinterpret_cast<ULONG_PTR>(GetPropW(it->first, kTokenProperty)) == it->second.token;
        if (same) { ++it; continue; }
        // A window that still exists only stopped blocking, e.g. an editor
        // opened beside an error box that has since closed.
        const bool exists = IsWindow(it->first) && IsWindowVisible(it->first);
        RecordHistoryLocked({{"action", exists ? "unblocked" : "closed"}, {"dialog_id", it->second.id},
            {"title", it->second.title}, {"open_ms", AgeMs(it->second.appeared)}, {"time_ms", EpochMs()}});
        it = open.erase(it);
    }
    for (HWND hwnd : blocked) {
        if (open.count(hwnd)) continue;
        ULONG_PTR token = reinterpret_cast<ULONG_PTR>(GetPropW(hwnd, kTokenProperty));
        if (!token) {
            token = next_token++;
            if (!SetPropW(hwnd, kTokenProperty, reinterpret_cast<HANDLE>(token))) continue;
        }
        Open dialog;
        dialog.token = token;
        dialog.id = std::to_string(GetCurrentProcessId()) + ":" + std::to_string(token);
        dialog.title = InternalText(hwnd);
        dialog.cls = ClassOf(hwnd);
        dialog.thread = GetWindowThreadProcessId(hwnd, nullptr);
        dialog.appeared = now;
        for (auto& weak : active)
            if (auto session = weak.lock())
                if (session->thread == dialog.thread && !session->baseline.count(hwnd)) dialog.during.push_back(session->request);
        RecordHistoryLocked({{"action", "appeared"}, {"dialog_id", dialog.id}, {"title", dialog.title},
            {"kind", IsQt(dialog.cls) ? "qt" : "win32"}, {"during_requests", dialog.during}, {"time_ms", EpochMs()}});
        open.emplace(hwnd, std::move(dialog));
    }
}

std::shared_ptr<Session> SessionFor(const Open& dialog) {
    std::lock_guard<std::mutex> lock(state_mutex);
    std::shared_ptr<Session> found;
    for (auto& weak : active)
        if (auto session = weak.lock())
            if (session->thread == dialog.thread && std::find(dialog.during.begin(), dialog.during.end(), session->request) != dialog.during.end())
                found = session;  // innermost operation wins
    return found;
}

// New recognized errors during an operation are acknowledged once each, up to
// kAutoLimit per operation, and the operation reports MAX_DIALOG_ERROR.
void AutoAcknowledge() {
    std::vector<std::pair<HWND, Open>> candidates;
    {
        std::lock_guard<std::mutex> lock(state_mutex);
        for (auto& [hwnd, dialog] : open) {
            if (dialog.checked || dialog.during.empty() || !KnownTitle(dialog.title)) continue;
            dialog.checked = true;
            candidates.push_back({hwnd, dialog});
        }
    }
    for (const auto& [hwnd, dialog] : candidates) {
        auto session = SessionFor(dialog);
        if (!session) continue;
        {
            std::lock_guard<std::mutex> lock(session->mutex);
            if (session->stop || session->attempted.size() >= kAutoLimit || !session->attempted.insert(dialog.id).second) continue;
            ++session->pending;
        }
        json event;
        bool retry = false;
        try {
            const json snapshot = Snapshot(hwnd, dialog.cls);
            // A Qt read refused (or withdrawn) while the main thread did not take
            // it is no reading of the dialog: leave it for a later tick.
            retry = snapshot.value("unavailable", "") == kNotPumping;
            if (!retry && KnownError(snapshot)) {
                const Pressed pressed = PressButton(hwnd, dialog.id, dialog.cls, Token(dialog.id, snapshot), 0);
                event = Event("acknowledged_error", dialog, pressed.snapshot);
                event["button"] = pressed.button.value("label", "");
                event["request_id"] = session->request;
                event["command"] = session->command;
            }
        } catch (const std::exception& e) {
            // A press refused because the main thread stopped pumping is retried
            // on a later tick, like a refused Qt read.
            retry = Message(e) == kNotPumping;
        } catch (...) { /* Recovery must never terminate Max; inspection remains available. */ }
        {
            // Publish before the operation can finish, so a fast return cannot
            // commit before its error is recorded.
            std::lock_guard<std::mutex> lock(session->mutex);
            if (!event.is_null()) session->events.push_back(event);
            if (retry) session->attempted.erase(dialog.id);
            --session->pending;
        }
        session->idle.notify_all();
        if (retry) {
            std::lock_guard<std::mutex> lock(state_mutex);
            auto found = open.find(hwnd);
            if (found != open.end() && found->second.token == dialog.token) found->second.checked = false;
        }
        if (!event.is_null()) RecordHistory(event);
    }
}

// Max shows "MAXScript Script Controller Exception" after the failing
// evaluation has returned (viewport redraw, scrubbing), outside any MCP
// operation, and it blocks the main thread until acknowledged. Max 2027 draws
// it with Qt; #32770 covers a Win32 message box with the same title.
bool IsScriptControllerException(HWND hwnd) {
    if (!OnMainThread(hwnd)) return false;
    const std::string cls = ClassOf(hwnd);
    if (!IsQt(cls) && cls != "#32770") return false;
    HWND owner = GetWindow(hwnd, GW_OWNER);
    DWORD owner_pid = 0;
    if (!owner || !GetWindowThreadProcessId(owner, &owner_pid) ||
        owner_pid != GetCurrentProcessId() || IsWindowEnabled(owner)) return false;
    wchar_t title[64]{};
    return InternalGetWindowText(hwnd, title, 64) > 0 && wcscmp(title, kScriptControllerException) == 0;
}

void CloseScriptControllerExceptions(std::map<HWND, Clock::time_point>& posted) {
    const auto now = Clock::now();
    for (auto it = posted.begin(); it != posted.end();)
        it = IsWindow(it->first) ? std::next(it) : posted.erase(it);
    for (HWND hwnd : Windows()) {
        if (!IsScriptControllerException(hwnd)) continue;
        auto seen_before = posted.find(hwnd);
        if (seen_before != posted.end() && now - seen_before->second < std::chrono::seconds(1)) continue;
        const bool first = seen_before == posted.end();
        // Read the error once for the history; the box is closed regardless.
        json snapshot = first ? Snapshot(hwnd, ClassOf(hwnd), 300) : json();
        // Closing is its only choice, OK. Posting never blocks this thread.
        if (!PostMessageW(hwnd, WM_CLOSE, 0, 0)) continue;
        posted[hwnd] = now;
        if (!first) continue;
        ++controller_closed;
        json requests_now = json::array();
        {
            std::lock_guard<std::mutex> lock(state_mutex);
            for (auto& weak : active) if (auto session = weak.lock()) requests_now.push_back(session->request);
        }
        bool truncated = false;
        RecordHistory({{"action", "closed_script_controller_exception"},
            {"title", "MAXScript Script Controller Exception"}, {"hwnd", reinterpret_cast<ULONG_PTR>(hwnd)},
            {"text", Clip(snapshot.value("text", ""), 2000, truncated)},
            {"time_ms", EpochMs()}, {"active_requests", requests_now}});
    }
}

void Monitor() {
    std::map<HWND, Clock::time_point> posted;
    for (;;) {
        {
            std::unique_lock<std::mutex> lock(monitor_mutex);
            if (monitor_wake.wait_for(lock, std::chrono::milliseconds(100), [] { return monitor_stop; })) return;
        }
        try {
            Refresh();
            CloseScriptControllerExceptions(posted);
            AutoAcknowledge();
        } catch (...) { /* The monitor must never terminate Max. */ }
    }
}

json Summary(HWND hwnd, const Open& dialog) {
    return {{"dialog_id", dialog.id}, {"hwnd", reinterpret_cast<ULONG_PTR>(hwnd)}, {"title", dialog.title},
        {"class", dialog.cls}, {"kind", IsQt(dialog.cls) ? "qt" : "win32"},
        {"main_thread", dialog.thread == main_thread}, {"age_ms", AgeMs(dialog.appeared)},
        {"during_requests", dialog.during}};
}

json Status() {
    std::lock_guard<std::mutex> lock(state_mutex);
    json dialogs = json::array(), in_flight = json::array();
    for (const auto& [hwnd, dialog] : open) dialogs.push_back(Summary(hwnd, dialog));
    for (const auto& [id, request] : requests)
        in_flight.push_back({{"request_id", id}, {"command", request.command}, {"state", request.state},
            {"main_thread", request.thread == main_thread}, {"age_ms", AgeMs(request.since)}});
    // A main thread that stopped pumping is busy or hung whatever dialog is open:
    // clients then diagnose the hang instead of reporting the dialog.
    return {{"pid", GetCurrentProcessId()}, {"dialogs", dialogs}, {"requests", in_flight},
        {"main_thread_pumping", MainThreadPumping()},
        {"monitor", {{"running", monitor_running.load()}, {"qt_reader", static_cast<bool>(qt.snapshot)},
            {"script_controller_exceptions_closed", controller_closed.load()}}}};
}

void SetRequestState(const std::string& id, const char* state, DWORD thread) {
    if (id.empty()) return;
    std::lock_guard<std::mutex> lock(state_mutex);
    auto found = requests.find(id);
    if (found == requests.end()) return;
    found->second.state = state;
    found->second.thread = thread;
}
}

// ── Public API ───────────────────────────────────────────────────
void Start(QtBackend backend) {
    std::lock_guard<std::mutex> lock(monitor_mutex);
    if (monitor.joinable()) return;
    main_thread = GetCurrentThreadId();
    qt = std::move(backend);
    if (!lane_cookie) {
        std::random_device random;
        lane_cookie = static_cast<WPARAM>((static_cast<unsigned long long>(random()) << 32) ^ random()) | 1;
    }
    WNDCLASSEXW wc{sizeof(wc)};
    wc.lpfnWndProc = LaneProc;
    wc.hInstance = GetModuleHandleW(nullptr);
    wc.lpszClassName = kLaneClass;
    RegisterClassExW(&wc);  // A repeated registration fails harmlessly.
    lane = CreateWindowExW(0, kLaneClass, L"", 0, 0, 0, 0, 0, HWND_MESSAGE, nullptr, wc.hInstance, nullptr);
    monitor_stop = false;
    monitor = std::thread(Monitor);
    monitor_running = true;
}

void SetMainThreadPumping(std::function<bool()> pumping) {
    std::lock_guard<std::mutex> lock(pumping_mutex);
    main_pumping = std::move(pumping);
}

void Stop() {
    {
        std::lock_guard<std::mutex> lock(monitor_mutex);
        monitor_stop = true;
    }
    monitor_wake.notify_all();
    if (monitor.joinable()) monitor.join();
    monitor_running = false;
    if (lane) {
        DestroyWindow(lane);
        lane = nullptr;
        UnregisterClassW(kLaneClass, GetModuleHandleW(nullptr));
    }
}

RequestContext::RequestContext(const std::string& request, const std::string& command)
    : previous_request(request_id), previous_command(command_type) {
    request_id = request;
    command_type = command;
    if (!request.empty() && command != "native:max_dialogs") {
        std::lock_guard<std::mutex> lock(state_mutex);
        registered = requests.emplace(request, Request{command, "dispatched", GetCurrentThreadId(), Clock::now()}).second;
    }
}
RequestContext::~RequestContext() {
    if (registered) {
        std::lock_guard<std::mutex> lock(state_mutex);
        requests.erase(request_id);
    }
    request_id = previous_request;
    command_type = previous_command;
}
std::string RequestId() { return request_id; }
std::string CommandType() { return command_type; }
void MarkQueued() { SetRequestState(request_id, "queued", main_thread); }

Guard::Guard(const std::string& request, const std::string& command) : session_(std::make_shared<Session>()), previous_(current) {
    session_->request = request;
    session_->command = command;
    // Only a dialog-owning thread needs a baseline; pipe workers own none.
    if (session_->thread == main_thread)
        for (HWND hwnd : Windows()) session_->baseline.insert(hwnd);
    {
        std::lock_guard<std::mutex> lock(state_mutex);
        active.erase(std::remove_if(active.begin(), active.end(), [](const auto& s) { return s.expired(); }), active.end());
        active.push_back(session_);
    }
    SetRequestState(request, session_->thread == main_thread ? "running" : "direct", session_->thread);
    current = session_;
}
Guard::~Guard() {
    Finish();
    SetRequestState(session_->request, "dispatched", session_->thread);
    current = previous_;
}
void Guard::Finish() {
    std::unique_lock<std::mutex> lock(session_->mutex);
    session_->stop = true;
    session_->idle.wait(lock, [&] { return session_->pending == 0; });
}
std::string Guard::Execute(const std::function<std::string()>& work) {
    std::string result, cause;
    try { result = work(); }
    catch (const std::exception& e) { cause = e.what(); }
    catch (...) { cause = "Unknown exception during bridge operation"; }
    Finish();  // Freeze recovery before deciding the operation's outcome.
    std::string error = Error(cause);
    if (!error.empty()) throw std::runtime_error(error);
    if (!cause.empty()) throw std::runtime_error(cause);
    return result;
}
std::string Guard::Error(const std::string& cause) const {
    std::lock_guard<std::mutex> lock(session_->mutex);
    if (session_->events.empty()) return {};
    return json{{"type", "NativeError"}, {"code", "MAX_DIALOG_ERROR"}, {"retryable", false},
        {"message", "Max showed an error dialog during this operation; it was acknowledged. Inspect the affected scene before retrying."},
        {"details", {{"dialogs", session_->events}, {"cause", cause}, {"scene_state", "verification_required"}}}}.dump();
}
DeferDismissed::DeferDismissed() { ++defer_depth; }
DeferDismissed::~DeferDismissed() { --defer_depth; }

void ThrowIfDismissed() {
    if (!current || defer_depth > 0) return;
    std::unique_lock<std::mutex> lock(current->mutex);
    // An acknowledgment publishes its event after posting the click, so the
    // operation can resume first. Wait for it: a commit must never precede the
    // error it should fail on. Bounded: a read the main thread does not take
    // here times out after 1.5 s anyway.
    current->idle.wait_for(lock, kPublishWait, [] { return current->pending == 0; });
    if (!current->events.empty())
        throw std::runtime_error(json{{"type", "NativeError"}, {"code", "MAX_DIALOG_ERROR"}, {"retryable", false},
            {"message", "Max displayed an error dialog during the operation."},
            {"details", {{"dialogs", current->events}, {"scene_state", "verification_required"}}}}.dump());
}

json OpenDialogs() {
    std::lock_guard<std::mutex> lock(state_mutex);
    json result = json::array();
    for (const auto& [hwnd, dialog] : open)
        result.push_back({{"dialog_id", dialog.id}, {"title", dialog.title}, {"age_ms", AgeMs(dialog.appeared)}});
    return result;
}

std::string Control(const std::string& command) {
    if (main_thread && GetCurrentThreadId() == main_thread)
        throw std::runtime_error("Dialog recovery requires the independent pipe control channel");
    const json args = command.empty() ? json::object() : json::parse(command);
    const std::string action = args.value("action", "inspect");
    if (action != "status" && action != "inspect" && action != "respond")
        throw std::runtime_error("action must be status, inspect or respond");
    Refresh();
    json result = Status();
    if (action == "status") return result.dump();

    if (action == "respond") {
        const std::string id = args.value("dialog_id", ""), expected = args.value("expected_dialog", "");
        const json spec = args.contains("button") ? args["button"] : json();
        if (id.empty() || expected.empty() || spec.is_null())
            throw std::runtime_error("respond requires dialog_id, expected_dialog and button from inspect");
        HWND hwnd = nullptr;
        Open dialog;
        {
            std::lock_guard<std::mutex> lock(state_mutex);
            for (const auto& [window, entry] : open)
                if (entry.id == id) { hwnd = window; dialog = entry; }
        }
        if (!hwnd) Fail("STALE_DIALOG", "that dialog is no longer open; inspect again");
        // A recognized error answered during an operation still fails it.
        auto session = KnownTitle(dialog.title) ? SessionFor(dialog) : nullptr;
        if (session) {
            std::lock_guard<std::mutex> lock(session->mutex);
            if (session->stop) session.reset();
            else ++session->pending;
        }
        json event;
        try {
            const Pressed pressed = PressButton(hwnd, id, dialog.cls, expected, spec);
            event = Event("button_press_posted", dialog, pressed.snapshot);
            event["button"] = pressed.button;
            if (session) {
                std::lock_guard<std::mutex> lock(session->mutex);
                if (KnownError(pressed.snapshot)) {
                    json failure = event;
                    failure["request_id"] = session->request;
                    failure["command"] = session->command;
                    session->events.push_back(failure);
                }
                --session->pending;
            }
        } catch (...) {
            if (session) {
                std::lock_guard<std::mutex> lock(session->mutex);
                --session->pending;
            }
            if (session) session->idle.notify_all();
            throw;
        }
        if (session) session->idle.notify_all();
        RecordHistory(event);
        // Posting is not proof that the dialog closed or what followed.
        result["response"] = event;
        return result.dump();
    }

    json dialogs = json::array();
    for (const auto& summary : result["dialogs"]) {
        if (dialogs.size() >= kMaxDialogs) { result["dialogs_truncated"] = true; break; }
        HWND hwnd = reinterpret_cast<HWND>(summary["hwnd"].get<ULONG_PTR>());
        const json snapshot = Snapshot(hwnd, summary["class"].get<std::string>());
        json item = summary;
        item.update(snapshot);
        item["title"] = summary["title"];
        item["expected_dialog"] = Token(summary["dialog_id"].get<std::string>(), snapshot);
        dialogs.push_back(std::move(item));
    }
    result["dialogs"] = dialogs;
    json recent = json::array();
    {
        std::lock_guard<std::mutex> lock(state_mutex);
        std::set<std::string> open_ids;
        for (const auto& [hwnd, dialog] : open) open_ids.insert(dialog.id);
        for (auto event : history) {
            if (event.contains("dialog_id")) event["still_open"] = open_ids.count(event["dialog_id"].get<std::string>()) != 0;
            else {
                HWND hwnd = reinterpret_cast<HWND>(event.value("hwnd", ULONG_PTR{0}));
                event["still_open"] = IsWindowVisible(hwnd) && IsScriptControllerException(hwnd);
            }
            recent.push_back(std::move(event));
        }
    }
    result["recent_actions"] = recent;
    result["policy"] = "Any listed button of a dialog on Max's main thread can be pressed with respond; dialogs of "
        "other threads are listed but never read or pressed. Cosmos browser, Material Editor and viewport windows "
        "are never dialogs. Automatic: Script Controller Exception boxes "
        "are closed at any time; recognized acknowledgment-only MAXScript errors are acknowledged only when new "
        "during an MCP operation, which then fails with MAX_DIALOG_ERROR.";
    return result.dump();
}
}
