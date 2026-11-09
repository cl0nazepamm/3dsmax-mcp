#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/core_bridge.h"

#include <imacroscript.h>
#include <actiontable.h>
#include <ifnpub.h>

using json = nlohmann::json;
using namespace HandlerHelpers;

// Local ParamType2 → string (same as plugin_introspect_handlers.cpp)
static std::string PType2Str(int ptype) {
    int base = ptype & ~TYPE_TAB;
    bool isTab = (ptype & TYPE_TAB) != 0;
    std::string name;
    switch (base) {
    case TYPE_FLOAT: name = "float"; break;
    case TYPE_INT: name = "int"; break;
    case TYPE_BOOL: name = "bool"; break;
    case TYPE_POINT3: name = "point3"; break;
    case TYPE_RGBA: name = "color"; break;
    case TYPE_STRING: case TYPE_FILENAME: name = "string"; break;
    case TYPE_INODE: name = "node"; break;
    case TYPE_MTL: name = "material"; break;
    case TYPE_TEXMAP: name = "texturemap"; break;
    case TYPE_ANGLE: name = "angle"; break;
    case TYPE_WORLD: name = "worldUnits"; break;
    case TYPE_COLOR_CHANNEL: name = "colorChannel"; break;
    case TYPE_TIMEVALUE: name = "timevalue"; break;
    case TYPE_INDEX: name = "index"; break;
    case TYPE_MATRIX3: name = "matrix3"; break;
    case TYPE_POINT4: name = "point4"; break;
    case TYPE_REFTARG: name = "refTarget"; break;
    case TYPE_VOID: name = "void"; break;
    case TYPE_ENUM: name = "enum"; break;
    case TYPE_VALUE: name = "value"; break;
    case TYPE_FPVALUE: name = "fpvalue"; break;
    case TYPE_OBJECT: name = "object"; break;
    case TYPE_CONTROL: name = "control"; break;
    case TYPE_INTERFACE: name = "interface"; break;
    case TYPE_TSTR: name = "tstr"; break;
    default: name = "type_" + std::to_string(base); break;
    }
    return isTab ? name + "[]" : name;
}

// ── native:list_macroscripts ────────────────────────────────────
// Walks MacroDir to enumerate every registered macroscript.
// Returns name, category, tooltip, source file, icon for each.
std::string NativeHandlers::ListMacroscripts(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&]() -> std::string {
        json p = params.empty() ? json::object() : json::parse(params, nullptr, false);
        std::string filterCategory = p.is_object() ? p.value("category", "") : "";
        std::string filterPattern = p.is_object() ? p.value("pattern", "") : "";
        int limit = p.is_object() ? p.value("limit", 500) : 500;

        MacroDir& macroDir = GetMacroScriptDir();
        int count = macroDir.Count();

        json macros = json::array();
        std::map<std::string, int> categoryCounts;

        for (int i = 0; i < count && (int)macros.size() < limit; i++) {
            MacroEntry* entry = macroDir.GetMacro(i);
            if (!entry) continue;

            std::string name = WideToUtf8(entry->GetName().data());
            std::string category = WideToUtf8(entry->GetCategory().data());
            std::string tooltip = WideToUtf8(entry->GetToolTip().data());
            std::string buttonText = WideToUtf8(entry->GetButtonText().data());
            std::string fileName = WideToUtf8(entry->GetFileName().data());

            categoryCounts[category]++;

            // Apply filters
            if (!filterCategory.empty()) {
                std::string catLower = category, filterLower = filterCategory;
                std::transform(catLower.begin(), catLower.end(), catLower.begin(), ::tolower);
                std::transform(filterLower.begin(), filterLower.end(), filterLower.begin(), ::tolower);
                if (catLower.find(filterLower) == std::string::npos) continue;
            }
            if (!filterPattern.empty() && !WildcardMatch(name, filterPattern) &&
                !WildcardMatch(category, filterPattern) &&
                !WildcardMatch(tooltip, filterPattern)) continue;

            json macro;
            macro["index"] = i;
            macro["id"] = (int)entry->GetID();
            macro["name"] = name;
            macro["category"] = category;
            if (!tooltip.empty()) macro["tooltip"] = tooltip;
            if (!buttonText.empty()) macro["buttonText"] = buttonText;
            if (!fileName.empty()) macro["sourceFile"] = fileName;

            macros.push_back(macro);
        }

        json result;
        result["totalRegistered"] = count;
        result["returned"] = (int)macros.size();
        result["categories"] = json::object();
        for (auto& [cat, cnt] : categoryCounts) {
            result["categories"][cat] = cnt;
        }
        result["macroscripts"] = macros;
        return result.dump();
    });
}

// ── native:list_action_tables ───────────────────────────────────
// Walks IActionManager to enumerate all action tables and their items.
// This covers every menu item, keyboard shortcut, and toolbar button.
std::string NativeHandlers::ListActionTables(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&]() -> std::string {
        json p = params.empty() ? json::object() : json::parse(params, nullptr, false);
        std::string filterPattern = p.is_object() ? p.value("pattern", "") : "";
        bool includeItems = p.is_object() ? p.value("include_items", false) : false;
        int limit = p.is_object() ? p.value("limit", 200) : 200;

        Interface* ip = GetCOREInterface();
        IActionManager* actionMgr = ip->GetActionManager();
        if (!actionMgr) throw std::runtime_error("ActionManager not available");

        int numTables = actionMgr->NumActionTables();
        json tables = json::array();

        for (int t = 0; t < numTables && (int)tables.size() < limit; t++) {
            ActionTable* table = actionMgr->GetTable(t);
            if (!table) continue;

            std::string tableName = WideToUtf8(table->GetName().data());

            if (!filterPattern.empty() && !WildcardMatch(tableName, filterPattern))
                continue;

            json tableJ;
            tableJ["index"] = t;
            tableJ["name"] = tableName;
            tableJ["id"] = (unsigned int)table->GetId();
            tableJ["contextId"] = (unsigned int)table->GetContextId();
            tableJ["numActions"] = table->Count();

            if (includeItems) {
                json items = json::array();
                for (int a = 0; a < table->Count(); a++) {
                    try {
                        ActionItem* item = table->GetActionByIndex(a);
                        if (!item) continue;

                        json itemJ;
                        itemJ["id"] = item->GetId();

                        // Safe text reads — each wrapped individually
                        try { MSTR s; item->GetMenuText(s);
                              if (s.data() && s.data()[0]) itemJ["menuText"] = WideToUtf8(s.data()); } catch (...) {}
                        try { MSTR s; item->GetDescriptionText(s);
                              if (s.data() && s.data()[0]) itemJ["description"] = WideToUtf8(s.data()); } catch (...) {}
                        try { MSTR s; item->GetCategoryText(s);
                              if (s.data() && s.data()[0]) itemJ["category"] = WideToUtf8(s.data()); } catch (...) {}
                        try { MSTR s; item->GetButtonText(s);
                              if (s.data() && s.data()[0]) itemJ["buttonText"] = WideToUtf8(s.data()); } catch (...) {}

                        // Skip IsEnabled/IsChecked — triggers evaluation chains that crash plugins
                        // Skip GetShortcutString — can crash on some items

                        // Macroscript link (safe — just reads a pointer)
                        try {
                            MacroEntry* macro = item->GetMacroScript();
                            if (macro) {
                                itemJ["macroscript"] = WideToUtf8(macro->GetName().data());
                                itemJ["macroCategory"] = WideToUtf8(macro->GetCategory().data());
                            }
                        } catch (...) {}

                        items.push_back(itemJ);
                    } catch (...) {
                        // Skip any item that crashes
                        continue;
                    }
                }
                tableJ["actions"] = items;
            }

            tables.push_back(tableJ);
        }

        json result;
        result["totalTables"] = numTables;
        result["returned"] = (int)tables.size();
        result["tables"] = tables;
        return result.dump();
    });
}

// ── native:introspect_interface ─────────────────────────────────
// Deep introspection of any FPInterface by name. Reads all functions,
// properties, and enumerations with full type signatures.
// Works on global interfaces (like RTUVExtInterface) and class interfaces.
std::string NativeHandlers::IntrospectInterface(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&]() -> std::string {
        json p = json::parse(params);
        std::string ifaceName = p.value("name", "");
        if (ifaceName.empty())
            throw std::runtime_error("name is required");

        // Search for the interface by name across all sources:
        // 1. Global FPInterfaces registered on ClassDescs
        // 2. Core interface
        FPInterfaceDesc* desc = nullptr;
        FPInterface* iface = nullptr;

        // Search DllDir for ClassDesc-registered interfaces
        std::wstring wname = Utf8ToWide(ifaceName);
        auto& dir = DllDir::GetInstance();
        for (int d = 0; d < dir.Count() && !desc; d++) {
            const DllDesc& dll = dir[d];
            for (int c = 0; c < dll.NumberOfClasses() && !desc; c++) {
                ClassDesc* cd = dll[c];
                if (!cd) continue;
                ClassDesc2* cd2 = dynamic_cast<ClassDesc2*>(cd);
                if (!cd2) continue;
                for (int i = 0; i < cd2->NumInterfaces(); i++) {
                    FPInterface* fi = cd2->GetInterfaceAt(i);
                    if (!fi) continue;
                    FPInterfaceDesc* d2 = fi->GetDesc();
                    if (!d2) continue;
                    if (d2->internal_name.data() &&
                        _wcsicmp(d2->internal_name.data(), wname.c_str()) == 0) {
                        desc = d2;
                        iface = fi;
                        break;
                    }
                }
            }
        }

        // Also try GetCOREInterface for static interfaces
        if (!desc) {
            Interface* ip = GetCOREInterface();
            // Try looking up by name via the FP system
            // FPInterface::GetInterfaceByName is not available, so we use
            // a different approach: iterate known interface IDs is impractical.
            // Instead, use MAXScript to resolve the interface name to an object.
            std::string script = "(local iface = " + ifaceName +
                "; if (classOf iface) == Interface then iface else undefined)";
            try {
                EvaluateMAXScript(Utf8ToWide(script), [&](FPValue& fpv) {
                    if (fpv.type == TYPE_INTERFACE && fpv.fpi != nullptr) {
                        iface = fpv.fpi;
                        desc = iface->GetDesc();
                    }
                });
            } catch (...) {}
        }

        if (!desc)
            throw std::runtime_error("Interface '" + ifaceName + "' not found");

        // Build result
        json result;
        result["name"] = desc->internal_name.data() ?
            WideToUtf8(desc->internal_name.data()) : ifaceName;
        result["id"] = json::array({
            (unsigned int)desc->ID.PartA(),
            (unsigned int)desc->ID.PartB()
        });
        if (desc->cd) {
            result["ownerClass"] = WideToUtf8(desc->cd->ClassName());
        }

        // Functions
        result["functions"] = json::array();
        for (int f = 0; f < desc->functions.Count(); f++) {
            FPFunctionDef* fn = desc->functions[f];
            if (!fn) continue;

            json fnJ;
            fnJ["id"] = (int)fn->ID;
            fnJ["name"] = fn->internal_name.data() ?
                WideToUtf8(fn->internal_name.data()) : "";
            fnJ["returnType"] = PType2Str(fn->result_type);

            // Parameters
            json fnParams = json::array();
            for (int pa = 0; pa < fn->params.Count(); pa++) {
                json paramJ;
                FPParamDef* fpd = fn->params[pa];
                if (!fpd) continue;
                paramJ["name"] = fpd->internal_name.data() ?
                    WideToUtf8(fpd->internal_name.data()) : "arg_" + std::to_string(pa);
                paramJ["type"] = PType2Str(fpd->type);
                fnParams.push_back(paramJ);
            }
            fnJ["params"] = fnParams;
            result["functions"].push_back(fnJ);
        }

        // Properties
        result["properties"] = json::array();
        for (int pr = 0; pr < desc->props.Count(); pr++) {
            FPPropDef* prop = desc->props[pr];
            if (!prop) continue;

            json propJ;
            propJ["name"] = (prop->internal_name.data() && prop->internal_name.data()[0]) ?
                WideToUtf8(prop->internal_name.data()) : "";
            propJ["type"] = PType2Str(prop->prop_type);
            propJ["readOnly"] = (prop->setter_ID == FP_NO_FUNCTION);

            // Read current value via MAXScript — safer than Invoke() which
            // can crash if the plugin isn't in active state
            if (iface && prop->getter_ID != FP_NO_FUNCTION) {
                try {
                    std::string propName = (prop->internal_name.data() && prop->internal_name.data()[0]) ?
                        WideToUtf8(prop->internal_name.data()) : "";
                    if (!propName.empty()) {
                        std::string script = "try (" + ifaceName + "." + propName + ") catch (undefined)";
                        EvaluateMAXScript(Utf8ToWide(script), [&](FPValue& val) {
                            switch (val.type) {
                            case TYPE_INT: propJ["value"] = val.i; break;
                            case TYPE_FLOAT: propJ["value"] = val.f; break;
                            case TYPE_BOOL: propJ["value"] = val.i != 0; break;
                            case TYPE_STRING:
                            case TYPE_FILENAME:
                                if (val.s) propJ["value"] = WideToUtf8(val.s); break;
                            case TYPE_TSTR:
                                if (val.tstr) propJ["value"] = WideToUtf8(val.tstr->data()); break;
                            default: break;
                            }
                        });
                    }
                } catch (...) {}
            }

            result["properties"].push_back(propJ);
        }

        // Enumerations
        result["enums"] = json::array();
        for (int e = 0; e < desc->enumerations.Count(); e++) {
            FPEnum* en = desc->enumerations[e];
            if (!en) continue;

            json enumJ;
            enumJ["id"] = (int)en->ID;
            json values = json::array();
            for (int v = 0; v < en->enumeration.Count(); v++) {
                json valJ;
                valJ["name"] = en->enumeration[v].name ?
                    WideToUtf8(en->enumeration[v].name) : "";
                valJ["value"] = en->enumeration[v].code;
                values.push_back(valJ);
            }
            enumJ["values"] = values;
            result["enums"].push_back(enumJ);
        }

        return result.dump();
    });
}

// ── Helper: resolve FPInterface by name ─────────────────────────
static FPInterface* FindFPInterfaceByName(const std::string& name) {
    std::wstring wname = Utf8ToWide(name);

    // Search ClassDesc-registered interfaces
    auto& dir = DllDir::GetInstance();
    for (int d = 0; d < dir.Count(); d++) {
        const DllDesc& dll = dir[d];
        for (int c = 0; c < dll.NumberOfClasses(); c++) {
            ClassDesc* cd = dll[c];
            if (!cd) continue;
            ClassDesc2* cd2 = dynamic_cast<ClassDesc2*>(cd);
            if (!cd2) continue;
            for (int i = 0; i < cd2->NumInterfaces(); i++) {
                FPInterface* fi = cd2->GetInterfaceAt(i);
                if (!fi) continue;
                FPInterfaceDesc* d2 = fi->GetDesc();
                if (d2 && d2->internal_name.data() &&
                    _wcsicmp(d2->internal_name.data(), wname.c_str()) == 0)
                    return fi;
            }
        }
    }

    // Fallback: resolve via MAXScript (for global/static interfaces)
    FPInterface* found = nullptr;
    try {
        std::string script = "(local iface = " + name +
            "; if (classOf iface) == Interface then iface else undefined)";
        EvaluateMAXScript(Utf8ToWide(script), [&](FPValue& fpv) {
            if (fpv.type == TYPE_INTERFACE && fpv.fpi != nullptr)
                found = fpv.fpi;
        });
    } catch (...) {}

    return found;
}

// ── native:invoke_interface ─────────────────────────────────────
// Call any FPInterface function or set properties directly via C++ SDK.
// No MAXScript parsing for function calls. Pure Invoke().
std::string NativeHandlers::InvokeInterface(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&]() -> std::string {
        json p = json::parse(params);
        std::string ifaceName = p.value("interface", "");
        std::string fnName = p.value("function", "");
        auto setProps = p.value("set", json::object());

        if (ifaceName.empty())
            throw std::runtime_error("interface is required");

        FPInterface* iface = FindFPInterfaceByName(ifaceName);
        if (!iface)
            throw std::runtime_error("Interface '" + ifaceName + "' not found");

        json result;
        result["interface"] = ifaceName;

        // Set properties first (if any)
        if (!setProps.empty()) {
            json setResults = json::object();
            FPInterfaceDesc* desc = iface->GetDesc();
            for (auto& [key, val] : setProps.items()) {
                bool found = false;
                if (desc) {
                    for (int pr = 0; pr < desc->props.Count(); pr++) {
                        FPPropDef* prop = desc->props[pr];
                        if (!prop || !prop->internal_name.data()) continue;
                        std::string pname = WideToUtf8(prop->internal_name.data());
                        if (_stricmp(pname.c_str(), key.c_str()) != 0) continue;
                        if (prop->setter_ID == FP_NO_FUNCTION) {
                            setResults[key] = "readonly";
                            found = true;
                            break;
                        }
                        // Use MAXScript for setting — type coercion is complex
                        try {
                            std::string script = ifaceName + "." + key + " = " + val.get<std::string>();
                            RunMAXScriptChecked(script);
                            setResults[key] = "ok";
                        } catch (...) {
                            setResults[key] = "error";
                        }
                        found = true;
                        break;
                    }
                }
                if (!found) setResults[key] = "not_found";
            }
            result["set"] = setResults;
            json unset = json::array();
            for (auto& [key, state] : setResults.items()) {
                if (state != "ok") unset.push_back(key + " (" + state.get<std::string>() + ")");
            }
            if (!unset.empty()) {
                result["warnings"] = json::array({std::to_string(unset.size()) +
                    " interface propert(ies) were not set: " + JoinNames(unset)});
            }
        }

        // Call function (if specified)
        if (!fnName.empty()) {
            FunctionID fid = iface->FindFn(Utf8ToWide(fnName).c_str());
            if (fid == FP_NO_FUNCTION)
                throw std::runtime_error("Function '" + fnName + "' not found on " + ifaceName);

            FPValue retVal;
            FPStatus status = iface->Invoke(fid, 0, retVal);
            if (status != FPS_OK)
                throw std::runtime_error("Invoke failed for " + fnName);

            result["function"] = fnName;
            result["status"] = "ok";

            switch (retVal.type) {
            case TYPE_INT: result["returnValue"] = retVal.i; break;
            case TYPE_FLOAT: result["returnValue"] = retVal.f; break;
            case TYPE_BOOL: result["returnValue"] = retVal.i != 0; break;
            case TYPE_STRING:
            case TYPE_FILENAME:
                if (retVal.s) result["returnValue"] = WideToUtf8(retVal.s); break;
            case TYPE_TSTR:
                if (retVal.tstr) result["returnValue"] = WideToUtf8(retVal.tstr->data()); break;
            case TYPE_VOID: break;
            default: break;
            }
        }

        return result.dump();
    });
}

// ── native:run_macroscript ──────────────────────────────────────
// Execute a macroscript by category + name. Pure SDK, no MAXScript parsing.
std::string NativeHandlers::RunMacroscript(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&]() -> std::string {
        json p = json::parse(params);
        std::string category = p.value("category", "");
        std::string name = p.value("name", "");

        if (name.empty())
            throw std::runtime_error("name is required");

        MacroDir& macroDir = GetMacroScriptDir();
        MacroEntry* entry = nullptr;

        if (!category.empty()) {
            entry = macroDir.FindMacro(
                Utf8ToWide(category).c_str(),
                Utf8ToWide(name).c_str()
            );
        }

        if (!entry) {
            int count = macroDir.Count();
            for (int i = 0; i < count; i++) {
                MacroEntry* e = macroDir.GetMacro(i);
                if (!e) continue;
                std::string eName = WideToUtf8(e->GetName().data());
                if (_stricmp(eName.c_str(), name.c_str()) == 0) {
                    entry = e;
                    break;
                }
            }
        }

        if (!entry)
            throw std::runtime_error("Macroscript '" + name + "' not found");

        entry->Execute();

        json result;
        result["status"] = "executed";
        result["name"] = WideToUtf8(entry->GetName().data());
        result["category"] = WideToUtf8(entry->GetCategory().data());
        return result.dump();
    });
}
