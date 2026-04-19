#include "mcp_bridge/main_thread_executor.h"

thread_local bool MainThreadExecutor::tl_direct_mode_ = false;

MainThreadExecutor::~MainThreadExecutor() {
    Shutdown();
}

void MainThreadExecutor::Initialize() {
    // Register a hidden window class
    WNDCLASSEX wc = {};
    wc.cbSize = sizeof(WNDCLASSEX);
    wc.lpfnWndProc = WndProc;
    wc.hInstance = GetModuleHandle(nullptr);
    wc.lpszClassName = L"MCPBridgeExecutor";

    wndclass_atom_ = RegisterClassEx(&wc);
    if (!wndclass_atom_) return;

    // Create hidden window — NOT HWND_MESSAGE so FindWindow/getChildHWND can find it
    hwnd_ = CreateWindowEx(
        0, L"MCPBridgeExecutor", L"MCPBridgeExecutor",
        0, 0, 0, 0, 0,
        nullptr,
        nullptr, GetModuleHandle(nullptr), nullptr
    );
}

void MainThreadExecutor::Shutdown() {
    if (hwnd_) {
        DestroyWindow(hwnd_);
        hwnd_ = nullptr;
    }
    if (wndclass_atom_) {
        UnregisterClass(L"MCPBridgeExecutor", GetModuleHandle(nullptr));
        wndclass_atom_ = 0;
    }
}

std::string MainThreadExecutor::ExecuteSync(
    std::function<std::string()> work, DWORD timeout_ms) {

    // Direct mode: run on calling thread, skip main-thread roundtrip.
    // Used for read-only handlers on pipe worker threads.
    if (tl_direct_mode_) {
        return work();
    }

    if (!hwnd_) {
        throw std::runtime_error("MainThreadExecutor not initialized");
    }

    auto item = std::make_shared<WorkItem>();
    item->work = std::move(work);

    // prevent shared_ptr from dying before main thread processes it
    auto* raw = new std::shared_ptr<WorkItem>(item);

    if (!PostMessage(hwnd_, WM_MCP_EXECUTE, 0, reinterpret_cast<LPARAM>(raw))) {
        delete raw;
        throw std::runtime_error("Failed to post work to main thread");
    }

    // Wait for main thread to complete the work
    std::unique_lock<std::mutex> lock(item->mutex);
    bool finished = item->cv.wait_for(lock,
        std::chrono::milliseconds(timeout_ms),
        [&] { return item->completed; });

    if (!finished) {
        throw std::runtime_error("Main thread execution timed out");
    }

    if (item->error) {
        throw std::runtime_error(item->error_message);
    }

    return item->result;
}

LRESULT CALLBACK MainThreadExecutor::WndProc(
    HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {

    // WM_MCP_EXECUTE + 1 with wParam=1: show chat window
    if (msg == WM_MCP_EXECUTE + 1 && wp == 1) {
        extern void ShowChat();
        ShowChat();
        return 0;
    }

    if (msg == WM_MCP_EXECUTE) {
        auto* raw = reinterpret_cast<std::shared_ptr<WorkItem>*>(lp);
        auto item = *raw;
        delete raw;

        {
            std::lock_guard<std::mutex> lock(item->mutex);
            try {
                item->result = item->work();
            } catch (const std::exception& e) {
                item->error = true;
                item->error_message = e.what();
            } catch (...) {
                item->error = true;
                item->error_message = "Unknown exception on main thread";
            }
            item->completed = true;
        }
        item->cv.notify_all();
        return 0;
    }
    return DefWindowProc(hwnd, msg, wp, lp);
}
