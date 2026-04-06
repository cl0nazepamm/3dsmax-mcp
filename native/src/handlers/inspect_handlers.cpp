#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/bridge_gup.h"

#include <iparamb2.h>
#include <decomp.h>
#include <triobj.h>
#include <algorithm>
#include <set>
#include <iInstanceMgr.h>
#include <set>
#include <map>

using json = nlohmann::json;
using namespace HandlerHelpers;

// ── Helper: read PB2 param value as string ──────────────────
static std::string ReadPB2Value(IParamBlock2* pb, ParamID pid, ParamType2 ptype, TimeValue t) {
    int baseType = ptype & ~TYPE_TAB;
    try {
        switch (baseType) {
        case TYPE_FLOAT:
        case TYPE_ANGLE:
        case TYPE_PCNT_FRAC:
        case TYPE_WORLD:
        case TYPE_COLOR_CHANNEL: {
            float f = 0;
            pb->GetValue(pid, t, f, FOREVER);
            return std::to_string(f);
        }
        case TYPE_INT:
        case TYPE_BOOL:
        case TYPE_TIMEVALUE:
        case TYPE_RADIOBTN_INDEX:
        case TYPE_INDEX: {
            int i = 0;
            pb->GetValue(pid, t, i, FOREVER);
            return std::to_string(i);
        }
        case TYPE_POINT3:
        case TYPE_RGBA: {
            Point3 pt(0, 0, 0);
            pb->GetValue(pid, t, pt, FOREVER);
            return "[" + std::to_string(pt.x) + "," + std::to_string(pt.y) + "," + std::to_string(pt.z) + "]";
        }
        case TYPE_STRING:
        case TYPE_FILENAME: {
            const MCHAR* s = nullptr;
            pb->GetValue(pid, t, s, FOREVER);
            return s ? WideToUtf8(s) : "";
        }
        case TYPE_POINT4:
        case TYPE_FRGBA: {
            Point4 p4(0, 0, 0, 0);
            pb->GetValue(pid, t, p4, FOREVER);
            return "[" + std::to_string(p4.x) + "," + std::to_string(p4.y) + "," + std::to_string(p4.z) + "," + std::to_string(p4.w) + "]";
        }
        default:
            return "<unsupported>";
        }
    }
    catch (...) {
        return "<error>";
    }
}

// ── Helper: PB2 type name ───────────────────────────────────
static std::string PB2TypeName(ParamType2 ptype) {
    int baseType = ptype & ~TYPE_TAB;
    switch (baseType) {
    case TYPE_FLOAT: return "float";
    case TYPE_ANGLE: return "angle";
    case TYPE_PCNT_FRAC: return "percent";
    case TYPE_WORLD: return "worldUnits";
    case TYPE_COLOR_CHANNEL: return "colorChannel";
    case TYPE_INT: return "integer";
    case TYPE_BOOL: return "boolean";
    case TYPE_TIMEVALUE: return "time";
    case TYPE_RADIOBTN_INDEX: return "radioIndex";
    case TYPE_INDEX: return "index";
    case TYPE_POINT3: return "point3";
    case TYPE_RGBA: return "color";
    case TYPE_STRING: return "string";
    case TYPE_FILENAME: return "filename";
    case TYPE_POINT4: return "point4";
    case TYPE_FRGBA: return "frgba";
    case TYPE_INODE: return "node";
    case TYPE_MTL: return "material";
    case TYPE_TEXMAP: return "texturemap";
    case TYPE_REFTARG: return "refTarget";
    case TYPE_MATRIX3: return "matrix3";
    default: return "other(" + std::to_string(baseType) + ")";
    }
}

// ── Helper: enumerate PB2 properties on an Animatable ───────
static json EnumPB2Properties(Animatable* anim, TimeValue t) {
    json props = json::array();
    if (!anim) return props;

    int numPB = anim->NumParamBlocks();
    for (int pb_idx = 0; pb_idx < numPB; pb_idx++) {
        IParamBlock2* pb = anim->GetParamBlock(pb_idx);
        if (!pb) continue;
        ParamBlockDesc2* desc = pb->GetDesc();
        if (!desc) continue;

        for (int p = 0; p < desc->count; p++) {
            ParamID pid = desc->IndextoID(p);
            const ParamDef& pd = desc->GetParamDef(pid);

            json prop;
            prop["name"] = pd.int_name ? WideToUtf8(pd.int_name) : ("param_" + std::to_string(pid));
            prop["declaredType"] = PB2TypeName(pd.type);

            // Skip tab/array types and reference types for value read
            int baseType = pd.type & ~TYPE_TAB;
            if (pd.type & TYPE_TAB) {
                prop["value"] = "<array>";
                prop["runtimeType"] = "Tab";
            }
            else if (baseType == TYPE_INODE || baseType == TYPE_MTL ||
                     baseType == TYPE_TEXMAP || baseType == TYPE_REFTARG ||
                     baseType == TYPE_MATRIX3) {
                // For reference types, just indicate presence
                prop["value"] = "<ref>";
                prop["runtimeType"] = PB2TypeName(pd.type);
            }
            else {
                std::string val = ReadPB2Value(pb, pid, pd.type, t);
                prop["value"] = val;
                prop["runtimeType"] = PB2TypeName(pd.type);
            }

            props.push_back(prop);
        }
    }
    return props;
}

// ── Helper: superclass name from SClass_ID ──────────────────
static std::string SuperClassName(SClass_ID scid) {
    if (scid == GEOMOBJECT_CLASS_ID) return "GeometryClass";
    if (scid == CAMERA_CLASS_ID) return "camera";
    if (scid == LIGHT_CLASS_ID) return "light";
    if (scid == SHAPE_CLASS_ID) return "shape";
    if (scid == HELPER_CLASS_ID) return "helper";
    if (scid == SYSTEM_CLASS_ID) return "system";
    if (scid == MATERIAL_CLASS_ID) return "material";
    if (scid == TEXMAP_CLASS_ID) return "textureMap";
    return "Unknown";
}

// ── native:inspect_object ───────────────────────────────────
std::string NativeHandlers::InspectObject(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string name = p.value("name", "");
        if (name.empty()) throw std::runtime_error("name is required");

        INode* node = FindNodeByName(name);
        if (!node) throw std::runtime_error("Object not found: " + name);

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        json result;
        result["name"] = WideToUtf8(node->GetName());

        // Class info
        ObjectState os = node->EvalWorldState(t);
        result["class"] = os.obj ? WideToUtf8(os.obj->ClassName().data()) : "Unknown";
        result["superclass"] = os.obj ? SuperClassName(os.obj->SuperClassID()) : "Unknown";

        // Base object class (under modifiers)
        Object* baseObj = node->GetObjectRef();
        while (baseObj && baseObj->SuperClassID() == GEN_DERIVOB_CLASS_ID) {
            baseObj = ((IDerivedObject*)baseObj)->GetObjRef();
        }
        result["baseObject"] = baseObj ? WideToUtf8(baseObj->ClassName().data()) : "Unknown";

        // Transform
        Matrix3 tm = node->GetNodeTM(t);
        Point3 pos = tm.GetTrans();
        result["position"] = json::array({pos.x, pos.y, pos.z});

        AffineParts ap;
        decomp_affine(tm, &ap);
        float euler[3];
        QuatToEuler(ap.q, euler, EULERTYPE_XYZ);
        result["rotation"] = json::array({RadToDeg(euler[0]), RadToDeg(euler[1]), RadToDeg(euler[2])});
        result["scale"] = json::array({ap.k.x, ap.k.y, ap.k.z});

        // Hierarchy
        INode* parent = node->GetParentNode();
        result["parent"] = (parent && !parent->IsRootNode()) ? json(WideToUtf8(parent->GetName())) : json(nullptr);

        json children = json::array();
        for (int i = 0; i < node->NumberOfChildren(); i++) {
            children.push_back(WideToUtf8(node->GetChildNode(i)->GetName()));
        }
        result["children"] = children;

        // Visibility & render flags
        result["isHidden"] = node->IsHidden() ? true : false;
        result["isFrozen"] = node->IsFrozen() ? true : false;
        result["renderable"] = node->Renderable() ? true : false;
        result["primaryVisibility"] = node->GetPrimaryVisibility() ? true : false;
        result["secondaryVisibility"] = node->GetSecondaryVisibility() ? true : false;
        result["castShadows"] = node->CastShadows() ? true : false;
        result["receiveShadows"] = node->RcvShadows() ? true : false;

        // Layer
        result["layer"] = NodeLayerName(node);

        // Wire color
        result["wirecolor"] = NodeWireColor(node);

        // Instance detection
        INodeTab instTab;
        int instCount = IInstanceMgr::GetInstanceMgr()->GetInstances(*node, instTab);
        result["isInstanced"] = instCount > 1;
        result["instanceCount"] = instCount;

        // Mesh stats
        result["numVerts"] = nullptr;
        result["numFaces"] = nullptr;
        if (os.obj && os.obj->CanConvertToType(triObjectClassID)) {
            TriObject* tri = (TriObject*)os.obj->ConvertToType(t, triObjectClassID);
            if (tri) {
                Mesh& mesh = tri->GetMesh();
                result["numVerts"] = mesh.getNumVerts();
                result["numFaces"] = mesh.getNumFaces();
                if (tri != os.obj) tri->MaybeAutoDelete();
            }
        }

        // Bounding box
        if (os.obj) {
            Box3 bbox;
            os.obj->GetDeformBBox(t, bbox);
            Matrix3 nodeTM = node->GetNodeTM(t);
            Point3 corners[8];
            corners[0] = Point3(bbox.Min().x, bbox.Min().y, bbox.Min().z) * nodeTM;
            corners[1] = Point3(bbox.Max().x, bbox.Min().y, bbox.Min().z) * nodeTM;
            corners[2] = Point3(bbox.Min().x, bbox.Max().y, bbox.Min().z) * nodeTM;
            corners[3] = Point3(bbox.Max().x, bbox.Max().y, bbox.Min().z) * nodeTM;
            corners[4] = Point3(bbox.Min().x, bbox.Min().y, bbox.Max().z) * nodeTM;
            corners[5] = Point3(bbox.Max().x, bbox.Min().y, bbox.Max().z) * nodeTM;
            corners[6] = Point3(bbox.Min().x, bbox.Max().y, bbox.Max().z) * nodeTM;
            corners[7] = Point3(bbox.Max().x, bbox.Max().y, bbox.Max().z) * nodeTM;
            Point3 wMin = corners[0], wMax = corners[0];
            for (int c = 1; c < 8; c++) {
                if (corners[c].x < wMin.x) wMin.x = corners[c].x;
                if (corners[c].y < wMin.y) wMin.y = corners[c].y;
                if (corners[c].z < wMin.z) wMin.z = corners[c].z;
                if (corners[c].x > wMax.x) wMax.x = corners[c].x;
                if (corners[c].y > wMax.y) wMax.y = corners[c].y;
                if (corners[c].z > wMax.z) wMax.z = corners[c].z;
            }
            Point3 dims = wMax - wMin;
            json bb;
            bb["min"] = json::array({wMin.x, wMin.y, wMin.z});
            bb["max"] = json::array({wMax.x, wMax.y, wMax.z});
            bb["dimensions"] = json::array({dims.x, dims.y, dims.z});
            result["boundingBox"] = bb;
        }

        // Modifiers with enable state
        json mods = json::array();
        Object* objRef = node->GetObjectRef();
        if (objRef && objRef->SuperClassID() == GEN_DERIVOB_CLASS_ID) {
            IDerivedObject* dobj = (IDerivedObject*)objRef;
            for (int m = 0; m < dobj->NumModifiers(); m++) {
                Modifier* mod = dobj->GetModifier(m);
                if (!mod) continue;
                json modJ;
                modJ["name"] = WideToUtf8(mod->GetName(false).data());
                modJ["class"] = WideToUtf8(mod->ClassName().data());
                modJ["enabled"] = mod->IsEnabled() ? true : false;
                modJ["enabledInViews"] = mod->IsEnabledInViews() ? true : false;
                modJ["enabledInRenders"] = mod->IsEnabledInRender() ? true : false;
                mods.push_back(modJ);
            }
        }
        result["modifiers"] = mods;

        // Material
        Mtl* mtl = node->GetMtl();
        if (mtl) {
            json matJ;
            matJ["name"] = WideToUtf8(mtl->GetName().data());
            matJ["class"] = WideToUtf8(mtl->ClassName().data());
            result["material"] = matJ;
        }
        else {
            result["material"] = nullptr;
        }

        return result.dump();
    });
}

// ── native:inspect_properties ───────────────────────────────
std::string NativeHandlers::InspectProperties(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string name = p.value("name", "");
        std::string target = p.value("target", "object");
        int modIndex = p.value("modifier_index", 0);

        if (name.empty()) throw std::runtime_error("name is required");

        INode* node = FindNodeByName(name);
        if (!node) throw std::runtime_error("Object not found: " + name);

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        json result;
        result["target"] = target;

        Animatable* tgt = nullptr;
        std::string className;

        if (target == "baseobject") {
            Object* baseObj = node->GetObjectRef();
            while (baseObj && baseObj->SuperClassID() == GEN_DERIVOB_CLASS_ID) {
                baseObj = ((IDerivedObject*)baseObj)->GetObjRef();
            }
            tgt = baseObj;
            className = baseObj ? WideToUtf8(baseObj->ClassName().data()) : "Unknown";
        }
        else if (target == "modifier") {
            if (modIndex <= 0) throw std::runtime_error("modifier_index (1-based) is required for target=modifier");
            Object* objRef = node->GetObjectRef();
            if (!objRef || objRef->SuperClassID() != GEN_DERIVOB_CLASS_ID) {
                throw std::runtime_error("No modifiers on " + name);
            }
            IDerivedObject* dobj = (IDerivedObject*)objRef;
            int idx = modIndex - 1;
            if (idx >= dobj->NumModifiers()) {
                throw std::runtime_error("Modifier index " + std::to_string(modIndex) + " out of range");
            }
            Modifier* mod = dobj->GetModifier(idx);
            tgt = mod;
            className = mod ? WideToUtf8(mod->ClassName().data()) : "Unknown";
        }
        else if (target == "material") {
            Mtl* mtl = node->GetMtl();
            if (!mtl) throw std::runtime_error("No material on " + name);
            tgt = mtl;
            className = WideToUtf8(mtl->ClassName().data());
        }
        else {
            // "object" — enumerate node-level properties via base object PB2
            // Plus some known node properties
            Object* baseObj = node->GetObjectRef();
            while (baseObj && baseObj->SuperClassID() == GEN_DERIVOB_CLASS_ID) {
                baseObj = ((IDerivedObject*)baseObj)->GetObjRef();
            }
            tgt = baseObj;

            ObjectState os = node->EvalWorldState(t);
            className = os.obj ? WideToUtf8(os.obj->ClassName().data()) : "Unknown";
        }

        result["class"] = className;

        if (!tgt) {
            result["propertyCount"] = 0;
            result["properties"] = json::array();
            return result.dump();
        }

        json props = EnumPB2Properties(tgt, t);
        result["propertyCount"] = props.size();
        result["properties"] = props;

        return result.dump();
    });
}

// ── native:get_materials ────────────────────────────────────
std::string NativeHandlers::GetMaterials(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        // Collect materials from scene nodes (assigned materials)
        INode* root = ip->GetRootNode();
        std::vector<INode*> allNodes;
        CollectNodes(root, allNodes);

        // Use a set to deduplicate by material pointer
        std::set<Mtl*> seenMtls;
        json materials = json::array();

        for (INode* node : allNodes) {
            Mtl* mtl = node->GetMtl();
            if (!mtl) continue;
            if (seenMtls.count(mtl)) continue;
            seenMtls.insert(mtl);

            json matJ;
            matJ["name"] = WideToUtf8(mtl->GetName().data());
            matJ["class"] = WideToUtf8(mtl->ClassName().data());
            matJ["subMtlCount"] = mtl->NumSubMtls();

            // Collect which nodes use this material
            json users = json::array();
            for (INode* n2 : allNodes) {
                if (n2->GetMtl() == mtl) {
                    users.push_back(WideToUtf8(n2->GetName()));
                }
            }
            matJ["usedBy"] = users;
            materials.push_back(matJ);
        }

        json result;
        result["count"] = materials.size();
        result["materials"] = materials;
        return result.dump();
    });
}

// ── native:find_objects_by_property ──────────────────────────
std::string NativeHandlers::FindObjectsByProperty(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string propName = p.value("property_name", "");
        std::string propValue = p.value("property_value", "");
        std::string classFilter = p.value("class_filter", "");

        if (propName.empty()) throw std::runtime_error("property_name is required");

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        INode* root = ip->GetRootNode();
        std::vector<INode*> allNodes;
        CollectNodes(root, allNodes);

        // Convert prop name to lowercase for matching
        std::string lprop = propName;
        std::transform(lprop.begin(), lprop.end(), lprop.begin(), ::tolower);

        json matches = json::array();

        for (INode* node : allNodes) {
            // Class filter
            if (!classFilter.empty()) {
                std::string cls = NodeClassName(node);
                if (!WildcardMatch(cls, "*" + classFilter + "*")) continue;
            }

            std::string foundValue;
            bool found = false;

            // Check known node-level properties first
            if (lprop == "renderable") {
                foundValue = node->Renderable() ? "true" : "false";
                found = true;
            }
            else if (lprop == "ishidden" || lprop == "hidden") {
                foundValue = node->IsHidden() ? "true" : "false";
                found = true;
            }
            else if (lprop == "isfrozen" || lprop == "frozen") {
                foundValue = node->IsFrozen() ? "true" : "false";
                found = true;
            }
            else if (lprop == "primaryvisibility") {
                foundValue = node->GetPrimaryVisibility() ? "true" : "false";
                found = true;
            }
            else if (lprop == "secondaryvisibility") {
                foundValue = node->GetSecondaryVisibility() ? "true" : "false";
                found = true;
            }
            else if (lprop == "castshadows") {
                foundValue = node->CastShadows() ? "true" : "false";
                found = true;
            }
            else if (lprop == "receiveshadows") {
                foundValue = node->RcvShadows() ? "true" : "false";
                found = true;
            }
            else if (lprop == "material") {
                Mtl* mtl = node->GetMtl();
                foundValue = mtl ? WideToUtf8(mtl->GetName().data()) : "undefined";
                found = true;
            }

            // Check base object PB2 properties
            if (!found) {
                Object* baseObj = node->GetObjectRef();
                while (baseObj && baseObj->SuperClassID() == GEN_DERIVOB_CLASS_ID) {
                    baseObj = ((IDerivedObject*)baseObj)->GetObjRef();
                }
                if (baseObj) {
                    std::wstring wparam = Utf8ToWide(propName);
                    int numPB = baseObj->NumParamBlocks();
                    for (int pb_idx = 0; pb_idx < numPB && !found; pb_idx++) {
                        IParamBlock2* pb = baseObj->GetParamBlock(pb_idx);
                        if (!pb) continue;
                        ParamBlockDesc2* desc = pb->GetDesc();
                        if (!desc) continue;
                        for (int pi = 0; pi < desc->count; pi++) {
                            ParamID pid = desc->IndextoID(pi);
                            const ParamDef& pd = desc->GetParamDef(pid);
                            if (pd.int_name && _wcsicmp(pd.int_name, wparam.c_str()) == 0) {
                                foundValue = ReadPB2Value(pb, pid, pd.type, t);
                                found = true;
                                break;
                            }
                        }
                    }
                }
            }

            if (!found) continue;

            // Value filter
            if (!propValue.empty()) {
                std::string lFound = foundValue, lTarget = propValue;
                std::transform(lFound.begin(), lFound.end(), lFound.begin(), ::tolower);
                std::transform(lTarget.begin(), lTarget.end(), lTarget.begin(), ::tolower);
                if (lFound != lTarget) continue;
            }

            json match;
            match["name"] = WideToUtf8(node->GetName());
            match["value"] = foundValue;
            matches.push_back(match);
        }

        return matches.dump();
    });
}

// ── native:get_instances ────────────────────────────────────
std::string NativeHandlers::GetInstances(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string name = p.value("name", "");
        if (name.empty()) throw std::runtime_error("name is required");

        INode* node = FindNodeByName(name);
        if (!node) throw std::runtime_error("Object not found: " + name);

        INodeTab instTab;
        int instCount = IInstanceMgr::GetInstanceMgr()->GetInstances(*node, instTab);

        json result;
        result["name"] = WideToUtf8(node->GetName());
        result["isInstanced"] = instCount > 1;
        result["instanceCount"] = instCount;

        json instances = json::array();
        for (int i = 0; i < instTab.Count(); i++) {
            instances.push_back(WideToUtf8(instTab[i]->GetName()));
        }
        result["instances"] = instances;

        return result.dump();
    });
}

// ── native:get_dependencies ─────────────────────────────────
std::string NativeHandlers::GetDependencies(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string name = p.value("name", "");
        std::string direction = p.value("direction", "dependents");
        if (name.empty()) throw std::runtime_error("name is required");
        std::transform(direction.begin(), direction.end(), direction.begin(), ::tolower);

        INode* node = FindNodeByName(name);
        if (!node) throw std::runtime_error("Object not found: " + name);

        json result;
        result["object"] = WideToUtf8(node->GetName());
        result["direction"] = direction;

        if (direction == "dependents") {
            // Enumerate all reference dependents.
            class DepEnumProc : public DependentEnumProc {
            public:
                std::map<std::string, int> classCounts;
                int total = 0;
                int proc(ReferenceMaker* rmaker) override {
                    if (rmaker) {
                        std::string cn = WideToUtf8(rmaker->ClassName().data());
                        classCounts[cn]++;
                        total++;
                    }
                    return DEP_ENUM_CONTINUE;
                }
            };

            DepEnumProc depProc;
            node->DoEnumDependents(&depProc);

            result["totalDependents"] = depProc.total;
            json byClass;
            for (auto& [cls, count] : depProc.classCounts) {
                byClass[cls] = count;
            }
            result["byClass"] = byClass;
            return result.dump();
        }

        if (direction == "dependentnodes") {
            // Enumerate only node dependents.
            class NodeDepEnumProc : public DependentEnumProc {
            public:
                std::map<std::string, int> classCounts;
                std::set<std::string> nodeNames;
                int total = 0;
                int proc(ReferenceMaker* rmaker) override {
                    if (!rmaker) {
                        return DEP_ENUM_CONTINUE;
                    }
                    if (rmaker->SuperClassID() != BASENODE_CLASS_ID) {
                        return DEP_ENUM_CONTINUE;
                    }
                    INode* depNode = static_cast<INode*>(rmaker);
                    std::string nodeName = WideToUtf8(depNode->GetName());
                    if (nodeNames.insert(nodeName).second) {
                        classCounts[NodeClassName(depNode)]++;
                        total++;
                    }
                    return DEP_ENUM_CONTINUE;
                }
            };

            NodeDepEnumProc nodeProc;
            node->DoEnumDependents(&nodeProc);

            result["totalDependents"] = nodeProc.total;
            json byClass;
            for (auto& [cls, count] : nodeProc.classCounts) {
                byClass[cls] = count;
            }
            result["byClass"] = byClass;

            json nodes = json::array();
            for (const auto& n : nodeProc.nodeNames) {
                nodes.push_back(n);
            }
            result["nodes"] = nodes;
            return result.dump();
        }

        throw std::runtime_error("direction must be 'dependents' or 'dependentnodes'");
    });
}

// ── Helper: classify PB2 param type into slot category ──────
static std::string ClassifyParam(ParamType2 ptype, const std::string& paramName) {
    int baseType = ptype & ~TYPE_TAB;

    // Explicit type-based classification
    if (baseType == TYPE_TEXMAP) return "map";
    if (baseType == TYPE_RGBA || baseType == TYPE_FRGBA) return "color";
    if (baseType == TYPE_BOOL) return "bool";
    if (baseType == TYPE_FLOAT || baseType == TYPE_INT ||
        baseType == TYPE_ANGLE || baseType == TYPE_PCNT_FRAC ||
        baseType == TYPE_WORLD || baseType == TYPE_COLOR_CHANNEL ||
        baseType == TYPE_TIMEVALUE || baseType == TYPE_RADIOBTN_INDEX ||
        baseType == TYPE_INDEX) return "numeric";

    // For reference types, check if the name suggests a map slot
    if (baseType == TYPE_REFTARG || baseType == TYPE_MTL || baseType == TYPE_INODE) {
        std::string lower = paramName;
        std::transform(lower.begin(), lower.end(), lower.begin(), ::tolower);
        if (lower.find("_map") != std::string::npos ||
            lower.find("_shader") != std::string::npos ||
            lower.find("_tex") != std::string::npos ||
            lower.find("texture") != std::string::npos) return "map";
        if (baseType == TYPE_MTL) return "other";
    }

    // Point3 — could be color or other
    if (baseType == TYPE_POINT3) {
        std::string lower = paramName;
        std::transform(lower.begin(), lower.end(), lower.begin(), ::tolower);
        if (lower.find("color") != std::string::npos ||
            lower.find("colour") != std::string::npos) return "color";
        return "numeric";
    }

    return "other";
}

// ── Helper: read a material PB2 param value as display string ──
static std::string ReadMtlParamValue(IParamBlock2* pb, ParamID pid, ParamType2 ptype, TimeValue t) {
    int baseType = ptype & ~TYPE_TAB;
    try {
        switch (baseType) {
        case TYPE_FLOAT:
        case TYPE_ANGLE:
        case TYPE_PCNT_FRAC:
        case TYPE_WORLD:
        case TYPE_COLOR_CHANNEL: {
            float f = 0;
            pb->GetValue(pid, t, f, FOREVER);
            char buf[64];
            snprintf(buf, sizeof(buf), "%.6g", f);
            return buf;
        }
        case TYPE_INT:
        case TYPE_TIMEVALUE:
        case TYPE_RADIOBTN_INDEX:
        case TYPE_INDEX: {
            int i = 0;
            pb->GetValue(pid, t, i, FOREVER);
            return std::to_string(i);
        }
        case TYPE_BOOL: {
            int i = 0;
            pb->GetValue(pid, t, i, FOREVER);
            return i ? "true" : "false";
        }
        case TYPE_RGBA:
        case TYPE_FRGBA: {
            if (baseType == TYPE_FRGBA) {
                Point4 p4(0, 0, 0, 0);
                pb->GetValue(pid, t, p4, FOREVER);
                char buf[128];
                snprintf(buf, sizeof(buf), "(color %.6g %.6g %.6g)", p4.x * 255.0f, p4.y * 255.0f, p4.z * 255.0f);
                return buf;
            }
            Point3 pt(0, 0, 0);
            pb->GetValue(pid, t, pt, FOREVER);
            char buf[128];
            snprintf(buf, sizeof(buf), "(color %g %g %g)", pt.x, pt.y, pt.z);
            return buf;
        }
        case TYPE_POINT3: {
            Point3 pt(0, 0, 0);
            pb->GetValue(pid, t, pt, FOREVER);
            char buf[128];
            snprintf(buf, sizeof(buf), "(color %g %g %g)", pt.x, pt.y, pt.z);
            return buf;
        }
        case TYPE_TEXMAP: {
            Texmap* tex = nullptr;
            pb->GetValue(pid, t, tex, FOREVER);
            if (!tex) return "undefined";
            return WideToUtf8(tex->GetName().data()) + ":" + WideToUtf8(tex->ClassName().data());
        }
        case TYPE_MTL: {
            Mtl* m = nullptr;
            pb->GetValue(pid, t, m, FOREVER);
            if (!m) return "undefined";
            return WideToUtf8(m->GetName().data()) + ":" + WideToUtf8(m->ClassName().data());
        }
        case TYPE_STRING:
        case TYPE_FILENAME: {
            const MCHAR* s = nullptr;
            pb->GetValue(pid, t, s, FOREVER);
            return s ? WideToUtf8(s) : "";
        }
        case TYPE_REFTARG: {
            ReferenceTarget* ref = nullptr;
            pb->GetValue(pid, t, ref, FOREVER);
            if (!ref) return "undefined";
            return WideToUtf8(ref->ClassName().data());
        }
        default:
            return "<unsupported>";
        }
    }
    catch (...) {
        return "<error>";
    }
}

// ── native:write_osl_shader ─────────────────────────────────
std::string NativeHandlers::WriteOSLShader(const std::string& params, MCPBridgeGUP* gup) {
    json p = json::parse(params, nullptr, false);
    std::string shaderName = p.value("shader_name", "");
    std::string oslCode = p.value("osl_code", "");
    std::string globalVar = p.value("global_var", "");
    auto properties = p.value("properties", std::map<std::string, std::string>{});

    if (shaderName.empty()) throw std::runtime_error("shader_name is required");
    if (oslCode.empty()) throw std::runtime_error("osl_code is required");

    if (globalVar.empty()) {
        globalVar = shaderName;
        for (auto& c : globalVar) {
            if (!isalnum(c) && c != '_') c = '_';
        }
        if (!globalVar.empty() && isdigit(globalVar[0])) globalVar = "m_" + globalVar;
    }

    return gup->GetExecutor().ExecuteSync([&]() -> std::string {
        // Step 1: Write .osl file — pure C++ file I/O
        wchar_t tempBuf[MAX_PATH];
        GetTempPathW(MAX_PATH, tempBuf);
        std::wstring oslDir = std::wstring(tempBuf) + L"osl_shaders\\";
        CreateDirectoryW(oslDir.c_str(), NULL);

        std::wstring oslPath = oslDir + Utf8ToWide(shaderName) + L".osl";
        {
            FILE* f = _wfopen(oslPath.c_str(), L"wb");
            if (!f) throw std::runtime_error("Failed to create OSL file");
            fwrite(oslCode.c_str(), 1, oslCode.size(), f);
            fclose(f);
        }
        std::string oslPathUtf8 = WideToUtf8(oslPath.c_str());

        // Step 2: Create OSLMap with inline code via RunMAXScript
        // OSLMap only compiles from inline string literals — file-read doesn't trigger compilation
        // Escape for MAXScript string: \ -> \\, " -> \", newlines -> \n
        std::string msEscaped;
        msEscaped.reserve(oslCode.size() + 64);
        for (char c : oslCode) {
            switch (c) {
                case '\\': msEscaped += "\\\\"; break;
                case '"':  msEscaped += "\\\""; break;
                case '\n': msEscaped += "\\n"; break;
                case '\r': break; // skip CR
                default:   msEscaped += c; break;
            }
        }
        std::string escapedPath = JsonEscape(oslPathUtf8);
        std::string createScript =
            "(\r\n"
            "global " + globalVar + " = OSLMap name:\"" + JsonEscape(shaderName) + "\"\r\n" +
            globalVar + ".OSLCode = \"" + msEscaped + "\"\r\n" +
            globalVar + ".OSLAutoUpdate = true\r\n" +
            globalVar + ".OSLPath = @\"" + escapedPath + "\"\r\n"
            "\"ok\"\r\n"
            ")";
        RunMAXScript(createScript);

        // Step 2b: Verify compilation succeeded
        std::string verifyScript = globalVar + ".OSLShaderName";
        std::string compiledName = RunMAXScript(verifyScript);
        bool compiled = (compiledName != "Example" && !compiledName.empty());

        // Step 3: Set custom properties (OSLMap lowercases param names)
        json okList = json::array();
        json errList = json::array();
        for (auto& [propName, propVal] : properties) {
            std::string lower = propName;
            std::transform(lower.begin(), lower.end(), lower.begin(), ::tolower);
            // Try lowercase first (OSLMap convention), then original
            std::string propScript =
                "try (" + globalVar + "." + lower + " = " + propVal + "; \"ok\") catch ("
                "try (" + globalVar + "." + propName + " = " + propVal + "; \"ok\") catch ("
                "\"fail\"))";
            std::string result = RunMAXScript(propScript);
            if (result == "ok") {
                okList.push_back(propName);
            } else {
                errList.push_back(propName + ": param not found after recompilation");
            }
        }

        // Build result
        json result;
        result["file"] = oslPathUtf8;
        result["globalVar"] = globalVar;
        result["shaderName"] = shaderName;
        result["compiled"] = compiled;
        result["compiledAs"] = compiledName;
        if (!okList.empty()) result["set"] = okList;
        if (!errList.empty()) result["errors"] = errList;

        if (!compiled) {
            result["message"] = "WARNING: OSL shader failed to compile (still 'Example'). "
                "Check: shader function name must match shader_name='" + shaderName + "', "
                "verify OSL syntax is valid. File written to " + oslPathUtf8;
        } else {
            result["message"] = "OSL shader '" + compiledName + "' compiled OK | Global: " + globalVar;
            if (!okList.empty()) {
                result["message"] = result["message"].get<std::string>() +
                    " | Set " + std::to_string(okList.size()) + " properties";
            }
        }
        return result.dump();
    });
}

// ── native:get_material_slots ───────────────────────────────
std::string NativeHandlers::GetMaterialSlots(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string name = p.value("name", "");
        int subMatIndex = p.value("sub_material_index", 0);
        bool includeValues = p.value("include_values", false);
        int maxSlots = p.value("max_slots", 60);
        std::string slotScope = p.value("slot_scope", "map");
        int maxPerGroup = p.value("max_per_group", 20);

        if (name.empty()) throw std::runtime_error("name is required");

        INode* node = FindNodeByName(name);
        if (!node) throw std::runtime_error("Object not found: " + name);

        Mtl* mtl = node->GetMtl();
        if (!mtl) throw std::runtime_error("No material assigned to " + name);

        // Navigate to sub-material if requested
        Mtl* targetMtl = mtl;
        if (subMatIndex > 0) {
            int idx = subMatIndex - 1;
            if (idx >= mtl->NumSubMtls()) {
                throw std::runtime_error("Sub-material index " + std::to_string(subMatIndex) +
                    " out of range (has " + std::to_string(mtl->NumSubMtls()) + ")");
            }
            targetMtl = mtl->GetSubMtl(idx);
            if (!targetMtl) {
                throw std::runtime_error("Sub-material at index " + std::to_string(subMatIndex) + " is null");
            }
        }

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        // Categorized slots
        json mapSlots = json::array();
        json colorSlots = json::array();
        json numericSlots = json::array();
        json boolSlots = json::array();
        json otherSlots = json::array();

        int scanned = 0;

        // Enumerate all PB2 parameters on the target material
        int numPB = targetMtl->NumParamBlocks();
        for (int pb_idx = 0; pb_idx < numPB && scanned < maxSlots; pb_idx++) {
            IParamBlock2* pb = targetMtl->GetParamBlock(pb_idx);
            if (!pb) continue;
            ParamBlockDesc2* desc = pb->GetDesc();
            if (!desc) continue;

            for (int pi = 0; pi < desc->count && scanned < maxSlots; pi++) {
                ParamID pid = desc->IndextoID(pi);
                const ParamDef& pd = desc->GetParamDef(pid);

                // Skip tab/array params
                if (pd.type & TYPE_TAB) continue;

                std::string paramName = pd.int_name ? WideToUtf8(pd.int_name) : ("param_" + std::to_string(pid));
                std::string category = ClassifyParam(pd.type, paramName);

                // Build slot entry
                json slot;
                slot["name"] = paramName;
                if (includeValues) {
                    slot["value"] = ReadMtlParamValue(pb, pid, pd.type, t);
                }

                // Route to the right category array
                if (category == "map") mapSlots.push_back(slot);
                else if (category == "color") colorSlots.push_back(slot);
                else if (category == "numeric") numericSlots.push_back(slot);
                else if (category == "bool") boolSlots.push_back(slot);
                else otherSlots.push_back(slot);

                scanned++;
            }
        }

        // Build result
        json result;
        result["name"] = WideToUtf8(targetMtl->GetName().data());
        result["class"] = WideToUtf8(targetMtl->ClassName().data());
        result["subMaterialIndex"] = subMatIndex;
        result["inspectedCount"] = scanned;

        json counts;
        counts["map"] = mapSlots.size();
        counts["color"] = colorSlots.size();
        counts["numeric"] = numericSlots.size();
        counts["bool"] = boolSlots.size();
        counts["other"] = otherSlots.size();
        result["counts"] = counts;

        // Apply scope and truncation
        json truncated;
        auto truncate = [&](json& arr, const std::string& key, json& out) {
            if ((int)arr.size() > maxPerGroup) {
                truncated[key] = arr.size();
                json trunc = json::array();
                for (int i = 0; i < maxPerGroup; i++) trunc.push_back(arr[i]);
                out[key] = trunc;
            } else {
                out[key] = arr;
            }
        };

        if (slotScope == "map" || slotScope == "all") {
            truncate(mapSlots, "mapSlots", result);
        }
        if (slotScope == "all") {
            truncate(colorSlots, "colorSlots", result);
            truncate(numericSlots, "numericSlots", result);
            truncate(boolSlots, "boolSlots", result);
            truncate(otherSlots, "otherSlots", result);
        }

        if (!truncated.empty()) {
            result["truncatedFrom"] = truncated;
        }

        return result.dump();
    });
}

// ── native:list_plugin_classes ──────────────────────────────
std::string NativeHandlers::ListPluginClasses(const std::string& params, MCPBridgeGUP* gup) {
    json p = json::parse(params, nullptr, false);
    std::string superclass = p.value("superclass", "material");

    std::string lower = superclass;
    std::transform(lower.begin(), lower.end(), lower.begin(), ::tolower);

    struct SCEntry { std::string name; SClass_ID id; };
    std::vector<SCEntry> scMap = {
        {"material",    MATERIAL_CLASS_ID},
        {"texturemap",  TEXMAP_CLASS_ID},
        {"modifier",    OSM_CLASS_ID},
        {"wsmodifier",  WSM_CLASS_ID},
        {"geometry",    GEOMOBJECT_CLASS_ID},
        {"shape",       SHAPE_CLASS_ID},
        {"camera",      CAMERA_CLASS_ID},
        {"light",       LIGHT_CLASS_ID},
        {"helper",      HELPER_CLASS_ID},
        {"system",      SYSTEM_CLASS_ID},
        {"atmospheric", ATMOSPHERIC_CLASS_ID},
        {"effect",      RENDER_EFFECT_CLASS_ID},
        {"renderer",    RENDERER_CLASS_ID},
    };

    bool enumAll = (lower == "all");

    std::vector<SClass_ID> targetSuperIDs;
    if (!enumAll) {
        for (auto& sc : scMap) {
            if (sc.name == lower) { targetSuperIDs.push_back(sc.id); break; }
        }
        if (targetSuperIDs.empty()) {
            return "{\"error\": \"Unknown superclass: " + superclass +
                   ". Use: material, texturemap, modifier, geometry, shape, "
                   "camera, light, helper, system, atmospheric, effect, renderer, or all\"}";
        }
    } else {
        for (auto& sc : scMap) targetSuperIDs.push_back(sc.id);
    }

    auto& dir = DllDir::GetInstance();
    int numDlls = dir.Count();

    std::map<std::string, json> byCategory;

    for (int d = 0; d < numDlls; d++) {
        const DllDesc& dll = dir[d];
        for (int c = 0; c < dll.NumberOfClasses(); c++) {
            ClassDesc* cd = dll[c];
            if (!cd) continue;
            SClass_ID scid = cd->SuperClassID();

            std::string catName;
            for (auto& sc : scMap) {
                if (sc.id == scid) { catName = sc.name; break; }
            }
            if (catName.empty()) continue;

            bool match = false;
            for (SClass_ID t : targetSuperIDs) { if (scid == t) { match = true; break; } }
            if (!match) continue;

            const MCHAR* cn = cd->ClassName();
            if (!cn || !*cn) continue;

            json entry;
            entry["name"] = WideToUtf8(cn);
            const MCHAR* intName = cd->InternalName();
            if (intName && *intName) {
                std::string iname = WideToUtf8(intName);
                if (iname != entry["name"].get<std::string>()) entry["internalName"] = iname;
            }
            byCategory[catName].push_back(entry);
        }
    }

    json result;
    result["superclass"] = superclass;
    if (enumAll) {
        for (auto& [cat, arr] : byCategory) {
            result[cat] = arr;
            result[cat + "Count"] = (int)arr.size();
        }
    } else {
        auto it = byCategory.find(lower);
        result["count"] = it != byCategory.end() ? (int)it->second.size() : 0;
        result["classes"] = it != byCategory.end() ? it->second : json::array();
    }
    return result.dump();
}
