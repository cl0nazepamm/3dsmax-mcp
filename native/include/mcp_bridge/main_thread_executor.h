#pragma once
#include <windows.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <functional>
#include <list>
#include <string>
#include <mutex>
#include <condition_variable>
#include <deque>
#include <memory>
#include <stdexcept>
#include <unordered_map>

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

    // Main thread, before GUP::Stop joins the pipe threads. Max no longer
    // pumps the window while it exits: every request that has not started
    // fails now, and later requests fail without being queued (in direct mode
    // too, so no pipe thread touches a dying scene). Idempotent; Shutdown()
    // calls it, and Initialize() accepts work again.
    void StopAccepting();

    // Call from main thread (GUP::Stop)
    void Shutdown();

    // True once StopAccepting()/Shutdown() ran, until the next Initialize().
    static bool IsShuttingDown() { return !s_accepting_.load(std::memory_order_acquire); }

    // Call from ANY thread. In direct mode, runs work on calling thread.
    // Otherwise blocks until work completes on main thread.
    std::string ExecuteSync(std::function<std::string()> work,
                            DWORD timeout_ms = 120000);

    // Direct mode control (thread-local, safe for concurrent pipe clients).
    // Out of line: thread_local data cannot cross a DLL boundary, so the core
    // reaches these through McpHostApi::setDirectMode/getDirectMode.
    static void EnableDirectMode();
    static void DisableDirectMode();
    static bool IsDirectMode();

    // Run on the main thread after each piece of queued work, before its caller
    // resumes. The bridge delivers the node events that work queued, so its reply
    // leaves with the scene journal current and a read on a pipe thread right
    // behind it (scene_delta) sees the change; Max delivers them on its own only
    // when it next idles. Set before work arrives.
    static void SetAfterWork(void (*hook)()) { s_after_work_.store(hook, std::memory_order_release); }

    // True on the thread that called Initialize() (Max's main thread), where
    // ExecuteSync runs work inline.
    bool IsOwnThread() const {
        return main_thread_id_ != 0 && GetCurrentThreadId() == main_thread_id_;
    }

    // Max's main thread: true while it runs a request, including inside the
    // nested message loops that request's SDK work pumps.
    static bool IsRunningWork() { return s_executing_; }

    // True on Max's main thread (the thread that called Initialize()), for
    // code without the executor instance. Unlike !IsDirectMode() this also
    // holds inside a nested Dispatch that runs on the main thread (invoke_tool,
    // run_tool_smoke), and it is false on every other thread whatever its
    // mode. Lock-free; any thread.
    static bool IsMainThread() {
        const DWORD id = s_main_thread_id_.load(std::memory_order_acquire);
        return id != 0 && GetCurrentThreadId() == id;
    }

    // Names the work items this thread submits (the shell labels each pipe
    // request with its cmd type, client id and request id) so health
    // snapshots can say whose request is queued or running.
    struct WorkLabel {
        std::string cmd_type;
        std::string client_id;
        std::string request_id;
    };
    static void SetThreadLabel(const WorkLabel& label) { tl_label_ = label; }
    static void SetThreadLabel(const std::string& cmd_type) { tl_label_ = WorkLabel{cmd_type, {}, {}}; }
    static const WorkLabel& ThreadLabel() { return tl_label_; }

    // Main-thread heartbeat period. A thread-pool timer posts a beat message to
    // the executor window (one outstanding at a time); the main thread stamps
    // the heartbeat when it retrieves it. Posted messages are retrieved in
    // order and ahead of input, WM_PAINT and WM_TIMER, exactly like
    // WM_MCP_EXECUTE, so a heartbeat older than a few periods means queued
    // bridge work would not be picked up either (busy, blocked, or a loop
    // that filters our messages). WM_TIMER is not used: it is starved by any
    // steady stream of posted messages or paints while the thread still pumps.
    static constexpr UINT kHeartbeatMs = 1000;
    // Age of the last heartbeat, -1 when unavailable. Lock-free; any thread.
    static long long HeartbeatAgeMs() {
        const long long beat = s_heartbeat_ms_.load(std::memory_order_acquire);
        return beat > 0 ? (std::max)(0LL, SteadyNowMs() - beat) : -1;
    }

    // Snapshot for the pipe-thread "health" command. Never waits on the main
    // thread or on a running item's mutex, so it answers while Max is hung.
    struct Health {
        bool initialized = false;
        bool shutting_down = false;
        size_t queued = 0;                 // posted or deferred, not started
        long long oldest_queued_ms = -1;   // -1: nothing queued
        WorkLabel oldest_queued_label;
        bool running = false;
        WorkLabel running_label;
        long long running_ms = -1;
        long long heartbeat_age_ms = -1;   // -1: heartbeat unavailable (timer not created)
        bool executor_window_hung = false; // IsHungAppWindow on the hidden window
        unsigned long long completed = 0;
    };
    Health GetHealth() const;

    static constexpr int kQueued = 0;
    static constexpr int kRunning = 1;
    static constexpr int kDone = 2;

    // Health bookkeeping, kept apart from WorkItem so the pending list never
    // owns a WorkItem: destroying one destroys its callback and captures, which
    // must not happen on a pipe thread under s_stats_mutex_. phase is atomic
    // so GetHealth never takes an item mutex: it must answer while Max is hung,
    // whatever any waiter or the main thread is doing with the item.
    struct Ticket {
        WorkLabel label;
        std::chrono::steady_clock::time_point posted_at{};
        std::atomic<int> phase{kQueued};
    };

    struct WorkItem {
        std::function<std::string()> work;
        std::string result;
        bool completed = false;
        bool started = false;
        bool cancelled = false;
        bool error = false;
        std::string error_message;
        std::string request_id, command_type;
        std::mutex mutex;
        std::condition_variable cv;
        std::shared_ptr<Ticket> ticket = std::make_shared<Ticket>();
    };

private:
    static LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp);
    static void RunWorkItem(const std::shared_ptr<WorkItem>& item);
    // Health bookkeeping: remember a posted item / which item is running.
    static void TrackPending(const std::shared_ptr<WorkItem>& item);
    static void SetRunning(const WorkItem* item);
    static long long SteadyNowMs();
    // Thread-pool timer callback: posts one heartbeat beat to the executor window.
    static void CALLBACK HeartbeatTimerProc(PTP_CALLBACK_INSTANCE, PVOID context, PTP_TIMER);

    HWND hwnd_ = nullptr;
    ATOM wndclass_atom_ = 0;
    // Set once in Initialize() (called on the Max main thread). Read-only after.
    DWORD main_thread_id_ = 0;
    // Process-wide copy for IsMainThread().
    static std::atomic<DWORD> s_main_thread_id_;
    PTP_TIMER heartbeat_timer_ = nullptr;

    // Requests whose callers are waiting, by id. WM_MCP_EXECUTE carries only
    // the id; a caller removes its entry when it stops waiting, so a message
    // for a request nobody waits on finds nothing and is ignored.
    static std::mutex s_waiting_mutex_;
    static std::unordered_map<UINT64, std::shared_ptr<WorkItem>> s_waiting_;
    static UINT64 s_last_id_;
    static std::atomic<bool> s_accepting_;

    static thread_local bool tl_direct_mode_;
    static thread_local WorkLabel tl_label_;
    static constexpr UINT WM_MCP_EXECUTE = WM_USER + 0x4D43;
    // Heartbeat beat (cookie in wParam, no payload). +1 is the macroscript channel.
    static constexpr UINT WM_MCP_HEARTBEAT = WM_MCP_EXECUTE + 2;
    // An unanswered beat is re-posted after this long, so a beat a foreign
    // loop swallowed cannot freeze the heartbeat for good (a hung thread gains
    // at most one tiny message per interval).
    static constexpr long long kBeatRepostMs = 10LL * kHeartbeatMs;

    // Health state. s_pending_ holds the tickets of posted items and is pruned
    // lazily; the running fields describe the item RunWorkItem is executing.
    // Guarded by s_stats_mutex_, which is only ever taken for short reads and
    // writes (never while waiting on anything else).
    static std::mutex s_stats_mutex_;
    static std::list<std::shared_ptr<Ticket>> s_pending_;
    static bool s_running_;
    static WorkLabel s_running_label_;
    static std::chrono::steady_clock::time_point s_running_since_;
    static std::atomic<unsigned long long> s_completed_;
    static std::atomic<long long> s_heartbeat_ms_;   // SteadyNowMs(); 0 = heartbeat unavailable
    static std::atomic<long long> s_beat_posted_ms_; // SteadyNowMs() of the outstanding beat; 0 = none
    // Copy of hwnd_ readable from pipe threads (hwnd_ itself is main-thread state).
    std::atomic<HWND> health_hwnd_{nullptr};

    // Re-entrancy guard. SDK calls inside a work item can run nested message
    // pumps (progress UI, redraws, deferred plugin loads); without this guard
    // a queued WM_MCP_EXECUTE gets dispatched in the MIDDLE of the running
    // item, interleaving theHold transactions on the global undo system —
    // observed as 0xC0000005 under concurrent mutating requests, followed by
    // persistent scene-state corruption. Items arriving while one is running
    // are deferred and drained after it completes. Main-thread-only state.
    static bool s_executing_;
    static std::deque<std::shared_ptr<WorkItem>> s_deferred_;
    static std::atomic<void (*)()> s_after_work_;

    // Per-process random secret. Sent in wParam alongside every
    // WM_MCP_EXECUTE so other processes can't start queued requests — same-user
    // processes can enumerate and PostMessage to top-level windows freely
    // (UIPI doesn't block WM_USER+ between same-integrity processes).
    static WPARAM s_execute_cookie_;
};
