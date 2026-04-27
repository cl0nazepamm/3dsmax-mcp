"""State Sets and Camera Sequencer tools.

Reads camera assignments, frame ranges, and state set data from
3ds Max's State Sets system via the .NET API
(Autodesk.Max.StateSets.Plugin).
"""

import json
from ..server import mcp, client


@mcp.tool()
def get_state_sets() -> str:
    """Get all State Sets with their camera assignments and frame ranges."""
    if client.native_available:
        response = client.send_command("{}", cmd_type="native:get_state_sets")
        return response.get("result", "")

    maxscript = r"""(
        fn getStateSetsJSON = (
            ssPlug = (dotNetClass "Autodesk.Max.StateSets.Plugin").Instance
            if ssPlug == undefined then return "{\"error\": \"State Sets plugin not available\"}"

            ssRoot = ssPlug.EntityManager.RootEntity
            if ssRoot == undefined then return "{\"error\": \"No root entity found\"}"

            ssMaster = undefined
            for i = 0 to ssRoot.Children.Count - 1 do (
                child = ssRoot.Children.Item[i]
                if (child.GetType()).Name == "Master" do (
                    ssMaster = child
                    exit
                )
            )
            if ssMaster == undefined then return "{\"error\": \"No State Sets Master found\"}"

            tpf = ticksPerFrame
            ssKids = ssMaster.Children
            ssJSON = "{\"ticksPerFrame\": " + tpf as string
            ssJSON += ", \"fps\": " + (frameRate as integer) as string
            ssJSON += ", \"stateSets\": ["

            for idx = 0 to ssKids.Count - 1 do (
                ssItem = ssKids.Item[idx]
                if idx > 0 do ssJSON += ","

                ssJSON += "{\"name\": \"" + ssItem.Name + "\""

                -- Camera
                camName = ""
                try (
                    cam = ssItem.ActiveViewportCamera
                    if cam != undefined do camName = cam.Name
                ) catch ()
                if camName != "" then
                    ssJSON += ", \"camera\": \"" + camName + "\""
                else
                    ssJSON += ", \"camera\": null"

                -- Render Range
                try (
                    rr = ssItem.RenderRange
                    if rr != undefined do (
                        startTick = rr.Start
                        endTick = rr.End
                        startFrame = startTick / tpf
                        endFrame = endTick / tpf
                        ssJSON += ", \"renderRange\": {\"startFrame\": " + startFrame as string
                        ssJSON += ", \"endFrame\": " + endFrame as string + "}"
                    )
                ) catch (
                    ssJSON += ", \"renderRange\": null"
                )

                -- Animation Range
                try (
                    ar = ssItem.AnimationRange
                    if ar != undefined then (
                        ssJSON += ", \"animRange\": {\"startFrame\": " + (ar.Start / tpf) as string
                        ssJSON += ", \"endFrame\": " + (ar.End / tpf) as string + "}"
                    ) else (
                        ssJSON += ", \"animRange\": null"
                    )
                ) catch (
                    ssJSON += ", \"animRange\": null"
                )

                -- Lock camera anim to range
                try (
                    lockVal = ssItem.IsLockingCameraAnimationToRange
                    if lockVal then ssJSON += ", \"lockCameraToRange\": true"
                    else ssJSON += ", \"lockCameraToRange\": false"
                ) catch (
                    ssJSON += ", \"lockCameraToRange\": false"
                )

                ssJSON += "}"
            )

            ssJSON += "]}"
            return ssJSON
        )
        getStateSetsJSON()
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")


@mcp.tool()
def get_camera_sequence() -> str:
    """Get only the camera-assigned State Sets, sorted by start frame."""
    if client.native_available:
        response = client.send_command("{}", cmd_type="native:get_camera_sequence")
        return response.get("result", "")

    maxscript = r"""(
        fn getCameraSequenceJSON = (
            ssPlug = (dotNetClass "Autodesk.Max.StateSets.Plugin").Instance
            if ssPlug == undefined then return "{\"error\": \"State Sets plugin not available\"}"

            ssRoot = ssPlug.EntityManager.RootEntity
            if ssRoot == undefined then return "{\"error\": \"No root entity found\"}"

            ssMaster = undefined
            for i = 0 to ssRoot.Children.Count - 1 do (
                child = ssRoot.Children.Item[i]
                if (child.GetType()).Name == "Master" do (
                    ssMaster = child
                    exit
                )
            )
            if ssMaster == undefined then return "{\"error\": \"No State Sets Master found\"}"

            tpf = ticksPerFrame

            -- Collect camera entries: #(startFrame, endFrame, cameraName, stateSetName)
            camEntries = #()
            ssKids = ssMaster.Children
            for idx = 0 to ssKids.Count - 1 do (
                ssItem = ssKids.Item[idx]
                camName = ""
                try (
                    cam = ssItem.ActiveViewportCamera
                    if cam != undefined do camName = cam.Name
                ) catch ()

                if camName != "" do (
                    startFrame = 0
                    endFrame = 0
                    try (
                        rr = ssItem.RenderRange
                        if rr != undefined do (
                            startFrame = rr.Start / tpf
                            endFrame = rr.End / tpf
                        )
                    ) catch ()
                    append camEntries #(startFrame, endFrame, camName, ssItem.Name)
                )
            )

            -- Sort by start frame (simple bubble sort)
            for i = 1 to camEntries.count do (
                for j = 1 to camEntries.count - i do (
                    if camEntries[j][1] > camEntries[j+1][1] do (
                        temp = camEntries[j]
                        camEntries[j] = camEntries[j+1]
                        camEntries[j+1] = temp
                    )
                )
            )

            seqJSON = "{\"fps\": " + (frameRate as integer) as string
            seqJSON += ", \"cameraCount\": " + camEntries.count as string
            seqJSON += ", \"sequence\": ["
            for i = 1 to camEntries.count do (
                if i > 1 do seqJSON += ","
                seqJSON += "{\"stateSet\": \"" + camEntries[i][4] + "\""
                seqJSON += ", \"camera\": \"" + camEntries[i][3] + "\""
                seqJSON += ", \"startFrame\": " + camEntries[i][1] as string
                seqJSON += ", \"endFrame\": " + camEntries[i][2] as string + "}"
            )
            seqJSON += "]}"
            return seqJSON
        )
        getCameraSequenceJSON()
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")
