@echo off
setlocal

set NATIVE_DIR=%~dp0
set CMAKE="C:\Program Files\CMake\bin\cmake.exe"

:: Configure if needed
if not exist "%NATIVE_DIR%build\CMakeCache.txt" (
    echo [1/3] Configuring...
    %CMAKE% -B "%NATIVE_DIR%build" -G "Visual Studio 17 2022" -A x64 "%NATIVE_DIR%"
    if %ERRORLEVEL% NEQ 0 goto :fail
)

:: Build
echo [2/3] Building...
%CMAKE% --build "%NATIVE_DIR%build" --config Release
if %ERRORLEVEL% NEQ 0 goto :fail

:: Deploy
set PLUGIN_FILE=mcp_bridge.gup
set PLUGIN_SRC=%NATIVE_DIR%build\Release\%PLUGIN_FILE%
set PLUGIN_DST=C:\Program Files\Autodesk\3ds Max 2026\plugins\%PLUGIN_FILE%

echo [3/3] Deploying %PLUGIN_FILE% to 3ds Max plugins...
copy /Y "%PLUGIN_SRC%" "%PLUGIN_DST%"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Deploy failed - retrying as Administrator...
    powershell -Command "Start-Process cmd -ArgumentList '/c copy /Y \"%PLUGIN_SRC%\" \"%PLUGIN_DST%\" && echo SUCCESS && pause' -Verb RunAs"
    goto :done
)

echo.
echo === Done! Restart 3ds Max to load %PLUGIN_FILE% ===
goto :done

:fail
echo.
echo === BUILD FAILED ===
pause
exit /b 1

:done
pause
