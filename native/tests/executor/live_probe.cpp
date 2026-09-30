#include <max.h>
#include "executor_test_suite.h"
#include <filesystem>
#include <fstream>

void ShowChat() {}
void ClaimNativeInstance() {}
void RunToolSmokeMacro() {}

// Load explicitly from a startup script in a disposable, empty Max session.
// No GUP class, transport listener, scene mutation, or autostart is installed.
extern "C" __declspec(dllexport) int RunExecutorProbe(const wchar_t* report_path) {
    std::ofstream report{std::filesystem::path(report_path)};
    try {
        if (!GetCOREInterface() || GetCOREInterface()->GetRootNode()->NumberOfChildren() != 0)
            throw std::runtime_error("The probe requires an empty 3ds Max scene");
        MainThreadExecutor executor;
        executor.Initialize();
        report << "Max PID=" << GetCurrentProcessId() << " empty_scene=true\n";
        report << ExecutorTests::RunOnMainThread(executor, true);
        report << "ALL PASS\n";
        return 0;
    } catch (const std::exception& e) {
        report << "FAIL " << e.what() << '\n';
        return 1;
    }
}
