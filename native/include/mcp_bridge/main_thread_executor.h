#pragma once
#include <windows.h>
#include <atomic>
#include <chrono>
#include <functional>
#include <string>
#include <mutex>
#include <condition_variable>
#include <deque>
#include <memory>
#include <stdexcept>

// Executes work on the 3ds Max main thread from a background thread.
// Uses a hidden Win32 window + WM_USER message to marshal calls.
//
// Direct mode: when enabled (per-thread), ExecuteSync runs the work
// function directly on the calling thread, skipping the main-thread
// roundtrip. Use for read-only handlers that don't mutate scene state
// or call RunMAXScript. Eliminates PostMessage + condition_variable
// latency for reads.
class MainThreadExecutor {
public:
    MainThreadExecutor() = default;
    ~MainThreadExecutor();

    // Call from main thread (GUP::Start)
    void Initialize();

    // Call from main thread (GUP::Stop)
    void Shutdown();

    // Call from ANY thread. In direct mode, runs work on calling thread.
    // Otherwise blocks until work completes on main thread.
    std::string ExecuteSync(std::function<std::string()> work,
                            DWORD timeout_ms = 120000);

    // Direct mode control (thread-local, safe for concurrent pipe clients)
    static void EnableDirectMode()  { tl_direct_mode_ = true; }
    static void DisableDirectMode() { tl_direct_mode_ = false; }
    static bool IsDirectMode()      { return tl_direct_mode_; }

    class CancelledBeforeStart : public std::runtime_error {
    public:
        CancelledBeforeStart() : std::runtime_error(
            "Task cancelled before execution: its queue deadline expired") {}
    };

    // One request can make several executor calls. Preserve nesting and keep
    // deadline/result metadata on the waiting thread, never process-global.
    class RequestScope {
    public:
        explicit RequestScope(DWORD timeout_ms = 120000);
        ~RequestScope();
        RequestScope(const RequestScope&) = delete;
        RequestScope& operator=(const RequestScope&) = delete;
    private:
        bool old_enabled_, old_late_;
        unsigned old_completed_;
        std::chrono::steady_clock::time_point old_deadline_;
    };
    static bool HasCompletedWork() { return tl_completed_work_ != 0; }
    static bool CompletedLate() { return tl_completed_late_; }

    enum class WorkState { pending, running, done, cancelled };
    struct WorkItem {
        std::function<std::string()> work;
        std::string result;
        std::atomic<WorkState> state{WorkState::pending};
        std::chrono::steady_clock::time_point deadline;
        std::chrono::steady_clock::time_point finished_at;
        bool error = false;
        std::string error_message;
        std::mutex mutex;
        std::condition_variable cv;
    };

private:
    static LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp);
    static void RunWorkItem(const std::shared_ptr<WorkItem>& item);

    HWND hwnd_ = nullptr;
    ATOM wndclass_atom_ = 0;
    // Set once in Initialize() (called on the Max main thread). Read-only after.
    DWORD main_thread_id_ = 0;

    static thread_local bool tl_direct_mode_;
    static thread_local bool tl_deadline_enabled_;
    static thread_local bool tl_completed_late_;
    static thread_local unsigned tl_completed_work_;
    static thread_local std::chrono::steady_clock::time_point tl_deadline_;
    static constexpr UINT WM_MCP_EXECUTE = WM_USER + 0x4D43;

    // Re-entrancy guard. SDK calls inside a work item can run nested message
    // pumps (progress UI, redraws, deferred plugin loads); without this guard
    // a queued WM_MCP_EXECUTE gets dispatched in the MIDDLE of the running
    // item, interleaving theHold transactions on the global undo system —
    // observed as 0xC0000005 under concurrent mutating requests, followed by
    // persistent scene-state corruption. Items arriving while one is running
    // are deferred and drained after it completes. Main-thread-only state.
    static bool s_executing_;
    static std::deque<std::shared_ptr<WorkItem>> s_deferred_;

    // Per-process random secret. Sent in wParam alongside every
    // WM_MCP_EXECUTE so cross-process attackers can't smuggle pointers
    // for WndProc to reinterpret_cast — same-user processes can enumerate
    // and PostMessage to top-level windows freely (UIPI doesn't block
    // WM_USER+ between same-integrity processes).
    static WPARAM s_execute_cookie_;
};
