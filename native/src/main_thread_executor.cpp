#include "mcp_bridge/main_thread_executor.h"

#include <random>

#ifndef MCP_EXECUTOR_WINDOW_CLASS
#define MCP_EXECUTOR_WINDOW_CLASS L"MCPBridgeExecutor"
#endif

thread_local bool MainThreadExecutor::tl_direct_mode_ = false;
thread_local bool MainThreadExecutor::tl_deadline_enabled_ = false;
thread_local bool MainThreadExecutor::tl_completed_late_ = false;
thread_local unsigned MainThreadExecutor::tl_completed_work_ = 0;
thread_local std::chrono::steady_clock::time_point MainThreadExecutor::tl_deadline_;

MainThreadExecutor::RequestScope::RequestScope(DWORD timeout_ms)
    : old_enabled_(tl_deadline_enabled_), old_late_(tl_completed_late_),
      old_completed_(tl_completed_work_), old_deadline_(tl_deadline_) {
    tl_deadline_enabled_ = true;
    tl_completed_late_ = false;
    tl_completed_work_ = 0;
    tl_deadline_ = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
}

MainThreadExecutor::RequestScope::~RequestScope() {
    tl_deadline_enabled_ = old_enabled_;
    tl_completed_late_ = old_late_;
    tl_completed_work_ = old_completed_;
    tl_deadline_ = old_deadline_;
}
WPARAM MainThreadExecutor::s_execute_cookie_ = 0;
bool MainThreadExecutor::s_executing_ = false;
std::deque<std::shared_ptr<MainThreadExecutor::WorkItem>> MainThreadExecutor::s_deferred_;

MainThreadExecutor::~MainThreadExecutor() {
    Shutdown();
}

void MainThreadExecutor::Initialize() {
    // Initialize() is called from GUP::Start on the Max main thread, so this is
    // the thread that owns hwnd_ and pumps WM_MCP_EXECUTE. ExecuteSync uses it
    // to detect re-entrant calls already on the main thread.
    main_thread_id_ = GetCurrentThreadId();

    // Generate a per-process cookie before the window exists. std::random_device
    // on MSVC is non-deterministic. Reject 0 so we have a single sentinel value
    // any unauthenticated sender will fail against.
    if (s_execute_cookie_ == 0) {
        std::random_device rd;
        uint64_t c = (static_cast<uint64_t>(rd()) << 32) ^ rd();
        if (c == 0) c = 0xC001'D00D'C0FFEEULL; // unreachable in practice
        s_execute_cookie_ = static_cast<WPARAM>(c);
    }

    // Register a hidden window class
    WNDCLASSEX wc = {};
    wc.cbSize = sizeof(WNDCLASSEX);
    wc.lpfnWndProc = WndProc;
    wc.hInstance = GetModuleHandle(nullptr);
    wc.lpszClassName = MCP_EXECUTOR_WINDOW_CLASS;

    wndclass_atom_ = RegisterClassEx(&wc);
    if (!wndclass_atom_) return;

    // Create hidden window — NOT HWND_MESSAGE so FindWindow/getChildHWND can
    // find it. The title is process-specific because MAXScript macroscripts
    // are persisted in a shared usermacros folder across Max instances.
    std::wstring window_title = L"MCPBridgeExecutor-" + std::to_wstring(GetCurrentProcessId());
    hwnd_ = CreateWindowEx(
        0, MCP_EXECUTOR_WINDOW_CLASS, window_title.c_str(),
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
        UnregisterClass(MCP_EXECUTOR_WINDOW_CLASS, GetModuleHandle(nullptr));
        wndclass_atom_ = 0;
    }
}

std::string MainThreadExecutor::ExecuteSync(
    std::function<std::string()> work, DWORD timeout_ms) {

    // Already on the main thread (e.g. a handler that re-enters Dispatch).
    // Posting to ourselves would
    // block the only thread that can pump the message — a guaranteed deadlock
    // until timeout. Run inline; we are already where the work needs to run.
    if (main_thread_id_ != 0 && GetCurrentThreadId() == main_thread_id_) {
        return work();
    }

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
    item->deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
    if (tl_deadline_enabled_ && tl_deadline_ < item->deadline)
        item->deadline = tl_deadline_;

    // prevent shared_ptr from dying before main thread processes it
    auto* raw = new std::shared_ptr<WorkItem>(item);

    if (!PostMessage(hwnd_, WM_MCP_EXECUTE, s_execute_cookie_, reinterpret_cast<LPARAM>(raw))) {
        delete raw;
        throw std::runtime_error("Failed to post work to main thread");
    }

    std::unique_lock<std::mutex> lock(item->mutex);
    auto terminal = [&] {
        const auto state = item->state.load();
        return state == WorkState::done || state == WorkState::cancelled;
    };
    if (!item->cv.wait_until(lock, item->deadline, terminal)) {
        auto expected = WorkState::pending;
        if (item->state.compare_exchange_strong(expected, WorkState::cancelled)) {
            // We won against pending -> running. No callback may execute now,
            // including after this caller's reference captures go out of scope.
            throw CancelledBeforeStart();
        }
        // Running work may borrow this caller's stack. A timeout is NOT licence
        // to unwind it. Wait through completion (including callback exceptions).
        item->cv.wait(lock, terminal);
    }
    if (item->state.load() == WorkState::cancelled)
        throw CancelledBeforeStart();
    ++tl_completed_work_;
    if (item->finished_at > item->deadline)
        tl_completed_late_ = true;

    if (item->error) {
        throw std::runtime_error(item->error_message);
    }

    return item->result;
}

LRESULT CALLBACK MainThreadExecutor::WndProc(
    HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {

    // WM_MCP_EXECUTE + 1 with small wParam commands: macroscript actions.
    if (msg == WM_MCP_EXECUTE + 1) {
        if (wp == 2) {
            extern void ClaimNativeInstance();
            ClaimNativeInstance();
        }
        return 0;
    }

    if (msg == WM_MCP_EXECUTE) {
        // Reject any sender that doesn't know our per-process cookie. lParam
        // is reinterpret_cast'd as a heap pointer; an attacker-supplied value
        // would be an arbitrary read/write/free + vtable-call primitive.
        if (wp != s_execute_cookie_) return 0;

        auto* raw = reinterpret_cast<std::shared_ptr<WorkItem>*>(lp);
        auto item = *raw;
        delete raw;

        // Delivered by a nested message pump while another item is running
        // (SDK work can pump: progress UI, redraws, deferred plugin loads).
        // Running it here would interleave theHold transactions on the global
        // undo system. Defer; the outer invocation drains after its item.
        if (s_executing_) {
            s_deferred_.push_back(std::move(item));
            return 0;
        }

        s_executing_ = true;
        RunWorkItem(item);
        while (!s_deferred_.empty()) {
            auto next = std::move(s_deferred_.front());
            s_deferred_.pop_front();
            RunWorkItem(next);
        }
        s_executing_ = false;
        return 0;
    }
    return DefWindowProc(hwnd, msg, wp, lp);
}

void MainThreadExecutor::RunWorkItem(const std::shared_ptr<WorkItem>& item) {
    {
        // Synchronize terminal publication with cv waiting. Never hold this
        // mutex while calling Max/SDK code: it can run nested message pumps.
        std::lock_guard<std::mutex> lock(item->mutex);
        auto expected = WorkState::pending;
        if (std::chrono::steady_clock::now() >= item->deadline) {
            item->state.compare_exchange_strong(expected, WorkState::cancelled);
            item->cv.notify_all();
            return;
        }
        if (!item->state.compare_exchange_strong(expected, WorkState::running))
            return;
    }

    try {
        item->result = item->work();
    } catch (const std::exception& e) {
        item->error = true;
        item->error_message = e.what();
    } catch (...) {
        item->error = true;
        item->error_message = "Unknown exception on main thread";
    }
    {
        std::lock_guard<std::mutex> lock(item->mutex);
        item->finished_at = std::chrono::steady_clock::now();
        auto expected = WorkState::running;
        item->state.compare_exchange_strong(expected, WorkState::done);
    }
    item->cv.notify_all();
}
