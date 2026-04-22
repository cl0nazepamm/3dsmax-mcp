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
    std::string SceneDelta(
        const std::string& params,
        MCPBridgeGUP* gup,
        const std::string& session_id = ""
    );
    void ResetSceneDeltaSessions();
    void ReleaseSceneDeltaSession(const std::string& session_id);

    // Phase 1: Object operations
    std::string GetObjectProperties(const std::string& params, MCPBridgeGUP* gup);
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
    std::string BatchModify(const std::string& params, MCPBridgeGUP* gup);

    // Phase 3: Inspect & scene query
    std::string InspectObject(const std::string& params, MCPBridgeGUP* gup);
    std::string InspectProperties(const std::string& params, MCPBridgeGUP* gup);
    std::string GetMaterials(const std::string& params, MCPBridgeGUP* gup);
    std::string FindObjectsByProperty(const std::string& params, MCPBridgeGUP* gup);
    std::string GetInstances(const std::string& params, MCPBridgeGUP* gup);
    std::string GetDependencies(const std::string& params, MCPBridgeGUP* gup);
    std::string GetMaterialSlots(const std::string& params, MCPBridgeGUP* gup);
    std::string WriteOSLShader(const std::string& params, MCPBridgeGUP* gup);

    // Phase 4: Scene management
    std::string SetParent(const std::string& params, MCPBridgeGUP* gup);
    std::string BatchRenameObjects(const std::string& params, MCPBridgeGUP* gup);
    std::string ManageScene(const std::string& params, MCPBridgeGUP* gup);

    // File access (new feature)
    std::string InspectMaxFile(const std::string& params, MCPBridgeGUP* gup);
    std::string MergeFromFile(const std::string& params, MCPBridgeGUP* gup);
    std::string BatchFileInfo(const std::string& params, MCPBridgeGUP* gup);

    // Viewport capture
    std::string CaptureMultiView(const std::string& params, MCPBridgeGUP* gup);
    std::string CaptureViewport(const std::string& params, MCPBridgeGUP* gup);
    std::string CaptureScreen(const std::string& params, MCPBridgeGUP* gup);

    // Phase 6: Material writes
    std::string AssignMaterial(const std::string& params, MCPBridgeGUP* gup);
    std::string SetMaterialProperty(const std::string& params, MCPBridgeGUP* gup);
    std::string SetMaterialProperties(const std::string& params, MCPBridgeGUP* gup);

    // Shell material creation
    std::string CreateShellMaterial(const std::string& params, MCPBridgeGUP* gup);

    // Plugin enumeration
    std::string ListPluginClasses(const std::string& params, MCPBridgeGUP* gup);

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

    // Chat UI (v0.6.0)
    std::string ChatUI(const std::string& params, MCPBridgeGUP* gup);
}
