#include "mcp_bridge/gdiplus_runtime.h"

#include <Windows.h>
#include <gdiplus.h>

#include <mutex>

#pragma comment(lib, "gdiplus.lib")

namespace {

std::mutex g_mutex;
ULONG_PTR g_token = 0;

}  // namespace

namespace GdiPlusRuntime {

bool EnsureStarted() {
    std::lock_guard<std::mutex> lock(g_mutex);
    if (g_token != 0) return true;

    Gdiplus::GdiplusStartupInput input;
    if (Gdiplus::GdiplusStartup(&g_token, &input, nullptr) != Gdiplus::Ok) {
        g_token = 0;
        return false;
    }
    return true;
}

void Shutdown() {
    std::lock_guard<std::mutex> lock(g_mutex);
    if (g_token == 0) return;
    Gdiplus::GdiplusShutdown(g_token);
    g_token = 0;
}

}  // namespace GdiPlusRuntime
