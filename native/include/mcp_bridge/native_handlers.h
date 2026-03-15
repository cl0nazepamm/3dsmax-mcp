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
}
