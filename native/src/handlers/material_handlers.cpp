#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/bridge_gup.h"

#include <iparamb2.h>
#include <set>
#include <vector>

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

// ── Helper: material classes whose name starts with the given token ─────────
// Turns a wrong class token into an actionable suggestion instead of a guess.
static std::vector<std::string> SuggestMaterialClasses(const std::string& token) {
    std::vector<std::string> out;
    if (token.empty()) return out;
    std::wstring wtok = Utf8ToWide(token);
    size_t tokLen = wtok.size();
    auto& dir = DllDir::GetInstance();
    int numDlls = dir.Count();
    for (int d = 0; d < numDlls && out.size() < 5; d++) {
        const DllDesc& dll = dir[d];
        int numClasses = dll.NumberOfClasses();
        for (int c = 0; c < numClasses && out.size() < 5; c++) {
            ClassDesc* cd = dll[c];
            if (!cd || cd->SuperClassID() != MATERIAL_CLASS_ID) continue;
            const MCHAR* candidates[2] = { cd->ClassName(), cd->InternalName() };
            for (const MCHAR* cand : candidates) {
                if (!cand || _wcsnicmp(cand, wtok.c_str(), tokLen) != 0) continue;
                std::string s = WideToUtf8(cand);
                bool dup = false;
                for (const std::string& existing : out) {
                    if (existing == s) { dup = true; break; }
                }
                if (!dup) out.push_back(s);
            }
        }
    }
    return out;
}

// ── native:assign_material (Pure SDK) ───────────────────────
std::string NativeHandlers::AssignMaterial(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        auto names = p.value("names", std::vector<std::string>{});
        auto handles = p.value("handles", std::vector<unsigned long long>{});
        std::string matClass = p.value("material_class", "");
        std::string matName = p.value("material_name", "");
        std::string matParams = p.value("params", "");

        if (names.empty() && handles.empty()) throw std::runtime_error("names or handles is required");
        if (matClass.empty()) throw std::runtime_error("material_class is required");

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        // Find material ClassDesc — the lookup MUST stay inside MATERIAL_CLASS_ID.
        // A name-only fallback across every superclass is not safe here: several
        // non-material classes share a name with a material — notably "Physical",
        // which is the Physical *Camera* (the material is "PhysicalMaterial").
        // Instantiating one of those and casting it to Mtl* dispatches through the
        // wrong vtable in SetName()/SetMtl()/redraw and faults with an SE Access
        // Violation (0xC0000005).
        ClassDesc* cd = FindClassDescByName(matClass, MATERIAL_CLASS_ID);
        if (!cd) {
            ClassDesc* other = FindClassDescByName(matClass);
            if (other && other->SuperClassID() != MATERIAL_CLASS_ID) {
                json hint;
                hint["message"] = "\"" + matClass + "\" is a registered class but not a material, "
                                  "so it cannot be created or assigned as one.";
                std::vector<std::string> suggestions = SuggestMaterialClasses(matClass);
                if (!suggestions.empty()) hint["didYouMean"] = suggestions;
                throw std::runtime_error(StructuredErrorPayload(
                    "BAD_PARAM", "Not a material class: " + matClass, hint));
            }
        }

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
                json assigned = json::array();
                std::set<INode*> seen;
                for (unsigned long long handle : handles) {
                    INode* node = FindNodeByHandle(handle);
                    if (node && seen.insert(node).second) {
                        std::string nodeName = WideToUtf8(node->GetName());
                        std::string assignScript = "(getNodeByName \"" + JsonEscape(nodeName) +
                            "\").material = __mcp_tmp_mtl";
                        RunMAXScript(assignScript);
                        assigned.push_back(NodeIdentityJson(node));
                        assignCount++;
                    } else if (!node) {
                        notFound.push_back({{"handle", handle}});
                    }
                }
                for (const auto& name : names) {
                    std::vector<INode*> matches = CollectNodesByExactName(name);
                    if (matches.empty()) {
                        notFound.push_back(name);
                        continue;
                    }
                    if (matches.size() > 1) {
                        json candidates = json::array();
                        for (INode* candidate : matches) candidates.push_back(NodeIdentityJson(candidate));
                        throw std::runtime_error(StructuredErrorPayload(
                            "AMBIGUOUS",
                            "Ambiguous object name: " + name,
                            {{"message", "Pass handles to disambiguate these object names."}, {"candidates", candidates}}));
                    }
                    INode* node = matches[0];
                    if (node && seen.insert(node).second) {
                        std::string nodeName = WideToUtf8(node->GetName());
                        std::string assignScript = "(getNodeByName \"" + JsonEscape(nodeName) +
                            "\").material = __mcp_tmp_mtl";
                        RunMAXScript(assignScript);
                        assigned.push_back(NodeIdentityJson(node));
                        assignCount++;
                    }
                }
                // Get the material name back
                std::string mtlName = RunMAXScript("__mcp_tmp_mtl.name");
                std::string mtlClass = RunMAXScript("(classOf __mcp_tmp_mtl) as string");
                ip->RedrawViews(t);

                json result;
                result["message"] = "Created " + mtlClass + " \"" + mtlName +
                    "\" and assigned to " + std::to_string(assignCount) + " object(s)";
                result["material"] = {
                    {"name", mtlName},
                    {"class", mtlClass},
                    {"requestedClass", matClass},
                };
                result["assigned"] = assigned;
                result["assignedCount"] = assignCount;
                result["notFound"] = notFound;
                return result.dump();
            } catch (const std::exception& e) {
                std::string message = e.what();
                json structured = json::parse(message, nullptr, false);
                if (!structured.is_discarded() && structured.is_object() && structured.contains("code")) {
                    throw;
                }
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
        json assigned = json::array();
        std::set<INode*> seen;
        for (unsigned long long handle : handles) {
            INode* node = FindNodeByHandle(handle);
            if (node && seen.insert(node).second) {
                node->SetMtl(mtl);
                assigned.push_back(NodeIdentityJson(node));
                assignCount++;
            } else if (!node) {
                notFound.push_back({{"handle", handle}});
            }
        }
        for (const auto& name : names) {
            std::vector<INode*> matches = CollectNodesByExactName(name);
            if (matches.empty()) {
                notFound.push_back(name);
                continue;
            }
            if (matches.size() > 1) {
                json candidates = json::array();
                for (INode* candidate : matches) candidates.push_back(NodeIdentityJson(candidate));
                throw std::runtime_error(StructuredErrorPayload(
                    "AMBIGUOUS",
                    "Ambiguous object name: " + name,
                    {{"message", "Pass handles to disambiguate these object names."}, {"candidates", candidates}}));
            }
            INode* node = matches[0];
            if (node && seen.insert(node).second) {
                node->SetMtl(mtl);
                assigned.push_back(NodeIdentityJson(node));
                assignCount++;
            }
        }

        mtl->NotifyDependents(FOREVER, PART_ALL, REFMSG_CHANGE);
        ip->RedrawViews(t);

        json result;
        result["message"] = "Created " + WideToUtf8(mtl->ClassName().data()) + " \"" +
                            WideToUtf8(mtl->GetName().data()) + "\" and assigned to " +
                            std::to_string(assignCount) + " object(s)";
        result["material"] = {
            {"name", WideToUtf8(mtl->GetName().data())},
            {"class", WideToUtf8(mtl->ClassName().data())},
            {"requestedClass", matClass},
        };
        result["assigned"] = assigned;
        result["assignedCount"] = assignCount;
        result["notFound"] = notFound;
        return result.dump();
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

        if (prop.empty()) throw std::runtime_error("property is required");

        INode* node = ResolveNodeFromPayload(p);
        name = WideToUtf8(node->GetName());

        Mtl* mtl = GetTargetMaterial(node, subMatIndex);
        if (!mtl) {
            if (subMatIndex > 0)
                throw std::runtime_error("Sub-material index " + std::to_string(subMatIndex) + " not found");
            throw std::runtime_error("No material assigned to " + name);
        }

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        auto propertyResult = [&](const std::string& message) -> std::string {
            json result = NodeIdentityJson(node);
            result["message"] = message;
            result["property"] = prop;
            result["value"] = value;
            result["material"] = {
                {"name", WideToUtf8(mtl->GetName().data())},
                {"class", WideToUtf8(mtl->ClassName().data())},
                {"sub_material_index", subMatIndex},
            };
            return result.dump();
        };

        // Pure SDK path: try IParamBlock2 first
        if (SetParamByName((Animatable*)mtl, prop, value, t)) {
            mtl->NotifyDependents(FOREVER, PART_ALL, REFMSG_CHANGE);
            ip->RedrawViews(t);
            return propertyResult("Set " + WideToUtf8(mtl->GetName().data()) + "." + prop);
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
        return propertyResult(result);
    });
}

// ── native:set_material_properties (Pure SDK + minimal fallback) ──
std::string NativeHandlers::SetMaterialProperties(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string name = p.value("name", "");
        auto properties = p.value("properties", std::map<std::string, std::string>{});
        int subMatIndex = p.value("sub_material_index", 0);

        if (properties.empty()) throw std::runtime_error("properties is required");

        INode* node = ResolveNodeFromPayload(p);
        name = WideToUtf8(node->GetName());

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

        json result = NodeIdentityJson(node);
        result["message"] = "Set " + std::to_string(okList.size()) + " properties on " +
                            WideToUtf8(mtl->GetName().data());
        result["material"] = {
            {"name", WideToUtf8(mtl->GetName().data())},
            {"class", WideToUtf8(mtl->ClassName().data())},
            {"sub_material_index", subMatIndex},
        };
        result["propertiesSet"] = okList;
        result["errors"] = errList;
        return result.dump();
    });
}

static Mtl* FindNamedMaterialTree(
    Mtl* material,
    const std::string& wanted,
    std::set<Mtl*>& visited) {
    if (!material || !visited.insert(material).second) return nullptr;
    if (_stricmp(WideToUtf8(material->GetName().data()).c_str(), wanted.c_str()) == 0)
        return material;
    for (int i = 0; i < material->NumSubMtls(); ++i) {
        if (Mtl* found = FindNamedMaterialTree(material->GetSubMtl(i), wanted, visited))
            return found;
    }
    return nullptr;
}

static Mtl* FindNamedMaterial(const std::string& wanted, Interface* ip) {
    std::set<Mtl*> visited;
    auto scanLibrary = [&](MtlBaseLib* library) -> Mtl* {
        if (!library) return nullptr;
        for (int i = 0; i < library->NumSubs(); ++i) {
            Animatable* entry = library->SubAnim(i);
            if (!entry || entry->SuperClassID() != MATERIAL_CLASS_ID) continue;
            if (Mtl* found = FindNamedMaterialTree(static_cast<Mtl*>(entry), wanted, visited))
                return found;
        }
        return nullptr;
    };

    if (Mtl* found = scanLibrary(ip->GetSceneMtls())) return found;
    MtlBaseLib& currentLibrary = ip->GetMaterialLibrary();
    if (Mtl* found = scanLibrary(&currentLibrary)) return found;
    for (int slot = 0; slot < 24; ++slot) {
        MtlBase* entry = ip->GetMtlSlot(slot);
        if (!entry || entry->SuperClassID() != MATERIAL_CLASS_ID) continue;
        if (Mtl* found = FindNamedMaterialTree(static_cast<Mtl*>(entry), wanted, visited))
            return found;
    }
    return nullptr;
}

// ── native:create_shell_material (pure SDK, existing materials) ──
std::string NativeHandlers::CreateShellMaterial(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        if (p.is_discarded() || !p.is_object())
            throw std::runtime_error("Invalid JSON params");

        const std::string shellName = p.value("shell_name", p.value("name", ""));
        const std::string renderName =
            p.value("render_material", p.value("render_material_name", ""));
        const std::string exportName =
            p.value("export_material", p.value("gltf_material_name", ""));
        const int renderSlot = p.value("render_slot", 0);
        const int viewportSlot = p.value("viewport_slot", 1);
        const auto assignTo = p.value("assign_to", std::vector<std::string>{});

        if (shellName.empty()) throw std::runtime_error("shell_name is required");
        if (renderName.empty()) throw std::runtime_error("render_material is required");
        if (renderSlot < 0 || renderSlot > 1 || viewportSlot < 0 || viewportSlot > 1)
            throw std::runtime_error("render_slot and viewport_slot must be 0 or 1");

        Interface* ip = GetCOREInterface();
        const TimeValue t = ip->GetTime();
        Mtl* renderMaterial = FindNamedMaterial(renderName, ip);
        if (!renderMaterial)
            throw std::runtime_error("Render material not found: " + renderName);

        Mtl* exportMaterial = nullptr;
        if (!exportName.empty()) {
            exportMaterial = FindNamedMaterial(exportName, ip);
        }

        auto* shellMaterial = static_cast<Mtl*>(
            ip->CreateInstance(MATERIAL_CLASS_ID, Class_ID(BAKE_SHELL_CLASS_ID, 0)));
        if (!shellMaterial)
            throw std::runtime_error("Shell_Material class is unavailable");

        shellMaterial->SetName(Utf8ToWide(shellName).c_str());
        shellMaterial->SetSubMtl(0, renderMaterial);
        if (exportMaterial) shellMaterial->SetSubMtl(1, exportMaterial);

        // Shell Material publishes these as PB2 integer parameters. Set both
        // explicitly instead of relying on class-version defaults.
        const bool setRender = SetParamByName(
            shellMaterial, "renderMtlIndex", std::to_string(renderSlot), t);
        const bool setViewport = SetParamByName(
            shellMaterial, "viewportMtlIndex", std::to_string(viewportSlot), t);
        if (!setRender || !setViewport) {
            shellMaterial->DeleteThis();
            throw std::runtime_error("Shell_Material slot parameters are unavailable");
        }

        json notFound = json::array();
        std::vector<INode*> assignmentTargets;
        std::set<INode*> seen;
        for (const std::string& name : assignTo) {
            std::vector<INode*> matches = CollectNodesByExactName(name);
            if (matches.empty()) {
                notFound.push_back(name);
                continue;
            }
            if (matches.size() > 1) {
                json candidates = json::array();
                for (INode* candidate : matches)
                    candidates.push_back(NodeIdentityJson(candidate));
                shellMaterial->DeleteThis();
                throw std::runtime_error(StructuredErrorPayload(
                    "AMBIGUOUS",
                    "Ambiguous object name: " + name,
                    {{"message", "Pass unique object names before assigning the Shell Material."},
                     {"candidates", candidates}}));
            }
            INode* node = matches.front();
            if (seen.insert(node).second) {
                assignmentTargets.push_back(node);
            }
        }

        json assigned = json::array();
        for (INode* node : assignmentTargets) {
            node->SetMtl(shellMaterial);
            assigned.push_back(NodeIdentityJson(node));
        }
        const int assignedCount = static_cast<int>(assignmentTargets.size());
        if (assignedCount == 0) {
            // A native material needs a ReferenceMaker owner after this handler
            // returns. Keep an unassigned Shell reachable in the user's scratch
            // library instead of leaking an inaccessible ReferenceTarget.
            ip->GetMaterialLibrary().Add(shellMaterial);
        }

        shellMaterial->NotifyDependents(FOREVER, PART_ALL, REFMSG_CHANGE);
        ip->RedrawViews(t);

        json result = {
            {"workflow", "shell_wrap"},
            {"shell_name", WideToUtf8(shellMaterial->GetName().data())},
            {"shell_class", MaxScriptVisibleClassName(shellMaterial)},
            {"render_material", WideToUtf8(renderMaterial->GetName().data())},
            {"render_material_class", MaxScriptVisibleClassName(renderMaterial)},
            {"render_slot", renderSlot},
            {"viewport_slot", viewportSlot},
            {"assigned_count", assignedCount},
            {"assigned", assigned},
            {"not_found", notFound},
            {"status", "success"},
        };
        if (exportMaterial) {
            result["export_material"] = WideToUtf8(exportMaterial->GetName().data());
            result["export_material_class"] =
                MaxScriptVisibleClassName(exportMaterial);
        }
        return result.dump();
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
        bool preview = p.value("preview", false) || p.value("dry_run", false);

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
                objects.push_back(NodeIdentityJson(n));
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
        result["dry_run"] = p.value("dry_run", false);
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

        INode* node = ResolveNodeFromPayload(p);
        name = WideToUtf8(node->GetName());

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
            result["handle"] = NodeHandle(node);
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
        result["handle"] = NodeHandle(node);
        result["sub_material_index"] = subIdx;
        result["material_name"] = assignedName;
        return result.dump();
    });
}
