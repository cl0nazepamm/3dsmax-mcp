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
class MainThreadExecutor {
public:
    MainThreadExecutor() = default;
    ~MainThreadExecutor();

    // Call from main thread (GUP::Start)
    void Initialize();

    // Call from main thread (GUP::Stop)
    void Shutdown();

    // Call from ANY thread. Blocks until work completes on main thread.
    std::string ExecuteSync(std::function<std::string()> work,
                            DWORD timeout_ms = 120000);

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

    static constexpr UINT WM_MCP_EXECUTE = WM_USER + 0x4D43;
};
