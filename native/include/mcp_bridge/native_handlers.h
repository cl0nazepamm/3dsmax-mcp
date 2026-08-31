#pragma once
#include <string>

class MCPBridgeGUP;

namespace NativeHandlers {
    // Scene reads
    std::string SceneInfo(const std::string& params, MCPBridgeGUP* gup);
    std::string Selection(const std::string& params, MCPBridgeGUP* gup);
    std::string SceneSnapshot(const std::string& params, MCPBridgeGUP* gup);
    std::string SelectionSnapshot(const std::string& params, MCPBridgeGUP* gup);
    std::string FindClassInstances(const std::string& params, MCPBridgeGUP* gup);
    std::string GetHierarchy(const std::string& params, MCPBridgeGUP* gup);
    std::string ResolveNodeRefs(const std::string& params, MCPBridgeGUP* gup);
    std::string SceneDelta(
        const std::string& params,
        MCPBridgeGUP* gup,
        const std::string& session_id = ""
    );
    void ResetSceneDeltaSessions();
    void ReleaseSceneDeltaSession(const std::string& session_id);

    // Preflighted multi-node mutations in one strict native undo hold.
    std::string ScenePatch(const std::string& params, MCPBridgeGUP* gup);

    // Deterministic scene-graph QA. Scan is read-only; fix has its own route so
    // dispatcher mutation/undo classification cannot be bypassed by payload.
    std::string SceneQAScan(const std::string& params, MCPBridgeGUP* gup);
    std::string SceneQAFix(const std::string& params, MCPBridgeGUP* gup);

    // Phase 1: Object operations
    std::string GetObjectProperties(const std::string& params, MCPBridgeGUP* gup);
    std::string AnalyzeNodeOrientation(const std::string& params, MCPBridgeGUP* gup);
    std::string SetObjectProperty(const std::string& params, MCPBridgeGUP* gup);
    std::string CreateObject(const std::string& params, MCPBridgeGUP* gup);
    std::string DeleteObjects(const std::string& params, MCPBridgeGUP* gup);
    std::string TransformObject(const std::string& params, MCPBridgeGUP* gup);
    std::string SelectObjects(const std::string& params, MCPBridgeGUP* gup);
    std::string SetVisibility(const std::string& params, MCPBridgeGUP* gup);
    std::string CloneObjects(const std::string& params, MCPBridgeGUP* gup);

    // Phase 2: Modifier operations
    std::string AddModifier(const std::string& params, MCPBridgeGUP* gup);
    std::string RemoveModifier(const std::string& params, MCPBridgeGUP* gup);
    std::string SetModifierState(const std::string& params, MCPBridgeGUP* gup);
    std::string CollapseModifierStack(const std::string& params, MCPBridgeGUP* gup);
    std::string MakeModifierUnique(const std::string& params, MCPBridgeGUP* gup);
    std::string SetModifierProperty(const std::string& params, MCPBridgeGUP* gup);

    // Max Creation Graph scripted modifiers. These handlers deliberately use
    // exact class IDs and typed PB2 node references instead of MAXScript class
    // evaluation so a stale/colliding generated wrapper cannot be applied.
    std::string MCGResolveClass(const std::string& params, MCPBridgeGUP* gup);
    std::string MCGApplyModifier(const std::string& params, MCPBridgeGUP* gup);
    std::string MCGSetNodeParameter(const std::string& params, MCPBridgeGUP* gup);
    std::string MCGInspectInstance(const std::string& params, MCPBridgeGUP* gup);

    // Phase 3: Inspect & scene query
    std::string InspectObject(const std::string& params, MCPBridgeGUP* gup);
    std::string InspectProperties(const std::string& params, MCPBridgeGUP* gup);
    std::string GetMaterials(const std::string& params, MCPBridgeGUP* gup);
    std::string FindObjectsByProperty(const std::string& params, MCPBridgeGUP* gup);
    std::string GetInstances(const std::string& params, MCPBridgeGUP* gup);
    std::string GetDependencies(const std::string& params, MCPBridgeGUP* gup);
    std::string GetMaterialSlots(const std::string& params, MCPBridgeGUP* gup);
    std::string GetMaterialLibrary(const std::string& params, MCPBridgeGUP* gup);
    std::string WriteOSLShader(const std::string& params, MCPBridgeGUP* gup);
    std::string InspectMaterialNetwork(const std::string& params, MCPBridgeGUP* gup);
    std::string ReplicateMaterial(const std::string& params, MCPBridgeGUP* gup);
    std::string ReplicateMaterialPreview(const std::string& params, MCPBridgeGUP* gup);
    std::string ReplicateMaterialApply(const std::string& params, MCPBridgeGUP* gup);

    // Phase 4: Scene management
    std::string SetParent(const std::string& params, MCPBridgeGUP* gup);
    std::string BatchRenameObjects(const std::string& params, MCPBridgeGUP* gup);
    std::string ManageScene(const std::string& params, MCPBridgeGUP* gup);
    std::string UndoLast(const std::string& params, MCPBridgeGUP* gup);

    // File access (new feature)
    std::string InspectMaxFile(const std::string& params, MCPBridgeGUP* gup);
    std::string MergeFromFile(const std::string& params, MCPBridgeGUP* gup);
    std::string BatchFileInfo(const std::string& params, MCPBridgeGUP* gup);

    // Viewport capture
    std::string CaptureMultiView(const std::string& params, MCPBridgeGUP* gup);
    std::string CaptureViewport(const std::string& params, MCPBridgeGUP* gup);
    std::string CaptureScreen(const std::string& params, MCPBridgeGUP* gup);
    std::string IsolateAndCaptureSelected(const std::string& params, MCPBridgeGUP* gup);

    // Phase 6: Material writes
    std::string AssignMaterial(const std::string& params, MCPBridgeGUP* gup);
    std::string SetMaterialProperty(const std::string& params, MCPBridgeGUP* gup);
    std::string SetMaterialProperties(const std::string& params, MCPBridgeGUP* gup);

    // Shell material creation
    std::string CreateShellMaterial(const std::string& params, MCPBridgeGUP* gup);
    std::string BackupMaterialLibrary(const std::string& params, MCPBridgeGUP* gup);

    // Plugin enumeration
    std::string ListPluginClasses(const std::string& params, MCPBridgeGUP* gup);
    std::string GetPluginCapabilities(const std::string& params, MCPBridgeGUP* gup);

    // Controller / track inspection
    std::string InspectTrackView(const std::string& params, MCPBridgeGUP* gup);
    std::string ListWireableParams(const std::string& params, MCPBridgeGUP* gup);

    // Plugin introspection (deep SDK reflection)
    std::string DiscoverClasses(const std::string& params, MCPBridgeGUP* gup);
    std::string IntrospectClass(const std::string& params, MCPBridgeGUP* gup);
    std::string IntrospectInstance(const std::string& params, MCPBridgeGUP* gup);

    // Scene organization
    std::string ManageLayers(const std::string& params, MCPBridgeGUP* gup);
    std::string ManageGroups(const std::string& params, MCPBridgeGUP* gup);
    std::string ManageSelectionSets(const std::string& params, MCPBridgeGUP* gup);

    // Deep SDK learning
    std::string WalkReferences(const std::string& params, MCPBridgeGUP* gup);
    std::string MapClassRelationships(const std::string& params, MCPBridgeGUP* gup);
    std::string LearnScenePatterns(const std::string& params, MCPBridgeGUP* gup);
    std::string WatchScene(const std::string& params, MCPBridgeGUP* gup);

    // Effects
    std::string GetEffects(const std::string& params, MCPBridgeGUP* gup);
    std::string ToggleEffect(const std::string& params, MCPBridgeGUP* gup);
    std::string DeleteEffect(const std::string& params, MCPBridgeGUP* gup);

    // Render
    std::string RenderScene(const std::string& params, MCPBridgeGUP* gup);

    // Render automation: kick a deferred render and emit a done-signal file at
    // NOTIFY_POST_RENDER. RenderStart returns immediately; the bridge is not
    // blocked for the render. Register/Unregister are called from GUP Start/Stop.
    // RenderCancel runs on the pipe thread (never marshals — the main thread IS
    // the render) and raises the SDK abort flag, like pressing Cancel.
    std::string RenderStart(const std::string& params, MCPBridgeGUP* gup);
    std::string RenderCancel(const std::string& params, MCPBridgeGUP* gup);
    void RegisterRenderNotifications();
    void UnregisterRenderNotifications();

    // Material replace
    std::string ReplaceMaterial(const std::string& params, MCPBridgeGUP* gup);
    std::string BatchReplaceMaterials(const std::string& params, MCPBridgeGUP* gup);

    // Texture map ops
    std::string CreateTextureMap(const std::string& params, MCPBridgeGUP* gup);
    std::string SetTextureMapProperties(const std::string& params, MCPBridgeGUP* gup);
    std::string SetSubMaterial(const std::string& params, MCPBridgeGUP* gup);

    // Controllers (extended)
    std::string AssignController(const std::string& params, MCPBridgeGUP* gup);
    std::string InspectController(const std::string& params, MCPBridgeGUP* gup);
    std::string SetControllerProps(const std::string& params, MCPBridgeGUP* gup);
    std::string AddControllerTarget(const std::string& params, MCPBridgeGUP* gup);
    std::string KeyframeTracks(const std::string& params, MCPBridgeGUP* gup);

    // Wire params
    std::string WireParams(const std::string& params, MCPBridgeGUP* gup);
    std::string GetWiredParams(const std::string& params, MCPBridgeGUP* gup);
    std::string UnwireParams(const std::string& params, MCPBridgeGUP* gup);

    // State sets
    std::string GetStateSets(const std::string& params, MCPBridgeGUP* gup);
    std::string GetCameraSequence(const std::string& params, MCPBridgeGUP* gup);

    // System discovery (macroscripts, actions, interfaces)
    std::string ListMacroscripts(const std::string& params, MCPBridgeGUP* gup);
    std::string ListActionTables(const std::string& params, MCPBridgeGUP* gup);
    std::string IntrospectInterface(const std::string& params, MCPBridgeGUP* gup);

    // Direct execution (no MAXScript parsing)
    std::string InvokeInterface(const std::string& params, MCPBridgeGUP* gup);
    std::string RunMacroscript(const std::string& params, MCPBridgeGUP* gup);

    // Chat UI (v0.7.0)
    std::string ChatUI(const std::string& params, MCPBridgeGUP* gup);

    // Live tool smoke testing (in-Max production path)
    std::string InvokeTool(const std::string& params, MCPBridgeGUP* gup);
    std::string RunToolSmoke(const std::string& params, MCPBridgeGUP* gup);

    // Main-thread (UI) hygiene: list what runs on the main thread and kill hooks
    std::string MainThread(const std::string& params, MCPBridgeGUP* gup);
}
