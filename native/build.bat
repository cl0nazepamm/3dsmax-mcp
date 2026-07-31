@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "NATIVE_DIR=%~dp0"
if "%NATIVE_DIR:~-1%"=="\" set "NATIVE_DIR=%NATIVE_DIR:~0,-1%"
set "CMAKE_EXE=%CMAKE_EXE%"
if not defined CMAKE_EXE set "CMAKE_EXE=C:\Program Files\CMake\bin\cmake.exe"

set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=all"

set "DO_DEPLOY=0"
if /I "%~2"=="deploy" set "DO_DEPLOY=1"
if /I "%~1"=="deploy" (
    set "TARGET=all"
    set "DO_DEPLOY=1"
)

if not exist "%CMAKE_EXE%" (
    echo CMake not found: %CMAKE_EXE%
    echo Set CMAKE_EXE to the full cmake.exe path or install CMake.
    exit /b 1
)

if /I "%TARGET%"=="all" (
    for %%Y in (2023 2024 2025 2026 2027) do call :build_one %%Y || goto :fail
    goto :done
)

call :build_one %TARGET%
if errorlevel 1 goto :fail
goto :done

:build_one
set "MAX_VERSION=%~1"
if "%MAX_VERSION%"=="" exit /b 1
if not "%MAX_VERSION%"=="2023" if not "%MAX_VERSION%"=="2024" if not "%MAX_VERSION%"=="2025" if not "%MAX_VERSION%"=="2026" if not "%MAX_VERSION%"=="2027" (
    echo Unsupported Max version: %MAX_VERSION%
    echo Usage: build.bat [all^|2023^|2024^|2025^|2026^|2027] [deploy]
    exit /b 1
)

set "MAXSDK_ENV=ADSK_3DSMAX_SDK_%MAX_VERSION%"
set "MAXSDK_PATH=!%MAXSDK_ENV%!"
set "BUILD_DIR=%NATIVE_DIR%\build-%MAX_VERSION%"
set "OUT_DIR=%NATIVE_DIR%\bin"
set "BUILT_GUP=%BUILD_DIR%\Release\mcp_bridge.gup"
set "STAGED_GUP=%OUT_DIR%\mcp_bridge_%MAX_VERSION%.gup"

echo %MAXSDK_PATH%

if not exist "%MAXSDK_PATH%\include\max.h" (
    echo Missing 3ds Max %MAX_VERSION% SDK: %MAXSDK_PATH%
    exit /b 1
)

echo.
echo === Building 3ds Max %MAX_VERSION% native bridge ===
echo [1/3] Configuring...
"%CMAKE_EXE%" -S "%NATIVE_DIR%" -B "%BUILD_DIR%" -G "Visual Studio 17 2022" -A x64 -DMAX_VERSION=%MAX_VERSION% "-DMAXSDK_PATH=%MAXSDK_PATH%"
if errorlevel 1 exit /b 1

echo [2/3] Building Release...
"%CMAKE_EXE%" --build "%BUILD_DIR%" --config Release
if errorlevel 1 exit /b 1

if not exist "%BUILT_GUP%" (
    echo Build finished but output was not found: %BUILT_GUP%
    exit /b 1
)

if not exist "%OUT_DIR%" mkdir "%OUT_DIR%"
echo [3/3] Staging %STAGED_GUP%
copy /Y "%BUILT_GUP%" "%STAGED_GUP%" >nul
if errorlevel 1 exit /b 1

set "BUNDLE_BIN=%NATIVE_DIR%\..\bundle\Contents\bin"
if not exist "%BUNDLE_BIN%" mkdir "%BUNDLE_BIN%"
copy /Y "%STAGED_GUP%" "%BUNDLE_BIN%\mcp_bridge_%MAX_VERSION%.gup" >nul
if errorlevel 1 exit /b 1

exit /b 0

:fail
echo.
echo === BUILD FAILED ===
exit /b 1

:done
echo.
if "%DO_DEPLOY%"=="1" (
    echo === Deploy: run uv run python install.py from repo root ===
)
echo === Done ===
