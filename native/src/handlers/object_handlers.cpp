#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/bridge_gup.h"

#include <iparamb2.h>
#include <istdplug.h>
#include <decomp.h>
#include <set>
#include <unordered_map>

using json = nlohmann::json;
using namespace HandlerHelpers;

// ── native:get_object_properties ────────────────────────────────
std::string NativeHandlers::GetObjectProperties(const std::string& params, MCPBridgeGUP* gup) {
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
        // Get superclass name via MAXScript — no direct SDK method
        if (os.obj) {
            SClass_ID scid = os.obj->SuperClassID();
            if (scid == GEOMOBJECT_CLASS_ID) result["superclass"] = "GeometryClass";
            else if (scid == CAMERA_CLASS_ID) result["superclass"] = "camera";
            else if (scid == LIGHT_CLASS_ID) result["superclass"] = "light";
            else if (scid == SHAPE_CLASS_ID) result["superclass"] = "shape";
            else if (scid == HELPER_CLASS_ID) result["superclass"] = "helper";
            else if (scid == SYSTEM_CLASS_ID) result["superclass"] = "system";
            else result["superclass"] = "Unknown";
        } else {
            result["superclass"] = "Unknown";
        }

        // Transform
        Matrix3 tm = node->GetNodeTM(t);
        Point3 pos = tm.GetTrans();
        result["position"] = json::array({pos.x, pos.y, pos.z});

        // Rotation (as Euler angles)
        AffineParts ap;
        decomp_affine(tm, &ap);
        float euler[3];
        QuatToEuler(ap.q, euler, EULERTYPE_XYZ);
        result["rotation"] = json::array({RadToDeg(euler[0]), RadToDeg(euler[1]), RadToDeg(euler[2])});

        // Scale
        result["scale"] = json::array({ap.k.x, ap.k.y, ap.k.z});

        // Hierarchy
        INode* parent = node->GetParentNode();
        result["parent"] = (parent && !parent->IsRootNode()) ?
            json(WideToUtf8(parent->GetName())) : json(nullptr);

        json children = json::array();
        for (int i = 0; i < node->NumberOfChildren(); i++) {
            children.push_back(WideToUtf8(node->GetChildNode(i)->GetName()));
        }
        result["children"] = children;

        // Layer
        result["layer"] = NodeLayerName(node);

        // Wire color
        result["wirecolor"] = NodeWireColor(node);

        // Material
        Mtl* mtl = node->GetMtl();
        result["material"] = mtl ? WideToUtf8(mtl->GetName().data()) : "none";

        // Modifiers
        json mods = json::array();
        Object* objRef = node->GetObjectRef();
        if (objRef && objRef->SuperClassID() == GEN_DERIVOB_CLASS_ID) {
            IDerivedObject* dobj = (IDerivedObject*)objRef;
            for (int m = 0; m < dobj->NumModifiers(); m++) {
                Modifier* mod = dobj->GetModifier(m);
                if (mod) mods.push_back(WideToUtf8(mod->ClassName().data()));
            }
        }
        result["modifiers"] = mods;

        // Mesh stats via snapshot
        result["numVerts"] = nullptr;
        result["numFaces"] = nullptr;
        if (os.obj && os.obj->CanConvertToType(triObjectClassID)) {
            BOOL needDel = FALSE;
            TriObject* tri = (TriObject*)os.obj->ConvertToType(t, triObjectClassID);
            if (tri) {
                Mesh& mesh = tri->GetMesh();
                result["numVerts"] = mesh.getNumVerts();
                result["numFaces"] = mesh.getNumFaces();
                if (tri != os.obj) tri->MaybeAutoDelete();
            }
        }

        // Bounding box dimensions
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
            result["dimensions"] = json::array({dims.x, dims.y, dims.z});
        }

        return result.dump();
    });
}

// ── Parse a Point3 from string "[x,y,z]" or "x,y,z" ────────────
static bool ParsePoint3(const std::string& s, Point3& out) {
    std::string clean = s;
    clean.erase(std::remove(clean.begin(), clean.end(), '['), clean.end());
    clean.erase(std::remove(clean.begin(), clean.end(), ']'), clean.end());
    clean.erase(std::remove(clean.begin(), clean.end(), ' '), clean.end());
    return sscanf(clean.c_str(), "%f,%f,%f", &out.x, &out.y, &out.z) == 3;
}

// ── Parse a Color from string "(color r g b)" or "r,g,b" or named ──
static bool ParseColor(const std::string& s, DWORD& out) {
    // Named colors
    std::string lower = s;
    std::transform(lower.begin(), lower.end(), lower.begin(), ::tolower);
    if (lower == "red")     { out = RGB(255,0,0); return true; }
    if (lower == "green")   { out = RGB(0,255,0); return true; }
    if (lower == "blue")    { out = RGB(0,0,255); return true; }
    if (lower == "white")   { out = RGB(255,255,255); return true; }
    if (lower == "black")   { out = RGB(0,0,0); return true; }
    if (lower == "yellow")  { out = RGB(255,255,0); return true; }
    if (lower == "orange")  { out = RGB(255,165,0); return true; }
    if (lower == "gray" || lower == "grey") { out = RGB(128,128,128); return true; }

    // Parse "(color r g b)" format
    int r, g, b;
    if (sscanf(s.c_str(), "(color %d %d %d)", &r, &g, &b) == 3 ||
        sscanf(s.c_str(), "color %d %d %d", &r, &g, &b) == 3) {
        out = RGB(r, g, b);
        return true;
    }
    // Parse "[r,g,b]" or "r,g,b"
    std::string clean = s;
    clean.erase(std::remove(clean.begin(), clean.end(), '['), clean.end());
    clean.erase(std::remove(clean.begin(), clean.end(), ']'), clean.end());
    if (sscanf(clean.c_str(), "%d,%d,%d", &r, &g, &b) == 3) {
        out = RGB(r, g, b);
        return true;
    }
    return false;
}

// ── native:set_object_property (Pure SDK) ───────────────────────
std::string NativeHandlers::SetObjectProperty(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string name = p.value("name", "");
        std::string prop = p.value("property", "");
        std::string value = p.value("value", "");

        if (name.empty()) throw std::runtime_error("name is required");
        if (prop.empty()) throw std::runtime_error("property is required");

        INode* node = FindNodeByName(name);
        if (!node) throw std::runtime_error("Object not found: " + name);

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        // Convert prop to lowercase for comparison
        std::string lprop = prop;
        std::transform(lprop.begin(), lprop.end(), lprop.begin(), ::tolower);

        // ── Node-level properties (direct SDK) ──────────────────
        if (lprop == "pos" || lprop == "position") {
            Point3 pt;
            if (ParsePoint3(value, pt)) {
                Matrix3 tm = node->GetNodeTM(t);
                tm.SetTrans(pt);
                node->SetNodeTM(t, tm);
                ip->RedrawViews(t);
                return "Set pos on " + WideToUtf8(node->GetName());
            }
            throw std::runtime_error("Cannot parse Point3 from: " + value);
        }

        if (lprop == "wirecolor") {
            DWORD col;
            if (ParseColor(value, col)) {
                node->SetWireColor(col);
                ip->RedrawViews(t);
                return "Set wirecolor on " + WideToUtf8(node->GetName());
            }
            throw std::runtime_error("Cannot parse color from: " + value);
        }

        if (lprop == "name") {
            std::wstring wval = Utf8ToWide(value);
            // Strip quotes
            if (wval.size() >= 2 && wval.front() == L'"' && wval.back() == L'"')
                wval = wval.substr(1, wval.size() - 2);
            node->SetName(wval.c_str());
            return "Set name on " + WideToUtf8(node->GetName());
        }

        if (lprop == "ishidden" || lprop == "hidden") {
            bool v = (value == "true" || value == "1" || value == "on");
            node->Hide(v ? TRUE : FALSE);
            ip->RedrawViews(t);
            return "Set isHidden on " + WideToUtf8(node->GetName());
        }

        if (lprop == "isfrozen" || lprop == "frozen") {
            bool v = (value == "true" || value == "1" || value == "on");
            node->Freeze(v ? TRUE : FALSE);
            ip->RedrawViews(t);
            return "Set isFrozen on " + WideToUtf8(node->GetName());
        }

        if (lprop == "renderable") {
            bool v = (value == "true" || value == "1" || value == "on");
            node->SetRenderable(v ? TRUE : FALSE);
            return "Set renderable on " + WideToUtf8(node->GetName());
        }

        // ── Base object IParamBlock2 properties ─────────────────
        ObjectState os = node->EvalWorldState(t);
        Object* baseObj = node->GetObjectRef();
        // Walk past derived objects to get the base
        while (baseObj && baseObj->SuperClassID() == GEN_DERIVOB_CLASS_ID) {
            baseObj = ((IDerivedObject*)baseObj)->GetObjRef();
        }

        if (baseObj && SetParamByName(baseObj, prop, value, t)) {
            baseObj->NotifyDependents(FOREVER, PART_ALL, REFMSG_CHANGE);
            ip->RedrawViews(t);
            return "Set " + prop + " on " + WideToUtf8(node->GetName());
        }

        throw std::runtime_error("Property not found or cannot set: " + prop);
    });
}

// ── Parse MAXScript-style "key:value" param pairs ──────────────
// Input: "radius:25 pos:[0,0,50] name:\"Foo\""
// Returns vector of {key, value} pairs
static std::vector<std::pair<std::string, std::string>> ParseParamString(const std::string& s) {
    std::vector<std::pair<std::string, std::string>> result;
    size_t i = 0;
    while (i < s.size()) {
        // Skip whitespace
        while (i < s.size() && s[i] == ' ') i++;
        if (i >= s.size()) break;

        // Find key (up to ':')
        size_t keyStart = i;
        while (i < s.size() && s[i] != ':') i++;
        if (i >= s.size()) break;
        std::string key = s.substr(keyStart, i - keyStart);
        i++; // skip ':'

        // Parse value — handle brackets and quotes
        size_t valStart = i;
        if (i < s.size() && s[i] == '[') {
            // Scan to matching ']'
            int depth = 1;
            i++;
            while (i < s.size() && depth > 0) {
                if (s[i] == '[') depth++;
                else if (s[i] == ']') depth--;
                i++;
            }
        } else if (i < s.size() && s[i] == '"') {
            // Scan to matching '"'
            i++;
            while (i < s.size() && s[i] != '"') {
                if (s[i] == '\\') i++; // skip escaped char
                i++;
            }
            if (i < s.size()) i++; // skip closing quote
        } else {
            // Scan to next space
            while (i < s.size() && s[i] != ' ') i++;
        }
        std::string val = s.substr(valStart, i - valStart);
        result.push_back({key, val});
    }
    return result;
}

static std::string ToLowerCopy(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(), ::tolower);
    return s;
}

static bool HasParam(const std::vector<std::pair<std::string, std::string>>& params,
                     const std::string& key) {
    std::string lkey = ToLowerCopy(key);
    for (const auto& [paramKey, _] : params) {
        if (ToLowerCopy(paramKey) == lkey) {
            return true;
        }
    }
    return false;
}

static void UpsertParam(std::vector<std::pair<std::string, std::string>>& params,
                        const std::string& key,
                        const std::string& value) {
    std::string lkey = ToLowerCopy(key);
    for (auto& [paramKey, paramValue] : params) {
        if (ToLowerCopy(paramKey) == lkey) {
            paramValue = value;
            return;
        }
    }
    params.push_back({key, value});
}

static std::string DefaultCreateObjectParams(const std::string& type) {
    static const std::unordered_map<std::string, std::string> kDefaults = {
        {"box", "length:25 width:25 height:25"},
        {"sphere", "radius:25"},
        {"cylinder", "radius:10 height:25"},
        {"cone", "radius1:15 radius2:0 height:25"},
        {"torus", "radius:20 radius2:5"},
        {"plane", "length:50 width:50"},
        {"teapot", "radius:15"},
        {"tube", "radius1:15 radius2:10 height:25"},
        {"pyramid", "width:25 depth:25 height:25"},
        {"geosphere", "radius:25"},
        {"hedra", "radius:15"},
        {"torusknot", "radius:20 radius2:4"},
        {"chamferbox", "length:25 width:25 height:25 fillet:2"},
        {"chamfercyl", "radius:10 height:25 fillet:2"},
        {"oiltank", "radius:15 height:25 capheight:5"},
        {"spindle", "radius:15 height:25 capheight:5"},
        {"capsule", "radius:10 height:25"},
    };
    auto it = kDefaults.find(ToLowerCopy(type));
    return it != kDefaults.end() ? it->second : "";
}

static std::string JsonValueToParamString(const json& value, bool rawString = false) {
    switch (value.type()) {
    case json::value_t::null:
    case json::value_t::discarded:
        return {};
    case json::value_t::string:
        return rawString ? value.get<std::string>() : value.dump();
    case json::value_t::object:
        return {};
    default:
        return value.dump();
    }
}

static void MergeJsonParams(const json& source,
                            std::vector<std::pair<std::string, std::string>>& out) {
    if (source.type() != json::value_t::object) {
        return;
    }

    for (auto it = source.begin(); it != source.end(); ++it) {
        std::string key = it.key();
        std::string lkey = ToLowerCopy(key);
        if (lkey == "position") {
            key = "pos";
            lkey = "pos";
        }
        std::string value = JsonValueToParamString(it.value(), lkey == "pos");
        if (value.empty()) {
            continue;
        }
        UpsertParam(out, key, value);
    }
}

static std::vector<std::pair<std::string, std::string>> BuildCreateObjectParams(
    const json& payload,
    const std::string& type) {

    std::vector<std::pair<std::string, std::string>> kvPairs;

    auto paramsIt = payload.find("params");
    if (paramsIt != payload.end()) {
        if (paramsIt->type() == json::value_t::string) {
            kvPairs = ParseParamString(paramsIt->get<std::string>());
        } else if (paramsIt->type() == json::value_t::object) {
            MergeJsonParams(*paramsIt, kvPairs);
        }
    }

    if (payload.type() == json::value_t::object) {
        for (auto it = payload.begin(); it != payload.end(); ++it) {
            std::string lkey = ToLowerCopy(it.key());
            if (lkey == "type" || lkey == "name" || lkey == "params") {
                continue;
            }
            json wrapper = json::object();
            wrapper[it.key()] = it.value();
            MergeJsonParams(wrapper, kvPairs);
        }
    }

    for (const auto& [key, value] : ParseParamString(DefaultCreateObjectParams(type))) {
        if (!HasParam(kvPairs, key)) {
            kvPairs.push_back({key, value});
        }
    }

    return kvPairs;
}

// ── native:create_object (Pure SDK) ─────────────────────────────
std::string NativeHandlers::CreateObject(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string type = p.value("type", "");
        std::string name = p.value("name", "");

        if (type.empty()) throw std::runtime_error("type is required");

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        // Find ClassDesc for this type — try geometry first, then all
        ClassDesc* cd = FindClassDescByName(type, GEOMOBJECT_CLASS_ID);
        if (!cd) cd = FindClassDescByName(type, SHAPE_CLASS_ID);
        if (!cd) cd = FindClassDescByName(type, LIGHT_CLASS_ID);
        if (!cd) cd = FindClassDescByName(type, CAMERA_CLASS_ID);
        if (!cd) cd = FindClassDescByName(type, HELPER_CLASS_ID);
        if (!cd) cd = FindClassDescByName(type, SYSTEM_CLASS_ID);
        if (!cd) cd = FindClassDescByName(type); // any superclass
        if (!cd) throw std::runtime_error("Unknown object class: " + type);

        // Create the object instance
        Object* obj = (Object*)ip->CreateInstance(cd->SuperClassID(), cd->ClassID());
        if (!obj) throw std::runtime_error("Failed to create instance of: " + type);

        // Create node in scene
        INode* node = ip->CreateObjectNode(obj);
        if (!node) throw std::runtime_error("Failed to create node for: " + type);

        // Set name
        if (!name.empty()) {
            std::wstring wname = Utf8ToWide(name);
            node->SetName(wname.c_str());
        }

        // Parse and apply params via IParamBlock2
        Point3 posOverride(0, 0, 0);
        bool hasPos = false;
        bool anyParamFailed = false;
        auto kvPairs = BuildCreateObjectParams(p, type);

        if (!kvPairs.empty()) {
            for (auto& [key, val] : kvPairs) {
                std::string lkey = key;
                std::transform(lkey.begin(), lkey.end(), lkey.begin(), ::tolower);

                // Handle pos specially — it's a node-level property
                if (lkey == "pos") {
                    if (ParsePoint3(val, posOverride)) {
                        hasPos = true;
                    }
                    continue;
                }

                // Try setting on base object's PB2
                if (!SetParamByName(obj, key, val, t)) {
                    anyParamFailed = true;
                }
            }

            // If any PB2 param failed (e.g. PB1/legacy objects like Capsule, Hedra),
            // fall back to MAXScript to apply all params at once
            if (anyParamFailed) {
                std::string nodeName = WideToUtf8(node->GetName());
                std::string ms = "(";
                for (auto& [key, val] : kvPairs) {
                    std::string lkey = key;
                    std::transform(lkey.begin(), lkey.end(), lkey.begin(), ::tolower);
                    if (lkey == "pos") continue; // handled separately
                    ms += "$'" + JsonEscape(nodeName) + "'." + key + " = " + val + "; ";
                }
                ms += "\"OK\")";
                try { RunMAXScript(ms); } catch (...) {}
            }
        }

        // Apply position
        if (hasPos) {
            Matrix3 tm = node->GetNodeTM(t);
            tm.SetTrans(posOverride);
            node->SetNodeTM(t, tm);
        }

        // Notify and redraw
        obj->NotifyDependents(FOREVER, PART_ALL, REFMSG_CHANGE);
        ip->RedrawViews(t);

        return WideToUtf8(node->GetName());
    });
}

// ── native:delete_objects ───────────────────────────────────────
std::string NativeHandlers::DeleteObjects(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        auto names = p.value("names", std::vector<std::string>{});
        if (names.empty()) throw std::runtime_error("names is required");

        Interface* ip = GetCOREInterface();
        json deleted = json::array();
        json notFound = json::array();

        for (const auto& name : names) {
            INode* node = FindNodeByName(name);
            if (node) {
                ip->DeleteNode(node);
                deleted.push_back(name);
            } else {
                notFound.push_back(name);
            }
        }

        json result;
        result["deleted"] = deleted;
        result["notFound"] = notFound;
        result["message"] = "Deleted " + std::to_string(deleted.size()) + " objects";
        if (!notFound.empty()) {
            result["message"] = result["message"].get<std::string>() +
                " | Not found: " + std::to_string(notFound.size());
        }
        return result.dump();
    });
}

// ── native:transform_object ────────────────────────────────────
std::string NativeHandlers::TransformObject(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string name = p.value("name", "");
        if (name.empty()) throw std::runtime_error("name is required");

        INode* node = FindNodeByName(name);
        if (!node) throw std::runtime_error("Object not found: " + name);

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();
        std::string coordSys = p.value("coordinate_system", "world");

        bool didSomething = false;

        // Move
        if (p.contains("move") && p["move"].type() == json::value_t::array && p["move"].size() == 3) {
            Point3 offset(p["move"][0].get<float>(), p["move"][1].get<float>(), p["move"][2].get<float>());
            Matrix3 tmAxis;  // identity by default in Max 2026
            node->Move(t, tmAxis, offset, TRUE, TRUE, PIV_NONE, TRUE);
            didSomething = true;
        }

        // Rotate
        if (p.contains("rotate") && p["rotate"].type() == json::value_t::array && p["rotate"].size() == 3) {
            float rx = DegToRad(p["rotate"][0].get<float>());
            float ry = DegToRad(p["rotate"][1].get<float>());
            float rz = DegToRad(p["rotate"][2].get<float>());

            Matrix3 tmAxis;
            if (coordSys == "local") {
                tmAxis = node->GetNodeTM(t);
                tmAxis.NoTrans();
            }

            AngAxis aax(Point3(1, 0, 0), rx);
            AngAxis aay(Point3(0, 1, 0), ry);
            AngAxis aaz(Point3(0, 0, 1), rz);

            Quat qx(aax), qy(aay), qz(aaz);
            Quat combined = qx * qy * qz;
            AngAxis finalAA(combined);

            node->Rotate(t, tmAxis, finalAA, TRUE, TRUE, PIV_NONE, TRUE);
            didSomething = true;
        }

        // Scale
        if (p.contains("scale") && p["scale"].type() == json::value_t::array) {
            auto& sv = p["scale"];
            Point3 scaleVal;
            if (sv.size() == 1) {
                float s = sv[0].get<float>();
                scaleVal = Point3(s, s, s);
            } else if (sv.size() == 3) {
                scaleVal = Point3(sv[0].get<float>(), sv[1].get<float>(), sv[2].get<float>());
            } else {
                throw std::runtime_error("scale must be [s] or [x,y,z]");
            }
            Matrix3 tmAxis;
            node->Scale(t, tmAxis, scaleVal, TRUE, TRUE, PIV_NONE, TRUE);
            didSomething = true;
        }

        if (!didSomething) {
            return "No transform parameters provided.";
        }

        ip->RedrawViews(t);

        // Return updated position
        Matrix3 finalTM = node->GetNodeTM(t);
        Point3 pos = finalTM.GetTrans();
        json result;
        result["message"] = "Transformed " + WideToUtf8(node->GetName());
        result["position"] = json::array({pos.x, pos.y, pos.z});
        return result.dump();
    });
}

// ── native:select_objects ──────────────────────────────────────
std::string NativeHandlers::SelectObjects(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);

        Interface* ip = GetCOREInterface();
        bool selectAll = p.value("all", false);
        auto names = p.value("names", std::vector<std::string>{});
        std::string pattern = p.value("pattern", "");
        std::string className = p.value("class_name", "");

        if (selectAll) {
            // Select all objects
            INode* root = ip->GetRootNode();
            std::vector<INode*> nodes;
            CollectNodes(root, nodes);
            ip->ClearNodeSelection(FALSE);
            for (INode* n : nodes) {
                ip->SelectNode(n, FALSE);
            }
            ip->RedrawViews(ip->GetTime());
            return "Selected " + std::to_string(nodes.size()) + " objects";
        }

        if (!names.empty()) {
            ip->ClearNodeSelection(FALSE);
            int found = 0;
            for (const auto& name : names) {
                INode* node = FindNodeByName(name);
                if (node) {
                    ip->SelectNode(node, FALSE);
                    found++;
                }
            }
            ip->RedrawViews(ip->GetTime());
            return "Selected " + std::to_string(found) + " of " +
                   std::to_string(names.size()) + " objects";
        }

        if (!pattern.empty() || !className.empty()) {
            INode* root = ip->GetRootNode();
            std::vector<INode*> allNodes;
            CollectNodes(root, allNodes);

            ip->ClearNodeSelection(FALSE);
            int found = 0;
            for (INode* n : allNodes) {
                bool match = false;
                if (!pattern.empty()) {
                    match = WildcardMatch(WideToUtf8(n->GetName()), pattern);
                } else {
                    match = (NodeClassName(n) == className);
                }
                if (match) {
                    ip->SelectNode(n, FALSE);
                    found++;
                }
            }
            ip->RedrawViews(ip->GetTime());

            std::string desc = !pattern.empty()
                ? ("matching \"" + pattern + "\"")
                : ("of class " + className);
            return "Selected " + std::to_string(found) + " objects " + desc;
        }

        return "At least one parameter (names, pattern, class_name, or all) must be provided.";
    });
}

// ── native:set_visibility ──────────────────────────────────────
std::string NativeHandlers::SetVisibility(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        auto names = p.value("names", std::vector<std::string>{});
        std::string pattern = p.value("pattern", "");
        std::string action = p.value("action", "hide");

        if (names.empty() && pattern.empty()) {
            throw std::runtime_error("At least one of names or pattern must be provided.");
        }

        // Collect target nodes
        std::vector<INode*> targets;
        if (!names.empty()) {
            for (const auto& name : names) {
                INode* node = FindNodeByName(name);
                if (node) targets.push_back(node);
            }
        } else {
            targets = CollectNodesByPattern(pattern);
        }

        int count = 0;
        for (INode* node : targets) {
            if (action == "hide") {
                node->Hide(TRUE);
            } else if (action == "show") {
                node->Hide(FALSE);
            } else if (action == "toggle") {
                node->Hide(!node->IsHidden());
            } else if (action == "freeze") {
                node->Freeze(TRUE);
            } else if (action == "unfreeze") {
                node->Freeze(FALSE);
            } else {
                throw std::runtime_error("Unknown action: " + action);
            }
            count++;
        }

        GetCOREInterface()->RedrawViews(GetCOREInterface()->GetTime());

        json result;
        result["action"] = action;
        result["count"] = count;
        result["message"] = action + ": " + std::to_string(count) + " objects";
        return result.dump();
    });
}

// ── native:clone_objects (Pure SDK) ─────────────────────────────
std::string NativeHandlers::CloneObjects(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        auto names = p.value("names", std::vector<std::string>{});
        std::string mode = p.value("mode", "copy");
        auto offsetVec = p.value("offset", std::vector<float>{0.0f, 0.0f, 0.0f});

        if (names.empty()) throw std::runtime_error("names is required");

        Interface* ip = GetCOREInterface();

        // Build INodeTab of source nodes
        INodeTab srcNodes;
        json notFound = json::array();
        for (const auto& name : names) {
            INode* node = FindNodeByName(name);
            if (node) {
                srcNodes.AppendNode(node);
            } else {
                notFound.push_back(name);
            }
        }

        if (srcNodes.Count() == 0) {
            throw std::runtime_error("No valid objects found to clone");
        }

        // Determine clone type
        CloneType ct = NODE_COPY;
        if (mode == "instance") ct = NODE_INSTANCE;
        else if (mode == "reference") ct = NODE_REFERENCE;

        // Clone
        Point3 offset(
            offsetVec.size() > 0 ? offsetVec[0] : 0.0f,
            offsetVec.size() > 1 ? offsetVec[1] : 0.0f,
            offsetVec.size() > 2 ? offsetVec[2] : 0.0f
        );
        INodeTab resultSource, resultTarget;
        bool ok = ip->CloneNodes(srcNodes, offset, true, ct, &resultSource, &resultTarget);

        if (!ok) {
            throw std::runtime_error("CloneNodes failed");
        }

        // Build result
        json cloneNames = json::array();
        for (int i = 0; i < resultTarget.Count(); i++) {
            cloneNames.push_back(WideToUtf8(resultTarget[i]->GetName()));
        }

        ip->RedrawViews(ip->GetTime());

        json result;
        result["cloned"] = cloneNames;
        result["notFound"] = notFound;
        return result.dump();
    });
}
