#pragma once

namespace GdiPlusRuntime {

// Start GDI+ lazily from a tool invocation, outside the DLL loader lock.
bool EnsureStarted();

// Pair the startup token during the GUP shutdown path.
void Shutdown();

}  // namespace GdiPlusRuntime
