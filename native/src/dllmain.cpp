#include <max.h>
#include <plugapi.h>
#include "mcp_bridge/bridge_gup.h"

HINSTANCE hInstance = nullptr;

BOOL WINAPI DllMain(HINSTANCE hinstDLL, ULONG fdwReason, LPVOID) {
    if (fdwReason == DLL_PROCESS_ATTACH) {
        hInstance = hinstDLL;
        DisableThreadLibraryCalls(hinstDLL);
    }
    return TRUE;
}

__declspec(dllexport) const TCHAR* LibDescription() {
    return _T("MCP Bridge - Native Named Pipe Server for 3dsmax-mcp");
}

__declspec(dllexport) int LibNumberClasses() {
    return 1;
}

__declspec(dllexport) ClassDesc* LibClassDesc(int i) {
    switch (i) {
        case 0: return GetMCPBridgeDesc();
        default: return nullptr;
    }
}

__declspec(dllexport) ULONG LibVersion() {
    return VERSION_3DSMAX;
}

__declspec(dllexport) int LibInitialize() {
    return TRUE;
}

__declspec(dllexport) int LibShutdown() {
    return TRUE;
}
