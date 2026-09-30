#include "executor_test_suite.h"
#include <iostream>

// Macro entry points in the production executor, irrelevant to this fixture.
void ShowChat() {}
void ClaimNativeInstance() {}
void RunToolSmokeMacro() {}

int main() {
    try {
        MainThreadExecutor executor;
        executor.Initialize();
        std::cout << ExecutorTests::RunOnMainThread(executor, false);
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "FAIL " << e.what() << '\n';
        return 1;
    }
}
