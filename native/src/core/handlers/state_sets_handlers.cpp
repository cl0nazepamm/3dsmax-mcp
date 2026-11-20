#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/core_bridge.h"

using json = nlohmann::json;
using namespace HandlerHelpers;

// ── native:get_state_sets ───────────────────────────────────────
std::string NativeHandlers::GetStateSets(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&]() -> std::string {
        std::string ms;
        ms += "(\n";
        ms += "  local DQ = bit.intAsChar 34 as string\n";
        ms += "  local BS = bit.intAsChar 92 as string\n";
        ms += "  fn __esc s = (s = substituteString s BS (BS+BS); s = substituteString s DQ (BS+DQ); s)\n";
        ms += "  local master = (dotNetClass \"Autodesk.Max.StateSets.Plugin\").Instance.EntityManager.RootEntity.Children.Item[0]\n";
        ms += "  local tpf = ticksPerFrame\n";
        ms += "  local fps = frameRate\n";
        ms += "  local results = \"[\"\n";
        ms += "  for i = 0 to (master.Children.Count - 1) do (\n";
        ms += "    local ss = master.Children.Item[i]\n";
        ms += "    local ssName = ss.Name as string\n";
        ms += "    local cam = try (ss.ActiveViewportCamera as string) catch (\"\")\n";
        ms += "    local renderStart = try ((ss.RenderRange.Start / tpf) as integer) catch (0)\n";
        ms += "    local renderEnd = try ((ss.RenderRange.End / tpf) as integer) catch (0)\n";
        ms += "    local animStart = try ((ss.AnimationRange.Start / tpf) as integer) catch (0)\n";
        ms += "    local animEnd = try ((ss.AnimationRange.End / tpf) as integer) catch (0)\n";
        ms += "    local lockCam = try (ss.IsLockingCameraAnimationToRange) catch (false)\n";
        ms += "    if i > 0 do results += \",\"\n";
        ms += "    results += \"{\" + DQ + \"name\" + DQ + \":\" + DQ + (__esc ssName) + DQ + \",\"\n";
        ms += "    results += DQ + \"camera\" + DQ + \":\" + DQ + (__esc cam) + DQ + \",\"\n";
        ms += "    results += DQ + \"renderRange\" + DQ + \":{\" + DQ + \"start\" + DQ + \":\" + (renderStart as string) + \",\" + DQ + \"end\" + DQ + \":\" + (renderEnd as string) + \"},\"\n";
        ms += "    results += DQ + \"animRange\" + DQ + \":{\" + DQ + \"start\" + DQ + \":\" + (animStart as string) + \",\" + DQ + \"end\" + DQ + \":\" + (animEnd as string) + \"},\"\n";
        ms += "    results += DQ + \"lockCameraToRange\" + DQ + \":\" + (if lockCam then \"true\" else \"false\") + \"}\"\n";
        ms += "  )\n";
        ms += "  results += \"]\"\n";
        ms += "  \"{\" + DQ + \"ticksPerFrame\" + DQ + \":\" + (tpf as string) + \",\" + DQ + \"fps\" + DQ + \":\" + (fps as string) + \",\" + DQ + \"stateSets\" + DQ + \":\" + results + \"}\"\n";
        ms += ")\n";
        return RunMAXScript(ms);
    });
}

// ── native:get_camera_sequence ──────────────────────────────────
std::string NativeHandlers::GetCameraSequence(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&]() -> std::string {
        std::string ms;
        ms += "(\n";
        ms += "  local DQ = bit.intAsChar 34 as string\n";
        ms += "  local BS = bit.intAsChar 92 as string\n";
        ms += "  fn __esc s = (s = substituteString s BS (BS+BS); s = substituteString s DQ (BS+DQ); s)\n";
        ms += "  local master = (dotNetClass \"Autodesk.Max.StateSets.Plugin\").Instance.EntityManager.RootEntity.Children.Item[0]\n";
        ms += "  local tpf = ticksPerFrame\n";
        ms += "  local fps = frameRate\n";
        ms += "  local camSets = #()\n";
        ms += "  for i = 0 to (master.Children.Count - 1) do (\n";
        ms += "    local ss = master.Children.Item[i]\n";
        ms += "    local cam = try (ss.ActiveViewportCamera as string) catch (\"\")\n";
        ms += "    if cam != \"\" and cam != \"undefined\" and cam != undefined do (\n";
        ms += "      local startF = try ((ss.RenderRange.Start / tpf) as integer) catch (0)\n";
        ms += "      local endF = try ((ss.RenderRange.End / tpf) as integer) catch (0)\n";
        ms += "      append camSets #(ss.Name as string, cam, startF, endF)\n";
        ms += "    )\n";
        ms += "  )\n";
        ms += "  for i = 1 to camSets.count do (\n";
        ms += "    for j = 1 to (camSets.count - i) do (\n";
        ms += "      if camSets[j][3] > camSets[j+1][3] do (\n";
        ms += "        local tmp = camSets[j]\n";
        ms += "        camSets[j] = camSets[j+1]\n";
        ms += "        camSets[j+1] = tmp\n";
        ms += "      )\n";
        ms += "    )\n";
        ms += "  )\n";
        ms += "  local r = \"[\"\n";
        ms += "  for i = 1 to camSets.count do (\n";
        ms += "    if i > 1 do r += \",\"\n";
        ms += "    r += \"{\" + DQ + \"stateSet\" + DQ + \":\" + DQ + (__esc camSets[i][1]) + DQ + \",\"\n";
        ms += "    r += DQ + \"camera\" + DQ + \":\" + DQ + (__esc camSets[i][2]) + DQ + \",\"\n";
        ms += "    r += DQ + \"startFrame\" + DQ + \":\" + (camSets[i][3] as string) + \",\"\n";
        ms += "    r += DQ + \"endFrame\" + DQ + \":\" + (camSets[i][4] as string) + \"}\"\n";
        ms += "  )\n";
        ms += "  r += \"]\"\n";
        ms += "  \"{\" + DQ + \"fps\" + DQ + \":\" + (fps as string) + \",\" + DQ + \"cameraCount\" + DQ + \":\" + (camSets.count as string) + \",\" + DQ + \"sequence\" + DQ + \":\" + r + \"}\"\n";
        ms += ")\n";
        return RunMAXScript(ms);
    });
}
