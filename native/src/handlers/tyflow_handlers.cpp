// ── tyFlow graph access ─────────────────────────────────────────
// tyFlow keeps its event graph private: wiring and operator type IDs have no
// MAXScript or SDK getter. The flow's own Save() writes all of it, so
// native:tyflow_graph_stream runs Save() into memory (no file, no save
// notifications, no dependencies) and returns that chunk stream for the
// Python decoder. Events are the flow's tyEvent sub-anims and operators the
// events' tyOperator sub-anims, in editor order.
//
// Every operator is a tyOperator wrapper around an implementation object
// TFlow_OP_<type> : TFlow_OP : FPMixinInterface. TFlow_OP's function map is the
// "Operator" interface (getName, setEnabled, remove, connect, ...). A few
// implementations (Shape, Birth Burst, Actor, Spline Paths) publish their own
// mixin instead and answer the Operator ID with nothing, so MAXScript cannot
// reach those functions. native:tyflow_operator calls TFlow_OP's dispatch on
// them as a base-class call: the dispatch is taken from operators that do
// answer, after RTTI shows the target derives from the same base at offset 0.

#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/core_bridge.h"

#include <ioapi.h>
#include <ifnpub.h>
#include <plugin.h>

#include <cstring>
#include <map>
#include <set>
#include <unordered_map>
#include <vector>

using json = nlohmann::json;
using namespace HandlerHelpers;

namespace {

// Records the chunk stream an object's Save() writes, in the .max file's
// layout: USHORT id, ULONG length including the 6-byte header, high bit set
// on chunks that hold chunks.
class MemorySave final : public ISave {
public:
    int GetRefID(void* target) override {
        if (!target) return -1;
        auto it = refs_.find(target);
        if (it != refs_.end()) return it->second;
        const int id = static_cast<int>(refs_.size());
        refs_.emplace(target, id);
        return id;
    }
    void BeginChunk(USHORT id) override {
        if (!open_.empty()) open_.back().container = true;
        open_.push_back({bytes_.size(), false});
        Append(&id, sizeof(id));
        const ULONG placeholder = 0;
        Append(&placeholder, sizeof(placeholder));
    }
    void EndChunk() override {
        if (open_.empty()) { balanced_ = false; return; }
        const Open chunk = open_.back();
        open_.pop_back();
        const size_t length = bytes_.size() - chunk.offset;
        if (length > 0x7fffffffu) { overflow_ = true; return; }
        const ULONG header = static_cast<ULONG>(length) | (chunk.container ? 0x80000000u : 0u);
        std::memcpy(&bytes_[chunk.offset + sizeof(USHORT)], &header, sizeof(header));
    }
    int CurChunkDepth() override { return static_cast<int>(open_.size()); }
    IOResult Write(const void* buf, ULONG nbytes, ULONG* nwrit) override {
        if (buf && nbytes) Append(buf, nbytes);
        if (nwrit) *nwrit = nbytes;
        return IO_OK;
    }
    // Like the file writer: the characters only, no terminator.
    IOResult WriteWString(const char* str) override {
        const std::wstring wide = Utf8ToWide(str ? str : "");
        Append(wide.data(), wide.size() * sizeof(wchar_t));
        return IO_OK;
    }
    IOResult WriteWString(const wchar_t* str) override {
        if (str) Append(str, wcslen(str) * sizeof(wchar_t));
        return IO_OK;
    }
    IOResult WriteCString(const char* str) override {
        if (str) Append(str, strlen(str));
        return IO_OK;
    }
    IOResult WriteCString(const wchar_t* str) override {
        const std::string narrow = WideToUtf8(str ? str : L"");
        Append(narrow.data(), narrow.size());
        return IO_OK;
    }
    FileIOType DoingWhat() override { return IOTYPE_MAX; }
    void DisableChunkWarnings(bool disable) override { warnings_ = !disable; }
    bool AreChunkWarningsEnabled() const override { return warnings_; }

    const std::string& Bytes() const { return bytes_; }
    bool Complete() const { return balanced_ && !overflow_ && open_.empty(); }

private:
    struct Open { size_t offset; bool container; };
    void Append(const void* data, size_t size) {
        bytes_.append(static_cast<const char*>(data), size);
    }
    std::string bytes_;
    std::vector<Open> open_;
    std::unordered_map<void*, int> refs_;
    bool balanced_ = true;
    bool overflow_ = false;
    bool warnings_ = true;
};

std::string Base64Encode(const std::string& bytes) {
    static const char* kAlphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string out;
    out.reserve((bytes.size() + 2) / 3 * 4);
    size_t i = 0;
    for (; i + 2 < bytes.size(); i += 3) {
        const uint32_t n = (uint32_t(uint8_t(bytes[i])) << 16) | (uint32_t(uint8_t(bytes[i + 1])) << 8) |
                           uint8_t(bytes[i + 2]);
        out += kAlphabet[n >> 18];
        out += kAlphabet[(n >> 12) & 63];
        out += kAlphabet[(n >> 6) & 63];
        out += kAlphabet[n & 63];
    }
    if (i < bytes.size()) {
        const bool two = i + 1 < bytes.size();
        const uint32_t n = (uint32_t(uint8_t(bytes[i])) << 16) | (two ? uint32_t(uint8_t(bytes[i + 1])) << 8 : 0);
        out += kAlphabet[n >> 18];
        out += kAlphabet[(n >> 12) & 63];
        out += two ? kAlphabet[(n >> 6) & 63] : '=';
        out += '=';
    }
    return out;
}

bool IsClass(Animatable* anim, const wchar_t* name) {
    return anim && anim->ClassName(false) == MSTR(name);
}

Object* TyFlowBase(INode* node) {
    Object* ref = node->GetObjectRef();
    Object* base = ref ? ref->FindBaseObject() : nullptr;
    if (!IsClass(base, L"tyFlow")) {
        throw std::runtime_error("Not a tyFlow object: " + WideToUtf8(node->GetName()));
    }
    return base;
}

struct FlowGraph {
    std::vector<Animatable*> events;
    std::vector<std::vector<Animatable*>> operators;  // per event, editor order
};

FlowGraph ReadFlow(Object* flow) {
    FlowGraph graph;
    for (int i = 0; i < flow->NumSubs(); ++i) {
        Animatable* ev = flow->SubAnim(i);
        if (!IsClass(ev, L"tyEvent")) continue;
        graph.events.push_back(ev);
        std::vector<Animatable*> ops;
        for (int j = 0; j < ev->NumSubs(); ++j) {
            Animatable* op = ev->SubAnim(j);
            if (IsClass(op, L"tyOperator")) ops.push_back(op);
        }
        graph.operators.push_back(std::move(ops));
    }
    return graph;
}

std::string SubAnimNameOf(Animatable* parent, Animatable* child) {
    for (int i = 0; i < parent->NumSubs(); ++i) {
        if (parent->SubAnim(i) == child) return WideToUtf8(parent->SubAnimName(i, false).data());
    }
    return {};
}

void* VTable(const void* object) { return *static_cast<void* const*>(object); }

// ── MSVC RTTI (x64, image-relative) ────────────────────────────
struct RttiLocator { DWORD signature, offset, cdOffset, typeRva, hierarchyRva, selfRva; };
struct RttiHierarchy { DWORD signature, attributes, count, arrayRva; };
struct RttiBase { DWORD typeRva, contained; int mdisp, pdisp, vdisp; DWORD attributes, hierarchyRva; };
struct RttiType { const void* vftable; const void* spare; char name[1]; };

struct ModuleImage { const char* base = nullptr; size_t size = 0; };

bool ImageOf(const void* address, ModuleImage& image) {
    HMODULE module = nullptr;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                            static_cast<LPCWSTR>(address), &module) || !module) return false;
    const auto* dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(module);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return false;
    const auto* nt = reinterpret_cast<const IMAGE_NT_HEADERS*>(reinterpret_cast<const char*>(module) + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return false;
    image.base = reinterpret_cast<const char*>(module);
    image.size = nt->OptionalHeader.SizeOfImage;
    return true;
}

bool InImage(const ModuleImage& image, const void* p, size_t bytes) {
    const char* c = static_cast<const char*>(p);
    return c >= image.base && c + bytes <= image.base + image.size;
}

// The complete-object locator behind a polymorphic subobject, checked so a
// binary built without RTTI is refused instead of misread.
const RttiLocator* LocatorOf(const void* subobject, ModuleImage& image) {
    const void* vtable = VTable(subobject);
    if (!ImageOf(vtable, image)) return nullptr;
    const auto* slot = static_cast<const RttiLocator* const*>(vtable) - 1;
    if (!InImage(image, slot, sizeof(*slot))) return nullptr;
    const RttiLocator* locator = *slot;
    if (!InImage(image, locator, sizeof(RttiLocator))) return nullptr;
    if (locator->signature != 1 || image.base + locator->selfRva != reinterpret_cast<const char*>(locator))
        return nullptr;
    if (!InImage(image, image.base + locator->typeRva, sizeof(RttiType)) ||
        !InImage(image, image.base + locator->hierarchyRva, sizeof(RttiHierarchy))) return nullptr;
    return locator;
}

std::string TypeName(const ModuleImage& image, DWORD typeRva) {
    const auto* type = reinterpret_cast<const RttiType*>(image.base + typeRva);
    const char* name = type->name;
    size_t length = 0;
    while (length < 512 && InImage(image, name + length, 1) && name[length]) ++length;
    return std::string(name, length);
}

bool IsSdkBase(const std::string& name) {
    static const std::set<std::string> kSdk = {
        ".?AVFPMixinInterface@@", ".?AVFPInterface@@", ".?AVBaseInterface@@",
        ".?AVInterfaceServer@@", ".?AVMaxHeapOperators@@",
    };
    return kSdk.count(name) > 0;
}

// Plugin base classes at offset 0 of a complete object (its own type excluded).
bool OffsetZeroBases(const void* subobject, std::string& complete, std::set<std::string>& bases) {
    ModuleImage image;
    const RttiLocator* locator = LocatorOf(subobject, image);
    if (!locator || locator->offset != 0 || locator->cdOffset != 0) return false;
    complete = TypeName(image, locator->typeRva);
    const auto* hierarchy = reinterpret_cast<const RttiHierarchy*>(image.base + locator->hierarchyRva);
    const auto* rvas = reinterpret_cast<const DWORD*>(image.base + hierarchy->arrayRva);
    if (hierarchy->count > 512 || !InImage(image, rvas, hierarchy->count * sizeof(DWORD))) return false;
    for (DWORD i = 1; i < hierarchy->count; ++i) {
        const auto* base = reinterpret_cast<const RttiBase*>(image.base + rvas[i]);
        if (!InImage(image, base, sizeof(RttiBase)) ||
            !InImage(image, image.base + base->typeRva, sizeof(RttiType))) return false;
        if (base->mdisp != 0 || base->pdisp != -1) continue;
        const std::string name = TypeName(image, base->typeRva);
        if (!IsSdkBase(name)) bases.insert(name);
    }
    return true;
}

// ── FPInterface::_dispatch_fn vtable slot ──────────────────────
// Read from the compiler's own vcall thunk for the member, then proven by a
// call through that slot on an object of ours.
using DispatchFn = FPStatus (*)(void* self, FunctionID fid, TimeValue t, FPValue* result, FPParams* p);
constexpr FPStatus kProbeStatus = 0x5ad1;

class DispatchProbe : public FPMixinInterface {
public:
    FPInterfaceDesc* GetDesc() override { return nullptr; }
    FPStatus _dispatch_fn(FunctionID, TimeValue, FPValue&, FPParams*) override { return kProbeStatus; }
    static const unsigned char* Thunk() {
        FPStatus (DispatchProbe::*member)(FunctionID, TimeValue, FPValue&, FPParams*) = &DispatchProbe::_dispatch_fn;
        const unsigned char* code;
        // MSVC: the code pointer leads; a this-adjustor may follow.
        static_assert(sizeof(member) >= sizeof(code), "unexpected member pointer size");
        std::memcpy(&code, &member, sizeof(code));
        return code;
    }
};

int DispatchSlot() {
    static int slot = -2;
    if (slot != -2) return slot;
    slot = -1;
    const unsigned char* code = DispatchProbe::Thunk();
    for (int hops = 0; hops < 4 && code && code[0] == 0xE9; ++hops) {  // incremental-link stub
        int32_t rel;
        std::memcpy(&rel, code + 1, sizeof(rel));
        code = code + 5 + rel;
    }
    if (!code || code[0] != 0x48 || code[1] != 0x8B || code[2] != 0x01 || code[3] != 0xFF) return slot;
    int offset = -1;
    if (code[4] == 0x20) offset = 0;                            // jmp [rax]
    else if (code[4] == 0x60) offset = static_cast<int8_t>(code[5]);  // jmp [rax+disp8]
    else if (code[4] == 0xA0) { int32_t disp; std::memcpy(&disp, code + 5, sizeof(disp)); offset = disp; }
    if (offset < 0 || offset % sizeof(void*) != 0) return slot;
    const int candidate = offset / static_cast<int>(sizeof(void*));
    DispatchProbe probe;
    auto fn = reinterpret_cast<DispatchFn>(static_cast<void* const*>(VTable(&probe))[candidate]);
    FPValue result;
    if (fn(static_cast<FPInterface*>(&probe), 0, 0, &result, nullptr) == kProbeStatus) slot = candidate;
    return slot;
}

// ── Operator interface access ───────────────────────────────────
FPInterfaceDesc* OperatorDesc(Animatable* op) {
    ClassDesc* cd = DllDir::GetInstance().ClassDir().FindClass(op->SuperClassID(), op->ClassID());
    if (!cd) return nullptr;
    for (int i = 0; i < cd->NumInterfaces(); ++i) {
        FPInterface* fi = cd->GetInterfaceAt(i);
        FPInterfaceDesc* desc = fi ? fi->GetDesc() : nullptr;
        if (desc && desc->internal_name == MSTR(L"Operator")) return desc;
    }
    return nullptr;
}

// The implementation object behind a tyOperator: the first mixin it answers.
FPInterface* ImplementationMixin(Animatable* op) {
    ClassDesc* cd = DllDir::GetInstance().ClassDir().FindClass(op->SuperClassID(), op->ClassID());
    if (!cd) return nullptr;
    for (int i = 0; i < cd->NumInterfaces(); ++i) {
        FPInterface* fi = cd->GetInterfaceAt(i);
        FPInterfaceDesc* desc = fi ? fi->GetDesc() : nullptr;
        if (!desc) continue;
        if (BaseInterface* answered = op->GetInterface(desc->GetID())) return static_cast<FPInterface*>(answered);
    }
    return nullptr;
}

struct OperatorAccess {
    FPInterfaceDesc* desc = nullptr;
    FPInterface* mixin = nullptr;
    DispatchFn baseDispatch = nullptr;  // set: call it on mixin instead of Invoke
    std::string via = "none";
    std::string reason;
};

OperatorAccess ResolveOperator(Animatable* op, const FlowGraph& graph) {
    OperatorAccess access;
    access.desc = OperatorDesc(op);
    if (!access.desc) { access.reason = "the operator class publishes no Operator interface"; return access; }
    const Interface_ID id = access.desc->GetID();
    if (BaseInterface* direct = op->GetInterface(id)) {
        access.mixin = static_cast<FPInterface*>(direct);
        access.via = "direct";
        return access;
    }
    FPInterface* target = ImplementationMixin(op);
    std::string targetType;
    std::set<std::string> targetBases;
    if (!target || !OffsetZeroBases(target, targetType, targetBases) || targetBases.empty()) {
        access.reason = "the operator's implementation could not be identified";
        return access;
    }
    const int slot = DispatchSlot();
    if (slot < 0) { access.reason = "the interface dispatch slot could not be located"; return access; }
    ModuleImage targetImage;
    if (!ImageOf(VTable(target), targetImage)) { access.reason = "no module for the operator"; return access; }

    // Dispatch shared by two or more operator types that answer the Operator
    // ID: that is the inherited base implementation, not a per-type override.
    std::map<void*, std::set<std::string>> typesByDispatch;
    for (const auto& ops : graph.operators) {
        for (Animatable* other : ops) {
            BaseInterface* answered = other->GetInterface(id);
            if (!answered) continue;
            auto* donor = static_cast<FPInterface*>(answered);
            std::string donorType;
            std::set<std::string> donorBases;
            if (!OffsetZeroBases(donor, donorType, donorBases)) continue;
            bool shared = false;
            for (const auto& base : donorBases) shared = shared || targetBases.count(base) > 0;
            if (!shared) continue;
            void* fn = static_cast<void* const*>(VTable(donor))[slot];
            if (!InImage(targetImage, fn, 1)) continue;
            typesByDispatch[fn].insert(donorType);
        }
    }
    for (const auto& [fn, types] : typesByDispatch) {
        if (types.size() < 2 || types.count(targetType)) continue;
        access.mixin = target;
        access.baseDispatch = reinterpret_cast<DispatchFn>(fn);
        access.via = "base_dispatch";
        return access;
    }
    access.reason = "no two operator types in this flow share the base Operator dispatch";
    return access;
}

FPFunctionDef* FindFunction(FPInterfaceDesc* desc, const std::string& name) {
    const MSTR wanted(Utf8ToWide(name).c_str());
    for (int i = 0; i < desc->functions.Count(); ++i) {
        FPFunctionDef* fn = desc->functions[i];
        if (fn && _wcsicmp(fn->internal_name.data(), wanted.data()) == 0) return fn;
    }
    return nullptr;
}

json ResultJson(const FPValue& value) {
    switch (static_cast<int>(value.type)) {
    case TYPE_bool: return value.b;
    case TYPE_BOOL: return value.i != 0;
    case TYPE_INT: return value.i;
    case TYPE_FLOAT: return value.f;
    case TYPE_STRING:
    case TYPE_FILENAME: return value.s ? WideToUtf8(value.s) : std::string();
    case TYPE_TSTR:
    case TYPE_TSTR_BV: return value.tstr ? WideToUtf8(value.tstr->data()) : std::string();
    default: return nullptr;
    }
}

}  // namespace

// ── native:tyflow_graph_stream ──────────────────────────────────
// {name|handle} → the flow's Save() stream (base64) and the event/operator
// anim handles in editor order. Read-only: nothing is written to disk and no
// file notifications are sent.
std::string NativeHandlers::TyFlowGraphStream(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        const json p = json::parse(params, nullptr, false);
        if (!p.is_object()) throw std::runtime_error("Invalid JSON params");
        INode* node = ResolveNodeFromPayload(p);
        Object* flow = TyFlowBase(node);

        MemorySave save;
        const IOResult io = flow->Save(&save);
        if (io != IO_OK || !save.Complete()) {
            throw std::runtime_error("tyFlow Save() did not complete (IOResult " + std::to_string(io) + ")");
        }

        const FlowGraph graph = ReadFlow(flow);
        json events = json::array();
        for (size_t e = 0; e < graph.events.size(); ++e) {
            json ops = json::array();
            for (Animatable* op : graph.operators[e]) {
                const OperatorAccess access = ResolveOperator(op, graph);
                ops.push_back({
                    {"name", SubAnimNameOf(graph.events[e], op)},
                    {"anim_handle", static_cast<unsigned long long>(Animatable::GetHandleByAnim(op))},
                    {"operator_interface", access.via},
                });
            }
            events.push_back({
                {"name", SubAnimNameOf(flow, graph.events[e])},
                {"anim_handle", static_cast<unsigned long long>(Animatable::GetHandleByAnim(graph.events[e]))},
                {"operators", std::move(ops)},
            });
        }

        json result;
        result["name"] = WideToUtf8(node->GetName());
        result["handle"] = NodeHandle(node);
        result["size"] = save.Bytes().size();
        result["stream"] = Base64Encode(save.Bytes());
        result["events"] = std::move(events);
        return result.dump();
    });
}

// ── native:tyflow_operator ──────────────────────────────────────
// {name|handle, operator_handle, function, value?, event_handle?} → calls one
// Operator interface function (getName, getEnabled, setName, setEnabled,
// remove, connect, disconnect) on an operator of that flow, whether or not
// the operator answers the Operator interface itself.
std::string NativeHandlers::TyFlowOperator(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        const json p = json::parse(params, nullptr, false);
        if (!p.is_object()) throw std::runtime_error("Invalid JSON params");
        INode* node = ResolveNodeFromPayload(p);
        Object* flow = TyFlowBase(node);
        const FlowGraph graph = ReadFlow(flow);

        const auto findAnim = [&](const char* key, bool events) -> Animatable* {
            if (!p.contains(key) || !p[key].is_number_unsigned()) {
                throw std::runtime_error(std::string(key) + " is required");
            }
            Animatable* anim = Animatable::GetAnimByHandle(static_cast<AnimHandle>(p[key].get<unsigned long long>()));
            if (events) {
                for (Animatable* ev : graph.events) if (ev == anim) return anim;
            } else {
                for (const auto& ops : graph.operators) for (Animatable* op : ops) if (op == anim) return anim;
            }
            throw std::runtime_error(std::string(key) + " is not an " + (events ? "event" : "operator") +
                                     " of " + WideToUtf8(node->GetName()) + "; read the graph again");
        };
        Animatable* op = findAnim("operator_handle", false);
        const std::string function = p.value("function", "");
        if (function.empty()) throw std::runtime_error("function is required");

        const OperatorAccess access = ResolveOperator(op, graph);
        if (!access.mixin) {
            throw std::runtime_error(json{
                {"type", "NativeError"},
                {"code", "TYFLOW_OPERATOR_UNREACHABLE"},
                {"message", "The Operator interface of this operator cannot be reached: " + access.reason + "."},
                {"retryable", false},
            }.dump());
        }
        FPFunctionDef* fn = FindFunction(access.desc, function);
        if (!fn) throw std::runtime_error("Operator has no function " + function);
        if (fn->params.Count() > 1) throw std::runtime_error(function + " takes more than one argument");

        MSTR text;
        FPParams args;
        if (fn->params.Count() == 1) {
            const int type = static_cast<int>(fn->params[0]->type);
            switch (type) {
            case TYPE_bool:
            case TYPE_BOOL: {
                if (!p.contains("value") || !p["value"].is_boolean())
                    throw std::runtime_error(function + " needs a boolean value");
                args.Load(1, type, p["value"].get<bool>() ? TRUE : FALSE);
                break;
            }
            case TYPE_STRING:
            case TYPE_FILENAME:
            case TYPE_TSTR:
            case TYPE_TSTR_BV: {
                if (!p.contains("value") || p["value"].type() != json::value_t::string)
                    throw std::runtime_error(function + " needs a string value");
                text = MSTR(Utf8ToWide(p["value"].get<std::string>()).c_str());
                if (type == TYPE_STRING || type == TYPE_FILENAME) args.Load(1, type, text.data());
                else args.Load(1, type, &text);
                break;
            }
            case TYPE_REFTARG:
            case TYPE_OBJECT: {
                auto* target = dynamic_cast<ReferenceTarget*>(findAnim("event_handle", true));
                if (!target) throw std::runtime_error("event_handle is not a reference target");
                args.Load(1, type, target);
                break;
            }
            default:
                throw std::runtime_error(function + " takes an unsupported parameter type " + std::to_string(type));
            }
        }

        FPValue result;
        const FPStatus status = access.baseDispatch
            ? access.baseDispatch(access.mixin, fn->ID, GetCOREInterface()->GetTime(), &result, &args)
            : access.mixin->Invoke(fn->ID, GetCOREInterface()->GetTime(), result, &args);
        if (status != FPS_OK) {
            throw std::runtime_error(function + " failed (FPStatus " + std::to_string(status) + ")");
        }
        json out;
        out["function"] = WideToUtf8(fn->internal_name.data());
        out["via"] = access.via;
        out["result"] = fn->result_type == TYPE_VOID ? json(nullptr) : ResultJson(result);
        return out.dump();
    });
}
