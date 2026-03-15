#pragma once
#include <string>
#include <vector>
#include <algorithm>
#include <nlohmann/json.hpp>
#include <max.h>
#include <maxapi.h>
#include <inode.h>
#include <object.h>
#include <modstack.h>
#include <ilayer.h>
#include <ILayerProperties.h>
#include <INodeLayerProperties.h>
#include <iparamb2.h>
#include <plugapi.h>

#include <maxscript/maxscript.h>
#include <maxscript/foundation/strings.h>
#include <CoreFunctions.h>

class MCPBridgeGUP;

namespace HandlerHelpers {

using json = nlohmann::json;

// ── UTF-8 <-> Wide conversion ───────────────────────────────────
inline std::string WideToUtf8(const wchar_t* w) {
    if (!w || !*w) return {};
    int len = WideCharToMultiByte(CP_UTF8, 0, w, -1, nullptr, 0, nullptr, nullptr);
    std::string s(len - 1, 0);
    WideCharToMultiByte(CP_UTF8, 0, w, -1, &s[0], len, nullptr, nullptr);
    return s;
}

inline std::wstring Utf8ToWide(const std::string& s) {
    if (s.empty()) return {};
    int len = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), nullptr, 0);
    std::wstring w(len, 0);
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), &w[0], len);
    return w;
}

// ── Node property helpers ───────────────────────────────────────
inline std::string NodeClassName(INode* node) {
    ObjectState os = node->EvalWorldState(GetCOREInterface()->GetTime());
    if (os.obj) {
        return WideToUtf8(os.obj->ClassName().data());
    }
    return "Unknown";
}

inline std::string NodeLayerName(INode* node) {
    INodeLayerProperties* nlp = static_cast<INodeLayerProperties*>(
        node->GetInterface(NODELAYERPROPERTIES_INTERFACE));
    if (nlp) {
        ILayerProperties* lp = nlp->getLayer();
        if (lp) {
            const MCHAR* name = lp->getName();
            if (name) return WideToUtf8(name);
        }
    }
    return "0";
}

inline json NodePosition(INode* node, TimeValue t) {
    Matrix3 tm = node->GetNodeTM(t);
    Point3 pos = tm.GetTrans();
    return json::array({pos.x, pos.y, pos.z});
}

inline json NodeWireColor(INode* node) {
    DWORD c = node->GetWireColor();
    return json::array({GetRValue(c), GetGValue(c), GetBValue(c)});
}

// ── Scene traversal ─────────────────────────────────────────────
inline void CollectNodes(INode* node, std::vector<INode*>& out) {
    for (int i = 0; i < node->NumberOfChildren(); i++) {
        INode* child = node->GetChildNode(i);
        out.push_back(child);
        CollectNodes(child, out);
    }
}

// ── Node lookup by name ─────────────────────────────────────────
inline INode* FindNodeByName(const std::string& name) {
    Interface* ip = GetCOREInterface();
    std::wstring wname = Utf8ToWide(name);
    return ip->GetINodeByName(wname.c_str());
}

// ── MAXScript execution (for hybrid handlers) ───────────────────
inline std::string RunMAXScript(const std::string& script) {
    std::wstring wcmd = Utf8ToWide(script);
    FPValue fpv;
    BOOL ok = FALSE;

    try {
        ok = ExecuteMAXScriptScript(
            wcmd.c_str(),
            MAXScript::ScriptSource::NotSpecified,
            FALSE,   // quietErrors
            &fpv,    // result
            TRUE     // logQuietErrors
        );
    } catch (...) {
        throw std::runtime_error("MAXScript execution exception");
    }

    if (!ok) {
        throw std::runtime_error("MAXScript execution failed");
    }

    // Convert FPValue to string
    if (fpv.type == TYPE_STRING || fpv.type == TYPE_FILENAME) {
        return WideToUtf8(fpv.s);
    }
    if (fpv.type == TYPE_TSTR) {
        return WideToUtf8(fpv.tstr->data());
    }
    if (fpv.type == TYPE_VALUE && fpv.v != nullptr) {
        try {
            const MCHAR* str = fpv.v->to_string();
            return WideToUtf8(str);
        } catch (...) {}
    }
    if (fpv.type == TYPE_INT) {
        return std::to_string(fpv.i);
    }
    if (fpv.type == TYPE_FLOAT) {
        return std::to_string(fpv.f);
    }

    // Fallback: wrap in string conversion
    std::wstring wrap = L"(" + wcmd + L") as string";
    FPValue fpv2;
    if (ExecuteMAXScriptScript(wrap.c_str(), MAXScript::ScriptSource::NotSpecified, TRUE, &fpv2)) {
        if (fpv2.type == TYPE_STRING || fpv2.type == TYPE_FILENAME) {
            return WideToUtf8(fpv2.s);
        }
        if (fpv2.type == TYPE_TSTR) {
            return WideToUtf8(fpv2.tstr->data());
        }
    }

    return "OK";
}

// ── JSON escape utility ─────────────────────────────────────────
inline std::string JsonEscape(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 8);
    for (char c : s) {
        switch (c) {
            case '"':  out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n";  break;
            case '\r': out += "\\r";  break;
            case '\t': out += "\\t";  break;
            default:   out += c;      break;
        }
    }
    return out;
}

// ── Wildcard pattern matching (case-insensitive) ────────────────
// Supports: * at start, end, both, or standalone. Exact match otherwise.
inline bool WildcardMatch(const std::string& name, const std::string& pattern) {
    if (pattern == "*") return true;
    // Case-insensitive copies
    std::string lname = name, lpat = pattern;
    std::transform(lname.begin(), lname.end(), lname.begin(), ::tolower);
    std::transform(lpat.begin(), lpat.end(), lpat.begin(), ::tolower);

    bool startsWild = !lpat.empty() && lpat.front() == '*';
    bool endsWild = !lpat.empty() && lpat.back() == '*';

    if (startsWild && endsWild) {
        std::string sub = lpat.substr(1, lpat.size() - 2);
        return lname.find(sub) != std::string::npos;
    }
    if (startsWild) {
        std::string suffix = lpat.substr(1);
        return lname.size() >= suffix.size() &&
               lname.compare(lname.size() - suffix.size(), suffix.size(), suffix) == 0;
    }
    if (endsWild) {
        std::string prefix = lpat.substr(0, lpat.size() - 1);
        return lname.compare(0, prefix.size(), prefix) == 0;
    }
    return lname == lpat;
}

// ── Collect nodes matching a wildcard pattern ───────────────────
inline std::vector<INode*> CollectNodesByPattern(const std::string& pattern) {
    Interface* ip = GetCOREInterface();
    INode* root = ip->GetRootNode();
    std::vector<INode*> all, matched;
    CollectNodes(root, all);
    for (INode* n : all) {
        if (WildcardMatch(WideToUtf8(n->GetName()), pattern))
            matched.push_back(n);
    }
    return matched;
}

// ── Find ClassDesc by class name (iterates all loaded plugins) ──
inline ClassDesc* FindClassDescByName(const std::string& className, SClass_ID superID = 0) {
    std::wstring wname = Utf8ToWide(className);
    auto& dir = DllDir::GetInstance();
    int numDlls = dir.Count();
    for (int d = 0; d < numDlls; d++) {
        const DllDesc& dll = dir[d];
        int numClasses = dll.NumberOfClasses();
        for (int c = 0; c < numClasses; c++) {
            ClassDesc* cd = dll[c];
            if (!cd) continue;
            if (superID != 0 && cd->SuperClassID() != superID) continue;
            const MCHAR* cn = cd->ClassName();
            if (cn && _wcsicmp(cn, wname.c_str()) == 0)
                return cd;
            // Also try internal name (some plugins differ)
            const MCHAR* intName = cd->InternalName();
            if (intName && _wcsicmp(intName, wname.c_str()) == 0)
                return cd;
        }
    }
    return nullptr;
}

// ── Parse a MAXScript-style value string into typed PB2 value ───
// Returns true if the param was set successfully.
inline bool SetPB2ParamFromString(IParamBlock2* pb, ParamID pid, ParamType2 ptype,
                                  const std::string& valStr, TimeValue t) {
    // Strip the TYPE_TAB flag for base type comparison
    int baseType = ptype & ~TYPE_TAB;

    switch (baseType) {
    case TYPE_FLOAT:
    case TYPE_ANGLE:
    case TYPE_PCNT_FRAC:
    case TYPE_WORLD:
    case TYPE_COLOR_CHANNEL: {
        float f = std::stof(valStr);
        return pb->SetValue(pid, t, f) != 0;
    }
    case TYPE_INT:
    case TYPE_BOOL:
    case TYPE_TIMEVALUE:
    case TYPE_RADIOBTN_INDEX:
    case TYPE_INDEX: {
        // Handle "true"/"false" for bools
        if (valStr == "true" || valStr == "on") return pb->SetValue(pid, t, 1) != 0;
        if (valStr == "false" || valStr == "off") return pb->SetValue(pid, t, 0) != 0;
        int i = std::stoi(valStr);
        return pb->SetValue(pid, t, i) != 0;
    }
    case TYPE_POINT3:
    case TYPE_RGBA: {
        // Parse multiple formats:
        // "[x,y,z]" or "x,y,z" — direct Point3
        // "(color r g b)" or "color r g b" — MAXScript color (0-255 range for RGBA)
        std::string s = valStr;
        float x = 0, y = 0, z = 0;

        // Try "(color r g b)" format
        if (sscanf(s.c_str(), "(color %f %f %f)", &x, &y, &z) == 3 ||
            sscanf(s.c_str(), "color %f %f %f", &x, &y, &z) == 3) {
            // For RGBA type, values are 0-255 in MAXScript but stored as 0-1 internally
            if (baseType == TYPE_RGBA) {
                // Actually Max PB2 RGBA stores as 0-255 Point3 for some materials
                // and 0-1 for others. Pass through as-is — the PB2 handles scaling.
            }
            Point3 pt(x, y, z);
            return pb->SetValue(pid, t, pt) != 0;
        }

        // Try "[x,y,z]" or "x,y,z"
        s.erase(std::remove(s.begin(), s.end(), '['), s.end());
        s.erase(std::remove(s.begin(), s.end(), ']'), s.end());
        if (sscanf(s.c_str(), "%f,%f,%f", &x, &y, &z) == 3) {
            Point3 pt(x, y, z);
            return pb->SetValue(pid, t, pt) != 0;
        }
        return false;
    }
    case TYPE_TEXMAP: {
        // "undefined" or "null" → clear the texture map slot
        std::string lower = valStr;
        std::transform(lower.begin(), lower.end(), lower.begin(), ::tolower);
        if (lower == "undefined" || lower == "null" || lower == "none") {
            return pb->SetValue(pid, t, (Texmap*)nullptr) != 0;
        }
        // For actual texmap assignment by reference, caller must use a different mechanism
        return false;
    }
    case TYPE_MTL: {
        std::string lower = valStr;
        std::transform(lower.begin(), lower.end(), lower.begin(), ::tolower);
        if (lower == "undefined" || lower == "null" || lower == "none") {
            return pb->SetValue(pid, t, (Mtl*)nullptr) != 0;
        }
        return false;
    }
    case TYPE_STRING:
    case TYPE_FILENAME: {
        // Remove surrounding quotes if present
        std::string s = valStr;
        if (s.size() >= 2 && s.front() == '"' && s.back() == '"')
            s = s.substr(1, s.size() - 2);
        std::wstring ws = Utf8ToWide(s);
        return pb->SetValue(pid, t, ws.c_str()) != 0;
    }
    default:
        return false;
    }
}

// ── Find and set a parameter by name on an object's IParamBlock2s ──
inline bool SetParamByName(Animatable* anim, const std::string& paramName,
                           const std::string& value, TimeValue t) {
    if (!anim) return false;
    std::wstring wparam = Utf8ToWide(paramName);

    // Iterate all param blocks on the object
    int numPB = anim->NumParamBlocks();
    for (int pb_idx = 0; pb_idx < numPB; pb_idx++) {
        IParamBlock2* pb = anim->GetParamBlock(pb_idx);
        if (!pb) continue;

        ParamBlockDesc2* desc = pb->GetDesc();
        if (!desc) continue;

        for (int p = 0; p < desc->count; p++) {
            const ParamDef& pd = desc->GetParamDef(desc->IndextoID(p));
            if (pd.int_name && _wcsicmp(pd.int_name, wparam.c_str()) == 0) {
                return SetPB2ParamFromString(pb, pd.ID, pd.type, value, t);
            }
        }
    }
    return false;
}

} // namespace HandlerHelpers
