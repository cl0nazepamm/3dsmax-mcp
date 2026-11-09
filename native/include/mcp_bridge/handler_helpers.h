#pragma once
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>
#include <algorithm>
#include <optional>
#include <cmath>
#include <functional>
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
#include <maxscript/compiler/parser.h>
#include <CoreFunctions.h>
#include "mcp_bridge/color_value.h"
#include "mcp_bridge/quiet_policy.h"

class MCPBridgeGUP;

// Fails the current bridge operation when Max showed an error dialog during
// it. Core code reaches the shell's DialogWatch through the host API
// (core_bridge.cpp); shell code passes DialogWatch::ThrowIfDismissed itself.
namespace McpHost::DialogWatch { void ThrowIfDismissed(); }
// Runs work on Max's main thread, also from a direct handler (core_bridge.cpp).
namespace McpHost { std::string RunOnMainThread(const std::function<std::string()>& work); }

namespace HandlerHelpers {

using json = nlohmann::json;

// MAXScript's evaluator, value heap and class registry belong to Max's main
// thread, and TempQuietMode switches a process-wide mode. Script work on any
// other thread is a bridge defect: fail it before it touches MAXScript.
inline void RequireMaxScriptThread() {
    if (GetCurrentThreadId() == GetCOREInterface15()->GetMainThreadID()) return;
    throw std::runtime_error(json{{"type", "NativeError"}, {"code", "INTERNAL_ERROR"}, {"retryable", false},
        {"message", "Internal bridge error: MAXScript was called off 3ds Max's main thread; the script did not run"}}.dump());
}

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

// Some plugins prefix a control character to force their class to the top of
// 3ds Max's alphabetical browsers (Corona registers "\rCoronaPhysicalMtl").
// MAXScript never exposes those raw bytes: `(classOf m) as string` reports
// _CoronaPhysicalMtl. Mirror that substitution in class-name outputs.
// JSON escaping itself is handled by the serializer.
inline std::string SanitizeScriptName(std::string value) {
    for (char& ch : value) {
        const unsigned char byte = static_cast<unsigned char>(ch);
        if (byte < 0x20 || byte == 0x7F) ch = '_';
    }
    return value;
}

// A relative rotation from XYZ Euler degrees that turns like MAXScript's
// `rotate node (eulerAngles x y z)`: about X, then Y, then Z, each
// counterclockwise looking down the axis. Built from rotation matrices:
// AngAxis(axis, +angle) and EulerToQuat both turn clockwise through
// INode::Rotate, since the SDK's quaternions are left-handed.
inline Quat EulerDegreesToQuat(float x, float y, float z) {
    return Quat(RotateXMatrix(DegToRad(x)) * RotateYMatrix(DegToRad(y)) * RotateZMatrix(DegToRad(z)));
}

// Accept SDK labels, the control-character aliases returned to clients, and
// MAXScript's spelling, where every space or punctuation mark of the label is
// an underscore ("RailClone Pro" is RailClone_Pro).
inline bool MatchesClassName(const std::wstring& requested, const MCHAR* candidate) {
    if (!candidate || !*candidate) return false;
    if (_wcsicmp(requested.c_str(), candidate) == 0) return true;
    std::wstring script(candidate);
    for (wchar_t& ch : script)
        if (!iswalnum(ch) && ch != L'_') ch = L'_';
    if (_wcsicmp(requested.c_str(), script.c_str()) == 0) return true;
    const auto sanitized = Utf8ToWide(SanitizeScriptName(WideToUtf8(candidate)));
    return _wcsicmp(requested.c_str(), sanitized.c_str()) == 0;
}

// MAXScript's `(classOf value) as string` exposes the script-facing class
// token, not the localized UI label returned by Animatable::ClassName().
inline std::string ScriptClassName(ClassDesc* descriptor) {
    if (!descriptor) return {};

    const MCHAR* internalName = descriptor->InternalName();
    if (internalName && *internalName) {
        return SanitizeScriptName(WideToUtf8(internalName));
    }

    const MCHAR* nonLocalizedName = descriptor->NonLocalizedClassName();
    if (nonLocalizedName && *nonLocalizedName) {
        return SanitizeScriptName(WideToUtf8(nonLocalizedName));
    }

    const MCHAR* className = descriptor->ClassName();
    return className ? SanitizeScriptName(WideToUtf8(className)) : std::string();
}

inline std::string ScriptClassName(Animatable* value) {
    if (!value) return {};

    ClassDesc* descriptor = DllDir::GetInstance().ClassDir().FindClass(
        value->SuperClassID(), value->ClassID());
    const std::string scriptName = ScriptClassName(descriptor);
    if (!scriptName.empty()) {
        return scriptName;
    }
    return SanitizeScriptName(WideToUtf8(value->ClassName().data()));
}

// The script-visible MAXClass name can differ from ClassDesc::InternalName()
// (OpenPBR_Material vs OpenPBR, for example). This is still native C++ metadata:
// it does not evaluate MAXScript. Call only from handlers marshalled to Max's
// main thread because the MAXScript class registry is not thread-safe.
inline std::string MaxScriptVisibleClassName(
    SClass_ID superClassId,
    const Class_ID& classId) {
    RequireMaxScriptThread();
    ScopedMaxScriptEvaluationContext evaluationContext;
    Class_ID lookupId = classId;
    MAXClass* maxClass = MAXClass::lookup_class(
        &lookupId,
        superClassId,
        true);
    if (maxClass && maxClass->name) {
        const MCHAR* value = maxClass->name->to_string();
        if (value && *value) {
            return SanitizeScriptName(WideToUtf8(value));
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
        return SanitizeScriptName(WideToUtf8(os.obj->ClassName().data()));
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

// Nodes named `name`. Exact matches win; when there is none, names that differ
// only in letter case match, as MAXScript name lookups do.
inline std::vector<INode*> CollectNodesByName(const std::string& name) {
    std::vector<INode*> all, exact, caseless;
    CollectNodes(GetCOREInterface()->GetRootNode(), all);
    const std::wstring wide = Utf8ToWide(name);
    for (INode* n : all) {
        const MCHAR* nodeName = n->GetName();
        if (wide == nodeName) exact.push_back(n);
        else if (exact.empty() && _wcsicmp(nodeName, wide.c_str()) == 0) caseless.push_back(n);
    }
    return exact.empty() ? caseless : exact;
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
    payload["retryable"] = (
        code == "BRIDGE_DOWN" || code == "RENDER_BUSY" || code == "USER_BUSY" ||
        code == "SCENE_CONFLICT" || code == "TRANSACTION_BUSY");
    if (!hint.is_null() && !hint.empty()) {
        payload["hint"] = hint;
    }
    return payload.dump();
}

// ── Node lookup by name ─────────────────────────────────────────
// Max lets several objects share a name (imported "Object001", repeated
// furniture), and Interface::GetINodeByName then returns either one. A name
// therefore resolves only when exactly one object carries it; a shared name
// fails with every candidate's identity instead of acting on a guess.
inline constexpr const char* kPassHandleAdvice = "Pass handle to disambiguate this object name.";
inline constexpr const char* kUniqueNamesAdvice =
    "This tool takes object names only. Give the objects unique names first "
    "(set_object_property with handle and property \"name\", or scene_patch rename), then retry.";

inline json NameCandidatesJson(const std::vector<INode*>& matches) {
    json candidates = json::array();
    for (INode* node : matches) candidates.push_back(NodeIdentityJson(node));
    return candidates;
}

[[noreturn]] inline void ThrowAmbiguousName(
    const std::string& name, const std::vector<INode*>& matches, const char* advice) {
    throw std::runtime_error(StructuredErrorPayload(
        "AMBIGUOUS",
        "Ambiguous object name: " + name,
        {{"message", advice}, {"candidates", NameCandidatesJson(matches)}}));
}

// The one object with this exact name, or nullptr when none has it; the
// caller reports a missing name in its own words.
inline INode* FindUniqueNodeByName(const std::string& name, const char* advice = kUniqueNamesAdvice) {
    const std::vector<INode*> matches = CollectNodesByName(name);
    if (matches.size() > 1) ThrowAmbiguousName(name, matches, advice);
    return matches.empty() ? nullptr : matches.front();
}

// ── Transform readback ──────────────────────────────────────────
// Constraint, script and expression controllers ignore Move, Rotate, Scale
// and SetNodeTM without an error, so edits compare the node transform before
// and after each requested part.
inline bool SameTransform(const Matrix3& a, const Matrix3& b) {
    for (int row = 0; row < 4; ++row) {
        for (int axis = 0; axis < 3; ++axis) {
            const float x = a.GetRow(row)[axis];
            const float y = b.GetRow(row)[axis];
            if (std::fabs(x - y) > 1e-6f * (std::max)({1.0f, std::fabs(x), std::fabs(y)})) return false;
        }
    }
    return true;
}

inline std::string ControllerClassName(Control* control) {
    if (!control) return "";
    MSTR name;
    control->GetClassName(name, false);
    return WideToUtf8(name.data());
}

// part is "move", "rotate" or "scale". A transform-level controller (Link,
// LookAt) has no separate part controllers, so it is named instead.
inline json IgnoredTransformPart(INode* node, const std::string& part) {
    Control* tm = node->GetTMController();
    Control* sub = nullptr;
    if (tm) {
        if (part == "move") sub = tm->GetPositionController();
        else if (part == "rotate") sub = tm->GetRotationController();
        else sub = tm->GetScaleController();
    }
    return {{"part", part}, {"controller", ControllerClassName(sub ? sub : tm)}};
}

inline constexpr const char* kTransformIgnoredAdvice =
    "Edit the controller's targets or values instead, or use assign_controller to give the track "
    "a keyframable controller.";

// MAXScript expression for a node already resolved here. getAnimByHandle
// reaches it by its animatable handle, so the script never looks the node up
// by name again.
inline std::string NodeScriptRef(INode* node) {
    return "(getAnimByHandle " + std::to_string(NodeHandle(node)) + ")";
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

    INode* node = FindUniqueNodeByName(name, kPassHandleAdvice);
    if (!node) {
        throw std::runtime_error("Object not found: " + name);
    }
    return node;
}

// MAXScript-level wrap that captures runtime exceptions (parse errors still
// surface via ExecuteMAXScriptScript returning FALSE). The wrapped script
// returns either the user's value, or a string with this sentinel prefix.
inline const char* MaxScriptErrorSentinel() { return "__MCP_MS_ERR__:"; }

// Joins names for a warning. Callers keep the text free of "not found:",
// which the Python envelope reads as an error even inside a success payload.
inline std::string JoinNames(const json& names, size_t limit = 10) {
    std::string out;
    size_t shown = 0;
    for (const auto& entry : names) {
        if (shown == limit) { out += ", ..."; break; }
        if (shown++) out += ", ";
        if (entry.type() == json::value_t::string) out += entry.get<std::string>();
        else if (entry.is_object() && entry.contains("name") &&
                 entry["name"].type() == json::value_t::string) out += entry["name"].get<std::string>();
        else if (entry.is_object() && entry.contains("handle")) out += "handle " + entry["handle"].dump();
        else out += entry.dump();
    }
    return out;
}

// ── Batch name resolution ───────────────────────────────────────
// A batch edit applies each requested name to the one object carrying it.
// A name several objects share is applied to none of them: like a missing
// name, it is skipped and reported, with its candidates.
struct NamedNodes {
    std::vector<INode*> nodes;        // request order, each node once
    json notFound = json::array();    // names no object carries
    json ambiguous = json::array();   // {name, candidates} per shared name
    bool Unresolved() const { return !notFound.empty() || !ambiguous.empty(); }
};

inline json AmbiguousNameJson(const std::string& name, const std::vector<INode*>& matches) {
    return {{"name", name}, {"candidates", NameCandidatesJson(matches)}};
}

inline NamedNodes ResolveNodesByNames(const std::vector<std::string>& names) {
    // One scene walk serves the whole list.
    std::unordered_map<std::string, std::vector<INode*>> byName;
    for (const auto& name : names) byName.emplace(name, std::vector<INode*>{});
    std::vector<INode*> all;
    CollectNodes(GetCOREInterface()->GetRootNode(), all);
    for (INode* node : all) {
        auto it = byName.find(WideToUtf8(node->GetName()));
        if (it != byName.end()) it->second.push_back(node);
    }
    // As in CollectNodesByName: a name with no exact match may match by case alone.
    for (auto& entry : byName) {
        if (!entry.second.empty()) continue;
        const std::wstring wide = Utf8ToWide(entry.first);
        for (INode* node : all)
            if (_wcsicmp(node->GetName(), wide.c_str()) == 0) entry.second.push_back(node);
    }

    NamedNodes out;
    std::unordered_set<INode*> seen;
    std::unordered_set<std::string> reported;
    for (const auto& name : names) {
        const std::vector<INode*>& matches = byName[name];
        if (matches.empty()) {
            out.notFound.push_back(name);
        } else if (matches.size() > 1) {
            if (reported.insert(name).second) out.ambiguous.push_back(AmbiguousNameJson(name, matches));
        } else if (seen.insert(matches.front()).second) {
            out.nodes.push_back(matches.front());
        }
    }
    return out;
}

// Error code for a batch that applied nothing because of its names.
inline const char* UnresolvedNamesCode(const NamedNodes& resolved) {
    return resolved.notFound.empty() && !resolved.ambiguous.empty() ? "AMBIGUOUS" : "NOT_FOUND";
}

inline json UnresolvedNamesHint(const NamedNodes& resolved, json hint = json::object()) {
    hint["notFound"] = resolved.notFound;
    if (!resolved.ambiguous.empty()) {
        hint["ambiguous"] = resolved.ambiguous;
        hint["message"] = kUniqueNamesAdvice;
    }
    return hint;
}

// Fails a batch in which no requested name matched exactly one object.
// consequence says what was left unchanged.
[[noreturn]] inline void ThrowUnresolvedNames(
    const NamedNodes& resolved, const std::string& consequence, const json& hint = json::object()) {
    const char* reason = resolved.ambiguous.empty()
        ? "No object matched the requested names; "
        : resolved.notFound.empty()
            ? "Each requested name is shared by several objects; "
            : "No requested name matched exactly one object; ";
    throw std::runtime_error(StructuredErrorPayload(
        UnresolvedNamesCode(resolved), reason + consequence, UnresolvedNamesHint(resolved, hint)));
}

// Marks a batch result partial for the shared names it skipped.
inline void AddAmbiguousNamesWarning(json& result, const json& ambiguous) {
    if (ambiguous.empty()) return;
    result["ambiguous"] = ambiguous;
    result["status"] = "partial";
    result["warnings"].push_back(std::to_string(ambiguous.size()) +
        " requested name(s) are shared by several objects and were skipped: " + JoinNames(ambiguous) +
        ". See ambiguous for each object's handle.");
}

inline const wchar_t* MaxScriptWrapPrefix() {
    return L"(\n"
           L"  local __mcp_err = undefined\n"
           L"  local __mcp_res = try (\n";
}
inline const wchar_t* MaxScriptWrapSuffix() {
    return L"\n  ) catch (__mcp_err = getCurrentException(); undefined)\n"
           L"  if __mcp_err != undefined then (\"__MCP_MS_ERR__:\" + __mcp_err) else __mcp_res\n"
           L")\n";
}

inline std::wstring WrapForErrorCapture(const std::wstring& wcmd) {
    return MaxScriptWrapPrefix() + wcmd + MaxScriptWrapSuffix();
}

// One-line, length-bounded MAXScript error text for a response message.
inline std::string CleanMaxScriptDetail(std::string detail) {
    for (auto& c : detail) if (c == '\r' || c == '\n' || c == '\t') c = ' ';
    const size_t first = detail.find_first_not_of(" -");
    detail.erase(0, first == std::string::npos ? detail.size() : first);
    while (!detail.empty() && detail.back() == ' ') detail.pop_back();
    if (detail.size() > 500) {
        size_t cut = 500;
        while (cut > 0 && (static_cast<unsigned char>(detail[cut]) & 0xC0) == 0x80) --cut;  // keep UTF-8 valid for json
        detail = detail.substr(0, cut) + "...";
    }
    return detail;
}

// ExecuteMAXScriptScript also returns FALSE when a script is aborted by
// something MAXScript try/catch does not catch (quitMax, escape, system
// exceptions), so a FALSE is not proof of a parse error. This re-compiles the
// same text WITHOUT evaluating it (Parser::compile only, never eval) to tell
// the two apart. Main thread only; skipped while Max quits or off the main
// thread. Shell and core both call it (RunMAXScript), so it reads Max's own
// state only, never the shell's executor.
// A parse error's detail comes from the unwrapped user text when that also
// fails, so line numbers and quoted code are the user's, not the wrapper's.
// Silent: compiler output goes to a private StringStream and CompileError is
// caught here, so nothing reaches the Listener.
// sdk_detail is the error text ExecuteMAXScriptScript returned (TYPE_TSTR in
// its result): the parse-error detail when the compile check found none, and
// appended as "Max reported: ..." to the other messages.
// Sets *code to "BAD_PARAM" or "MAXSCRIPT_INTERRUPTED". Never throws.
inline std::string MaxScriptFailureMessage(const std::wstring& wcmd, std::string* code = nullptr,
                                           const std::string& sdk_detail = {}) {
    static const char* kUnclassified =
        "MAXScript execution failed: the script did not complete and the syntax check "
        "could not run, so this is either a parse error or an interruption "
        "(quitMax/resetMaxFile/exit, escape/abort, or a system exception).";
    auto withSdk = [&sdk_detail](std::string message) {
        try {
            const std::string extra = CleanMaxScriptDetail(sdk_detail);
            if (!extra.empty()) message += " Max reported: " + extra;
        } catch (...) {}
        return message;
    };
    if (code) *code = "BAD_PARAM";
    try {
        Interface7* ip = GetCOREInterface7();
        if (ip && ip->QuitingApp()) {
            if (code) *code = "MAXSCRIPT_INTERRUPTED";
            return withSdk("MAXScript did not complete: 3ds Max is shutting down (e.g. after quitMax), "
                           "so the syntax check was skipped. If the script ended the Max session that is expected.");
        }
        // The compile check uses MAXScript's thread state: main thread only.
        if (GetCurrentThreadId() != GetCOREInterface15()->GetMainThreadID()) return withSdk(kUnclassified);

        ScopedMaxScriptEvaluationContext context;
        MAXScript_TLS* _tls = context.Get_TLS();
        four_typed_value_locals_tls(StringStream* source, StringStream* errors, Parser* parser, StringStream* text);
        // Parser updates the current-source thread locals; restore them on every path.
        struct SourceRestore {
            MAXScript_TLS* tls; decltype(_tls->source_file) file; decltype(_tls->current_pkg) pkg;
            decltype(_tls->source_pos) pos; decltype(_tls->source_line) line; decltype(_tls->source_flags) flags;
            ~SourceRestore() {
                tls->source_file = file; tls->current_pkg = pkg; tls->source_pos = pos;
                tls->source_line = line; tls->source_flags = flags;
            }
        } restoreSource{_tls, _tls->source_file, _tls->current_pkg, _tls->source_pos,
                        _tls->source_line, _tls->source_flags};
        // 0 = compiles, 1 = compile error (detail set), -1 = the check itself failed.
        auto compileOnly = [&](const std::wstring& text, std::string& detail) -> int {
            vl.source = new StringStream(text.c_str());
            vl.errors = new StringStream();
            vl.parser = new Parser(vl.errors);
            int result = 0;
            try {
                MAXScriptException::ScopedMXSCallstackCaptureDisable noCallstack(_tls);
                vl.source->flush_whitespace();
                while (!vl.source->at_eos() || vl.parser->back_tracked) {
                    vl.parser->compile(vl.source, MAXScript::ScriptSource::NonEmbedded);  // code is never eval()'d
                    vl.source->flush_whitespace();
                }
                if (vl.parser->expr_level != 0) {
                    result = 1;
                    detail = "unexpected end of script";
                }
            } catch (CompileError& e) {
                clear_error_source_data(_tls);  // this catch eats the mxs exception
                result = 1;
                vl.text = new StringStream();
                e.sprin1(vl.text);
                detail = WideToUtf8(vl.text->to_string());
            } catch (...) {
                clear_error_source_data(_tls);
                result = -1;
            }
            vl.source->close();
            return result;
        };

        std::string detail;
        const int compiled = compileOnly(wcmd, detail);
        if (compiled < 0) return withSdk(kUnclassified);
        if (compiled == 0) {
            if (code) *code = "MAXSCRIPT_INTERRUPTED";
            return withSdk("MAXScript did not complete (not a parse error): the script was interrupted, e.g. by "
                           "quitMax/resetMaxFile/exit, an escape/abort, or a system exception. If it ended or "
                           "reset the Max session that is expected.");
        }
        const std::wstring prefix = MaxScriptWrapPrefix(), suffix = MaxScriptWrapSuffix();
        if (wcmd.size() >= prefix.size() + suffix.size() &&
            wcmd.compare(0, prefix.size(), prefix) == 0 &&
            wcmd.compare(wcmd.size() - suffix.size(), suffix.size(), suffix) == 0) {
            std::string userDetail;
            const std::wstring userText = wcmd.substr(prefix.size(), wcmd.size() - prefix.size() - suffix.size());
            if (compileOnly(userText, userDetail) == 1 && !userDetail.empty()) detail = userDetail;
        }
        detail = CleanMaxScriptDetail(detail);
        if (detail.empty()) detail = CleanMaxScriptDetail(sdk_detail);
        return detail.empty()
            ? std::string("MAXScript execution failed (parse error)")
            : "MAXScript execution failed (parse error): " + detail;
    } catch (...) {
        return withSdk(kUnclassified);
    }
}

// ── MAXScript execution (for hybrid handlers) ───────────────────
// Evaluates on the calling thread, which must be Max's main thread. Shell code
// runs there already and passes its own dialog check.
inline std::string RunMAXScript(
    const std::string& script,
    void (*throwIfDialogDismissed)()) {
    RequireMaxScriptThread();
    // Tool scripts are batch work: prompts take their defaults. Quiet mode is
    // one process-wide flag that TempQuietMode saves and restores; it changes
    // only here, on Max's main thread. A script that could prompt to save or
    // discard the scene runs with quiet mode forced off, even if
    // something else left Max quiet: its prompt must reach the agent, not
    // take a default.
    TempQuietMode quiet(QuietPolicy::MentionsSceneFileCommand(script) ? FALSE : TRUE);
    std::wstring wcmd = WrapForErrorCapture(Utf8ToWide(script));
    FPValue fpv;
    BOOL ok = FALSE;

    try {
        ok = ExecuteMAXScriptScript(
            wcmd.c_str(),
            MAXScript::ScriptSource::NonEmbedded,
            TRUE,    // quietErrors: never route agent diagnostics to modal UI
            &fpv,    // result
            TRUE     // logQuietErrors
        );
    } catch (...) {
        throw std::runtime_error("MAXScript execution exception");
    }

    if (!ok) {
        const std::string detail = fpv.type == TYPE_TSTR && fpv.tstr ? WideToUtf8(fpv.tstr->data()) : "";
        // Explicit code, so compiler detail quoting the script cannot be
        // re-keyworded by NormalizeNativeError (e.g. "not found" -> NOT_FOUND).
        std::string code;
        std::string message = MaxScriptFailureMessage(wcmd, &code, detail);
        throw std::runtime_error(StructuredErrorPayload(code, message));
    }

    throwIfDialogDismissed();
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

// Core code. A direct handler runs on a pipe thread, where ExecuteSync stays
// inline: evaluation and result conversion are sent to Max's main thread.
inline std::string RunMAXScript(const std::string& script) {
    return McpHost::RunOnMainThread([&script] {
        return RunMAXScript(script, &McpHost::DialogWatch::ThrowIfDismissed);
    });
}

// Core code that reads the FPValue itself: no error wrapper, quiet mode or
// dialog check. `read` runs on Max's main thread right after evaluation, while
// the result is valid. False when the script failed.
inline bool EvaluateMAXScript(const std::wstring& script, const std::function<void(FPValue&)>& read) {
    bool ok = false;
    McpHost::RunOnMainThread([&] {
        RequireMaxScriptThread();
        FPValue fpv;
        ok = ExecuteMAXScriptScript(script.c_str(), MAXScript::ScriptSource::NonEmbedded, TRUE, &fpv) != FALSE;
        if (ok) read(fpv);
        return std::string();
    });
    return ok;
}

// RunMAXScript returns a runtime exception as a sentinel string, so a caller
// that ignores the result reports success for an edit Max rejected. Mutations
// use this form: the throw reaches the dispatcher, which cancels its undo hold.
inline std::string RunMAXScriptChecked(const std::string& script) {
    std::string result = RunMAXScript(script);
    const std::string sentinel = MaxScriptErrorSentinel();
    if (result.compare(0, sentinel.size(), sentinel) == 0)
        throw std::runtime_error(result.substr(sentinel.size()));
    return result;
}

// Parses an explicit boolean literal. Anything else is rejected rather than
// read as false, so "True" or "yes" cannot silently clear a flag.
inline bool ParseBoolLiteral(const std::string& value, bool& out) {
    std::string lower = value;
    std::transform(lower.begin(), lower.end(), lower.begin(), ::tolower);
    if (lower == "true" || lower == "1" || lower == "on" || lower == "yes") { out = true; return true; }
    if (lower == "false" || lower == "0" || lower == "off" || lower == "no") { out = false; return true; }
    return false;
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
// MAXScript matchPattern rules, case-insensitive: '*' matches any run of
// characters (anywhere in the pattern, "Wall*_A*" included), '?' one character.
inline bool WildcardMatch(const std::string& name, const std::string& pattern) {
    if (pattern == "*") return true;
    const auto lower = [](char c) { return static_cast<char>(::tolower(static_cast<unsigned char>(c))); };
    size_t n = 0, p = 0, star = std::string::npos, resume = 0;
    while (n < name.size()) {
        if (p < pattern.size() && (pattern[p] == '?' || (pattern[p] != '*' && lower(pattern[p]) == lower(name[n])))) {
            ++n;
            ++p;
        } else if (p < pattern.size() && pattern[p] == '*') {
            star = p++;
            resume = n;
        } else if (star != std::string::npos) {
            p = star + 1;
            n = ++resume;
        } else {
            return false;
        }
    }
    while (p < pattern.size() && pattern[p] == '*') ++p;
    return p == pattern.size();
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

// ── Track paths ─────────────────────────────────────────────────
// One animatable track of a node: its parent and its sub-anim index there.
struct SubAnimTrack {
    Animatable* parent = nullptr;
    int index = -1;
    Animatable* anim() const { return parent ? parent->SubAnim(index) : nullptr; }
    MSTR name() const { return parent ? parent->SubAnimName(index, false) : MSTR(); }
};

// Sub-anim names compare without case, a space matching an underscore
// ("X Position" is #X_Position in MAXScript).
inline bool SameTrackName(std::string a, std::string b) {
    for (std::string* s : {&a, &b}) {
        std::transform(s->begin(), s->end(), s->begin(), ::tolower);
        std::replace(s->begin(), s->end(), ' ', '_');
    }
    return a == b;
}

// The child of `owner` named `token`, looking one level further through
// unnamed-in-paths levels such as a parameter block ("Parameters"), so
// "[#Object (Box)][#height]" finds what "[#Object (Box)][#Parameters][#height]" names.
inline std::optional<SubAnimTrack> FindTrackChild(Animatable* owner, const std::string& token) {
    if (!owner) return std::nullopt;
    for (int s = 0; s < owner->NumSubs(); s++)
        if (owner->SubAnim(s) && SameTrackName(token, WideToUtf8(owner->SubAnimName(s, false).data())))
            return SubAnimTrack{owner, s};
    for (int s = 0; s < owner->NumSubs(); s++) {
        Animatable* level = owner->SubAnim(s);
        if (!level || level->SuperClassID() != PARAMETER_BLOCK2_CLASS_ID) continue;
        for (int c = 0; c < level->NumSubs(); c++)
            if (level->SubAnim(c) && SameTrackName(token, WideToUtf8(level->SubAnimName(c, false).data())))
                return SubAnimTrack{level, c};
    }
    return std::nullopt;
}

// A node's track from a path as the tools list it (list_wireable_params,
// inspect_track_view: "[#Transform][#Position][#X Position]",
// "[#Object (Box)][#Parameters][#height]", ".baseObject[#width]"), a transform
// shorthand ("pos", "pos.x", "rotation.z", "scale") or a bare base-object
// parameter ("radius"). nullopt when no track has that path.
inline std::optional<SubAnimTrack> ResolveTrackPath(INode* node, const std::string& rawPath) {
    if (!node) return std::nullopt;
    std::string path = rawPath;
    path.erase(0, path.find_first_not_of(" \t"));
    path.erase(path.find_last_not_of(" \t") + 1);
    std::string lower = path;
    std::transform(lower.begin(), lower.end(), lower.begin(), ::tolower);

    // Transform shorthands, the names MAXScript users reach for first.
    static const std::pair<const char*, const char*> kParts[] = {
        {"position", "Position"}, {"pos", "Position"}, {"rotation", "Rotation"}, {"rot", "Rotation"},
        {"scale", "Scale"}};
    std::vector<std::string> tokens;
    Animatable* start = node;
    if (lower == "transform") {
        tokens = {"Transform"};
    } else {
        for (const auto& [alias, part] : kParts) {
            const std::string a(alias);
            if (lower == a) tokens = {"Transform", part};
            else if (lower.size() == a.size() + 2 && lower.compare(0, a.size(), a) == 0 && lower[a.size()] == '.' &&
                     std::string("xyz").find(lower.back()) != std::string::npos)
                tokens = {"Transform", part, std::string(1, char(::toupper(lower.back()))) + " " + part};
            if (!tokens.empty()) break;
        }
    }
    if (tokens.empty()) {
        // ".baseObject" / "baseObject" starts at the base object.
        for (const std::string prefix : {".baseobject", "baseobject"}) {
            if (lower.rfind(prefix, 0) == 0) {
                Object* object = node->GetObjectRef();
                start = object ? object->FindBaseObject() : nullptr;
                path = path.substr(prefix.size());
                break;
            }
        }
        for (size_t i = 0; i < path.size();) {
            const size_t open = path.find('[', i);
            if (open == std::string::npos) break;
            const size_t close = path.find(']', open);
            if (close == std::string::npos) return std::nullopt;
            std::string token = path.substr(open + 1, close - open - 1);
            if (!token.empty() && token[0] == '#') token.erase(0, 1);
            if (!token.empty()) tokens.push_back(token);
            i = close + 1;
        }
        // A bare name ("radius", "width"): a parameter of the base object.
        if (tokens.empty() && !path.empty() && path.find_first_of(".[]") == std::string::npos) {
            Object* object = node->GetObjectRef();
            start = object ? object->FindBaseObject() : nullptr;
            tokens = {path};
        }
    }
    if (tokens.empty() || !start) return std::nullopt;
    std::optional<SubAnimTrack> ref;
    Animatable* current = start;
    for (const std::string& token : tokens) {
        // "Parameters" names the parameter block level that FindTrackChild
        // already looks through.
        if (SameTrackName(token, "Parameters") && ref) {
            if (auto exact = FindTrackChild(current, token)) { ref = exact; current = ref->anim(); }
            continue;
        }
        ref = FindTrackChild(current, token);
        if (!ref) return std::nullopt;
        current = ref->anim();
    }
    return ref;
}

// MAXScript for that track as a SubAnim value: (getSubAnim <parent> <index>).
inline std::string TrackScriptRef(const SubAnimTrack& track) {
    return "(getSubAnim (getAnimByHandle " + std::to_string(Animatable::GetHandleByAnim(track.parent)) + ") " +
           std::to_string(track.index + 1) + ")";
}

// The track or a NOT_FOUND error that says which paths work.
inline SubAnimTrack RequireTrackPath(INode* node, const std::string& path, const std::string& role = "Track") {
    if (auto track = ResolveTrackPath(node, path)) return *track;
    throw std::runtime_error(StructuredErrorPayload("NOT_FOUND",
        role + " not found: " + path + " on " + WideToUtf8(node->GetName()) +
        ". Use a path from list_wireable_params or inspect_track_view (\"[#Transform][#Position][#X Position]\"), "
        "a transform shorthand (pos, pos.x, rotation.z, scale) or a base-object parameter name (radius)."));
}

// The class's name in MAXScript (Bend, SpaceBend, BoxtextureMap), which is
// neither its UI label nor always its internal name (BendMod). Read from
// MAXScript's class table on the main thread; elsewhere the script-facing
// internal name stands in.
inline std::string MaxScriptClassName(ClassDesc* cd) {
    if (!cd) return {};
    if (GetCurrentThreadId() == GetCOREInterface15()->GetMainThreadID()) {
        try {
            ScopedMaxScriptEvaluationContext context;
            Class_ID id = cd->ClassID();
            MAXClass* cls = MAXClass::lookup_class(&id, cd->SuperClassID(), false);
            if (cls && cls->name) return WideToUtf8(cls->name->to_string());
        } catch (...) {
        }
    }
    return ScriptClassName(cd);
}

// ── Find ClassDesc by class name (iterates all loaded plugins) ──
// The class directory, not DllDir: it also lists deferred-load plug-ins
// (RailClone, ...), which DllDir shows only once something loaded them. The
// matching plug-in is loaded on return; InternalName is read only from
// plug-ins already loaded, so a lookup never loads every deferred plug-in.
inline ClassDesc* FindClassDescByName(const std::string& className, SClass_ID superID = 0) {
    const std::wstring wname = Utf8ToWide(className);
    ClassDirectory& directory = ClassDirectory::GetInstance();
    struct Match { ClassEntry* entry; SClass_ID super; };
    std::vector<Match> matches;
    for (int s = 0; s < directory.Count(); s++) {
        SubClassList& list = directory[s];
        if (superID != 0 && list.SuperID() != superID) continue;
        for (int i = 0; i < list.Count(ACC_ALL); i++) {
            ClassEntry& entry = list[i];
            bool matches_name = MatchesClassName(wname, entry.ClassName().data()) ||
                                MatchesClassName(wname, entry.NonLocalizedClassName().data());
            if (!matches_name && entry.IsLoaded()) {
                ClassDesc* cd = entry.CD();
                matches_name = cd && MatchesClassName(wname, cd->InternalName());
            }
            if (!matches_name) continue;
            const bool seen = std::any_of(matches.begin(), matches.end(), [&](const Match& m) {
                return m.entry->ClassID() == entry.ClassID() && m.super == list.SuperID();
            });
            if (!seen) matches.push_back({&entry, list.SuperID()});
        }
    }
    const auto loaded = [](const Match& m) -> ClassDesc* {
        ClassDesc* cd = m.entry->FullCD();
        return cd && cd->SuperClassID() == m.super && cd->ClassID() == m.entry->ClassID() ? cd : nullptr;
    };
    // No label matched: MAXScript's own class names (SpaceBend, BoxtextureMap),
    // for the plug-ins already loaded.
    if (matches.empty()) {
        for (int s = 0; s < directory.Count(); s++) {
            SubClassList& list = directory[s];
            if (superID != 0 && list.SuperID() != superID) continue;
            for (int i = 0; i < list.Count(ACC_ALL); i++) {
                ClassEntry& entry = list[i];
                if (!entry.IsLoaded()) continue;
                ClassDesc* cd = entry.CD();
                if (cd && _wcsicmp(Utf8ToWide(MaxScriptClassName(cd)).c_str(), wname.c_str()) == 0)
                    matches.push_back({&entry, list.SuperID()});
            }
        }
    }
    if (matches.empty()) return nullptr;
    if (matches.size() == 1) return loaded(matches.front());
    // Several classes share the UI label ("Box" is the Box object and the Box
    // texture map, BoxtextureMap in MAXScript): the one whose MAXScript name
    // is the name asked for wins.
    ClassDesc* exact = nullptr;
    int exactCount = 0;
    std::string candidates;
    for (const Match& m : matches) {
        ClassDesc* cd = loaded(m);
        if (!cd) continue;
        const std::string script = MaxScriptClassName(cd);
        candidates += (candidates.empty() ? "" : ", ") + script;
        if (_wcsicmp(Utf8ToWide(script).c_str(), wname.c_str()) == 0) {
            exact = cd;
            ++exactCount;
        }
    }
    if (exactCount == 1) return exact;
    throw std::runtime_error(StructuredErrorPayload(
        "AMBIGUOUS", "Class name is ambiguous: " + className + " names " + candidates +
                     "; pass the MAXScript name of the one you mean"));
}

// Whole-string numbers only: std::stof and std::stoi read "12abc" as 12 and
// throw on "abc"; a value that is not entirely a number is rejected instead.
inline bool ParseWholeNumber(const std::string& text, float& out) {
    try {
        size_t used = 0;
        out = std::stof(text, &used);
        return text.find_first_not_of(" 	", used) == std::string::npos;
    } catch (const std::exception&) {
        return false;
    }
}

inline bool ParseWholeNumber(const std::string& text, int& out) {
    try {
        size_t used = 0;
        out = std::stoi(text, &used);
        return text.find_first_not_of(" 	", used) == std::string::npos;
    } catch (const std::exception&) {
        return false;
    }
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
        float f = 0.0f;
        if (!ParseWholeNumber(valStr, f)) return false;
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
        int i = 0;
        if (!ParseWholeNumber(valStr, i)) return false;
        return pb->SetValue(pid, t, i) != 0;
    }
    case TYPE_RGBA:
    case TYPE_FRGBA: {
        std::array<float, 4> rgba;
        if (!ParseColorValue(valStr, rgba)) return false;
        if (baseType == TYPE_FRGBA)
            return pb->SetValue(pid, t, AColor(rgba[0], rgba[1], rgba[2], rgba[3])) != 0;
        return pb->SetValue(pid, t, Color(rgba[0], rgba[1], rgba[2])) != 0;
    }
    case TYPE_POINT3: {
        // Parse multiple formats:
        // "[x,y,z]" or "x,y,z" — direct Point3
        // "(color r g b)" or "color r g b" — MAXScript color (0-255 range for RGBA)
        std::string s = valStr;
        float x = 0, y = 0, z = 0;

        // Try "(color r g b)" format
        if (sscanf(s.c_str(), "(color %f %f %f)", &x, &y, &z) == 3 ||
            sscanf(s.c_str(), "color %f %f %f", &x, &y, &z) == 3) {
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
