@echo off
setlocal

:: ── 3dsmax-mcp quick dev cycle ────────────────────────────────
:: Kills Max, builds C++ bridge, deploys bridge + MaxScript, relaunches.
:: Usage: dev.bat [scenefile.max]

set PROJECT_DIR=%~dp0
set NATIVE_DIR=%PROJECT_DIR%native\
set CMAKE="C:\Program Files\CMake\bin\cmake.exe"
set MAX_EXE="C:\Program Files\Autodesk\3ds Max 2026\3dsmax.exe"
set MAX_PLUGINS=C:\Program Files\Autodesk\3ds Max 2026\plugins
set MAX_SCRIPTS=C:\Program Files\Autodesk\3ds Max 2026\scripts

:: Save scene path argument (optional)
set SCENE=%~1

:: ── Kill Max ────────────────────────────────────────────────────
echo [1/5] Killing 3ds Max...
taskkill /F /IM 3dsmax.exe >nul 2>&1
timeout /t 2 /nobreak >nul

:: ── Build C++ bridge ────────────────────────────────────────────
echo [2/5] Building C++ bridge...
if not exist "%NATIVE_DIR%build\CMakeCache.txt" (
    %CMAKE% -B "%NATIVE_DIR%build" -G "Visual Studio 17 2022" -A x64 "%NATIVE_DIR%"
    if %ERRORLEVEL% NEQ 0 goto :fail
)
%CMAKE% --build "%NATIVE_DIR%build" --config Release
if %ERRORLEVEL% NEQ 0 goto :fail

:: ── Deploy C++ bridge ───────────────────────────────────────────
echo [3/5] Deploying mcp_bridge.gup...
copy /Y "%NATIVE_DIR%build\Release\mcp_bridge.gup" "%MAX_PLUGINS%\mcp_bridge.gup" >nul
if %ERRORLEVEL% NEQ 0 (
    echo Deploy failed - need admin. Elevating...
    powershell -Command "Start-Process cmd -ArgumentList '/c copy /Y \"%NATIVE_DIR%build\Release\mcp_bridge.gup\" \"%MAX_PLUGINS%\mcp_bridge.gup\"' -Verb RunAs -Wait"
)

:: ── Deploy MaxScript ────────────────────────────────────────────
echo [4/5] Deploying MaxScript server...
if not exist "%MAX_SCRIPTS%\mcp" mkdir "%MAX_SCRIPTS%\mcp" >nul 2>&1
copy /Y "%PROJECT_DIR%maxscript\mcp_server.ms" "%MAX_SCRIPTS%\mcp\mcp_server.ms" >nul
copy /Y "%PROJECT_DIR%maxscript\startup\mcp_autostart.ms" "%MAX_SCRIPTS%\startup\mcp_autostart.ms" >nul
if %ERRORLEVEL% NEQ 0 (
    echo MaxScript deploy failed - need admin. Elevating...
    powershell -Command "Start-Process cmd -ArgumentList '/c if not exist \"%MAX_SCRIPTS%\mcp\" mkdir \"%MAX_SCRIPTS%\mcp\" & copy /Y \"%PROJECT_DIR%maxscript\mcp_server.ms\" \"%MAX_SCRIPTS%\mcp\mcp_server.ms\" & copy /Y \"%PROJECT_DIR%maxscript\startup\mcp_autostart.ms\" \"%MAX_SCRIPTS%\startup\mcp_autostart.ms\"' -Verb RunAs -Wait"
)

:: ── Relaunch ────────────────────────────────────────────────────
echo [5/5] Launching 3ds Max...
if "%SCENE%"=="" (
    start "" %MAX_EXE%
) else (
    start "" %MAX_EXE% "%SCENE%"
)

echo.
echo === Done! Max is starting with fresh bridge + server. ===
exit /b 0

:fail
echo.
echo === BUILD FAILED ===
pause
exit /b 1
