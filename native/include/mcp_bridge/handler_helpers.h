#pragma once
#include <string>
#include <vector>
#include <algorithm>
#include <stdexcept>
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
#include <ISceneEventManager.h>

#include <maxscript/maxscript.h>
#include <maxscript/foundation/strings.h>
#include <maxscript/maxwrapper/mxsobjects.h>
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

// MAXScript's `(classOf value) as string` exposes the script-facing class
// token, not the localized UI label returned by Animatable::ClassName().
inline std::string ScriptClassName(ClassDesc* descriptor) {
    if (!descriptor) return {};

    const MCHAR* internalName = descriptor->InternalName();
    if (internalName && *internalName) {
        return WideToUtf8(internalName);
    }

    const MCHAR* nonLocalizedName = descriptor->NonLocalizedClassName();
    if (nonLocalizedName && *nonLocalizedName) {
        return WideToUtf8(nonLocalizedName);
    }

    const MCHAR* className = descriptor->ClassName();
    return className ? WideToUtf8(className) : std::string();
}

inline std::string ScriptClassName(Animatable* value) {
    if (!value) return {};

    ClassDesc* descriptor = DllDir::GetInstance().ClassDir().FindClass(
        value->SuperClassID(), value->ClassID());
    const std::string scriptName = ScriptClassName(descriptor);
    if (!scriptName.empty()) {
        return scriptName;
    }
    return WideToUtf8(value->ClassName().data());
}

// The script-visible MAXClass name can differ from ClassDesc::InternalName()
// (OpenPBR_Material vs OpenPBR, for example). This is still native C++ metadata:
// it does not evaluate MAXScript. Call only from handlers marshalled to Max's
// main thread because the MAXScript class registry is not thread-safe.
inline std::string MaxScriptVisibleClassName(
    SClass_ID superClassId,
    const Class_ID& classId) {
    ScopedMaxScriptEvaluationContext evaluationContext;
    Class_ID lookupId = classId;
    MAXClass* maxClass = MAXClass::lookup_class(
        &lookupId,
        superClassId,
        true);
    if (maxClass && maxClass->name) {
        const MCHAR* value = maxClass->name->to_string();
        if (value && *value) {
            return WideToUtf8(value);
        }
    }
    ClassDesc* descriptor = DllDir::GetInstance().ClassDir().FindClass(
        superClassId,
        classId);
    return ScriptClassName(descriptor);
}

inline std::string MaxScriptVisibleClassName(Animatable* value) {
    if (!value) return {};
    return MaxScriptVisibleClassName(
        value->SuperClassID(),
        value->ClassID());
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

inline unsigned long long NodeHandle(INode* node) {
    if (!node) return 0ULL;
    return static_cast<unsigned long long>(Animatable::GetHandleByAnim(node));
}

inline json NodeIdentityJson(INode* node) {
    json out;
    if (!node) return out;
    out["name"] = WideToUtf8(node->GetName());
    out["handle"] = NodeHandle(node);
    out["class"] = NodeClassName(node);
    out["layer"] = NodeLayerName(node);
    return out;
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

inline std::vector<INode*> CollectNodesByExactName(const std::string& name) {
    Interface* ip = GetCOREInterface();
    INode* root = ip->GetRootNode();
    std::vector<INode*> all, matched;
    CollectNodes(root, all);
    for (INode* n : all) {
        if (WideToUtf8(n->GetName()) == name) {
            matched.push_back(n);
        }
    }
    return matched;
}

// ── Node lookup by name ─────────────────────────────────────────
inline INode* FindNodeByName(const std::string& name) {
    Interface* ip = GetCOREInterface();
    std::wstring wname = Utf8ToWide(name);
    return ip->GetINodeByName(wname.c_str());
}

inline unsigned long long PayloadHandleValue(const json& payload, const std::string& key = "handle") {
    auto it = payload.find(key);
    if (it == payload.end() || it->is_null()) return 0ULL;
    try {
        if (it->is_number_unsigned()) return it->get<unsigned long long>();
        if (it->is_number_integer()) {
            long long value = it->get<long long>();
            return value > 0 ? static_cast<unsigned long long>(value) : 0ULL;
        }
        // type() check instead of is_string(): MAXScript's value.h defines a
        // function-like is_string macro that breaks the method call when this
        // header lands after maxscript includes.
        if (it->type() == json::value_t::string) {
            std::string value = it->get<std::string>();
            if (!value.empty()) return std::stoull(value);
        }
    } catch (...) {
        return 0ULL;
    }
    return 0ULL;
}

inline INode* FindNodeByHandle(unsigned long long handle) {
    if (handle == 0ULL) return nullptr;
    return NodeEventNamespace::GetNodeByKey(static_cast<NodeEventNamespace::NodeKey>(handle));
}

inline std::string StructuredErrorPayload(
    const std::string& code,
    const std::string& message,
    const json& hint = json()) {
    json payload;
    payload["type"] = "NativeError";
    payload["message"] = message;
    payload["code"] = code;
    payload["retryable"] = (code == "BRIDGE_DOWN" || code == "RENDER_BUSY");
    if (!hint.is_null() && !hint.empty()) {
        payload["hint"] = hint;
    }
    return payload.dump();
}

inline INode* ResolveNodeFromPayload(
    const json& payload,
    const std::string& nameKey = "name",
    const std::string& handleKey = "handle") {

    const unsigned long long handle = PayloadHandleValue(payload, handleKey);
    if (handle != 0ULL) {
        INode* byHandle = FindNodeByHandle(handle);
        if (!byHandle) {
            throw std::runtime_error("Object handle not found: " + std::to_string(handle));
        }
        return byHandle;
    }

    const std::string name = payload.value(nameKey, "");
    if (name.empty()) {
        throw std::runtime_error(nameKey + " or " + handleKey + " is required");
    }

    std::vector<INode*> matches = CollectNodesByExactName(name);
    if (matches.empty()) {
        throw std::runtime_error("Object not found: " + name);
    }
    if (matches.size() > 1) {
        json candidates = json::array();
        for (INode* node : matches) {
            candidates.push_back(NodeIdentityJson(node));
        }
        json hint = {
            {"message", "Pass handle to disambiguate this object name."},
            {"candidates", candidates},
        };
        throw std::runtime_error(StructuredErrorPayload(
            "AMBIGUOUS",
            "Ambiguous object name: " + name,
            hint));
    }
    return matches[0];
}

// MAXScript-level wrap that captures runtime exceptions (parse errors still
// surface via ExecuteMAXScriptScript returning FALSE). The wrapped script
// returns either the user's value, or a string with this sentinel prefix.
inline const char* MaxScriptErrorSentinel() { return "__MCP_MS_ERR__:"; }

inline std::wstring WrapForErrorCapture(const std::wstring& wcmd) {
    return std::wstring(
        L"(\n"
        L"  local __mcp_err = undefined\n"
        L"  local __mcp_res = try (\n"
    ) + wcmd + std::wstring(
        L"\n  ) catch (__mcp_err = getCurrentException(); undefined)\n"
        L"  if __mcp_err != undefined then (\"__MCP_MS_ERR__:\" + __mcp_err) else __mcp_res\n"
        L")\n"
    );
}

// ── MAXScript execution (for hybrid handlers) ───────────────────
inline std::string RunMAXScript(const std::string& script) {
    std::wstring wcmd = WrapForErrorCapture(Utf8ToWide(script));
    FPValue fpv;
    BOOL ok = FALSE;

    try {
        ok = ExecuteMAXScriptScript(
            wcmd.c_str(),
            MAXScript::ScriptSource::NonEmbedded,
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
    if (fpv.type == TYPE_BOOL) {
        return fpv.i ? "true" : "false";
    }

    // Never re-evaluate the script to stringify the result: scripts with side
    // effects (e.g. "max undo") would run twice.
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

// ── Normalize sub-anim path for execute() ───────────────────────
// Replaces [#Object (ClassName)] with .baseObject — parentheses in
// class names break MAXScript's execute() parser.
inline std::string NormalizeSubAnimPath(const std::string& path) {
    std::string result = path;

    // Ensure [name] tokens have # prefix → [#name] (needed by MAXScript execute())
    // Handles paths from get_wired_params which may omit the # prefix
    std::string fixed;
    for (size_t i = 0; i < result.size(); i++) {
        if (result[i] == '[' && (i + 1 < result.size()) && result[i + 1] != '#') {
            fixed += "[#";
        } else {
            fixed += result[i];
        }
    }
    result = fixed;

    // Replace [#Object (anything)] with .baseObject — parentheses break execute()
    auto pos = result.find("[#Object (");
    if (pos != std::string::npos) {
        auto end = result.find(")]", pos);
        if (end != std::string::npos) {
            result = result.substr(0, pos) + ".baseObject" + result.substr(end + 2);
        }
    }
    // Also handle without # (legacy): [Object (anything)]
    pos = result.find("[Object (");
    if (pos != std::string::npos) {
        auto end = result.find(")]", pos);
        if (end != std::string::npos) {
            result = result.substr(0, pos) + ".baseObject" + result.substr(end + 2);
        }
    }

    // Strip [#Parameters] — track view grouping node, not addressable in MAXScript
    std::string paramToken = "[#Parameters]";
    pos = result.find(paramToken);
    while (pos != std::string::npos) {
        result.erase(pos, paramToken.length());
        pos = result.find(paramToken);
    }
    return result;
}

// ── Walk sub-anim path to resolve a track from a node ───────────
// Path format: "[#Transform][#Position][#Z Position]" or "[#transform][#position][#z_position]"
// Returns the Animatable* at that path, or nullptr if not found.
inline Animatable* ResolveSubAnimPath(INode* node, const std::string& path) {
    if (!node) return nullptr;
    Animatable* current = node;

    // Parse [#name] tokens from the path
    std::vector<std::string> tokens;
    size_t i = 0;
    while (i < path.size()) {
        auto open = path.find("[#", i);
        if (open == std::string::npos) {
            // Also try [  without #
            open = path.find('[', i);
            if (open == std::string::npos) break;
            auto close = path.find(']', open);
            if (close == std::string::npos) break;
            std::string tok = path.substr(open + 1, close - open - 1);
            if (!tok.empty() && tok != "Parameters") tokens.push_back(tok);
            i = close + 1;
        } else {
            auto close = path.find(']', open);
            if (close == std::string::npos) break;
            std::string tok = path.substr(open + 2, close - open - 2);
            if (!tok.empty() && tok != "Parameters") tokens.push_back(tok);
            i = close + 1;
        }
    }

    // Walk sub-anims matching by name (case-insensitive)
    for (const auto& tok : tokens) {
        bool found = false;
        int numSubs = current->NumSubs();
        for (int s = 0; s < numSubs; s++) {
            Animatable* sub = current->SubAnim(s);
            if (!sub) continue;
            MSTR subName = current->SubAnimName(s, false);
            std::string sname = WideToUtf8(subName.data());
            // Case-insensitive compare, also handle underscore vs space
            std::string lTok = tok, lName = sname;
            std::transform(lTok.begin(), lTok.end(), lTok.begin(), ::tolower);
            std::transform(lName.begin(), lName.end(), lName.begin(), ::tolower);
            // Replace spaces with underscores for comparison
            std::replace(lTok.begin(), lTok.end(), ' ', '_');
            std::replace(lName.begin(), lName.end(), ' ', '_');
            if (lTok == lName) {
                current = sub;
                found = true;
                break;
            }
        }
        if (!found) return nullptr;
    }
    return current;
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
