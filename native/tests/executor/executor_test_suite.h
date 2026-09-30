#pragma once
#include "mcp_bridge/main_thread_executor.h"
#include <commctrl.h>
#include <atomic>
#include <future>
#include <sstream>
#include <thread>
#include <vector>

namespace ExecutorTests {
inline void Require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
inline void Pump() {
    MSG message;
    while (PeekMessage(&message, nullptr, 0, 0, PM_REMOVE)) {
        TranslateMessage(&message);
        DispatchMessage(&message);
    }
}
inline void PumpFor(DWORD ms) {
    const auto end = GetTickCount64() + ms;
    while (GetTickCount64() < end) { Pump(); Sleep(1); }
}

// Called by a worker while the real owning/UI thread pumps its window.
inline std::string Suite(MainThreadExecutor& executor, bool live) {
    using Clock = std::chrono::steady_clock;
    const DWORD blocked_ms = live ? 10000 : 300;
    const DWORD queue_ms = live ? 2000 : 40;
    const DWORD running_ms = live ? 3000 : 150;
    const DWORD run_deadline_ms = live ? 500 : 20;
    std::ostringstream report;
    std::atomic<int> effects{0};

    // 1: Cancellation while still in the Windows queue must never execute,
    // even after the waiting caller and its captured stack have unwound.
    std::promise<void> entered;
    auto blocker = std::async(std::launch::async, [&] {
        return executor.ExecuteSync([&] {
            entered.set_value(); Sleep(blocked_ms); return "blocker";
        }, blocked_ms + 5000);
    });
    entered.get_future().wait();
    bool cancelled = false;
    {
        std::string stack_value = "must not run";
        try {
            executor.ExecuteSync([&] { ++effects; return stack_value; }, queue_ms);
        } catch (const MainThreadExecutor::CancelledBeforeStart&) { cancelled = true; }
    }
    Require(cancelled, "queued task was not cancelled");
    blocker.get();
    executor.ExecuteSync([] { return "queue drained"; });
    Require(effects == 0, "cancelled Windows-queue task executed later");
    report << "PASS queued cancellation: side effects=0\n";

    // 2: Running callback borrows a caller-local string past its deadline.
    {
        MainThreadExecutor::RequestScope scope(run_deadline_ms);
        std::string borrowed = "alive";
        const auto start = Clock::now();
        const auto result = executor.ExecuteSync([&] {
            Sleep(running_ms); borrowed += " after deadline"; return borrowed;
        });
        Require(result == "alive after deadline", "lost late result/reference lifetime");
        Require(Clock::now() - start >= std::chrono::milliseconds(running_ms), "caller returned while callback was running");
        Require(MainThreadExecutor::CompletedLate(), "late result not identified");
    }
    report << "PASS running deadline: waited, reference valid, real late result\n";

    // 3: An SDK-style progress window and nested pump deliver the next item
    // into s_deferred_; it must be cancelled there, not interleave mutations.
    std::promise<void> pumping;
    auto nested = std::async(std::launch::async, [&] {
        return executor.ExecuteSync([&] {
            HWND progress = CreateWindowEx(0, PROGRESS_CLASS, L"Executor test",
                WS_POPUP, 0, 0, 100, 20, nullptr, nullptr, GetModuleHandle(nullptr), nullptr);
            Require(progress != nullptr, "progress window creation failed");
            pumping.set_value(); PumpFor(blocked_ms); DestroyWindow(progress);
            return "nested blocker";
        }, blocked_ms + 5000);
    });
    pumping.get_future().wait();
    cancelled = false;
    try { executor.ExecuteSync([&] { ++effects; return "forbidden"; }, queue_ms); }
    catch (const MainThreadExecutor::CancelledBeforeStart&) { cancelled = true; }
    Require(cancelled, "deferred task did not time out");
    nested.get();
    executor.ExecuteSync([] { return "deferred drained"; });
    Require(effects == 0, "cancelled deferred task executed");
    {
        MainThreadExecutor::RequestScope scope(run_deadline_ms);
        std::string borrowed = "nested alive";
        auto result = executor.ExecuteSync([&] { PumpFor(running_ms); return borrowed; });
        Require(result == borrowed && MainThreadExecutor::CompletedLate(), "running nested-pump callback lost its caller/result");
    }
    report << "PASS nested progress pump: cancelled deferred task skipped; running task completes\n";

    // 4: One hundred ordinary requests, with exact side-effect/result counts.
    for (int i = 0; i != 100; ++i) {
        MainThreadExecutor::RequestScope scope;
        Require(executor.ExecuteSync([&] { return std::to_string(++effects); }) == std::to_string(i+1), "fast request lost/duplicated");
        Require(!MainThreadExecutor::CompletedLate(), "late state leaked between requests");
    }
    Require(effects == 100, "100 requests did not produce 100 effects");
    report << "PASS sequential requests: 100 results, 100 side effects\n";

    // Both sides race to claim pending. Whichever CAS wins is authoritative.
    const int before = effects;
    int done = 0, skipped = 0;
    for (int i = 0; i != 200; ++i) {
        try { executor.ExecuteSync([&] { ++effects; return "race"; }, i % 2); ++done; }
        catch (const MainThreadExecutor::CancelledBeforeStart&) { ++skipped; }
    }
    executor.ExecuteSync([] { return "races drained"; });
    Require(effects - before == done && done+skipped == 200, "CAS race executed a cancelled task or lost work");
    report << "PASS 200 deadline/start races: executed=" << done << ", cancelled=" << skipped << "\n";

    // Throwing running callbacks still reach done, so the waiting stack lives.
    bool caught = false;
    {
        MainThreadExecutor::RequestScope scope(run_deadline_ms);
        try { executor.ExecuteSync([&]() -> std::string { Sleep(running_ms); throw std::runtime_error("expected failure"); }); }
        catch (const std::runtime_error& e) { caught = std::string(e.what()) == "expected failure"; }
        Require(caught && MainThreadExecutor::CompletedLate(), "late exception did not complete/preserve status");
    }
    report << "PASS running exception: completed, late, original error preserved\n";
    return report.str();
}

// Use the same production executor inside Max and in the standalone Win32 test.
inline std::string RunOnMainThread(MainThreadExecutor& executor, bool live) {
    INITCOMMONCONTROLSEX controls{sizeof(controls), ICC_PROGRESS_CLASS};
    InitCommonControlsEx(&controls);
    auto future = std::async(std::launch::async, [&] { return Suite(executor, live); });
    while (future.wait_for(std::chrono::milliseconds(0)) != std::future_status::ready) {
        Pump(); Sleep(1);
    }
    Pump();
    return future.get();
}
}
