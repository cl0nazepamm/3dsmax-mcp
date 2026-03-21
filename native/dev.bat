@echo off
setlocal

:: ── mcp_bridge quick dev cycle ────────────────────────────────
:: Kills Max, builds, deploys, relaunches with last scene.
:: Usage: dev.bat [scenefile.max]

set NATIVE_DIR=%~dp0
set CMAKE="C:\Program Files\CMake\bin\cmake.exe"
set MAX_EXE="C:\Program Files\Autodesk\3ds Max 2026\3dsmax.exe"
set MAX_PLUGINS=C:\Program Files\Autodesk\3ds Max 2026\plugins

:: Save scene path argument (optional)
set SCENE=%~1

:: ── Kill Max ────────────────────────────────────────────────────
echo [1/4] Killing 3ds Max...
taskkill /F /IM 3dsmax.exe >nul 2>&1
timeout /t 2 /nobreak >nul

:: ── Build ───────────────────────────────────────────────────────
echo [2/4] Building...
if not exist "%NATIVE_DIR%build\CMakeCache.txt" (
    %CMAKE% -B "%NATIVE_DIR%build" -G "Visual Studio 17 2022" -A x64 "%NATIVE_DIR%"
    if %ERRORLEVEL% NEQ 0 goto :fail
)
%CMAKE% --build "%NATIVE_DIR%build" --config Release
if %ERRORLEVEL% NEQ 0 goto :fail

:: ── Deploy ──────────────────────────────────────────────────────
echo [3/4] Deploying...
copy /Y "%NATIVE_DIR%build\Release\mcp_bridge.gup" "%MAX_PLUGINS%\mcp_bridge.gup" >nul
if %ERRORLEVEL% NEQ 0 (
    echo Deploy failed - need admin. Elevating...
    powershell -Command "Start-Process cmd -ArgumentList '/c copy /Y \"%NATIVE_DIR%build\Release\mcp_bridge.gup\" \"%MAX_PLUGINS%\mcp_bridge.gup\"' -Verb RunAs -Wait"
)

:: ── Relaunch ────────────────────────────────────────────────────
echo [4/4] Launching 3ds Max...
if "%SCENE%"=="" (
    start "" %MAX_EXE%
) else (
    start "" %MAX_EXE% "%SCENE%"
)

echo.
echo === Done! Max is starting. ===
exit /b 0

:fail
echo.
echo === BUILD FAILED ===
pause
exit /b 1
