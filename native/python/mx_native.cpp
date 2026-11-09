// In-process calls to the bridge's own handlers. The request is the one a
// named-pipe client sends, routed by the bridge the same way (reload gate,
// safe mode, main thread; on the main thread the handler runs inline). Heavy
// operations reuse their existing handlers through this; typed wrappers turn
// the JSON reply into Python values.
#include "mx_common.h"

#include <nanobind/eval.h>

#include <atomic>
#include <psapi.h>

namespace mx {
inline namespace MX_ABI_TAG {

using json = nlohmann::json;
using BridgeCall = const char* (*)(const char* request, size_t length, size_t* responseLength);

// The bridge plugin (mcp_bridge*.gup) exports mcp_bridge_call; it is never
// unloaded while Max runs, so the address is found once.
static BridgeCall FindBridge() {
    static BridgeCall cached = nullptr;
    if (cached) return cached;
    std::vector<HMODULE> modules(512);
    DWORD needed = 0;
    HANDLE process = GetCurrentProcess();
    while (K32EnumProcessModules(process, modules.data(), DWORD(modules.size() * sizeof(HMODULE)), &needed) &&
           needed > modules.size() * sizeof(HMODULE))
        modules.resize(needed / sizeof(HMODULE));
    modules.resize(needed / sizeof(HMODULE));
    for (HMODULE module : modules) {
        if (auto call = reinterpret_cast<BridgeCall>(GetProcAddress(module, "mcp_bridge_call"))) {
            cached = call;
            break;
        }
    }
    return cached;
}

// The bridge's error payload ({code, message, hint}) or plain text, as the
// matching _mx error.
[[noreturn]] static void ThrowBridgeError(const json& error, const std::string& type) {
    json payload = error;
    if ((error.is_string)()) {
        const std::string text = error.get<std::string>();
        payload = json::parse(text, nullptr, false);
        if (payload.is_discarded() || !payload.is_object()) throw MaxError(type + ": " + text);
    }
    if (!payload.is_object()) throw MaxError(type + " failed");
    std::string message = payload.value("message", "");
    if (message.empty() && payload.contains("error") && (payload["error"].is_string)()) message = payload["error"].get<std::string>();
    if (message.empty()) message = type + " failed";
    const json hint = payload.value("hint", json());
    if (hint.is_object() && hint.contains("message") && (hint["message"].is_string)())
        message += " (" + hint["message"].get<std::string>() + ")";
    else if ((hint.is_string)())
        message += " (" + hint.get<std::string>() + ")";
    const std::string code = payload.value("code", "");
    if (code == "NOT_FOUND") throw NotFound(message);
    if (code == "AMBIGUOUS") throw Ambiguous(message);
    if (code == "BAD_PARAM" || code == "INVALID_PARAM" || code == "INVALID_ARGUMENT") throw BadParam(message);
    throw MaxError(code.empty() ? message : "[" + code + "] " + message);
}

json NativeJson(const std::string& type, const json& args) {
    RequireMain();
    BridgeCall call = FindBridge();
    if (!call) throw MaxError("the MCP bridge plugin is not loaded in this 3ds Max");
    static std::atomic<unsigned> counter{0};
    const bool maxscript = type == "maxscript";
    json request = {
        {"command", maxscript ? args.value("code", std::string()) : args.dump()},
        {"type", type},
        {"requestId", "mx-" + std::to_string(++counter)},
        {"protocolVersion", 2},
    };
    if (maxscript && args.contains("quiet")) request["quiet"] = args["quiet"];
    const std::string text = request.dump();
    size_t length = 0;
    const char* raw = call(text.c_str(), text.size(), &length);
    // Copy at once: the bridge reuses its reply buffer for the next call.
    const std::string reply = raw ? std::string(raw, length) : std::string();
    const json response = json::parse(reply, nullptr, false);
    if (!response.is_object()) throw MaxError(type + ": unreadable reply from the bridge");
    if (!response.value("success", false)) ThrowBridgeError(response.value("error", json("")), type);

    const json raw_result = response.value("result", json(""));
    if (!(raw_result.is_string)()) return raw_result;
    const std::string result = raw_result.get<std::string>();
    const std::string sentinel = HandlerHelpers::MaxScriptErrorSentinel();
    if (result.rfind(sentinel, 0) == 0) throw MaxError("MAXScript error: " + result.substr(sentinel.size()));
    json parsed = json::parse(result, nullptr, false);
    if (parsed.is_discarded()) return json(result);
    if (parsed.is_object()) {
        const bool error = parsed.contains("error") && !parsed["error"].is_null() &&
                           !((parsed["error"].is_string)() && parsed["error"].get<std::string>().empty());
        const std::string status = Lower(parsed.value("status", ""));
        if (error || status == "error" || status == "failed") ThrowBridgeError(parsed, type);
    }
    return parsed;
}

// ── JSON <-> Python ──────────────────────────────────────────────

nb::object ToPython(const json& value) {
    switch (value.type()) {
    case json::value_t::null: return nb::none();
    case json::value_t::boolean: return nb::bool_(value.get<bool>());
    case json::value_t::number_integer: return nb::int_(value.get<long long>());
    case json::value_t::number_unsigned: return nb::int_(value.get<unsigned long long>());
    case json::value_t::number_float: return nb::float_(value.get<double>());
    case json::value_t::string: return nb::str(value.get<std::string>().c_str());
    case json::value_t::array: {
        nb::list items;
        for (const json& item : value) items.append(ToPython(item));
        return items;
    }
    case json::value_t::object: {
        nb::dict out;
        for (auto it = value.begin(); it != value.end(); ++it) out[it.key().c_str()] = ToPython(it.value());
        return out;
    }
    default: return nb::none();
    }
}

json FromPython(nb::handle value) {
    if (value.is_none()) return nullptr;
    if (nb::isinstance<nb::bool_>(value)) return nb::cast<bool>(value);
    if (nb::isinstance<nb::int_>(value)) return nb::cast<long long>(value);
    if (nb::isinstance<nb::float_>(value)) return nb::cast<double>(value);
    if (nb::isinstance<nb::str>(value)) return nb::cast<std::string>(value);
    if (nb::isinstance<Node>(value)) return (unsigned long long)nb::cast<Node>(value).handle;
    if (nb::isinstance<nb::dict>(value)) {
        json out = json::object();
        for (auto [key, item] : nb::borrow<nb::dict>(value)) out[nb::cast<std::string>(nb::str(key))] = FromPython(item);
        return out;
    }
    if (IsSequence(value)) {
        json out = json::array();
        for (nb::handle item : value) out.push_back(FromPython(item));
        return out;
    }
    throw BadParam("cannot pass " + WithArticle(TypeNameOf(value)) + " to 3ds Max");
}

nb::object MakeProperty(nb::handle cls, const char* name, bool settable, const char* doc) {
    static nb::object factory;
    if (!factory.is_valid()) {
        nb::dict scope;
        nb::exec(
            "def make(get, set, doc):\n"
            "    fget = lambda self: getattr(self, get)()\n"
            "    fset = (lambda self, value: getattr(self, set)(value)) if set else None\n"
            "    return property(fget, fset, None, doc)\n", scope);
        factory = scope["make"];
    }
    const std::string base(name);
    return factory(nb::str(("_get_" + base).c_str()), settable ? nb::object(nb::str(("_set_" + base).c_str())) : nb::none(),
                   nb::str(doc));
}

void BindNative(nb::module_& m) {
    AddProperty<Node>("selected",
        [](const Node& n) { return Resolve(n)->Selected() != 0; },
        [](const Node& n, bool on) {
            INode* node = Resolve(n);
            GetCOREInterface()->SelectNode(node, 0);
            if (!on) GetCOREInterface()->DeSelectNode(node);
        },
        "Whether the node is selected; setting it adds to or removes from the selection.");

    m.def("native", [](const std::string& type, nb::kwargs args) {
        const std::string full = type == "maxscript" || type.rfind("native:", 0) == 0 ? type : "native:" + type;
        return ToPython(NativeJson(full, FromPython(args)));
    }, "type"_a, "args"_a,
       "Run one of the bridge's handlers in-process: native('scene_delta', ...). Returns its reply as Python values.");
    m.def("maxscript", [](const std::string& code) {
        const json result = NativeJson("maxscript", {{"code", code}});
        return (result.is_string)() ? nb::str(result.get<std::string>().c_str()) : nb::str(result.dump().c_str());
    }, "code"_a, "Evaluate MAXScript; returns the result as text.");
}

} // namespace MX_ABI_TAG
} // namespace mx
