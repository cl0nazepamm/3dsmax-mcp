#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/core_bridge.h"

#include <iparamb2.h>
#include <map>
#include <set>
#include <utility>
#include <vector>

using json = nlohmann::json;
using namespace HandlerHelpers;

// Texture map property values are MAXScript literal text; JSON numbers and
// booleans print as the same literal.
static std::string MaxScriptLiteralText(const json& value) {
    if (value.type() == json::value_t::string) return value.get<std::string>();
    return value.dump();
}

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
        while (i < s.size() && std::isspace(static_cast<unsigned char>(s[i]))) i++;
        if (i >= s.size()) break;
        size_t keyStart = i;
        while (i < s.size() && s[i] != ':') i++;
        if (i >= s.size())
            throw std::runtime_error(StructuredErrorPayload("BAD_PARAM", "Expected material key:value parameters"));
        std::string key = s.substr(keyStart, i - keyStart);
        if (key.empty() || key.find_first_of(" \t\r\n") != std::string::npos)
            throw std::runtime_error(StructuredErrorPayload("BAD_PARAM", "Invalid material parameter name"));
        i++;
        while (i < s.size() && std::isspace(static_cast<unsigned char>(s[i]))) i++;
        size_t valStart = i;
        if (i < s.size() && (s[i] == '[' || s[i] == '(')) {
            char open = s[i], close = (open == '[') ? ']' : ')';
            int depth = 1; i++;
            while (i < s.size() && depth > 0) {
                if (s[i] == open) depth++;
                else if (s[i] == close) depth--;
                i++;
            }
            if (depth != 0)
                throw std::runtime_error(StructuredErrorPayload("BAD_PARAM", "Unclosed material parameter value: " + key));
        } else if (i < s.size() && s[i] == '"') {
            i++;
            while (i < s.size() && s[i] != '"') { if (s[i] == '\\') i++; i++; }
            if (i >= s.size())
                throw std::runtime_error(StructuredErrorPayload("BAD_PARAM", "Unclosed material string: " + key));
            i++;
        } else {
            while (i < s.size() && !std::isspace(static_cast<unsigned char>(s[i]))) i++;
        }
        if (i == valStart)
            throw std::runtime_error(StructuredErrorPayload("BAD_PARAM", "Missing material parameter value: " + key));
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
                if (!cand) continue;
                std::string s = SanitizeScriptName(WideToUtf8(cand));
                const auto alias = Utf8ToWide(s);
                if (_wcsnicmp(cand, wtok.c_str(), tokLen) != 0 &&
                    _wcsnicmp(alias.c_str(), wtok.c_str(), tokLen) != 0) continue;
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

        // Validate every target before creating a material or touching assignments.
        for (unsigned long long handle : handles) {
            if (!FindNodeByHandle(handle))
                throw std::runtime_error(StructuredErrorPayload("NOT_FOUND", "Material target handle not found",
                    {{"handle", handle}}));
        }
        for (const auto& name : names) {
            auto matches = CollectNodesByName(name);
            if (matches.empty())
                throw std::runtime_error(StructuredErrorPayload("NOT_FOUND", "Material target not found: " + name));
            if (matches.size() > 1)
                throw std::runtime_error(StructuredErrorPayload("AMBIGUOUS", "Ambiguous material target: " + name,
                    {{"message", "Pass handles to disambiguate material targets."}}));
        }

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
                // Clear the global first: an undefined class token evaluates to
                // undefined without an error, and assigning that (or a material
                // left over from an earlier call) would clear or replace the
                // targets' materials while reporting a new one.
                RunMAXScriptChecked("global __mcp_tmp_mtl = undefined; __mcp_tmp_mtl = " + script);
                if (RunMAXScriptChecked("(superClassOf __mcp_tmp_mtl == material) as string") != "true")
                    throw std::runtime_error("the expression did not create a material");
                // Now assign via MAXScript too since we can't get the Mtl* back easily
                int assignCount = 0;
                json notFound = json::array();
                json assigned = json::array();
                std::set<INode*> seen;
                for (unsigned long long handle : handles) {
                    INode* node = FindNodeByHandle(handle);
                    if (node && seen.insert(node).second) {
                        std::string assignScript = "(getAnimByHandle " + std::to_string(NodeHandle(node)) +
                            ").material = __mcp_tmp_mtl";
                        RunMAXScriptChecked(assignScript);
                        assigned.push_back(NodeIdentityJson(node));
                        assignCount++;
                    } else if (!node) {
                        notFound.push_back({{"handle", handle}});
                    }
                }
                for (const auto& name : names) {
                    std::vector<INode*> matches = CollectNodesByName(name);
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
                        std::string assignScript = "(getAnimByHandle " + std::to_string(NodeHandle(node)) +
                            ").material = __mcp_tmp_mtl";
                        RunMAXScriptChecked(assignScript);
                        assigned.push_back(NodeIdentityJson(node));
                        assignCount++;
                    }
                }
                // Get the material name back
                std::string mtlName = RunMAXScriptChecked("__mcp_tmp_mtl.name");
                std::string mtlClass = RunMAXScriptChecked("(classOf __mcp_tmp_mtl) as string");
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
                throw std::runtime_error("Unknown material class: " + matClass + " (" + message + ")");
            }
        }

        // Pure SDK path continues — set name
        if (!matName.empty()) {
            std::wstring wname = Utf8ToWide(matName);
            mtl->SetName(wname.c_str());
        }

        // Reject failed parameters before any node receives this new material.
        json appliedParameters = json::array();
        try {
            if (!matParams.empty()) {
                auto kvPairs = ParseMtlParams(matParams);
                if (kvPairs.empty())
                    throw std::runtime_error(StructuredErrorPayload("BAD_PARAM", "Expected material key:value parameters"));
                for (auto& [key, val] : kvPairs) {
                    bool applied = false;
                    try { applied = SetParamByName((Animatable*)mtl, key, val, t); } catch (const std::exception&) {}
                    if (!applied)
                        throw std::runtime_error(StructuredErrorPayload("BAD_PARAM", "Failed to set material parameter: " + key,
                            {{"property", key}, {"value", val}, {"material_class", matClass}}));
                    appliedParameters.push_back(key);
                }
            }
        } catch (...) {
            mtl->DeleteThis();
            throw;
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
            std::vector<INode*> matches = CollectNodesByName(name);
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
        result["message"] = "Created " + SanitizeScriptName(WideToUtf8(mtl->ClassName().data())) + " \"" +
                            WideToUtf8(mtl->GetName().data()) + "\" and assigned to " +
                            std::to_string(assignCount) + " object(s)";
        result["material"] = {
            {"name", WideToUtf8(mtl->GetName().data())},
            {"class", SanitizeScriptName(WideToUtf8(mtl->ClassName().data()))},
            {"requestedClass", matClass},
        };
        result["assigned"] = assigned;
        result["assignedCount"] = assignCount;
        result["notFound"] = notFound;
        result["appliedParameters"] = appliedParameters;
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
                {"class", SanitizeScriptName(WideToUtf8(mtl->ClassName().data()))},
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
            matExpr = "(getAnimByHandle " + std::to_string(NodeHandle(node)) + ").material[" + std::to_string(subMatIndex) + "]";
        else
            matExpr = "(getAnimByHandle " + std::to_string(NodeHandle(node)) + ").material";

        // A rejected assignment throws, so the dispatcher reports it and cancels
        // its hold instead of returning the MAXScript error text as a success.
        std::string script = matExpr + "." + prop + " = " + value + "; \"Set " + JsonEscape(prop) + "\"";
        std::string result;
        try {
            result = RunMAXScriptChecked(script);
        } catch (const std::exception& error) {
            throw std::runtime_error("Error setting " + prop + ": " + error.what());
        }
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
            matExpr = "(getAnimByHandle " + std::to_string(NodeHandle(node)) + ").material[" + std::to_string(subMatIndex) + "]";
        else
            matExpr = "(getAnimByHandle " + std::to_string(NodeHandle(node)) + ").material";

        for (auto& [prop, value] : properties) {
            // Try pure SDK IParamBlock2 first
            if (SetParamByName((Animatable*)mtl, prop, value, t)) {
                okList.push_back(prop);
                continue;
            }

            // Fallback for texture map / reference assignments
            try {
                std::string script = matExpr + "." + prop + " = " + value;
                RunMAXScriptChecked(script);
                okList.push_back(prop);
            } catch (const std::exception& error) {
                errList.push_back(prop + ": failed to set (" + error.what() + ")");
            } catch (...) {
                errList.push_back(prop + ": failed to set");
            }
        }

        if (okList.empty()) {
            throw std::runtime_error(StructuredErrorPayload(
                "BAD_PARAM", "No material property was set on " + WideToUtf8(mtl->GetName().data()),
                {{"errors", errList}}));
        }

        mtl->NotifyDependents(FOREVER, PART_ALL, REFMSG_CHANGE);
        ip->RedrawViews(t);

        json result = NodeIdentityJson(node);
        result["message"] = "Set " + std::to_string(okList.size()) + " properties on " +
                            WideToUtf8(mtl->GetName().data());
        result["material"] = {
            {"name", WideToUtf8(mtl->GetName().data())},
            {"class", SanitizeScriptName(WideToUtf8(mtl->ClassName().data()))},
            {"sub_material_index", subMatIndex},
        };
        result["propertiesSet"] = okList;
        result["errors"] = errList;
        if (!errList.empty()) {
            result["status"] = "partial";
            result["warnings"] = json::array({std::to_string(errList.size()) + " of " +
                std::to_string(properties.size()) + " material properties were not set; see errors."});
        }
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
            std::vector<INode*> matches = CollectNodesByName(name);
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

// ── replace_material / batch_replace_materials ──────────────────
// Targets match by exact name on an object's own material and, unless
// include_sub_materials is false, in every sub-material slot of its material
// tree (Multi/Sub and any other material that exposes sub-materials). The
// source may live on an object, in a sub-material slot, in a compact Material
// Editor slot, among the scene materials or in the current material library,
// searched in that order; source_from limits the search to one of those places.

static constexpr int kMaterialEditorSlotCount = 24;

// source_from values ("object", the search's found_in name, is accepted for "node").
// Empty means "search every place in order".
static bool IsReplaceSourcePlace(const std::string& place) {
    return place.empty() || place == "node" || place == "object" || place == "sub_material" ||
           place == "material_editor" || place == "scene_materials" || place == "material_library";
}

static std::string ReadReplaceSourceFrom(const json& p, const std::string& fallback) {
    std::string from = fallback;
    // json::is_string() collides with the MAXScript is_string macro.
    if (p.contains("source_from") && p["source_from"].type() == json::value_t::string)
        from = p["source_from"].get<std::string>();
    else if (p.contains("source_from") && !p["source_from"].is_null())
        throw std::runtime_error("source_from must be a string");
    if (!IsReplaceSourcePlace(from))
        throw std::runtime_error("source_from must be one of node, sub_material, material_editor, "
                                 "scene_materials, material_library (got '" + from + "')");
    return from == "object" ? std::string("node") : from;
}

// The search's found_in names an object as "object"; source_from and
// source_found_in call it "node".
static std::string ReplaceSourcePlace(const std::string& foundIn) {
    return foundIn == "object" ? std::string("node") : foundIn;
}

static bool MaterialNamed(Mtl* mtl, const std::wstring& name) {
    return mtl && name == mtl->GetName().data();
}

static std::string MaterialName(Mtl* mtl) {
    return mtl ? WideToUtf8(mtl->GetName().data()) : std::string();
}

// A batch preview chains entries without touching the scene: later entries
// read the assignments earlier entries planned through this overlay. Applied
// runs leave it empty and read the live scene.
struct MaterialReplaceOverlay {
    std::map<INode*, Mtl*> nodes;
    std::map<std::pair<Mtl*, int>, Mtl*> slots;

    Mtl* NodeMtl(INode* node) const {
        auto it = nodes.find(node);
        return it != nodes.end() ? it->second : node->GetMtl();
    }
    Mtl* SubMtl(Mtl* parent, int index) const {
        auto it = slots.find({parent, index});
        return it != slots.end() ? it->second : parent->GetSubMtl(index);
    }
};

// Distinct materials with one exact name, in lookup priority: object
// materials, their sub-material trees, then Material Editor slots and their
// trees, then (source searches only) the scene materials and the current
// material library. Each hit records where it was found. `from` keeps only
// the hits of one place (a source_from value).
struct NamedMaterialSearch {
    const std::wstring& name;
    const MaterialReplaceOverlay& overlay;
    std::string from;
    bool libraries = false;
    std::set<Mtl*> visited;
    std::set<Mtl*> seen;
    std::vector<std::pair<Mtl*, json>> found;

    void Add(Mtl* mtl, json where) {
        if (!from.empty() && ReplaceSourcePlace(where.value("found_in", "")) != from) return;
        if (seen.insert(mtl).second) found.push_back({mtl, std::move(where)});
    }

    void ScanLibrary(MtlBaseLib* library, const char* place, const std::set<Mtl*>* skip = nullptr) {
        if (!library) return;
        for (int i = 0; i < library->Count(); ++i) {
            MtlBase* entry = (*library)[i];
            if (!entry || entry->SuperClassID() != MATERIAL_CLASS_ID) continue;
            Mtl* mtl = static_cast<Mtl*>(entry);
            if (skip && skip->count(mtl)) continue;
            if (MaterialNamed(mtl, name)) Add(mtl, {{"found_in", place}});
        }
    }

    // Every material in the objects' trees, read through the overlay or, with
    // `planned` null, from the scene as it is.
    static void CollectUsed(Mtl* mtl, const MaterialReplaceOverlay* planned, std::set<Mtl*>& out) {
        if (!mtl || !out.insert(mtl).second) return;
        for (int i = 0; i < mtl->NumSubMtls(); ++i)
            CollectUsed(planned ? planned->SubMtl(mtl, i) : mtl->GetSubMtl(i), planned, out);
    }

    void Walk(Mtl* parent, const json& origin) {
        if (!parent || !visited.insert(parent).second) return;
        for (int i = 0; i < parent->NumSubMtls(); ++i) {
            Mtl* sub = overlay.SubMtl(parent, i);
            if (!sub) continue;
            if (MaterialNamed(sub, name)) {
                json where = origin;
                where["found_in"] = "sub_material";
                where["parent_material"] = MaterialName(parent);
                where["slot_index"] = i + 1;
                Add(sub, std::move(where));
            }
            Walk(sub, origin);
        }
    }

    void Run(const std::vector<INode*>& nodes) {
        for (INode* node : nodes) {
            Mtl* mtl = overlay.NodeMtl(node);
            if (MaterialNamed(mtl, name))
                Add(mtl, {{"found_in", "object"}, {"object", WideToUtf8(node->GetName())},
                          {"handle", NodeHandle(node)}});
        }
        for (INode* node : nodes)
            Walk(overlay.NodeMtl(node), {{"object", WideToUtf8(node->GetName())}});
        Interface* ip = GetCOREInterface();
        for (int slot = 0; slot < kMaterialEditorSlotCount; ++slot) {
            MtlBase* entry = ip->GetMtlSlot(slot);
            if (!entry || entry->SuperClassID() != MATERIAL_CLASS_ID) continue;
            Mtl* mtl = static_cast<Mtl*>(entry);
            const json origin = {{"material_editor_slot", slot + 1}};
            if (MaterialNamed(mtl, name)) {
                json where = origin;
                where["found_in"] = "material_editor";
                Add(mtl, std::move(where));
            }
            Walk(mtl, origin);
        }
        if (!libraries) return;
        // The scene material list is rebuilt only on request, so it still holds
        // the materials of deleted objects; those must not become sources.
        if (Interface17* ip17 = GetCOREInterface17()) ip17->UpdateSceneMaterialLib();
        // A batch preview plans earlier entries through the overlay. A material
        // they take off every object is listed now, but not when they apply.
        std::set<Mtl*> plannedAway;
        if (!overlay.nodes.empty() || !overlay.slots.empty()) {
            std::set<Mtl*> live, planned;
            for (INode* node : nodes) {
                CollectUsed(node->GetMtl(), nullptr, live);
                CollectUsed(overlay.NodeMtl(node), &overlay, planned);
            }
            for (Mtl* mtl : live)
                if (!planned.count(mtl)) plannedAway.insert(mtl);
        }
        ScanLibrary(ip->GetSceneMtls(), "scene_materials", &plannedAway);
        ScanLibrary(&ip->GetMaterialLibrary(), "material_library");
    }
};

// One planned change: an object's own material, or one sub-material slot.
struct MaterialReplaceMatch {
    INode* node = nullptr;
    Mtl* parent = nullptr;
    int slot = -1;
    Mtl* previous = nullptr;
    std::vector<INode*> users;  // objects whose material tree reaches the slot
};

// Walks one object's material tree. A slot holding a material named `target`
// is a match; the matched material is not descended because its slot is the
// one being replaced.
static void CollectSlotMatches(
    Mtl* parent, INode* user, const std::wstring& target,
    const MaterialReplaceOverlay& overlay, std::set<Mtl*>& visited,
    std::vector<MaterialReplaceMatch>& matches,
    std::map<std::pair<Mtl*, int>, size_t>& slotIndex) {
    if (!parent || !visited.insert(parent).second) return;
    for (int i = 0; i < parent->NumSubMtls(); ++i) {
        Mtl* sub = overlay.SubMtl(parent, i);
        if (!sub) continue;
        if (!MaterialNamed(sub, target)) {
            CollectSlotMatches(sub, user, target, overlay, visited, matches, slotIndex);
            continue;
        }
        auto [it, inserted] = slotIndex.emplace(std::make_pair(parent, i), matches.size());
        if (inserted) {
            MaterialReplaceMatch match;
            match.parent = parent;
            match.slot = i;
            match.previous = sub;
            matches.push_back(std::move(match));
        }
        matches[it->second].users.push_back(user);
    }
}

// True when `owner` referencing `source` would close a reference cycle, for
// example when the source contains the slot's parent material. TestForLoop
// returns REF_SUCCEED when the new reference is safe. A batch preview also
// follows sub-material assignments planned by earlier entries, which the live
// reference graph does not contain yet.
static bool WouldCreateLoop(
    Mtl* source, ReferenceMaker* owner, Mtl* ownerMtl,
    const MaterialReplaceOverlay& overlay) {
    if (source->TestForLoop(FOREVER, owner) != REF_SUCCEED) return true;
    if (!ownerMtl || overlay.slots.empty()) return false;
    std::set<Mtl*> visited;
    std::vector<Mtl*> pending{source};
    while (!pending.empty()) {
        Mtl* mtl = pending.back();
        pending.pop_back();
        if (mtl == ownerMtl) return true;
        if (!visited.insert(mtl).second) continue;
        for (int i = 0; i < mtl->NumSubMtls(); ++i) {
            if (Mtl* sub = overlay.SubMtl(mtl, i)) pending.push_back(sub);
        }
    }
    return false;
}

static std::string DescribeMaterialLocation(const json& where) {
    const std::string foundIn = where.value("found_in", "");
    if (foundIn == "object")
        return "on object '" + where.value("object", "") + "'";
    if (foundIn == "material_editor")
        return "in Material Editor slot " + std::to_string(where.value("material_editor_slot", 0));
    if (foundIn == "scene_materials") return "among the scene materials";
    if (foundIn == "material_library") return "in the current material library";
    return "in slot " + std::to_string(where.value("slot_index", 0)) + " of '" +
           where.value("parent_material", "") + "'";
}

static std::string MaterialReplaceStatus(
    size_t matchCount, int changed, const json& skipped, bool targetExists, bool preview) {
    if (matchCount == 0) return targetExists ? "no_objects" : "no_match";
    if (changed > 0) return preview ? "preview" : "replaced";
    for (const json& skip : skipped) {
        if (skip.value("reason", "") != "already_source") return "blocked";
    }
    return "unchanged";
}

// Plans one replacement against the scene as it is now (or as the overlay
// says it will be), then applies it unless previewing. Every set is read back.
// sourceFrom limits the source search to one place; includeSubs false matches
// objects' own materials only. A preview with missingSourceOk plans without a
// source (source_exists false), so the caller still sees what the target
// would affect.
static json RunMaterialReplacement(
    const std::string& sourceName, const std::string& targetName, bool preview,
    bool identityObjects, const std::vector<INode*>& nodes, MaterialReplaceOverlay& overlay,
    const std::string& sourceFrom = std::string(), bool includeSubs = true, bool missingSourceOk = false) {
    const std::wstring sourceW = Utf8ToWide(sourceName);
    const std::wstring targetW = Utf8ToWide(targetName);

    json entry;
    entry["source_material"] = sourceName;
    entry["target_material"] = targetName;
    if (!sourceFrom.empty()) entry["source_from"] = sourceFrom;
    entry["include_sub_materials"] = includeSubs;

    NamedMaterialSearch sourceSearch{sourceW, overlay, sourceFrom, true};
    sourceSearch.Run(nodes);
    const std::string sourceScope = sourceFrom.empty()
        ? std::string("on objects, in sub-material slots, in the Material Editor, among the scene materials "
                      "or in the current material library")
        : "(searched only source_from=" + sourceFrom + ")";
    entry["source_exists"] = !sourceSearch.found.empty();
    json warnings = json::array();
    Mtl* source = nullptr;
    if (sourceSearch.found.empty()) {
        if (!(preview && missingSourceOk)) {
            entry["status"] = "error";
            entry["error"] = "Source material '" + sourceName + "' not found " + sourceScope;
            return entry;
        }
        // source_exists false says it; the Python tool adds the warning.
    } else {
        source = sourceSearch.found.front().first;
        json sourceInfo = sourceSearch.found.front().second;
        sourceInfo["name"] = MaterialName(source);
        sourceInfo["class"] = MaxScriptVisibleClassName(source);
        sourceInfo["candidates"] = static_cast<int>(sourceSearch.found.size());
        entry["source_found_in"] = ReplaceSourcePlace(sourceInfo.value("found_in", ""));
        if (sourceSearch.found.size() > 1) {
            entry["source_ambiguous"] = true;
            entry["source_candidates"] = static_cast<int>(sourceSearch.found.size());
            warnings.push_back(std::to_string(sourceSearch.found.size()) + " materials are named '" +
                sourceName + "'; used the one " + DescribeMaterialLocation(sourceInfo) + ".");
        }
        entry["source"] = sourceInfo;
    }

    std::vector<MaterialReplaceMatch> matches;
    std::map<std::pair<Mtl*, int>, size_t> slotIndex;
    for (INode* node : nodes) {
        Mtl* own = overlay.NodeMtl(node);
        if (!own) continue;
        if (MaterialNamed(own, targetW)) {
            MaterialReplaceMatch match;
            match.node = node;
            match.previous = own;
            match.users.push_back(node);
            matches.push_back(std::move(match));
            continue;
        }
        if (!includeSubs) continue;
        std::set<Mtl*> visited;
        CollectSlotMatches(own, node, targetW, overlay, visited, matches, slotIndex);
    }

    auto objectJson = [&](INode* node) -> json {
        return identityObjects ? NodeIdentityJson(node) : json(WideToUtf8(node->GetName()));
    };

    json replacedObjects = json::array();
    json replacedSlots = json::array();
    json skipped = json::array();
    std::set<INode*> affected;
    for (MaterialReplaceMatch& match : matches) {
        json where;
        std::string reason;
        if (match.node) {
            where = {{"kind", "object"}, {"object", WideToUtf8(match.node->GetName())},
                     {"handle", NodeHandle(match.node)}};
            if (!source) {
                // Preview without a source: report the match, plan nothing.
            } else if (match.previous == source) {
                reason = "already_source";
            } else if (WouldCreateLoop(source, match.node, nullptr, overlay)) {
                reason = "would_create_loop";
            } else if (preview) {
                overlay.nodes[match.node] = source;
            } else {
                match.node->SetMtl(source);
                Mtl* now = match.node->GetMtl();
                if (now != source) {
                    reason = "readback_mismatch";
                    where["actual_material"] = MaterialName(now);
                }
            }
        } else {
            MSTR slotLabel = match.parent->GetSubMtlSlotName(match.slot, false);
            where = {{"kind", "sub_material"},
                     {"parent_material", MaterialName(match.parent)},
                     {"parent_class", MaxScriptVisibleClassName(match.parent)},
                     {"slot_index", match.slot + 1},
                     {"slot_name", WideToUtf8(slotLabel.data())},
                     {"previous_material", MaterialName(match.previous)},
                     {"object_count", static_cast<int>(match.users.size())}};
            if (!source) {
                // Preview without a source: report the match, plan nothing.
            } else if (match.previous == source) {
                reason = "already_source";
            } else if (match.parent == source) {
                reason = "parent_is_source";
            } else if (WouldCreateLoop(source, match.parent, match.parent, overlay)) {
                reason = "would_create_loop";
            } else if (preview) {
                overlay.slots[{match.parent, match.slot}] = source;
            } else {
                match.parent->SetSubMtl(match.slot, source);
                Mtl* now = match.parent->GetSubMtl(match.slot);
                if (now != source) {
                    // Materials may expose a sub-material read-only and ignore SetSubMtl.
                    reason = now == match.previous ? "set_rejected" : "readback_mismatch";
                    where["actual_material"] = MaterialName(now);
                } else {
                    match.parent->NotifyDependents(FOREVER, PART_ALL, REFMSG_CHANGE);
                }
            }
        }

        if (!reason.empty()) {
            where["reason"] = reason;
            skipped.push_back(std::move(where));
            continue;
        }
        if (match.node) {
            replacedObjects.push_back(objectJson(match.node));
        } else {
            where.erase("kind");
            replacedSlots.push_back(std::move(where));
        }
        affected.insert(match.users.begin(), match.users.end());
    }

    json affectedObjects = json::array();
    for (INode* node : nodes) {
        if (affected.count(node)) affectedObjects.push_back(objectJson(node));
    }

    const int changed = static_cast<int>(replacedObjects.size() + replacedSlots.size());
    bool targetExists = !matches.empty();
    if (!targetExists) {
        NamedMaterialSearch targetSearch{targetW, overlay};
        targetSearch.Run(nodes);
        targetExists = !targetSearch.found.empty();
    }
    const std::string status =
        MaterialReplaceStatus(matches.size(), changed, skipped, targetExists, preview);

    int refused = 0;
    for (const json& skip : skipped) {
        if (skip.value("reason", "") != "already_source") ++refused;
    }
    const std::string verb = preview ? "would be" : "was";
    if (status == "no_match") {
        warnings.push_back("No material named '" + targetName +
            "' exists on objects, in their sub-material slots or in the Material Editor.");
    } else if (status == "no_objects") {
        warnings.push_back("'" + targetName + "' exists only in the Material Editor; no object "
            "or sub-material slot uses it, so nothing " + verb + " replaced.");
    } else if (status == "blocked") {
        warnings.push_back("Every match of '" + targetName + "' was skipped, so nothing " + verb +
            " replaced; see skipped for reasons.");
    } else if (status == "unchanged") {
        warnings.push_back("Every match of '" + targetName + "' already uses '" + sourceName +
            "', so nothing " + verb + " replaced.");
    } else if (refused > 0) {
        warnings.push_back(std::to_string(refused) + " match(es) were skipped; see skipped for reasons.");
    }

    entry["status"] = status;
    entry["preview"] = preview;
    entry["match_count"] = static_cast<int>(matches.size());
    entry["replaced_count"] = changed;
    entry["reassigned_count"] = static_cast<int>(replacedObjects.size());
    entry["replaced_objects"] = replacedObjects;
    entry["replaced_slot_count"] = static_cast<int>(replacedSlots.size());
    entry["replaced_slots"] = replacedSlots;
    entry["skipped_count"] = static_cast<int>(skipped.size());
    entry["skipped"] = skipped;
    entry["affected_count"] = static_cast<int>(affectedObjects.size());
    entry["affected_objects"] = affectedObjects;
    if (!warnings.empty()) entry["warnings"] = warnings;
    return entry;
}

// ── native:replace_material ─────────────────────────────────────
std::string NativeHandlers::ReplaceMaterial(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&]() -> std::string {
        json p = json::parse(params);
        std::string sourceName = p.value("source_material", "");
        std::string targetName = p.value("target_material", "");
        bool preview = p.value("preview", false);
        bool includeSubs = p.value("include_sub_materials", true);
        const std::string sourceFrom = ReadReplaceSourceFrom(p, "");

        if (sourceName.empty() || targetName.empty())
            throw std::runtime_error("source_material and target_material are required");

        Interface* ip = GetCOREInterface();
        std::vector<INode*> nodes;
        CollectNodes(ip->GetRootNode(), nodes);

        MaterialReplaceOverlay overlay;
        json result = RunMaterialReplacement(sourceName, targetName, preview, false, nodes, overlay,
                                             sourceFrom, includeSubs, true);
        if (result.value("status", "") == "error")
            throw std::runtime_error(StructuredErrorPayload("NOT_FOUND", result.value("error", "")));

        if (!preview && result.value("replaced_count", 0) > 0)
            ip->RedrawViews(ip->GetTime());
        return result.dump();
    });
}

// ── native:batch_replace_materials ──────────────────────────────
// Entries apply in order inside the dispatcher's single undo transaction, and
// each one is evaluated against the scene the previous entries left. Previews
// chain the same way through an overlay instead of the scene.
std::string NativeHandlers::BatchReplaceMaterials(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&]() -> std::string {
        json p = json::parse(params);
        auto replacements = p.contains("replacements") && !p["replacements"].is_null()
                            ? p["replacements"] : json::array();
        bool preview = p.value("preview", false) || p.value("dry_run", false);
        bool includeSubs = p.value("include_sub_materials", true);
        const std::string defaultFrom = ReadReplaceSourceFrom(p, "");

        Interface* ip = GetCOREInterface();
        std::vector<INode*> nodes;
        CollectNodes(ip->GetRootNode(), nodes);

        MaterialReplaceOverlay overlay;
        json results = json::array();
        int totalReplaced = 0;
        int totalSlots = 0;
        int totalSkipped = 0;
        int errorCount = 0;

        for (const auto& rep : replacements) {
            std::string src = rep.is_object() ? rep.value("source", rep.value("source_material", "")) : "";
            std::string tgt = rep.is_object() ? rep.value("target", rep.value("target_material", "")) : "";

            if (src.empty() || tgt.empty()) {
                json entry;
                entry["source_material"] = src;
                entry["target_material"] = tgt;
                entry["status"] = "error";
                entry["error"] = "missing source or target";
                results.push_back(entry);
                ++errorCount;
                continue;
            }

            // source_from: per entry, or the batch-wide default.
            std::string sourceFrom = defaultFrom;
            try {
                sourceFrom = ReadReplaceSourceFrom(rep, defaultFrom);
            } catch (const std::exception& e) {
                json entry;
                entry["source_material"] = src;
                entry["target_material"] = tgt;
                entry["status"] = "error";
                entry["error"] = e.what();
                results.push_back(entry);
                ++errorCount;
                continue;
            }

            json entry = RunMaterialReplacement(src, tgt, preview, true, nodes, overlay, sourceFrom, includeSubs);
            if (entry.value("status", "") == "error") ++errorCount;
            totalReplaced += entry.value("replaced_count", 0);
            totalSlots += entry.value("replaced_slot_count", 0);
            totalSkipped += entry.value("skipped_count", 0);
            results.push_back(entry);
        }

        if (!preview && totalReplaced > 0) {
            ip->RedrawViews(ip->GetTime());
        }

        json result;
        result["results"] = results;
        result["total_replaced"] = totalReplaced;
        result["total_replaced_slots"] = totalSlots;
        result["total_skipped"] = totalSkipped;
        result["include_sub_materials"] = includeSubs;
        result["error_count"] = errorCount;
        result["status"] = totalReplaced > 0 ? (preview ? "preview" : "replaced") : "no_changes";
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

        // An unknown class token evaluates to undefined without an error, so
        // confirm the global now holds a texture map before reporting it.
        try {
            RunMAXScriptChecked(script);
        } catch (const std::exception& error) {
            throw std::runtime_error("Could not create " + mapClass + ": " + error.what());
        }
        if (RunMAXScriptChecked("(superClassOf " + globalVar + " == textureMap) as string") != "true")
            throw std::runtime_error(StructuredErrorPayload(
                "BAD_PARAM", "Unknown texture map class: " + mapClass,
                {{"message", "map_class must name a texture map class, for example Bitmaptexture or Noise."}}));

        // Set properties
        json setProps = json::array();
        json errors = json::array();
        for (auto& [key, val] : properties.items()) {
            std::string propScript = "try (" + globalVar + "." + key + " = " +
                                     MaxScriptLiteralText(val) + "; true) catch (false)";
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
        if (!errors.empty()) {
            result["errors"] = errors;
            result["warnings"] = json::array({"The map was created, but " + std::to_string(errors.size()) +
                " propert(ies) were not set: " + JoinNames(errors)});
        }
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
        if (!properties.is_object() || properties.empty())
            throw std::runtime_error("properties must name at least one property to set");
        // An undefined global makes every assignment fail; report that directly.
        if (RunMAXScriptChecked("(globalVars.isGlobal #" + globalVar + " and " + globalVar +
                                " != undefined) as string") != "true")
            throw std::runtime_error(StructuredErrorPayload(
                "NOT_FOUND", "MAXScript global is undefined: " + globalVar,
                {{"message", "Pass the global_var returned by create_texture_map."}}));

        json setProps = json::array();
        json errors = json::array();

        for (auto& [key, val] : properties.items()) {
            std::string script = "try (" + globalVar + "." + key + " = " +
                                 MaxScriptLiteralText(val) + "; true) catch (false)";
            std::string r = RunMAXScript(script);
            if (r == "true") {
                setProps.push_back(key);
            } else {
                errors.push_back(key);
            }
        }
        if (setProps.empty())
            throw std::runtime_error(StructuredErrorPayload(
                "BAD_PARAM", "No property was set on " + globalVar,
                {{"errors", errors}}));

        json result;
        result["status"] = errors.empty() ? "ok" : "partial";
        result["global_var"] = globalVar;
        result["set_properties"] = setProps;
        if (!errors.empty()) {
            result["errors"] = errors;
            result["warnings"] = json::array({std::to_string(errors.size()) +
                " propert(ies) were not set: " + JoinNames(errors)});
        }
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
            // Create new material at slot via MAXScript. Reset the global first:
            // an unknown class token evaluates to undefined without an error and
            // would otherwise reuse an older material.
            std::string script = "global __mcp_sub_mtl = undefined; __mcp_sub_mtl = " + matClass + "()";
            if (!matName.empty()) {
                script = "global __mcp_sub_mtl = undefined; __mcp_sub_mtl = " + matClass + " name:\"" +
                         JsonEscape(matName) + "\"";
            }
            if (!msParams.empty()) {
                script = "global __mcp_sub_mtl = undefined; __mcp_sub_mtl = " + matClass + " " + msParams;
                if (!matName.empty()) script += " name:\"" + JsonEscape(matName) + "\"";
            }
            try {
                RunMAXScriptChecked(script);
            } catch (const std::exception& error) {
                throw std::runtime_error("Could not create " + matClass + ": " + error.what());
            }
            if (RunMAXScriptChecked("(superClassOf __mcp_sub_mtl == material) as string") != "true")
                throw std::runtime_error(StructuredErrorPayload(
                    "BAD_PARAM", "Unknown material class: " + matClass));

            // Now get the created material and assign to slot. materialList is
            // the Multi/Sub-Object slot array; other parents reject it.
            std::string assignScript =
                "(getAnimByHandle " + std::to_string(NodeHandle(node)) + ").material.materialList[" +
                std::to_string(subIdx) + "] = __mcp_sub_mtl; __mcp_sub_mtl.name";
            std::string resultName;
            try {
                resultName = RunMAXScriptChecked(assignScript);
            } catch (const std::exception& error) {
                throw std::runtime_error("Could not assign sub-material " + std::to_string(subIdx) +
                    " on " + name + ": " + error.what());
            }

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
