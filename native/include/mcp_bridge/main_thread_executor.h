#pragma once
#include <windows.h>
#include <functional>
#include <string>
#include <mutex>
#include <condition_variable>
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

    struct WorkItem {
        std::function<std::string()> work;
        std::string result;
        bool completed = false;
        bool error = false;
        std::string error_message;
        std::mutex mutex;
        std::condition_variable cv;
    };

private:
    static LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp);

    HWND hwnd_ = nullptr;
    ATOM wndclass_atom_ = 0;

    static thread_local bool tl_direct_mode_;
    static constexpr UINT WM_MCP_EXECUTE = WM_USER + 0x4D43;

    // Per-process random secret. Sent in wParam alongside every
    // WM_MCP_EXECUTE so cross-process attackers can't smuggle pointers
    // for WndProc to reinterpret_cast — same-user processes can enumerate
    // and PostMessage to top-level windows freely (UIPI doesn't block
    // WM_USER+ between same-integrity processes).
    static WPARAM s_execute_cookie_;
};
