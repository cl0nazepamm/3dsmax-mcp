#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/bridge_gup.h"

#include <iparamb2.h>

using json = nlohmann::json;
using namespace HandlerHelpers;

// ── Helper: navigate to target material (with sub-material support) ──
static Mtl* GetTargetMaterial(INode* node, int subMatIndex) {
    Mtl* mtl = node->GetMtl();
    if (!mtl) return nullptr;
    if (subMatIndex <= 0) return mtl;
    int idx = subMatIndex - 1;
    if (idx >= mtl->NumSubMtls()) return nullptr;
    return mtl->GetSubMtl(idx);
}

// ── Helper: parse MAXScript-style key:value param string ────
static std::vector<std::pair<std::string, std::string>> ParseMtlParams(const std::string& s) {
    std::vector<std::pair<std::string, std::string>> result;
    size_t i = 0;
    while (i < s.size()) {
        while (i < s.size() && s[i] == ' ') i++;
        if (i >= s.size()) break;
        size_t keyStart = i;
        while (i < s.size() && s[i] != ':') i++;
        if (i >= s.size()) break;
        std::string key = s.substr(keyStart, i - keyStart);
        i++;
        size_t valStart = i;
        if (i < s.size() && (s[i] == '[' || s[i] == '(')) {
            char open = s[i], close = (open == '[') ? ']' : ')';
            int depth = 1; i++;
            while (i < s.size() && depth > 0) {
                if (s[i] == open) depth++;
                else if (s[i] == close) depth--;
                i++;
            }
        } else if (i < s.size() && s[i] == '"') {
            i++;
            while (i < s.size() && s[i] != '"') { if (s[i] == '\\') i++; i++; }
            if (i < s.size()) i++;
        } else {
            while (i < s.size() && s[i] != ' ') i++;
        }
        result.push_back({key, s.substr(valStart, i - valStart)});
    }
    return result;
}

// ── native:assign_material (Pure SDK) ───────────────────────
std::string NativeHandlers::AssignMaterial(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        auto names = p.value("names", std::vector<std::string>{});
        std::string matClass = p.value("material_class", "");
        std::string matName = p.value("material_name", "");
        std::string matParams = p.value("params", "");

        if (names.empty()) throw std::runtime_error("names is required");
        if (matClass.empty()) throw std::runtime_error("material_class is required");

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        // Find material ClassDesc — try SDK DllDir first
        ClassDesc* cd = FindClassDescByName(matClass, MATERIAL_CLASS_ID);
        if (!cd) cd = FindClassDescByName(matClass);

        Mtl* mtl = nullptr;

        if (cd) {
            // Pure SDK path
            mtl = (Mtl*)ip->CreateInstance(cd->SuperClassID(), cd->ClassID());
        }

        if (!mtl) {
            // Fallback: some plugins (Arnold, scripted materials) don't register
            // in DllDir under their MAXScript class name. Create via MAXScript.
            std::string nameParam = matName.empty() ? "" : " name:\"" + JsonEscape(matName) + "\"";
            std::string script = "(" + matClass + nameParam + " " + matParams + ")";
            try {
                RunMAXScript("global __mcp_tmp_mtl = " + script);
                // Now assign via MAXScript too since we can't get the Mtl* back easily
                int assignCount = 0;
                json notFound = json::array();
                for (const auto& name : names) {
                    INode* node = FindNodeByName(name);
                    if (node) {
                        std::string assignScript = "(getNodeByName \"" + JsonEscape(name) +
                            "\").material = __mcp_tmp_mtl";
                        RunMAXScript(assignScript);
                        assignCount++;
                    } else {
                        notFound.push_back(name);
                    }
                }
                // Get the material name back
                std::string mtlName = RunMAXScript("__mcp_tmp_mtl.name");
                std::string mtlClass = RunMAXScript("(classOf __mcp_tmp_mtl) as string");
                ip->RedrawViews(t);

                std::string msg = "Created " + mtlClass + " \"" + mtlName +
                    "\" and assigned to " + std::to_string(assignCount) + " object(s)";
                if (!notFound.empty())
                    msg += " | Not found: " + std::to_string(notFound.size());
                return msg;
            } catch (...) {
                throw std::runtime_error("Unknown material class: " + matClass);
            }
        }

        // Pure SDK path continues — set name
        if (!matName.empty()) {
            std::wstring wname = Utf8ToWide(matName);
            mtl->SetName(wname.c_str());
        }

        // Set params via IParamBlock2
        if (!matParams.empty()) {
            auto kvPairs = ParseMtlParams(matParams);
            for (auto& [key, val] : kvPairs) {
                SetParamByName((Animatable*)mtl, key, val, t);
            }
        }

        // Assign to nodes
        int assignCount = 0;
        json notFound = json::array();
        for (const auto& name : names) {
            INode* node = FindNodeByName(name);
            if (node) {
                node->SetMtl(mtl);
                assignCount++;
            } else {
                notFound.push_back(name);
            }
        }

        mtl->NotifyDependents(FOREVER, PART_ALL, REFMSG_CHANGE);
        ip->RedrawViews(t);

        std::string msg = "Created " + WideToUtf8(mtl->ClassName().data()) + " \"" +
                          WideToUtf8(mtl->GetName().data()) + "\" and assigned to " +
                          std::to_string(assignCount) + " object(s)";
        if (!notFound.empty())
            msg += " | Not found: " + std::to_string(notFound.size());
        return msg;
    });
}

// ── native:set_material_property (Pure SDK + minimal fallback) ──
std::string NativeHandlers::SetMaterialProperty(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string name = p.value("name", "");
        std::string prop = p.value("property", "");
        std::string value = p.value("value", "");
        int subMatIndex = p.value("sub_material_index", 0);

        if (name.empty()) throw std::runtime_error("name is required");
        if (prop.empty()) throw std::runtime_error("property is required");

        INode* node = FindNodeByName(name);
        if (!node) throw std::runtime_error("Object not found: " + name);

        Mtl* mtl = GetTargetMaterial(node, subMatIndex);
        if (!mtl) {
            if (subMatIndex > 0)
                throw std::runtime_error("Sub-material index " + std::to_string(subMatIndex) + " not found");
            throw std::runtime_error("No material assigned to " + name);
        }

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        // Pure SDK path: try IParamBlock2 first
        if (SetParamByName((Animatable*)mtl, prop, value, t)) {
            mtl->NotifyDependents(FOREVER, PART_ALL, REFMSG_CHANGE);
            ip->RedrawViews(t);
            return "Set " + WideToUtf8(mtl->GetName().data()) + "." + prop;
        }

        // For texture map / material reference assignments that reference a MAXScript
        // global variable (e.g. value="FresnelGlow"), we must resolve the variable name
        // to a pointer. This is the ONE case where pure SDK can't work — MAXScript globals
        // have no SDK accessor. We use a single targeted RunMAXScript call.
        std::string matExpr;
        if (subMatIndex > 0)
            matExpr = "(getNodeByName \"" + JsonEscape(name) + "\").material[" + std::to_string(subMatIndex) + "]";
        else
            matExpr = "(getNodeByName \"" + JsonEscape(name) + "\").material";

        std::string script = "try (" + matExpr + "." + prop + " = " + value +
                             "; \"Set " + JsonEscape(prop) + "\") catch (\"Error: \" + getCurrentException())";
        std::string result = RunMAXScript(script);
        ip->RedrawViews(t);
        return result;
    });
}

// ── native:set_material_properties (Pure SDK + minimal fallback) ──
std::string NativeHandlers::SetMaterialProperties(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string name = p.value("name", "");
        auto properties = p.value("properties", std::map<std::string, std::string>{});
        int subMatIndex = p.value("sub_material_index", 0);

        if (name.empty()) throw std::runtime_error("name is required");
        if (properties.empty()) throw std::runtime_error("properties is required");

        INode* node = FindNodeByName(name);
        if (!node) throw std::runtime_error("Object not found: " + name);

        Mtl* mtl = GetTargetMaterial(node, subMatIndex);
        if (!mtl) {
            if (subMatIndex > 0)
                throw std::runtime_error("Sub-material index " + std::to_string(subMatIndex) + " not found");
            throw std::runtime_error("No material assigned to " + name);
        }

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        json okList = json::array();
        json errList = json::array();

        // Build MAXScript mat expression for fallback
        std::string matExpr;
        if (subMatIndex > 0)
            matExpr = "(getNodeByName \"" + JsonEscape(name) + "\").material[" + std::to_string(subMatIndex) + "]";
        else
            matExpr = "(getNodeByName \"" + JsonEscape(name) + "\").material";

        for (auto& [prop, value] : properties) {
            // Try pure SDK IParamBlock2 first
            if (SetParamByName((Animatable*)mtl, prop, value, t)) {
                okList.push_back(prop);
                continue;
            }

            // Fallback for texture map / reference assignments
            try {
                std::string script = matExpr + "." + prop + " = " + value;
                RunMAXScript(script);
                okList.push_back(prop);
            } catch (...) {
                errList.push_back(prop + ": failed to set");
            }
        }

        mtl->NotifyDependents(FOREVER, PART_ALL, REFMSG_CHANGE);
        ip->RedrawViews(t);

        std::string msg = "Set " + std::to_string(okList.size()) + " properties on " +
                          WideToUtf8(mtl->GetName().data());
        if (!okList.empty()) {
            msg += ":";
            for (size_t i = 0; i < okList.size(); i++)
                msg += (i > 0 ? ", " : " ") + okList[i].get<std::string>();
        }
        if (!errList.empty()) {
            msg += " | Errors:";
            for (size_t i = 0; i < errList.size(); i++)
                msg += (i > 0 ? "; " : " ") + errList[i].get<std::string>();
        }
        return msg;
    });
}

// ── native:create_shell_material (Arnold UberBitmap + glTF Shell) ──
std::string NativeHandlers::CreateShellMaterial(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        if (p.is_discarded()) throw std::runtime_error("Invalid JSON params");

        std::string shellName       = p.value("name", "");
        std::string arnoldName      = p.value("render_material_name", "");
        std::string gltfMatName     = p.value("gltf_material_name", "");
        std::string baseColorPath   = p.value("base_color_path", "");
        std::string ormPath         = p.value("orm_path", "");
        std::string normalPath      = p.value("normal_path", "");
        auto assignTo               = p.value("assign_to", std::vector<std::string>{});

        if (shellName.empty()) throw std::runtime_error("name is required");
        if (arnoldName.empty()) throw std::runtime_error("render_material_name is required");
        if (baseColorPath.empty()) throw std::runtime_error("base_color_path is required");
        if (ormPath.empty()) throw std::runtime_error("orm_path is required");

        // Escape strings for safe embedding in MAXScript
        std::string eShellName     = JsonEscape(shellName);
        std::string eArnoldName    = JsonEscape(arnoldName);
        std::string eGltfMatName   = JsonEscape(gltfMatName);
        std::string eBaseColorPath = JsonEscape(baseColorPath);
        std::string eOrmPath       = JsonEscape(ormPath);
        std::string eNormalPath    = JsonEscape(normalPath);

        // OSL path for UberBitmap2 — resolved dynamically from Max install dir

        // Build MAXScript
        std::string script;
        script.reserve(4096);

        script += "(\n";
        script += "  local oslPath = (getDir #maxRoot) + \"OSL\\\\UberBitmap2.osl\"\n";

        // --- Find existing glTF material by name if provided ---
        script += "  local gltfMat = undefined\n";
        if (!gltfMatName.empty()) {
            script += "  for obj in objects do (\n";
            script += "    if obj.material != undefined do (\n";
            script += "      if (classOf obj.material == Multimaterial) then (\n";
            script += "        for i = 1 to obj.material.numsubs do (\n";
            script += "          if obj.material[i] != undefined and obj.material[i].name == \"" + eGltfMatName + "\" do (\n";
            script += "            gltfMat = obj.material[i]\n";
            script += "          )\n";
            script += "        )\n";
            script += "      ) else (\n";
            script += "        if obj.material.name == \"" + eGltfMatName + "\" do gltfMat = obj.material\n";
            script += "      )\n";
            script += "    )\n";
            script += "  )\n";
        }

        // --- UberBitmap for BaseColor ---
        script += "  local uberBC = OSLMap()\n";
        script += "  uberBC.OSLPath = oslPath\n";
        script += "  uberBC.OSLAutoUpdate = true\n";
        script += "  uberBC.filename = \"" + eBaseColorPath + "\"\n";
        script += "  uberBC.name = \"UberBitmap_BaseColor\"\n";

        // --- UberBitmap for ORM ---
        script += "  local uberORM = OSLMap()\n";
        script += "  uberORM.OSLPath = oslPath\n";
        script += "  uberORM.OSLAutoUpdate = true\n";
        script += "  uberORM.filename = \"" + eOrmPath + "\"\n";
        script += "  uberORM.name = \"UberBitmap_ORM\"\n";

        // --- MultiOutputChannelTexmapToTexmap splitters ---
        // BaseColor Col output (index 1)
        script += "  local bcCol = MultiOutputChannelTexmapToTexmap()\n";
        script += "  bcCol.sourceMap = uberBC\n";
        script += "  bcCol.outputChannelIndex = 1\n";
        script += "  bcCol.name = \"BC_Col\"\n";

        // ORM R output (index 2) = AO
        script += "  local ormR = MultiOutputChannelTexmapToTexmap()\n";
        script += "  ormR.sourceMap = uberORM\n";
        script += "  ormR.outputChannelIndex = 2\n";
        script += "  ormR.name = \"ORM_R_AO\"\n";

        // ORM G output (index 3) = Roughness
        script += "  local ormG = MultiOutputChannelTexmapToTexmap()\n";
        script += "  ormG.sourceMap = uberORM\n";
        script += "  ormG.outputChannelIndex = 3\n";
        script += "  ormG.name = \"ORM_G_Roughness\"\n";

        // ORM B output (index 4) = Metalness
        script += "  local ormB = MultiOutputChannelTexmapToTexmap()\n";
        script += "  ormB.sourceMap = uberORM\n";
        script += "  ormB.outputChannelIndex = 4\n";
        script += "  ormB.name = \"ORM_B_Metalness\"\n";

        // --- ai_multiply: diffuse * AO ---
        script += "  local aiMul = ai_multiply()\n";
        script += "  aiMul.input1_shader = bcCol\n";
        script += "  aiMul.input2_shader = ormR\n";
        script += "  aiMul.name = \"Diffuse_x_AO\"\n";

        // --- ai_standard_surface ---
        script += "  local arnoldMat = ai_standard_surface()\n";
        script += "  arnoldMat.name = \"" + eArnoldName + "\"\n";
        script += "  arnoldMat.base_color_shader = aiMul\n";
        script += "  arnoldMat.specular_roughness_shader = ormG\n";
        script += "  arnoldMat.metalness_shader = ormB\n";

        // --- Optional: Normal map chain ---
        if (!normalPath.empty()) {
            script += "  local uberNrm = OSLMap()\n";
            script += "  uberNrm.OSLPath = oslPath\n";
            script += "  uberNrm.OSLAutoUpdate = true\n";
            script += "  uberNrm.filename = \"" + eNormalPath + "\"\n";
            script += "  uberNrm.name = \"UberBitmap_Normal\"\n";

            script += "  local nrmCol = MultiOutputChannelTexmapToTexmap()\n";
            script += "  nrmCol.sourceMap = uberNrm\n";
            script += "  nrmCol.outputChannelIndex = 1\n";
            script += "  nrmCol.name = \"Normal_Col\"\n";

            script += "  local aiNrm = ai_normal_map()\n";
            script += "  aiNrm.input_shader = nrmCol\n";
            script += "  aiNrm.name = \"NormalMap\"\n";

            script += "  local aiBump = ai_bump2d()\n";
            script += "  aiBump.bump_map_shader = aiNrm\n";
            script += "  aiBump.name = \"Bump2D\"\n";

            script += "  arnoldMat.normal_shader = aiBump\n";
        }

        // --- Shell_Material ---
        script += "  local shellMat = Shell_Material()\n";
        script += "  shellMat.name = \"" + eShellName + "\"\n";
        script += "  shellMat.originalMaterial = arnoldMat\n";
        script += "  if gltfMat != undefined do shellMat.bakedMaterial = gltfMat\n";
        script += "  shellMat.renderMtlIndex = 0\n";
        script += "  shellMat.viewportMtlIndex = 1\n";

        // --- Assign to objects ---
        script += "  local assignCount = 0\n";
        script += "  local notFoundNames = #()\n";
        if (!assignTo.empty()) {
            script += "  local targetNames = #(";
            for (size_t i = 0; i < assignTo.size(); i++) {
                if (i > 0) script += ", ";
                script += "\"" + JsonEscape(assignTo[i]) + "\"";
            }
            script += ")\n";
            script += "  for n in targetNames do (\n";
            script += "    local obj = getNodeByName n\n";
            script += "    if obj != undefined then (\n";
            script += "      obj.material = shellMat\n";
            script += "      assignCount += 1\n";
            script += "    ) else (\n";
            script += "      append notFoundNames n\n";
            script += "    )\n";
            script += "  )\n";
        }

        // --- Build result string ---
        script += "  local gltfStatus = if gltfMat != undefined then (\"found: \" + gltfMat.name) else \"not found\"\n";
        script += "  local hasNormal = ";
        script += (normalPath.empty() ? "false" : "true");
        script += "\n";
        script += "  local resultStr = \"{\" +\n";
        script += "    \"\\\"shell\\\":\\\"\" + shellMat.name + \"\\\",\" +\n";
        script += "    \"\\\"arnold\\\":\\\"\" + arnoldMat.name + \"\\\",\" +\n";
        script += "    \"\\\"gltf\\\":\\\"\" + gltfStatus + \"\\\",\" +\n";
        script += "    \"\\\"hasNormal\\\":\" + (if hasNormal then \"true\" else \"false\") + \",\" +\n";
        script += "    \"\\\"assignedCount\\\":\" + (assignCount as string) + \",\" +\n";
        script += "    \"\\\"notFoundCount\\\":\" + (notFoundNames.count as string) +\n";
        script += "    \"}\"\n";
        script += "  resultStr\n";
        script += ")\n";

        std::string result = RunMAXScript(script);

        Interface* ip = GetCOREInterface();
        ip->RedrawViews(ip->GetTime());

        return result;
    });
}

// ── native:replace_material ─────────────────────────────────────
std::string NativeHandlers::ReplaceMaterial(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&]() -> std::string {
        json p = json::parse(params);
        std::string sourceName = p.value("source_material", "");
        std::string targetName = p.value("target_material", "");
        bool preview = p.value("preview", false);

        if (sourceName.empty() || targetName.empty())
            throw std::runtime_error("source_material and target_material are required");

        Interface* ip = GetCOREInterface();
        INode* root = ip->GetRootNode();
        std::vector<INode*> all;
        CollectNodes(root, all);

        // Find source and target material instances by scanning all nodes
        Mtl* sourceMtl = nullptr;
        Mtl* targetMtl = nullptr;
        std::vector<INode*> affectedNodes;

        for (INode* node : all) {
            Mtl* mtl = node->GetMtl();
            if (!mtl) continue;
            std::string mtlName = WideToUtf8(mtl->GetName().data());
            if (mtlName == sourceName) sourceMtl = mtl;
            if (mtlName == targetName) {
                targetMtl = mtl;
                affectedNodes.push_back(node);
            }
        }

        if (!sourceMtl)
            throw std::runtime_error("Source material '" + sourceName + "' not found in scene");

        json affectedList = json::array();
        for (INode* n : affectedNodes) {
            affectedList.push_back(WideToUtf8(n->GetName()));
        }

        if (preview) {
            json result;
            result["source_material"] = sourceName;
            result["target_material"] = targetName;
            result["affected_count"] = (int)affectedNodes.size();
            result["affected_objects"] = affectedList;
            result["preview"] = true;
            return result.dump();
        }

        // Replace: assign source material to all objects that had target
        for (INode* n : affectedNodes) {
            n->SetMtl(sourceMtl);
        }

        ip->RedrawViews(ip->GetTime());

        json result;
        result["source_material"] = sourceName;
        result["target_material"] = targetName;
        result["replaced_count"] = (int)affectedNodes.size();
        result["replaced_objects"] = affectedList;
        result["status"] = "replaced";
        return result.dump();
    });
}

// ── native:batch_replace_materials ──────────────────────────────
std::string NativeHandlers::BatchReplaceMaterials(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&]() -> std::string {
        json p = json::parse(params);
        auto replacements = p.contains("replacements") && !p["replacements"].is_null()
                            ? p["replacements"] : json::array();
        bool preview = p.value("preview", false);

        Interface* ip = GetCOREInterface();
        INode* root = ip->GetRootNode();
        std::vector<INode*> all;
        CollectNodes(root, all);

        // Build material name -> Mtl* map and name -> nodes map
        std::map<std::string, Mtl*> mtlMap;
        std::map<std::string, std::vector<INode*>> mtlNodes;

        for (INode* node : all) {
            Mtl* mtl = node->GetMtl();
            if (!mtl) continue;
            std::string name = WideToUtf8(mtl->GetName().data());
            mtlMap[name] = mtl;
            mtlNodes[name].push_back(node);
        }

        json results = json::array();
        int totalReplaced = 0;

        for (const auto& rep : replacements) {
            std::string src = rep.value("source", rep.value("source_material", ""));
            std::string tgt = rep.value("target", rep.value("target_material", ""));

            json entry;
            entry["source_material"] = src;
            entry["target_material"] = tgt;

            if (src.empty() || tgt.empty()) {
                entry["status"] = "error";
                entry["error"] = "missing source or target";
                results.push_back(entry);
                continue;
            }

            auto srcIt = mtlMap.find(src);
            if (srcIt == mtlMap.end()) {
                entry["status"] = "error";
                entry["error"] = "source material not found";
                results.push_back(entry);
                continue;
            }

            auto tgtIt = mtlNodes.find(tgt);
            if (tgtIt == mtlNodes.end() || tgtIt->second.empty()) {
                entry["replaced_count"] = 0;
                entry["status"] = preview ? "preview" : "no_objects";
                results.push_back(entry);
                continue;
            }

            json objects = json::array();
            for (INode* n : tgtIt->second) {
                objects.push_back(WideToUtf8(n->GetName()));
                if (!preview) {
                    n->SetMtl(srcIt->second);
                }
            }

            entry["replaced_count"] = (int)tgtIt->second.size();
            entry["replaced_objects"] = objects;
            entry["status"] = preview ? "preview" : "replaced";
            totalReplaced += (int)tgtIt->second.size();
            results.push_back(entry);
        }

        if (!preview) {
            ip->RedrawViews(ip->GetTime());
        }

        json result;
        result["results"] = results;
        result["total_replaced"] = totalReplaced;
        result["preview"] = preview;
        return result.dump();
    });
}

// ── native:create_texture_map ───────────────────────────────────
std::string NativeHandlers::CreateTextureMap(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&]() -> std::string {
        json p = json::parse(params);
        std::string mapClass = p.value("map_class", "");
        std::string mapName = p.value("map_name", "");
        std::string msParams = p.value("params", "");
        auto properties = p.value("properties", json::object());
        std::string globalVar = p.value("global_var", "");

        if (mapClass.empty())
            throw std::runtime_error("map_class is required");

        // Generate unique global var if not provided
        if (globalVar.empty()) {
            globalVar = "__mcp_texmap_" + std::to_string(GetTickCount64());
        }

        // Create texture map via MAXScript (plugin creation requires it)
        std::string script = "global " + globalVar + " = " + mapClass + "()";
        if (!mapName.empty()) {
            script = "global " + globalVar + " = " + mapClass + " name:\"" + JsonEscape(mapName) + "\"";
        }
        if (!msParams.empty()) {
            // Insert params before closing paren
            script = "global " + globalVar + " = " + mapClass + " " + msParams;
            if (!mapName.empty()) {
                script += " name:\"" + JsonEscape(mapName) + "\"";
            }
        }

        RunMAXScript(script);

        // Set properties
        json setProps = json::array();
        json errors = json::array();
        for (auto& [key, val] : properties.items()) {
            std::string propScript = "try (" + globalVar + "." + key + " = " +
                                     val.get<std::string>() + "; true) catch (false)";
            std::string r = RunMAXScript(propScript);
            if (r == "true") {
                setProps.push_back(key);
            } else {
                errors.push_back(key);
            }
        }

        json result;
        result["status"] = "created";
        result["global_var"] = globalVar;
        result["map_class"] = mapClass;
        if (!mapName.empty()) result["map_name"] = mapName;
        if (!setProps.empty()) result["set_properties"] = setProps;
        if (!errors.empty()) result["errors"] = errors;
        return result.dump();
    });
}

// ── native:set_texture_map_properties ───────────────────────────
std::string NativeHandlers::SetTextureMapProperties(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&]() -> std::string {
        json p = json::parse(params);
        std::string globalVar = p.value("global_var", "");
        auto properties = p.value("properties", json::object());

        if (globalVar.empty())
            throw std::runtime_error("global_var is required");

        json setProps = json::array();
        json errors = json::array();

        for (auto& [key, val] : properties.items()) {
            std::string script = "try (" + globalVar + "." + key + " = " +
                                 val.get<std::string>() + "; true) catch (false)";
            std::string r = RunMAXScript(script);
            if (r == "true") {
                setProps.push_back(key);
            } else {
                errors.push_back(key);
            }
        }

        json result;
        result["status"] = "ok";
        result["global_var"] = globalVar;
        result["set_properties"] = setProps;
        if (!errors.empty()) result["errors"] = errors;
        return result.dump();
    });
}

// ── native:set_sub_material ─────────────────────────────────────
std::string NativeHandlers::SetSubMaterial(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&]() -> std::string {
        json p = json::parse(params);
        std::string name = p.value("name", "");
        int subIdx = p.value("sub_material_index", 0);
        std::string matClass = p.value("material_class", "");
        std::string matName = p.value("material_name", "");
        std::string msParams = p.value("params", "");
        int sourceIndex = p.value("source_index", 0);

        if (name.empty())
            throw std::runtime_error("name is required");

        INode* node = FindNodeByName(name);
        if (!node) throw std::runtime_error("Object '" + name + "' not found");

        Mtl* parentMtl = node->GetMtl();
        if (!parentMtl) throw std::runtime_error("Object has no material");

        int numSub = parentMtl->NumSubMtls();
        if (subIdx < 1 || subIdx > numSub)
            throw std::runtime_error("Sub-material index " + std::to_string(subIdx) +
                                     " out of range (1-" + std::to_string(numSub) + ")");

        if (sourceIndex > 0) {
            // Copy from another slot
            if (sourceIndex > numSub)
                throw std::runtime_error("Source index out of range");
            Mtl* srcMtl = parentMtl->GetSubMtl(sourceIndex - 1);
            parentMtl->SetSubMtl(subIdx - 1, srcMtl);
        } else if (!matClass.empty()) {
            // Create new material at slot via MAXScript
            std::string script = "global __mcp_sub_mtl = " + matClass + "()";
            if (!matName.empty()) {
                script = "global __mcp_sub_mtl = " + matClass + " name:\"" +
                         JsonEscape(matName) + "\"";
            }
            if (!msParams.empty()) {
                script = "global __mcp_sub_mtl = " + matClass + " " + msParams;
                if (!matName.empty()) script += " name:\"" + JsonEscape(matName) + "\"";
            }
            RunMAXScript(script);

            // Now get the created material and assign to slot
            std::string assignScript =
                "(getNodeByName \"" + JsonEscape(name) + "\").material.materialList[" +
                std::to_string(subIdx) + "] = __mcp_sub_mtl; __mcp_sub_mtl.name";
            std::string resultName = RunMAXScript(assignScript);

            json result;
            result["status"] = "assigned";
            result["object"] = name;
            result["sub_material_index"] = subIdx;
            result["material_name"] = resultName;
            result["material_class"] = matClass;
            return result.dump();
        } else {
            throw std::runtime_error("Either material_class or source_index must be provided");
        }

        Interface* ip = GetCOREInterface();
        ip->RedrawViews(ip->GetTime());

        // Read back name
        Mtl* assigned = parentMtl->GetSubMtl(subIdx - 1);
        std::string assignedName = assigned ? WideToUtf8(assigned->GetName().data()) : "unknown";

        json result;
        result["status"] = "assigned";
        result["object"] = name;
        result["sub_material_index"] = subIdx;
        result["material_name"] = assignedName;
        return result.dump();
    });
}
