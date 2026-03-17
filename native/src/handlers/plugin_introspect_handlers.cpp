#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/bridge_gup.h"

#include <ifnpub.h>
#include <iparamb2.h>
#include <modstack.h>
#include <set>
#include <map>

using json = nlohmann::json;
using namespace HandlerHelpers;

// ── ParamType2 → human-readable string ──────────────────────────
static std::string ParamTypeToString(int ptype) {
    int base = ptype & ~TYPE_TAB;
    bool isTab = (ptype & TYPE_TAB) != 0;
    std::string name;
    switch (base) {
    case TYPE_FLOAT:          name = "float"; break;
    case TYPE_INT:            name = "int"; break;
    case TYPE_RGBA:           name = "color"; break;
    case TYPE_POINT3:         name = "point3"; break;
    case TYPE_BOOL:           name = "bool"; break;
    case TYPE_ANGLE:          name = "angle"; break;
    case TYPE_PCNT_FRAC:      name = "percent"; break;
    case TYPE_WORLD:          name = "worldUnits"; break;
    case TYPE_STRING:         name = "string"; break;
    case TYPE_FILENAME:       name = "filename"; break;
    case TYPE_TEXMAP:         name = "texturemap"; break;
    case TYPE_MTL:            name = "material"; break;
    case TYPE_BITMAP:         name = "bitmap"; break;
    case TYPE_INODE:          name = "node"; break;
    case TYPE_REFTARG:        name = "refTarget"; break;
    case TYPE_INDEX:          name = "index"; break;
    case TYPE_MATRIX3:        name = "matrix3"; break;
    case TYPE_POINT4:         name = "point4"; break;
    case TYPE_FRGBA:          name = "frgba"; break;
    case TYPE_ENUM:           name = "enum"; break;
    case TYPE_TIMEVALUE:      name = "time"; break;
    case TYPE_RADIOBTN_INDEX: name = "radioIndex"; break;
    case TYPE_COLOR_CHANNEL:  name = "colorChannel"; break;
    case TYPE_POINT2:         name = "point2"; break;
    case TYPE_VALUE:          name = "maxValue"; break;
    case TYPE_FPVALUE:        name = "fpValue"; break;
    case TYPE_OBJECT:         name = "object"; break;
    case TYPE_CONTROL:        name = "controller"; break;
    default:                  name = "type_" + std::to_string(base); break;
    }
    if (isTab) name += "[]";
    return name;
}

// ── Extract default value from ParamDef as JSON ─────────────────
static json ParamDefValue(const ParamDef& pd) {
    int base = pd.type & ~TYPE_TAB;
    try {
        switch (base) {
        case TYPE_FLOAT:
        case TYPE_ANGLE:
        case TYPE_PCNT_FRAC:
        case TYPE_WORLD:
        case TYPE_COLOR_CHANNEL:
            return pd.def.f;
        case TYPE_INT:
        case TYPE_BOOL:
        case TYPE_INDEX:
        case TYPE_TIMEVALUE:
        case TYPE_RADIOBTN_INDEX:
            return pd.def.i;
        case TYPE_RGBA:
        case TYPE_POINT3:
            return json::array({pd.def.p->x, pd.def.p->y, pd.def.p->z});
        default:
            return nullptr;
        }
    } catch (...) {
        return nullptr;
    }
}

// ── Extract range from ParamDef ─────────────────────────────────
static json ParamDefRange(const ParamDef& pd) {
    int base = pd.type & ~TYPE_TAB;
    try {
        switch (base) {
        case TYPE_FLOAT:
        case TYPE_ANGLE:
        case TYPE_PCNT_FRAC:
        case TYPE_WORLD:
        case TYPE_COLOR_CHANNEL:
            return json::array({pd.range_low.f, pd.range_high.f});
        case TYPE_INT:
        case TYPE_BOOL:
        case TYPE_INDEX:
        case TYPE_TIMEVALUE:
        case TYPE_RADIOBTN_INDEX:
            return json::array({pd.range_low.i, pd.range_high.i});
        default:
            return nullptr;
        }
    } catch (...) {
        return nullptr;
    }
}

// ── Build ParamBlock2 descriptor JSON ───────────────────────────
static json DescribeParamBlock(ParamBlockDesc2* desc) {
    json pb;
    pb["name"] = desc->int_name != nullptr ? WideToUtf8(desc->int_name) : "";
    pb["id"] = desc->ID;
    pb["params"] = json::array();

    for (int i = 0; i < desc->count; i++) {
        ParamID pid = desc->IndextoID(i);
        const ParamDef& pd = desc->GetParamDef(pid);

        json param;
        param["name"] = pd.int_name ? WideToUtf8(pd.int_name) : ("param_" + std::to_string(pid));
        param["id"] = (int)pid;
        param["type"] = ParamTypeToString(pd.type);
        param["animatable"] = (pd.flags & P_ANIMATABLE) != 0;

        json def = ParamDefValue(pd);
        if (!def.is_null()) param["default"] = def;

        json range = ParamDefRange(pd);
        if (!range.is_null()) param["range"] = range;

        pb["params"].push_back(param);
    }
    return pb;
}

// ── Build FPInterface descriptor JSON ───────────────────────────
static json DescribeInterface(FPInterface* fpi) {
    json iface;
    FPInterfaceDesc* desc = nullptr;
    try {
        desc = fpi->GetDesc();
    } catch (...) {
        return nullptr;
    }
    if (!desc) return nullptr;

    iface["name"] = (desc->internal_name.data() && desc->internal_name.data()[0])
        ? WideToUtf8(desc->internal_name.data()) : "";
    iface["id"] = json::array({
        (int)desc->GetID().PartA(),
        (int)desc->GetID().PartB()
    });

    // Functions
    iface["functions"] = json::array();
    for (int f = 0; f < desc->functions.Count(); f++) {
        FPFunctionDef* fdef = desc->functions[f];
        if (!fdef) continue;

        json func;
        func["name"] = (fdef->internal_name.data() && fdef->internal_name.data()[0])
            ? WideToUtf8(fdef->internal_name.data()) : "";
        func["returnType"] = ParamTypeToString(fdef->result_type);

        func["params"] = json::array();
        for (int p = 0; p < fdef->params.Count(); p++) {
            FPParamDef* fpd = fdef->params[p];
            if (!fpd) continue;
            json param;
            param["name"] = (fpd->internal_name.data() && fpd->internal_name.data()[0])
                ? WideToUtf8(fpd->internal_name.data()) : ("arg" + std::to_string(p));
            param["type"] = ParamTypeToString(fpd->type);
            func["params"].push_back(param);
        }
        iface["functions"].push_back(func);
    }

    // Properties
    iface["properties"] = json::array();
    for (int p = 0; p < desc->props.Count(); p++) {
        FPPropDef* pdef = desc->props[p];
        if (!pdef) continue;

        json prop;
        prop["name"] = (pdef->internal_name.data() && pdef->internal_name.data()[0])
            ? WideToUtf8(pdef->internal_name.data()) : "";
        prop["type"] = ParamTypeToString(pdef->prop_type);
        prop["readOnly"] = (pdef->setter_ID == FP_NO_FUNCTION);
        iface["properties"].push_back(prop);
    }

    return iface;
}

// ── Build SubAnim tree ──────────────────────────────────────────
static json DescribeSubAnims(Animatable* anim, int depth, int maxDepth) {
    json subs = json::array();
    if (!anim || depth >= maxDepth) return subs;

    int n = anim->NumSubs();
    for (int i = 0; i < n; i++) {
        json sub;
        try {
            MSTR name = anim->SubAnimName(i, false);
            sub["name"] = WideToUtf8(name.data());
        } catch (...) {
            sub["name"] = "sub_" + std::to_string(i);
        }
        sub["index"] = i;

        Animatable* child = anim->SubAnim(i);
        if (child) {
            sub["class"] = WideToUtf8(child->ClassName().data());
            if (depth < maxDepth - 1) {
                json childSubs = DescribeSubAnims(child, depth + 1, maxDepth);
                if (!childSubs.empty()) sub["children"] = childSubs;
            }
        } else {
            sub["class"] = nullptr;
        }
        subs.push_back(sub);
    }
    return subs;
}

// ═════════════════════════════════════════════════════════════════
// native:discover_classes — enumerate ALL registered classes
// ═════════════════════════════════════════════════════════════════
std::string NativeHandlers::DiscoverClasses(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = params.empty() ? json::object() : json::parse(params, nullptr, false);

        // Optional filters
        std::string filterSuper = p.is_object() && p.contains("superclass")
            ? p["superclass"].get<std::string>() : "";
        std::string filterPattern = p.is_object() && p.contains("pattern")
            ? p["pattern"].get<std::string>() : "";
        int limit = p.is_object() && p.contains("limit")
            ? p["limit"].get<int>() : 500;

        // Map superclass name to SClass_ID
        SClass_ID filterSID = 0;
        if (!filterSuper.empty()) {
            std::string lower = filterSuper;
            for (auto& c : lower) c = (char)tolower((unsigned char)c);
            if (lower == "geometry" || lower == "geometryclass") filterSID = GEOMOBJECT_CLASS_ID;
            else if (lower == "modifier") filterSID = OSM_CLASS_ID;
            else if (lower == "material") filterSID = MATERIAL_CLASS_ID;
            else if (lower == "texturemap" || lower == "texture") filterSID = TEXMAP_CLASS_ID;
            else if (lower == "helper") filterSID = HELPER_CLASS_ID;
            else if (lower == "light") filterSID = LIGHT_CLASS_ID;
            else if (lower == "camera") filterSID = CAMERA_CLASS_ID;
            else if (lower == "controller") filterSID = CTRL_FLOAT_CLASS_ID; // approximate
            else if (lower == "shape" || lower == "spline") filterSID = SHAPE_CLASS_ID;
            else if (lower == "wsm" || lower == "spacewarp") filterSID = WSM_CLASS_ID;
        }

        auto& dir = DllDir::GetInstance();
        json classes = json::array();
        int count = 0;

        for (int d = 0; d < dir.Count() && count < limit; d++) {
            const DllDesc& dll = dir[d];
            for (int c = 0; c < dll.NumberOfClasses() && count < limit; c++) {
                ClassDesc* cd = dll[c];
                if (!cd) continue;

                // Filter by superclass
                if (filterSID != 0 && cd->SuperClassID() != filterSID) continue;

                std::string className = cd->ClassName()
                    ? WideToUtf8(cd->ClassName()) : "";
                std::string internalName = cd->InternalName()
                    ? WideToUtf8(cd->InternalName()) : "";

                // Filter by pattern
                if (!filterPattern.empty()) {
                    if (!WildcardMatch(className, filterPattern) &&
                        !WildcardMatch(internalName, filterPattern))
                        continue;
                }

                if (className.empty() && internalName.empty()) continue;

                json entry;
                entry["name"] = className;
                if (!internalName.empty() && internalName != className)
                    entry["internalName"] = internalName;
                entry["classID"] = json::array({
                    (unsigned int)cd->ClassID().PartA(),
                    (unsigned int)cd->ClassID().PartB()
                });

                // SuperClass name
                SClass_ID sid = cd->SuperClassID();
                if (sid == GEOMOBJECT_CLASS_ID) entry["superclass"] = "geometry";
                else if (sid == OSM_CLASS_ID) entry["superclass"] = "modifier";
                else if (sid == MATERIAL_CLASS_ID) entry["superclass"] = "material";
                else if (sid == TEXMAP_CLASS_ID) entry["superclass"] = "texturemap";
                else if (sid == HELPER_CLASS_ID) entry["superclass"] = "helper";
                else if (sid == LIGHT_CLASS_ID) entry["superclass"] = "light";
                else if (sid == CAMERA_CLASS_ID) entry["superclass"] = "camera";
                else if (sid == SHAPE_CLASS_ID) entry["superclass"] = "shape";
                else if (sid == WSM_CLASS_ID) entry["superclass"] = "spacewarp";
                else entry["superclass"] = "sid_" + std::to_string(sid);

                std::string cat = cd->Category() ? WideToUtf8(cd->Category()) : "";
                if (!cat.empty()) entry["category"] = cat;

                // Counts for quick summary
                ClassDesc2* cd2 = dynamic_cast<ClassDesc2*>(cd);
                if (cd2) {
                    entry["numParamBlocks"] = cd2->NumParamBlockDescs();
                    entry["numInterfaces"] = cd2->NumInterfaces();
                }

                classes.push_back(entry);
                count++;
            }
        }

        json result;
        result["totalFound"] = count;
        result["classes"] = classes;
        return result.dump();
    });
}

// ═════════════════════════════════════════════════════════════════
// native:introspect_class — full API surface of a class by name
// ═════════════════════════════════════════════════════════════════
std::string NativeHandlers::IntrospectClass(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = params.empty() ? json::object() : json::parse(params, nullptr, false);
        if (!p.is_object() || !p.contains("class_name"))
            throw std::runtime_error("class_name is required");

        std::string className = p["class_name"].get<std::string>();

        // Find the ClassDesc
        ClassDesc* cd = FindClassDescByName(className);
        if (!cd)
            throw std::runtime_error("Class not found: " + className);

        json result;
        result["className"] = cd->ClassName() ? WideToUtf8(cd->ClassName()) : className;
        if (cd->InternalName())
            result["internalName"] = WideToUtf8(cd->InternalName());
        result["classID"] = json::array({
            (unsigned int)cd->ClassID().PartA(),
            (unsigned int)cd->ClassID().PartB()
        });

        // SuperClass
        SClass_ID sid = cd->SuperClassID();
        if (sid == GEOMOBJECT_CLASS_ID) result["superclass"] = "geometry";
        else if (sid == OSM_CLASS_ID) result["superclass"] = "modifier";
        else if (sid == MATERIAL_CLASS_ID) result["superclass"] = "material";
        else if (sid == TEXMAP_CLASS_ID) result["superclass"] = "texturemap";
        else if (sid == HELPER_CLASS_ID) result["superclass"] = "helper";
        else if (sid == LIGHT_CLASS_ID) result["superclass"] = "light";
        else if (sid == CAMERA_CLASS_ID) result["superclass"] = "camera";
        else result["superclass"] = "sid_" + std::to_string(sid);

        if (cd->Category())
            result["category"] = WideToUtf8(cd->Category());

        // ParamBlock2 descriptors
        ClassDesc2* cd2 = dynamic_cast<ClassDesc2*>(cd);
        result["paramBlocks"] = json::array();
        if (cd2) {
            for (int i = 0; i < cd2->NumParamBlockDescs(); i++) {
                ParamBlockDesc2* pbd = cd2->GetParamBlockDesc(i);
                if (pbd) {
                    result["paramBlocks"].push_back(DescribeParamBlock(pbd));
                }
            }
        }

        // FPInterfaces
        result["interfaces"] = json::array();
        if (cd2) {
            for (int i = 0; i < cd2->NumInterfaces(); i++) {
                try {
                    FPInterface* fpi = cd2->GetInterfaceAt(i);
                    if (fpi) {
                        json iface = DescribeInterface(fpi);
                        if (!iface.is_null()) {
                            result["interfaces"].push_back(iface);
                        }
                    }
                } catch (...) {
                    // Skip malformed interfaces
                }
            }
        }

        return result.dump();
    });
}

// ═════════════════════════════════════════════════════════════════
// native:introspect_instance — deep introspection of a live object
// ═════════════════════════════════════════════════════════════════
std::string NativeHandlers::IntrospectInstance(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = params.empty() ? json::object() : json::parse(params, nullptr, false);
        if (!p.is_object() || !p.contains("name"))
            throw std::runtime_error("name is required");

        std::string objName = p["name"].get<std::string>();
        bool includeSubAnims = p.value("include_subanims", false);
        int subAnimDepth = p.value("subanim_depth", 3);

        INode* node = FindNodeByName(objName);
        if (!node)
            throw std::runtime_error("Object not found: " + objName);

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        json result;
        result["name"] = WideToUtf8(node->GetName());

        // Evaluate the object
        ObjectState os = node->EvalWorldState(t);
        Object* obj = os.obj;
        if (!obj)
            throw std::runtime_error("Cannot evaluate object: " + objName);

        result["class"] = WideToUtf8(obj->ClassName().data());
        result["classID"] = json::array({
            (unsigned int)obj->ClassID().PartA(),
            (unsigned int)obj->ClassID().PartB()
        });

        // SuperClass
        SClass_ID sid = obj->SuperClassID();
        if (sid == GEOMOBJECT_CLASS_ID) result["superclass"] = "geometry";
        else if (sid == OSM_CLASS_ID) result["superclass"] = "modifier";
        else if (sid == MATERIAL_CLASS_ID) result["superclass"] = "material";
        else if (sid == TEXMAP_CLASS_ID) result["superclass"] = "texturemap";
        else if (sid == HELPER_CLASS_ID) result["superclass"] = "helper";
        else if (sid == LIGHT_CLASS_ID) result["superclass"] = "light";
        else if (sid == CAMERA_CLASS_ID) result["superclass"] = "camera";
        else result["superclass"] = "sid_" + std::to_string(sid);

        // ParamBlock2 instances with LIVE VALUES
        result["paramBlocks"] = json::array();
        int numPB = obj->NumParamBlocks();
        for (int i = 0; i < numPB; i++) {
            IParamBlock2* pb = obj->GetParamBlock(i);
            if (!pb) continue;

            ParamBlockDesc2* desc = pb->GetDesc();
            if (!desc) continue;

            json pbj;
            pbj["name"] = desc->int_name != nullptr ? WideToUtf8(desc->int_name) : "";
            pbj["id"] = desc->ID;
            pbj["params"] = json::array();

            for (int j = 0; j < desc->count; j++) {
                ParamID pid = desc->IndextoID(j);
                const ParamDef& pd = desc->GetParamDef(pid);

                json param;
                param["name"] = pd.int_name ? WideToUtf8(pd.int_name) : "";
                param["id"] = (int)pid;
                param["type"] = ParamTypeToString(pd.type);

                int base = pd.type & ~TYPE_TAB;
                bool isTab = (pd.type & TYPE_TAB) != 0;

                // Read live value
                if (!isTab) {
                    try {
                        switch (base) {
                        case TYPE_FLOAT:
                        case TYPE_ANGLE:
                        case TYPE_PCNT_FRAC:
                        case TYPE_WORLD:
                        case TYPE_COLOR_CHANNEL:
                            param["value"] = pb->GetFloat(pid, t);
                            break;
                        case TYPE_INT:
                        case TYPE_BOOL:
                        case TYPE_INDEX:
                        case TYPE_TIMEVALUE:
                        case TYPE_RADIOBTN_INDEX:
                            param["value"] = pb->GetInt(pid, t);
                            break;
                        case TYPE_POINT3:
                        case TYPE_RGBA: {
                            Point3 v = pb->GetPoint3(pid, t);
                            param["value"] = json::array({v.x, v.y, v.z});
                            break;
                        }
                        case TYPE_STRING:
                        case TYPE_FILENAME: {
                            const MCHAR* s = pb->GetStr(pid, t);
                            param["value"] = s ? WideToUtf8(s) : "";
                            break;
                        }
                        case TYPE_TEXMAP: {
                            Texmap* tm = pb->GetTexmap(pid, t);
                            if (tm) param["value"] = WideToUtf8(tm->GetName().data());
                            else param["value"] = nullptr;
                            break;
                        }
                        case TYPE_MTL: {
                            Mtl* m = pb->GetMtl(pid, t);
                            if (m) param["value"] = WideToUtf8(m->GetName().data());
                            else param["value"] = nullptr;
                            break;
                        }
                        case TYPE_INODE: {
                            INode* n = pb->GetINode(pid, t);
                            if (n) param["value"] = WideToUtf8(n->GetName());
                            else param["value"] = nullptr;
                            break;
                        }
                        default:
                            param["value"] = nullptr;
                            break;
                        }
                    } catch (...) {
                        param["value"] = nullptr;
                    }
                } else {
                    // Tab (array) — just report count
                    try {
                        param["tabCount"] = pb->Count(pid);
                    } catch (...) {}
                }

                // Range
                json range = ParamDefRange(pd);
                if (!range.is_null()) param["range"] = range;

                pbj["params"].push_back(param);
            }
            result["paramBlocks"].push_back(pbj);
        }

        // FPInterfaces on the live object
        result["interfaces"] = json::array();
        // Try ClassDesc2 interfaces first
        ClassDesc* cd = FindClassDescByName(WideToUtf8(obj->ClassName().data()));
        ClassDesc2* cd2 = cd ? dynamic_cast<ClassDesc2*>(cd) : nullptr;
        if (cd2) {
            for (int i = 0; i < cd2->NumInterfaces(); i++) {
                try {
                    FPInterface* fpi = cd2->GetInterfaceAt(i);
                    if (fpi) {
                        json iface = DescribeInterface(fpi);
                        if (!iface.is_null()) {
                            result["interfaces"].push_back(iface);
                        }
                    }
                } catch (...) {}
            }
        }

        // Modifier stack
        result["modifiers"] = json::array();
        Object* objRef = node->GetObjectRef();
        if (objRef && objRef->SuperClassID() == GEN_DERIVOB_CLASS_ID) {
            IDerivedObject* dobj = (IDerivedObject*)objRef;
            for (int m = 0; m < dobj->NumModifiers(); m++) {
                Modifier* mod = dobj->GetModifier(m);
                if (!mod) continue;

                json modj;
                modj["name"] = WideToUtf8(mod->GetName(false).data());
                modj["class"] = WideToUtf8(mod->ClassName().data());
                modj["enabled"] = mod->IsEnabled() ? true : false;
                modj["classID"] = json::array({
                    (unsigned int)mod->ClassID().PartA(),
                    (unsigned int)mod->ClassID().PartB()
                });

                // Modifier param blocks
                modj["paramBlocks"] = json::array();
                int modPB = mod->NumParamBlocks();
                for (int mp = 0; mp < modPB; mp++) {
                    IParamBlock2* mpb = mod->GetParamBlock(mp);
                    if (mpb && mpb->GetDesc()) {
                        modj["paramBlocks"].push_back(DescribeParamBlock(mpb->GetDesc()));
                    }
                }

                result["modifiers"].push_back(modj);
            }
        }

        // Material
        Mtl* mtl = node->GetMtl();
        if (mtl) {
            json matj;
            matj["name"] = WideToUtf8(mtl->GetName().data());
            matj["class"] = WideToUtf8(mtl->ClassName().data());
            matj["numSubMtls"] = mtl->NumSubMtls();
            matj["numSubTexmaps"] = mtl->NumSubTexmaps();

            // Material param blocks
            matj["paramBlocks"] = json::array();
            int matPB = mtl->NumParamBlocks();
            for (int mp = 0; mp < matPB; mp++) {
                IParamBlock2* mpb = mtl->GetParamBlock(mp);
                if (mpb && mpb->GetDesc()) {
                    matj["paramBlocks"].push_back(DescribeParamBlock(mpb->GetDesc()));
                }
            }

            result["material"] = matj;
        }

        // SubAnim tree (optional, can be large)
        if (includeSubAnims) {
            result["subAnims"] = DescribeSubAnims(obj, 0, subAnimDepth);
        }

        return result.dump();
    });
}
