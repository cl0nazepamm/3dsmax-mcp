@echo off
:: Deploy mcp_bridge.gup to 3ds Max plugins folder (requires Run as Administrator)
:: Also deploys config template and skill file to %LOCALAPPDATA%\3dsmax-mcp\
set PLUGIN_SRC=%~dp0build\Release\mcp_bridge.gup
set PLUGIN_DST=C:\Program Files\Autodesk\3ds Max 2026\plugins\mcp_bridge.gup

set CONFIG_SRC=%~dp0..\mcp_config.ini
set CONFIG_DIR=%LOCALAPPDATA%\3dsmax-mcp
set CONFIG_DST=%CONFIG_DIR%\mcp_config.ini

set ENV_SRC=%~dp0..\.env.example
set ENV_DST=%CONFIG_DIR%\.env

set SKILL_SRC=%~dp0..\skills\3dsmax-mcp-dev\SKILL.md
set SKILL_DIR=%CONFIG_DIR%\skill
set SKILL_DST=%SKILL_DIR%\SKILL.md

if not exist "%PLUGIN_SRC%" (
    echo ERROR: Build first! %PLUGIN_SRC% not found.
    pause
    exit /b 1
)

:: Ensure config + skill directories exist (user-local, no admin needed)
if not exist "%CONFIG_DIR%" mkdir "%CONFIG_DIR%"
if not exist "%SKILL_DIR%"  mkdir "%SKILL_DIR%"

:: Config template: only copy if user doesn't already have one
if not exist "%CONFIG_DST%" (
    copy /Y "%CONFIG_SRC%" "%CONFIG_DST%" >nul
    echo Installed config template: %CONFIG_DST%
) else (
    echo Preserving existing config: %CONFIG_DST%
)

:: .env template: only copy if user doesn't already have one (preserve api_key)
if not exist "%ENV_DST%" (
    copy /Y "%ENV_SRC%" "%ENV_DST%" >nul
    echo Installed .env template:   %ENV_DST%
    echo   ^> edit this file and add your OPENROUTER_API_KEY
) else (
    echo Preserving existing .env:  %ENV_DST%
)

:: Skill file: always overwrite — it's the source of truth for chat system prompt
copy /Y "%SKILL_SRC%" "%SKILL_DST%" >nul
if %ERRORLEVEL% EQU 0 (
    echo Deployed skill:            %SKILL_DST%
)

:: Plugin: requires admin
copy /Y "%PLUGIN_SRC%" "%PLUGIN_DST%"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Deploy failed - retrying as Administrator...
    powershell -Command "Start-Process cmd -ArgumentList '/c copy /Y \"%PLUGIN_SRC%\" \"%PLUGIN_DST%\" && echo SUCCESS && pause' -Verb RunAs"
    goto :done
)

echo Deployed mcp_bridge.gup to 3ds Max plugins folder.
echo Restart 3ds Max to load the plugin.

:done
pause
