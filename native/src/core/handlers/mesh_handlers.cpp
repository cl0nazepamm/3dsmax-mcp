#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/core_bridge.h"
#include "mcp_bridge/handler_helpers.h"

#include <hold.h>
#include <iepoly.h>
#include <mnmesh.h>
#include <triobj.h>

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <vector>

using json = nlohmann::json;
using namespace HandlerHelpers;

namespace {

// ── Base-cage token ─────────────────────────────────────────────
// Two XXH64 streams with different seeds run over the same 32-bit words; their
// 128-bit pair is the token. It guards against stale component IDs, not
// against crafted collisions, so speed matters more than a cryptographic hash.
constexpr uint64_t kPrime1 = 0x9E3779B185EBCA87ULL;
constexpr uint64_t kPrime2 = 0xC2B2AE3D27D4EB4FULL;
constexpr uint64_t kPrime3 = 0x165667B19E3779F9ULL;
constexpr uint64_t kPrime4 = 0x85EBCA77C2B2AE63ULL;
constexpr uint64_t kPrime5 = 0x27D4EB2F165667C5ULL;
constexpr uint64_t kSeeds[2] = {0x0ULL, 0x6D6573682D63616EULL};

inline uint64_t Rotl(uint64_t value, int bits) { return (value << bits) | (value >> (64 - bits)); }

inline uint64_t Round(uint64_t acc, uint64_t lane) {
    acc += lane * kPrime2;
    return Rotl(acc, 31) * kPrime1;
}

inline uint64_t MergeRound(uint64_t acc, uint64_t lane) {
    acc ^= Round(0, lane);
    return acc * kPrime1 + kPrime4;
}

class CageHash {
public:
    CageHash() : words_(kWords) {
        for (int s = 0; s < 2; ++s) {
            acc_[s][0] = kSeeds[s] + kPrime1 + kPrime2;
            acc_[s][1] = kSeeds[s] + kPrime2;
            acc_[s][2] = kSeeds[s];
            acc_[s][3] = kSeeds[s] - kPrime1;
        }
    }

    void Word(uint32_t value) {
        words_[count_++] = value;
        if (count_ == kWords) {
            Stripes(kWords / 8);
            total_ += kWords * 4;
            count_ = 0;
        }
    }

    void Float(float value) {
        uint32_t bits;
        std::memcpy(&bits, &value, sizeof bits);
        Word(bits);
    }

    // Call once, after the last word.
    std::string Digest() {
        const size_t stripes = count_ / 8;
        Stripes(stripes);
        const uint64_t total = total_ + count_ * 4;
        const uint32_t* tail = words_.data() + stripes * 8;
        const size_t tailWords = count_ - stripes * 8;
        char text[33];
        std::snprintf(text, sizeof text, "%016llx%016llx",
            static_cast<unsigned long long>(Finish(0, total, tail, tailWords)),
            static_cast<unsigned long long>(Finish(1, total, tail, tailWords)));
        return text;
    }

private:
    // 16 KB of whole 32-byte stripes, so only the final block has a tail.
    static constexpr size_t kWords = 4096;

    void Stripes(size_t stripes) {
        const uint32_t* words = words_.data();
        for (size_t i = 0; i < stripes; ++i, words += 8) {
            uint64_t lanes[4];
            std::memcpy(lanes, words, sizeof lanes);
            for (int s = 0; s < 2; ++s)
                for (int k = 0; k < 4; ++k) acc_[s][k] = Round(acc_[s][k], lanes[k]);
        }
    }

    uint64_t Finish(int s, uint64_t total, const uint32_t* tail, size_t tailWords) const {
        uint64_t h;
        if (total >= 32) {
            h = Rotl(acc_[s][0], 1) + Rotl(acc_[s][1], 7) + Rotl(acc_[s][2], 12) + Rotl(acc_[s][3], 18);
            for (int k = 0; k < 4; ++k) h = MergeRound(h, acc_[s][k]);
        } else {
            h = kSeeds[s] + kPrime5;
        }
        h += total;
        for (; tailWords >= 2; tailWords -= 2, tail += 2) {
            uint64_t lane;
            std::memcpy(&lane, tail, sizeof lane);
            h ^= Round(0, lane);
            h = Rotl(h, 27) * kPrime1 + kPrime4;
        }
        if (tailWords) {
            h ^= static_cast<uint64_t>(*tail) * kPrime1;
            h = Rotl(h, 23) * kPrime2 + kPrime3;
        }
        h ^= h >> 33;
        h *= kPrime2;
        h ^= h >> 29;
        h *= kPrime3;
        h ^= h >> 32;
        return h;
    }

    std::vector<uint32_t> words_;
    size_t count_ = 0;
    uint64_t total_ = 0;
    uint64_t acc_[2][4];
};

constexpr uint32_t kTokenVersion = 1;
constexpr uint32_t kLiveSlot = 0;
constexpr uint32_t kDeadSlot = 1;

// The mesh that MAXScript's polyop functions edit for `obj.baseobject` when it
// is an Editable Poly (the base object beneath all modifiers), else nullptr.
MNMesh* EditablePolyMesh(INode* node) {
    Object* ref = node->GetObjectRef();
    Object* base = ref ? ref->FindBaseObject() : nullptr;
    if (!base || base->SuperClassID() != GEOMOBJECT_CLASS_ID || base->ClassID() != EPOLYOBJ_CLASS_ID)
        return nullptr;
    auto* poly = static_cast<EPoly*>(base->GetInterface(EPOLY_INTERFACE));
    return poly ? poly->GetMeshPtr() : nullptr;
}

// Changes whenever the node, its object transform, or any vertex position,
// edge or face of its base cage changes. Dead slots keep their IDs until the
// mesh collapses them, so each slot hashes its state and live slots their data.
std::string CageToken(INode* node, const MNMesh& mesh) {
    CageHash hash;
    hash.Word(kTokenVersion);
    const unsigned long long handle = NodeHandle(node);
    hash.Word(static_cast<uint32_t>(handle));
    hash.Word(static_cast<uint32_t>(handle >> 32));
    const int numVerts = mesh.VNum(), numEdges = mesh.ENum(), numFaces = mesh.FNum();
    hash.Word(static_cast<uint32_t>(numVerts));
    hash.Word(static_cast<uint32_t>(numEdges));
    hash.Word(static_cast<uint32_t>(numFaces));
    // MAXScript's obj.objectTransform at the current time.
    const Matrix3 tm = node->GetObjectTM(GetCOREInterface()->GetTime());
    for (int row = 0; row < 4; ++row) {
        const Point3 axis = tm.GetRow(row);
        hash.Float(axis.x);
        hash.Float(axis.y);
        hash.Float(axis.z);
    }
    for (int i = 0; i < numVerts; ++i) {
        const MNVert* vert = mesh.V(i);
        if (vert->GetFlag(MN_DEAD)) {
            hash.Word(kDeadSlot);
            continue;
        }
        hash.Word(kLiveSlot);
        hash.Float(vert->p.x);
        hash.Float(vert->p.y);
        hash.Float(vert->p.z);
    }
    for (int i = 0; i < numEdges; ++i) {
        const MNEdge* edge = mesh.E(i);
        if (edge->GetFlag(MN_DEAD)) {
            hash.Word(kDeadSlot);
            continue;
        }
        hash.Word(kLiveSlot);
        hash.Word(static_cast<uint32_t>(edge->v1));
        hash.Word(static_cast<uint32_t>(edge->v2));
    }
    for (int i = 0; i < numFaces; ++i) {
        const MNFace* face = mesh.F(i);
        if (face->GetFlag(MN_DEAD)) {
            hash.Word(kDeadSlot);
            continue;
        }
        hash.Word(kLiveSlot);
        hash.Word(static_cast<uint32_t>(face->deg));
        for (int k = 0; k < face->deg; ++k) hash.Word(static_cast<uint32_t>(face->vtx[k]));
    }
    return hash.Digest();
}

// ── native:mesh_script ──────────────────────────────────────────

struct MeshScriptRequest {
    std::string script;
    std::string resolve;
    std::string expected;
    std::string undo;
    std::string finish;
};

std::string OptionalString(const json& payload, const char* key) {
    auto it = payload.find(key);
    if (it == payload.end() || it->is_null()) return {};
    if (it->type() != json::value_t::string)
        throw std::runtime_error(StructuredErrorPayload("BAD_PARAM", std::string(key) + " must be a string."));
    return it->get<std::string>();
}

MeshScriptRequest ParseRequest(const std::string& params) {
    const json payload = json::parse(params, nullptr, false);
    if (!payload.is_object())
        throw std::runtime_error(StructuredErrorPayload("BAD_PARAM", "mesh_script payload must be a JSON object."));
    MeshScriptRequest request;
    request.script = OptionalString(payload, "script");
    request.resolve = OptionalString(payload, "resolve");
    request.expected = OptionalString(payload, "expected_mesh");
    request.undo = OptionalString(payload, "undo");
    request.finish = OptionalString(payload, "finish");
    if (request.script.empty())
        throw std::runtime_error(StructuredErrorPayload("BAD_PARAM", "script is required."));
    if (!request.expected.empty() && request.resolve.empty())
        throw std::runtime_error(StructuredErrorPayload("BAD_PARAM", "expected_mesh requires resolve."));
    if (request.undo.size() > 128)
        throw std::runtime_error(StructuredErrorPayload("BAD_PARAM", "undo must contain at most 128 UTF-8 bytes."));
    return request;
}

unsigned long long ParseHandle(const std::string& text, const char* source) {
    size_t digits = 0;
    while (digits < text.size() && text[digits] >= '0' && text[digits] <= '9') ++digits;
    unsigned long long handle = 0;
    if (digits && digits == text.size() && digits <= 20) {
        try { handle = std::stoull(text); } catch (...) { handle = 0; }
    }
    if (!handle)
        throw std::runtime_error(std::string("Mesh ") + source + " did not report a node handle");
    return handle;
}

INode* RequireNode(unsigned long long handle) {
    INode* node = FindNodeByHandle(handle);
    if (!node) throw std::runtime_error("Node handle is no longer valid");
    return node;
}

// The undo step a mesh edit owns. Without a label the request is a read and
// opens no hold. Like the dispatcher's native transactions, it never joins an
// operation that already holds theHold.
class MeshHold {
public:
    explicit MeshHold(const std::string& label) {
        if (label.empty()) return;
        if (theHold.Holding()) {
            throw std::runtime_error(StructuredErrorPayload(
                "USER_BUSY", "3ds Max has an open undo operation. Finish or cancel that operation, then retry."));
        }
        label_ = MSTR(Utf8ToWide(label).c_str());
        theHold.Begin();
        active_ = true;
    }

    ~MeshHold() {
        if (!active_) return;
        try { theHold.Cancel(); } catch (...) {}
    }

    void Accept() {
        if (!active_) return;
        theHold.Accept(label_);
        active_ = false;
    }

private:
    bool active_ = false;
    MSTR label_;
};

} // namespace

// Runs one mesh tool's MAXScript with the base-cage token in a single
// main-thread step, so nothing can edit the mesh between the token check, the
// script and the token readback.
//   resolve        MAXScript returning the target's handle before the script.
//   expected_mesh  token the resolved node must still match (needs resolve).
//   undo           label of the one hold wrapping the script; omitted for reads.
//   script         returns "<handle>\n<output>": the node it read or edited.
//   finish         UI follow-up with obj bound to that node, run after the hold
//                  is accepted; its errors are ignored because the edit has
//                  already committed.
// Returns {handle, output, mesh_token, token_before?}; mesh_token is the
// script's node after the script.
std::string NativeHandlers::MeshScript(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        const MeshScriptRequest request = ParseRequest(params);
        json result = json::object();
        unsigned long long resolved = 0;
        if (!request.resolve.empty())
            resolved = ParseHandle(RunMAXScriptChecked(request.resolve), "resolve");
        if (!request.expected.empty()) {
            INode* node = RequireNode(resolved);
            const MNMesh* mesh = EditablePolyMesh(node);
            if (!mesh) throw std::runtime_error("Stale mesh token: object type changed");
            std::string before = CageToken(node, *mesh);
            if (before != request.expected) throw std::runtime_error("Stale mesh token: inspect_mesh again");
            result["token_before"] = std::move(before);
        }

        MeshHold hold(request.undo);
        const std::string output = RunMAXScriptChecked(request.script);
        const size_t lineEnd = output.find('\n');
        std::string firstLine = output.substr(0, lineEnd);
        if (!firstLine.empty() && firstLine.back() == '\r') firstLine.pop_back();
        const unsigned long long handle = ParseHandle(firstLine, "script");
        if (resolved && handle != resolved)
            throw std::runtime_error("Mesh script reported a different node than it resolved");
        INode* node = RequireNode(handle);
        const MNMesh* mesh = EditablePolyMesh(node);
        if (!mesh) throw std::runtime_error("Editable Poly base required for a mesh token");
        result["mesh_token"] = CageToken(node, *mesh);
        hold.Accept();

        result["handle"] = handle;
        result["output"] = lineEnd == std::string::npos ? std::string() : output.substr(lineEnd + 1);
        if (!request.finish.empty()) {
            const std::string finish =
                "(\nlocal obj = getAnimByHandle " + std::to_string(handle) + "\n" + request.finish + "\n)";
            try { RunMAXScript(finish); } catch (...) {}
        }
        return result.dump();
    });
}

// ── native:geometry_snapshot ────────────────────────────────────
// Writes the triangle mesh MAXScript's snapshotAsMesh returns (evaluated
// stack, world space) in geometry_qa's text format. Nothing in the scene
// changes and no undo record is made.
namespace {

std::string Base64(const std::string& bytes) {
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

// node.modifiers counts the modifiers of every derived object on the node,
// world-space ones included.
int ModifierCount(INode* node) {
    int count = 0;
    for (Object* obj = node->GetObjectRef(); obj && obj->SuperClassID() == GEN_DERIVOB_CLASS_ID;) {
        auto* derived = static_cast<IDerivedObject*>(obj);
        count += derived->NumModifiers();
        obj = derived->GetObjRef();
    }
    return count;
}

INode* SnapshotTarget(const json& request) {
    const std::string name = request.value("name", "");
    if (const unsigned long long handle = PayloadHandleValue(request)) {
        INode* node = FindNodeByHandle(handle);
        if (!node) throw std::runtime_error("Node handle is no longer valid");
        if (!name.empty() && WideToUtf8(node->GetName()) != name)
            throw std::runtime_error("Node handle/name mismatch");
        return node;
    }
    if (!name.empty()) {
        INode* node = FindUniqueNodeByName(name, kPassHandleAdvice);
        if (!node) throw std::runtime_error("Object not found: " + name);
        return node;
    }
    Interface* ip = GetCOREInterface();
    if (ip->GetSelNodeCount() != 1) throw std::runtime_error("Select exactly one mesh or pass name/handle");
    return ip->GetSelNode(0);
}

// geometry_qa passes a new snapshot.txt inside its own mcp_geometry_qa_
// scratch directory. Nothing else is accepted and an existing file is never
// replaced, so the request cannot write anywhere else.
std::filesystem::path ScratchFile(const std::string& path) {
    const std::filesystem::path file(Utf8ToWide(path));
    if (!file.is_absolute() || file.filename() != L"snapshot.txt" ||
        file.parent_path().filename().wstring().rfind(L"mcp_geometry_qa_", 0) != 0)
        throw std::runtime_error("path must be snapshot.txt in an mcp_geometry_qa_ scratch directory");
    return file;
}

void WriteNewFile(const std::filesystem::path& file, const std::string& data) {
    HANDLE handle = CreateFileW(file.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (handle == INVALID_HANDLE_VALUE) throw std::runtime_error("Cannot create geometry QA snapshot file");
    size_t written = 0;
    while (written < data.size()) {
        const DWORD chunk = static_cast<DWORD>(std::min<size_t>(data.size() - written, size_t(1) << 30));
        DWORD done = 0;
        if (!WriteFile(handle, data.data() + written, chunk, &done, nullptr) || done == 0) break;
        written += done;
    }
    CloseHandle(handle);
    if (written != data.size()) {
        DeleteFileW(file.c_str());
        throw std::runtime_error("Cannot write geometry QA snapshot file");
    }
}

// Releases a TriObject that ConvertToType made for this read only.
struct ConvertedTri {
    Object* source;
    TriObject* tri;
    ~ConvertedTri() { if (tri && tri != source) tri->MaybeAutoDelete(); }
};

}  // namespace

std::string NativeHandlers::GeometrySnapshot(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        const json request = json::parse(params, nullptr, false);
        if (!request.is_object()) throw std::runtime_error("Invalid geometry snapshot request");
        const std::filesystem::path file = ScratchFile(request.value("path", ""));
        const long long maxFaces = request.value("max_faces", 100000LL);
        if (maxFaces < 1 || maxFaces > 500000) throw std::runtime_error("max_faces must be from 1 to 500000");

        INode* node = SnapshotTarget(request);
        const TimeValue t = GetCOREInterface()->GetTime();
        const ObjectState os = node->EvalWorldState(t);
        if (!os.obj || !os.obj->CanConvertToType(triObjectClassID))
            throw std::runtime_error("Object does not provide an evaluated triangle mesh");
        ConvertedTri converted{os.obj, static_cast<TriObject*>(os.obj->ConvertToType(t, triObjectClassID))};
        if (!converted.tri) throw std::runtime_error("Object does not provide an evaluated triangle mesh");
        const Mesh& mesh = converted.tri->GetMesh();
        const int nv = mesh.getNumVerts();
        const int nf = mesh.getNumFaces();
        if (nf > maxFaces || nv > maxFaces * 3)
            throw std::runtime_error("Geometry QA limit exceeded; reduce subdivision or raise max_faces (at most 500000)");

        // An object already in world space (space warps applied) gets an
        // identity matrix here, as in snapshotAsMesh.
        const Matrix3 tm = node->GetObjTMAfterWSM(t);
        std::string out;
        out.reserve(64 + static_cast<size_t>(nv) * 40 + static_cast<size_t>(nf) * 24);
        out += "NAME|" + Base64(WideToUtf8(node->GetName())) + "\n";
        out += "META|" + std::to_string(NodeHandle(node)) + "|" + std::to_string(nv) + "|" + std::to_string(nf) +
               "|" + std::to_string(ModifierCount(node)) + "|" + std::to_string(t) + "\n";
        char line[128];
        for (int i = 0; i < nv; ++i) {
            const Point3 p = mesh.verts[i] * tm;
            const int n = std::snprintf(line, sizeof(line), "V|%.9g,%.9g,%.9g\n", p.x, p.y, p.z);
            out.append(line, static_cast<size_t>(n));
        }
        for (int i = 0; i < nf; ++i) {
            const DWORD* v = mesh.faces[i].v;
            const int n = std::snprintf(line, sizeof(line), "F|%lu,%lu,%lu\n", v[0] + 1, v[1] + 1, v[2] + 1);
            out.append(line, static_cast<size_t>(n));
        }
        WriteNewFile(file, out);
        return "OK";
    });
}
