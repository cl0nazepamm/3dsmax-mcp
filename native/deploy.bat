@echo off
:: Deploy mcp_bridge.gup to 3ds Max plugins folder (requires Run as Administrator)
set PLUGIN_SRC=%~dp0build\Release\mcp_bridge.gup
set PLUGIN_DST=C:\Program Files\Autodesk\3ds Max 2026\plugins\mcp_bridge.gup

if not exist "%PLUGIN_SRC%" (
    echo ERROR: Build first! %PLUGIN_SRC% not found.
    pause
    exit /b 1
)

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
