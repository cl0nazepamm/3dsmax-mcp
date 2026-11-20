// Mesh data, mesh editing and geometry checks.
//
// A node has two meshes. The evaluated triangle mesh (modifiers and space
// warps applied, what renders) is read by node.verts()/faces(), mesh_qa() and
// contacts(). The Editable Poly base cage beneath the modifiers (node.poly)
// carries the 1-based vertex, edge and face IDs that polyop and edits use.
// Cage reads come straight from its MNMesh. Cage edits run as MAXScript
// through the bridge's native:mesh_script, which makes each call one undo step,
// rolls it back on failure and hashes the cage into the token that refuses IDs
// read from a mesh that has changed since.
#include "mx_common.h"

// What the mesh members take, named for their signatures. Each text is read
// off the code that parses the value.

// The operations of Poly.edit, a list or tuple of dicts (Edit, OpScript,
// OpArgs): 'op', 'level' and the op's mode are text, 'selection' is 'current',
// 'all' or a list of IDs, offsets, factors and pivots are (x, y, z), amounts
// are numbers, and segments and iterations are ints.
MX_ARG_TYPE(PolyOpsArg, "collections.abc.Sequence[dict[str, str | int | float | _mx.Point3 | collections.abc.Sequence[int]]]")
// The vertices of create_mesh (MeshVerts): a list or tuple of world positions.
MX_ARG_TYPE(MeshPointsArg, "collections.abc.Sequence[_mx.Point3]")
// The polygons of create_mesh (MeshFaces): a list or tuple of faces, each the
// 1-based vertex IDs of its corners.
MX_ARG_TYPE(MeshPolygonsArg, "collections.abc.Sequence[collections.abc.Sequence[int]]")

#include <iepoly.h>
#include <iInstanceMgr.h>
#include <mnmesh.h>
#include <triobj.h>
#include <units.h>

#include <array>
#include <cstdint>
#include <cstdio>
#include <limits>
#include <memory>
#include <numeric>
#include <unordered_map>
#include <unordered_set>

namespace mx {
inline namespace MX_ABI_TAG {

using json = nlohmann::json;
using HandlerHelpers::NodeHandle;

constexpr double kPi = 3.14159265358979323846;
constexpr float kInf = std::numeric_limits<float>::infinity();

static bool Finite(const Point3& p) { return std::isfinite(p.x) && std::isfinite(p.y) && std::isfinite(p.z); }

static Point3 Unit(const Point3& p) {
    const float len = Length(p);
    return len > 1e-6f ? p / len : Point3(0, 0, 0);
}

static uint64_t EdgeKey(int u, int v) {
    return (uint64_t(uint32_t(std::min(u, v))) << 32) | uint32_t(std::max(u, v));
}

static nb::object NodeOrNone(unsigned long long handle) {
    INode* node = HandlerHelpers::FindNodeByHandle(handle);
    return InScene(node) ? nb::cast(Wrap(node)) : nb::none();
}

// ── Evaluated mesh ───────────────────────────────────────────────

struct TriMesh {
    std::vector<Point3> verts;
    std::vector<std::array<int, 3>> faces;  // 0-based vertex indices
};

// Geometry that evaluates to triangles; camera and light targets are geometry
// objects without any.
static bool HasMesh(Object* obj) {
    return obj && obj->SuperClassID() == GEOMOBJECT_CLASS_ID && obj->ClassID() != Class_ID(TARGET_CLASS_ID, 0) &&
           obj->CanConvertToType(triObjectClassID);
}

// Frees a TriObject that ConvertToType made for one read.
struct TempTri {
    TriObject* tri;
    bool owned;
    ~TempTri() { if (owned) tri->MaybeAutoDelete(); }
};

// The node's evaluated triangle mesh at t, in world space (as snapshotAsMesh)
// or in the object's own space; nullopt for nodes without one.
static std::optional<TriMesh> EvalMesh(INode* node, TimeValue t, bool world) {
    ObjectState os = node->EvalWorldState(t);
    if (!HasMesh(os.obj)) return std::nullopt;
    auto* tri = static_cast<TriObject*>(os.obj->ConvertToType(t, triObjectClassID));
    if (!tri) return std::nullopt;
    const TempTri temp{tri, tri != os.obj};
    const Mesh& mesh = tri->GetMesh();
    Matrix3 tm;
    tm.IdentityMatrix();
    if (world) tm = node->GetObjTMAfterWSM(t);
    TriMesh out;
    out.verts.reserve(mesh.getNumVerts());
    for (int i = 0; i < mesh.getNumVerts(); ++i) out.verts.push_back(mesh.verts[i] * tm);
    out.faces.reserve(mesh.getNumFaces());
    for (int i = 0; i < mesh.getNumFaces(); ++i) {
        const DWORD* v = mesh.faces[i].v;
        out.faces.push_back({int(v[0]), int(v[1]), int(v[2])});
    }
    return out;
}

static TriMesh RequireMesh(INode* node, TimeValue t, bool world) {
    if (auto mesh = EvalMesh(node, t, world)) return std::move(*mesh);
    throw BadParam("'" + NodeName(node) + "' (" + ClassOf(node) + ") has no mesh; node.mesh_counts is None for such nodes");
}

// The analyses index vertices by face and need finite coordinates.
static void CheckMesh(const TriMesh& mesh, INode* node) {
    for (const Point3& p : mesh.verts)
        if (!Finite(p)) throw MaxError("'" + NodeName(node) + "' has non-finite vertex coordinates");
    const int count = int(mesh.verts.size());
    for (const auto& f : mesh.faces)
        for (int v : f)
            if (v < 0 || v >= count) throw MaxError("'" + NodeName(node) + "' has a triangle with a missing vertex");
}

// What the lists hold, so the signatures say it. A cage list has None in a deleted slot.
using Points = nb::typed<nb::list, AsPoint>;
using CagePointList = nb::typed<nb::list, std::optional<AsPoint>>;
using Triangles = nb::typed<nb::list, nb::typed<nb::tuple, int, int, int>>;

static nb::list PointList(const std::vector<Point3>& points) {
    nb::list out;
    for (const Point3& p : points) out.append(FromPoint3(p));
    return out;
}

static Points NodeVerts(const Node& n, bool world) {
    INode* node = Resolve(n);
    return PointList(RequireMesh(node, Now(), world).verts);
}

static Triangles NodeFaces(const Node& n) {
    INode* node = Resolve(n);
    nb::list out;
    for (const auto& f : RequireMesh(node, Now(), false).faces) out.append(nb::make_tuple(f[0] + 1, f[1] + 1, f[2] + 1));
    return out;
}

// ── Orientation ──────────────────────────────────────────────────

// World bounds of the evaluated object, or nullopt without geometry.
static std::optional<Box3> WorldBox(INode* node, TimeValue t) {
    ObjectState os = node->EvalWorldState(t);
    if (!os.obj) return std::nullopt;
    Matrix3 tm = node->GetObjTMAfterWSM(t);
    Box3 box;
    os.obj->GetDeformBBox(t, box, &tm, FALSE);
    if (box.IsEmpty()) return std::nullopt;
    return box;
}

static nb::dict Orientation(const Node& n) {
    INode* node = Resolve(n);
    const TimeValue t = Now();
    const Matrix3 tm = node->GetNodeTM(t);
    const Point3 pivot = tm.GetTrans();
    nb::dict out;
    out["pivot"] = FromPoint3(pivot);
    out["axes"] = nb::make_tuple(FromPoint3(Unit(tm.GetRow(0))), FromPoint3(Unit(tm.GetRow(1))), FromPoint3(Unit(tm.GetRow(2))));
    out["matrix"] = nb::make_tuple(FromPoint3(tm.GetRow(0)), FromPoint3(tm.GetRow(1)), FromPoint3(tm.GetRow(2)),
                                   FromPoint3(tm.GetRow(3)));
    const std::optional<Box3> box = WorldBox(node, t);
    out["center"] = box ? FromPoint3(box->Center()) : nb::none();
    out["size"] = box ? FromPoint3(box->Width()) : nb::none();
    out["pivot_to_center"] = box ? FromPoint3(box->Center() - pivot) : nb::none();
    return out;
}

// ── Cage: the Editable Poly base ─────────────────────────────────

struct Poly { AnimHandle node = 0; };

enum class Level { Vertex, Edge, Face };

static Level ParseLevel(const std::string& name) {
    const std::string key = Lower(name);
    if (key == "vertex") return Level::Vertex;
    if (key == "edge") return Level::Edge;
    if (key == "face") return Level::Face;
    throw BadParam("level must be 'vertex', 'edge' or 'face', not '" + name + "'");
}

static const char* LevelName(Level level) {
    return level == Level::Vertex ? "vertex" : level == Level::Edge ? "edge" : "face";
}

static const char* Plural(Level level) {
    return level == Level::Vertex ? "vertices" : level == Level::Edge ? "edges" : "faces";
}

// The level's name inside polyop function names: getNumVerts, deleteEdges.
static const char* PolyopName(Level level) {
    return level == Level::Vertex ? "Vert" : level == Level::Edge ? "Edge" : "Face";
}

// The mesh polyop edits for an Editable Poly base, else nullptr.
static MNMesh* CageMesh(INode* node) {
    Object* base = BaseObject(node);
    if (!base || base->SuperClassID() != GEOMOBJECT_CLASS_ID || base->ClassID() != EPOLYOBJ_CLASS_ID) return nullptr;
    auto* epoly = static_cast<EPoly*>(base->GetInterface(EPOLY_INTERFACE));
    return epoly ? epoly->GetMeshPtr() : nullptr;
}

struct Cage {
    INode* node;
    MNMesh* mesh;
};

static Cage ResolveCage(const Poly& p) {
    INode* node = Resolve(Node{p.node});
    MNMesh* mesh = CageMesh(node);
    if (!mesh)
        throw BadParam("'" + NodeName(node) + "' has " + WithArticle(ClassOf(node)) +
                       " base, not an Editable_Poly; node.convert_to_poly() collapses its stack into one");
    return {node, mesh};
}

static int ComponentCount(const MNMesh& m, Level level) {
    return level == Level::Vertex ? m.VNum() : level == Level::Edge ? m.ENum() : m.FNum();
}

static const FlagUser& Component(const MNMesh& m, Level level, int i) {
    if (level == Level::Vertex) return *m.V(i);
    if (level == Level::Edge) return *m.E(i);
    return *m.F(i);
}

// Deleted components keep their slot, and so their ID, until the mesh collapses.
static bool Alive(const MNMesh& m, Level level, int i) { return !Component(m, level, i).GetFlag(MN_DEAD); }

static std::vector<int> ComponentVerts(const MNMesh& m, Level level, int i) {
    if (level == Level::Vertex) return {i};
    if (level == Level::Edge) return {m.E(i)->v1, m.E(i)->v2};
    const MNFace* face = m.F(i);
    return std::vector<int>(face->vtx, face->vtx + face->deg);
}

// Vertex positions in world space (obj.objectTransform) or object space.
static std::vector<Point3> CagePoints(const Cage& c, bool world) {
    Matrix3 tm;
    tm.IdentityMatrix();
    if (world) tm = c.node->GetObjectTM(Now());
    std::vector<Point3> points(c.mesh->VNum());
    for (int i = 0; i < c.mesh->VNum(); ++i) points[i] = c.mesh->V(i)->p * tm;
    return points;
}

static Point3 MeanOf(const std::vector<Point3>& points, const std::vector<int>& verts) {
    Point3 sum(0, 0, 0);
    for (int v : verts) sum += points[v];
    return sum / float(verts.size());
}

// Newell's normal: the area-weighted normal of a possibly nonplanar polygon,
// summed relative to its first vertex for precision far from the origin.
static Point3 FaceNormal(const std::vector<Point3>& points, const MNFace& face) {
    const Point3 origin = points[face.vtx[0]];
    double x = 0, y = 0, z = 0;
    for (int k = 0; k < face.deg; ++k) {
        const Point3 a = points[face.vtx[k]] - origin;
        const Point3 b = points[face.vtx[(k + 1) % face.deg]] - origin;
        x += double(a.y - b.y) * (double(a.z) + b.z);
        y += double(a.z - b.z) * (double(a.x) + b.x);
        z += double(a.x - b.x) * (double(a.y) + b.y);
    }
    const double len = std::sqrt(x * x + y * y + z * z);
    if (len < 1e-12) return Point3(0, 0, 0);
    return Point3(float(x / len), float(y / len), float(z / len));
}

// 0-based slots of 1-based IDs.
static std::vector<int> Slots(const MNMesh& m, Level level, const std::vector<int>& ids) {
    const int count = ComponentCount(m, level);
    std::vector<int> out;
    out.reserve(ids.size());
    for (int id : ids) {
        if (id < 1 || id > count)
            throw BadParam(std::string(LevelName(level)) + " " + std::to_string(id) + " does not exist; the cage has " +
                           std::to_string(count) + " " + Plural(level) + ", numbered from 1");
        out.push_back(id - 1);
    }
    return out;
}

static std::vector<int> AllSlots(const MNMesh& m, Level level) {
    std::vector<int> out(ComponentCount(m, level));
    std::iota(out.begin(), out.end(), 0);
    return out;
}

static std::vector<int> LiveSlots(const MNMesh& m, Level level, std::vector<int> slots) {
    slots.erase(std::remove_if(slots.begin(), slots.end(), [&](int i) { return !Alive(m, level, i); }), slots.end());
    return slots;
}

static nb::list IdList(const std::vector<int>& slots) {
    nb::list out;
    for (int i : slots) out.append(i + 1);
    return out;
}

// One value per slot (all, or the given IDs); None for a deleted slot.
template <typename F>
static nb::list PerComponent(const MNMesh& m, Level level, const std::optional<std::vector<int>>& ids, F&& value) {
    nb::list out;
    for (int i : ids ? Slots(m, level, *ids) : AllSlots(m, level))
        out.append(Alive(m, level, i) ? value(i) : nb::none());
    return out;
}

static nb::typed<nb::tuple, int, int, int> CageCounts(const Poly& p) {
    const Cage c = ResolveCage(p);
    return nb::make_tuple(c.mesh->VNum(), c.mesh->ENum(), c.mesh->FNum());
}

static CagePointList CageVerts(const Poly& p, const std::optional<std::vector<int>>& ids, bool world) {
    const Cage c = ResolveCage(p);
    const std::vector<Point3> points = CagePoints(c, world);
    return PerComponent(*c.mesh, Level::Vertex, ids, [&](int i) { return FromPoint3(points[i]); });
}

static nb::typed<nb::list, std::optional<nb::typed<nb::tuple, int, int>>> CageEdges(const Poly& p, const std::optional<std::vector<int>>& ids) {
    const Cage c = ResolveCage(p);
    return PerComponent(*c.mesh, Level::Edge, ids, [&](int i) -> nb::object {
        return nb::make_tuple(c.mesh->E(i)->v1 + 1, c.mesh->E(i)->v2 + 1);
    });
}

static nb::typed<nb::list, std::optional<nb::typed<nb::list, int>>> CageFaces(const Poly& p, const std::optional<std::vector<int>>& ids) {
    const Cage c = ResolveCage(p);
    return PerComponent(*c.mesh, Level::Face, ids,
                        [&](int i) -> nb::object { return IdList(ComponentVerts(*c.mesh, Level::Face, i)); });
}

static CagePointList CageCenters(const Poly& p, const std::string& levelName, const std::optional<std::vector<int>>& ids,
                            bool world) {
    const Cage c = ResolveCage(p);
    const Level level = ParseLevel(levelName);
    const std::vector<Point3> points = CagePoints(c, world);
    return PerComponent(*c.mesh, level, ids,
                        [&](int i) { return FromPoint3(MeanOf(points, ComponentVerts(*c.mesh, level, i))); });
}

static CagePointList CageNormals(const Poly& p, const std::optional<std::vector<int>>& ids, bool world) {
    const Cage c = ResolveCage(p);
    const std::vector<Point3> points = CagePoints(c, world);
    return PerComponent(*c.mesh, Level::Face, ids, [&](int i) { return FromPoint3(FaceNormal(points, *c.mesh->F(i))); });
}

static std::vector<Node> Instances(const Poly& p) {
    const Cage c = ResolveCage(p);
    INodeTab tab;
    IInstanceMgr::GetInstanceMgr()->GetInstances(*c.node, tab);
    std::vector<Node> out{Wrap(c.node)};
    for (int i = 0; i < tab.Count(); ++i)
        if (tab[i] != c.node && InScene(tab[i])) out.push_back(Wrap(tab[i]));
    return out;
}

static std::string PolyRepr(const Poly& p) {
    RequireMain();
    INode* node = HandlerHelpers::FindNodeByHandle(p.node);
    const MNMesh* mesh = InScene(node) ? CageMesh(node) : nullptr;
    if (!mesh) return "<Poly #" + std::to_string(p.node) + " unavailable>";
    return "<Poly '" + NodeName(node) + "' " + std::to_string(mesh->VNum()) + "v " + std::to_string(mesh->ENum()) + "e " +
           std::to_string(mesh->FNum()) + "f>";
}

// ── find ─────────────────────────────────────────────────────────
// The selection filters of inspect_mesh; every given filter must pass.

struct Filter {
    bool current = false;
    std::optional<std::pair<Point3, Point3>> box;
    std::optional<Point3> nearPoint;
    float radius = 0;
    std::optional<Point3> normal;
    float minDot = 0;
    std::optional<bool> boundary;
    std::optional<float> maxDot;  // sharp edges: their faces' normals differ at least this much
};

static float CosDegrees(double angle, const char* what) {
    if (!std::isfinite(angle) || angle < 0 || angle > 180)
        throw BadParam(std::string(what) + " is an angle from 0 to 180 degrees");
    return float(std::cos(angle * kPi / 180.0));
}

static std::pair<Point3, Point3> ToBox(nb::handle value) {
    std::vector<nb::handle> corners;
    if (IsSequence(value))
        for (nb::handle corner : value) corners.push_back(corner);
    if (corners.size() != 2) throw BadParam("bbox is ((min x, y, z), (max x, y, z)), like node.bbox");
    const Point3 lo = ToPoint3(corners[0], "bbox min"), hi = ToPoint3(corners[1], "bbox max");
    if (!Finite(lo) || !Finite(hi)) throw BadParam("bbox corners must be finite");
    if (lo.x > hi.x || lo.y > hi.y || lo.z > hi.z) throw BadParam("bbox min must not exceed its max");
    return {lo, hi};
}

static Filter MakeFilter(Level level, bool current, nb::handle bbox, nb::handle nearPoint, std::optional<double> radius,
                         nb::handle normal, std::optional<double> angle, std::optional<bool> boundary,
                         std::optional<double> sharp) {
    Filter f;
    f.current = current;
    if (!bbox.is_none()) f.box = ToBox(bbox);
    if (nearPoint.is_none() != !radius) throw BadParam("near and radius go together: find('vertex', near=(0, 0, 50), radius=5)");
    if (radius) {
        if (!std::isfinite(*radius) || *radius <= 0) throw BadParam("radius must be a positive distance");
        f.nearPoint = ToPoint3(nearPoint, "near");
        if (!Finite(*f.nearPoint)) throw BadParam("near must be a finite point");
        f.radius = float(*radius);
    }
    if (angle && normal.is_none()) throw BadParam("angle goes with normal: find('face', normal=(0, 0, 1), angle=20)");
    if (!normal.is_none()) {
        if (level != Level::Face) throw BadParam("normal filters faces: find('face', normal=(0, 0, 1))");
        const Point3 n = ToPoint3(normal, "normal");
        if (!Finite(n) || Length(n) < 1e-9f) throw BadParam("normal must be a nonzero direction");
        f.normal = n / Length(n);
        f.minDot = CosDegrees(angle.value_or(10.0), "angle");
    }
    if ((boundary || sharp) && level != Level::Edge)
        throw BadParam("boundary and sharp filter edges: find('edge', boundary=True)");
    f.boundary = boundary;
    if (sharp) f.maxDot = CosDegrees(*sharp, "sharp");
    return f;
}

static bool Within(const Point3& p, const std::pair<Point3, Point3>& box) {
    const auto& [lo, hi] = box;
    return p.x >= lo.x && p.y >= lo.y && p.z >= lo.z && p.x <= hi.x && p.y <= hi.y && p.z <= hi.z;
}

static bool Passes(const MNMesh& m, const std::vector<Point3>& points, Level level, int i, const Filter& f) {
    if (!Alive(m, level, i)) return false;
    if (f.current && !Component(m, level, i).GetFlag(MN_SEL)) return false;
    if (f.box || f.nearPoint) {
        const Point3 c = MeanOf(points, ComponentVerts(m, level, i));
        if (f.box && !Within(c, *f.box)) return false;
        if (f.nearPoint && Length(c - *f.nearPoint) > f.radius) return false;
    }
    if (f.normal && DotProd(FaceNormal(points, *m.F(i)), *f.normal) < f.minDot) return false;
    const MNEdge* edge = level == Level::Edge ? m.E(i) : nullptr;
    if (f.boundary && (edge->f2 < 0) != *f.boundary) return false;
    if (f.maxDot) {
        if (edge->f2 < 0) return false;
        if (DotProd(FaceNormal(points, *m.F(edge->f1)), FaceNormal(points, *m.F(edge->f2))) > *f.maxDot) return false;
    }
    return true;
}

static nb::typed<nb::list, int> Find(const Poly& p, const std::string& levelName, const std::optional<std::vector<int>>& ids, bool current,
                     BoxOrNone bbox, PointOrNone nearPoint, std::optional<double> radius, PointOrNone normal,
                     std::optional<double> angle, std::optional<bool> boundary, std::optional<double> sharp) {
    const Cage c = ResolveCage(p);
    const Level level = ParseLevel(levelName);
    const Filter f = MakeFilter(level, current, bbox, nearPoint, radius, normal, angle, boundary, sharp);
    const std::vector<Point3> points = CagePoints(c, true);
    std::vector<int> kept;
    for (int i : ids ? Slots(*c.mesh, level, *ids) : AllSlots(*c.mesh, level))
        if (Passes(*c.mesh, points, level, i, f)) kept.push_back(i);
    std::sort(kept.begin(), kept.end());
    kept.erase(std::unique(kept.begin(), kept.end()), kept.end());
    return IdList(kept);
}

// ── native:mesh_script ───────────────────────────────────────────

// #{1,4..9} from 1-based IDs.
static std::string BitsLiteral(std::vector<int> ids) {
    std::sort(ids.begin(), ids.end());
    ids.erase(std::unique(ids.begin(), ids.end()), ids.end());
    std::string out = "#{";
    for (size_t i = 0; i < ids.size();) {
        size_t j = i;
        while (j + 1 < ids.size() && ids[j + 1] == ids[j] + 1) ++j;
        out += (i ? "," : "") + std::to_string(ids[i]) + (j > i ? ".." + std::to_string(ids[j]) : "");
        i = j + 1;
    }
    return out + "}";
}

struct MeshReply {
    unsigned long long handle = 0;
    std::string output;
    std::string token;
};

// One mesh_script call: the script returns "<node handle>\n<output>" and the
// reply carries that node's cage token. With an undo label the call is one
// undo step, rolled back if the script fails; with expected the bridge first
// refuses a cage whose token differs.
static MeshReply RunMeshScript(const std::string& script, const std::string& undo, const std::string& expected = {},
                               unsigned long long handle = 0, const std::string& finish = {}) {
    json args = {{"script", script}};
    if (!undo.empty()) args["undo"] = undo;
    if (!expected.empty()) {
        args["expected_mesh"] = expected;
        args["resolve"] = "\"" + std::to_string(handle) + "\"";
    }
    if (!finish.empty()) args["finish"] = finish;
    json reply;
    {
        NoAnimate noAnimate;
        reply = NativeJson("native:mesh_script", args);
    }
    const auto handleIt = reply.find("handle"), outputIt = reply.find("output"), tokenIt = reply.find("mesh_token");
    if (handleIt == reply.end() || !handleIt->is_number_integer() || outputIt == reply.end() ||
        outputIt->type() != json::value_t::string || tokenIt == reply.end() || tokenIt->type() != json::value_t::string)
        throw MaxError("native:mesh_script returned an incomplete reply; read the mesh again before retrying");
    return {handleIt->get<unsigned long long>(), outputIt->get<std::string>(), tokenIt->get<std::string>()};
}

static std::string CageToken(INode* node) {
    return RunMeshScript("\"" + std::to_string(NodeHandle(node)) + "\\n\"", "").token;
}

// ── edit ─────────────────────────────────────────────────────────

struct OpSpec {
    std::string name;
    std::vector<std::string> keys;  // values besides op, level and selection
    std::vector<Level> levels;      // the first is the default
};

static const std::vector<OpSpec>& Ops() {
    const std::vector<Level> any = {Level::Face, Level::Edge, Level::Vertex};
    static const std::vector<OpSpec> ops = {
        {"select", {}, any},
        {"move", {"offset"}, any},
        {"scale", {"factors", "pivot"}, any},
        {"extrude", {"amount", "mode"}, {Level::Face}},
        {"bevel", {"amount", "outline", "mode"}, {Level::Face}},
        {"inset", {"amount", "mode"}, {Level::Face}},
        {"chamfer", {"amount", "segments"}, {Level::Edge}},
        {"connect", {"segments", "pinch", "slide"}, {Level::Edge}},
        {"bridge", {"segments"}, {Level::Face, Level::Edge}},
        {"cap", {}, {Level::Edge}},
        {"delete", {}, any},
        {"relax", {"amount", "iterations"}, any},
    };
    return ops;
}

// One operation's dict and its name in errors: "operation 2 (chamfer)".
struct OpArgs {
    nb::dict values;
    std::string label;

    bool Has(const char* key) const { return values.contains(key); }

    double Number(const char* key, std::optional<double> fallback = std::nullopt) const {
        if (!Has(key)) {
            if (fallback) return *fallback;
            throw BadParam(label + " needs " + key);
        }
        nb::object v = values[key];
        if (nb::isinstance<nb::bool_>(v) || !(nb::isinstance<nb::int_>(v) || nb::isinstance<nb::float_>(v)))
            throw BadParam(label + ": " + key + " must be a number, not " + TypeNameOf(v));
        const double x = nb::cast<double>(v);
        if (!std::isfinite(x)) throw BadParam(label + ": " + key + " must be finite");
        return x;
    }

    double Positive(const char* key) const {
        const double x = Number(key);
        if (x <= 0) throw BadParam(label + ": " + key + " must be positive");
        return x;
    }

    double Between(const char* key, double fallback, double lo, double hi) const {
        const double x = Number(key, fallback);
        if (x < lo || x > hi) throw BadParam(label + ": " + key + " must be " + ReprOf(nb::float_(lo)) + " to " + ReprOf(nb::float_(hi)));
        return x;
    }

    int Integer(const char* key, int fallback, int lo, int hi) const {
        if (!Has(key)) return fallback;
        nb::object v = values[key];
        if (nb::isinstance<nb::bool_>(v) || !nb::isinstance<nb::int_>(v))
            throw BadParam(label + ": " + key + " must be a whole number, not " + TypeNameOf(v));
        const long long x = nb::cast<long long>(v);
        if (x < lo || x > hi)
            throw BadParam(label + ": " + key + " must be " + std::to_string(lo) + " to " + std::to_string(hi));
        return int(x);
    }

    Point3 Vector(const char* key) const {
        if (!Has(key)) throw BadParam(label + " needs " + key + " (x, y, z)");
        const nb::object v = values[key];
        const Point3 p = ToPoint3(v, label + ": " + key);
        if (!Finite(p)) throw BadParam(label + ": " + key + " must be finite");
        return p;
    }

    std::string Text(const char* key, const char* fallback) const {
        if (!Has(key)) return fallback;
        nb::object v = values[key];
        if (!nb::isinstance<nb::str>(v)) throw BadParam(label + ": " + key + " must be text");
        return Lower(nb::cast<std::string>(v));
    }
};

// `local chosen = <BitArray>` of the operation's live components.
static std::string SelectionScript(const OpArgs& op, Level level) {
    const std::string kind = PolyopName(level);
    const std::string count = "(polyop.getNum" + kind + "s mesh)";
    nb::object sel = op.Has("selection") ? nb::object(op.values["selection"]) : nb::object(nb::str("current"));
    std::string out;
    if (nb::isinstance<nb::str>(sel) && Lower(nb::cast<std::string>(sel)) == "current") {
        out = "local chosen = polyop.get" + kind + "Selection mesh\n";
    } else if (nb::isinstance<nb::str>(sel) && Lower(nb::cast<std::string>(sel)) == "all") {
        out = "local chosen = #{}\nif " + count + " > 0 do chosen = #{1.." + count + "}\n";
    } else if (IsSequence(sel)) {
        std::vector<int> ids;
        try {
            ids = nb::cast<std::vector<int>>(sel);
        } catch (const nb::cast_error&) {
            throw BadParam(op.label + ": selection IDs must be whole numbers");
        }
        if (ids.empty()) throw BadParam(op.label + ": the selection is empty");
        const int top = *std::max_element(ids.begin(), ids.end());
        if (*std::min_element(ids.begin(), ids.end()) < 1) throw BadParam(op.label + ": IDs start at 1");
        out = "local chosen = " + BitsLiteral(ids) + "\n"
              "if " + std::to_string(top) + " > " + count + " do throw (" +
              MxsString(op.label + ": " + LevelName(level) + " " + std::to_string(top) + " does not exist; the cage has ") +
              " + " + count + " as string + " + MxsString(std::string(" ") + Plural(level)) + ")\n";
    } else {
        throw BadParam(op.label + ": selection is a list of IDs, 'current' or 'all'; "
                       "get IDs first: ids = poly.find('face', normal=(0, 0, 1))");
    }
    return out + "chosen -= polyop.getDead" + kind + "s mesh\n";
}

static std::string Affected(Level level) {
    if (level == Level::Vertex) return "local affected = chosen\n";
    return std::string("local affected = polyop.getVertsUsing") + PolyopName(level) + " mesh chosen\n";
}

static int ExtrusionType(const OpArgs& op) {
    const std::string mode = op.Text("mode", "group");
    if (mode == "group") return 0;
    if (mode == "local") return 1;
    if (mode == "polygon") return 2;
    throw BadParam(op.label + ": mode must be 'group', 'local' or 'polygon'");
}

static std::string Fails(const OpArgs& op, const std::string& call, const std::string& what) {
    return "if not (" + call + ") do throw " + MxsString(op.label + ": " + what);
}

// The operation's edit of `chosen`, a port of mesh_edit's.
static std::string OpBody(const std::string& name, const OpArgs& op, Level level) {
    const std::string lv = LevelName(level);
    if (name == "select") return "";
    if (name == "move")
        return Affected(level) + "local invTM = inverse tm\n"
               "for v in affected do polyop.setVert mesh v (((polyop.getVert mesh v) * tm + " +
               MxsPoint(op.Vector("offset")) + ") * invTM)";
    if (name == "scale") {
        const std::string factors = MxsPoint(op.Vector("factors"));
        const std::string pivot = op.Has("pivot")
            ? "local pivot = " + MxsPoint(op.Vector("pivot")) + "\n"
            : "local pivot = [0,0,0]\nfor v in affected do pivot += (polyop.getVert mesh v) * tm\npivot /= affected.numberSet\n";
        return Affected(level) + pivot + "local invTM = inverse tm\n"
               "for v in affected do (local p = (polyop.getVert mesh v) * tm - pivot; polyop.setVert mesh v ((pivot + p * " +
               factors + ") * invTM))";
    }
    if (name == "extrude" || name == "bevel" || name == "inset") {
        const std::string type = "mesh.extrusionType = " + std::to_string(ExtrusionType(op)) + "\n";
        if (name == "extrude") return type + "polyop.extrudeFaces mesh chosen " + MxsNumber(op.Number("amount"));
        if (name == "bevel")
            return type + "polyop.bevelFaces mesh chosen " + MxsNumber(op.Number("amount")) + " " + MxsNumber(op.Number("outline", 0.0));
        return type + "polyop.bevelFaces mesh chosen 0.0 " + MxsNumber(-op.Positive("amount"));
    }
    if (name == "chamfer") {
        const double amount = op.Positive("amount");
        return "mesh.edgeChamferSegments = " + std::to_string(op.Integer("segments", 1, 1, 32)) +
               "\nmesh.edgeChamferSmooth = true\nmesh.EditablePoly.chamferEdges " + MxsNumber(amount) + " open:false";
    }
    if (name == "connect")
        return "mesh.connectEdgeSegments = " + std::to_string(op.Integer("segments", 1, 1, 100)) +
               "\nmesh.connectEdgePinch = " + MxsNumber(op.Between("pinch", 0, -100, 100)) +
               "\nmesh.connectEdgeSlide = " + MxsNumber(op.Between("slide", 0, -100, 100)) + "\n" +
               Fails(op, "mesh.EditablePoly.ConnectEdges()", "the edges could not be connected");
    if (name == "bridge")
        return "mesh.bridgeSegments = " + std::to_string(op.Integer("segments", 1, 1, 100)) + "\nmesh.bridgeSelected = 1\n" +
               Fails(op, "mesh.EditablePoly.bridge selLevel:#" + lv, "the selection could not be bridged");
    if (name == "cap") return Fails(op, "mesh.EditablePoly.capHoles #Edge", "the selection has no border to cap");
    if (name == "delete") return std::string("polyop.delete") + PolyopName(level) + "s mesh chosen";
    // relax
    return "mesh.relaxAmount = " + MxsNumber(op.Between("amount", 0.25, 0, 1)) +
           "\nmesh.relaxIterations = " + std::to_string(op.Integer("iterations", 1, 1, 100)) +
           "\nmesh.relaxHoldBoundaryPoints = true\nmesh.relaxHoldOuterPoints = true\n" +
           Fails(op, "mesh.EditablePoly.Relax selLevel:#" + lv, "the selection could not be relaxed");
}

static const OpSpec& FindOp(const OpArgs& op) {
    std::vector<std::string> names;
    for (const OpSpec& spec : Ops()) names.push_back(spec.name);
    if (!op.Has("op")) throw BadParam(op.label + " needs 'op', one of " + Join(names));
    const std::string name = op.Text("op", "");
    for (const OpSpec& spec : Ops())
        if (spec.name == name) return spec;
    throw BadParam(op.label + ": unknown op '" + name + "'." + Suggest(name, names) + " Ops: " + Join(names));
}

static Level OpLevel(const OpArgs& op, const OpSpec& spec) {
    if (!op.Has("level")) return spec.levels.front();
    const Level level = ParseLevel(op.Text("level", ""));
    if (std::find(spec.levels.begin(), spec.levels.end(), level) == spec.levels.end()) {
        std::vector<std::string> names;
        for (Level l : spec.levels) names.push_back(LevelName(l));
        throw BadParam(op.label + " works on " + Join(names) + " level, not " + LevelName(level));
    }
    return level;
}

static void CheckKeys(const OpArgs& op, const OpSpec& spec) {
    for (nb::handle key : op.values.keys()) {
        const std::string k = nb::cast<std::string>(nb::str(key));
        if (k == "op" || k == "level" || k == "selection") continue;
        if (std::find(spec.keys.begin(), spec.keys.end(), k) == spec.keys.end())
            throw BadParam(op.label + ": unknown key '" + k + "'; " + spec.name + " takes " +
                           (spec.keys.empty() ? std::string("no values") : Join(spec.keys)));
    }
}

struct Step {
    std::string script;
    Level level;
};

// One operation as a block that edits `chosen` and reports
// STEP|op|matched|before counts|after counts.
static Step OpScript(nb::handle item, size_t index) {
    if (!nb::isinstance<nb::dict>(item))
        throw BadParam("operation " + std::to_string(index) + " must be a dict: {'op': 'extrude', 'selection': ids, 'amount': 5}");
    OpArgs op{nb::borrow<nb::dict>(item), "operation " + std::to_string(index)};
    const OpSpec& spec = FindOp(op);
    op.label += " (" + spec.name + ")";
    CheckKeys(op, spec);
    const Level level = OpLevel(op, spec);
    const std::string kind = PolyopName(level);
    const std::string script =
        "(\n" +
        SelectionScript(op, level) +
        "if chosen.numberSet == 0 do throw " + MxsString(op.label + ": the selection has no " + Plural(level)) + "\n"
        "local beforeCounts = mcCounts mesh\n"
        "polyop.set" + kind + "Selection mesh chosen\n" +
        OpBody(spec.name, op, level) + "\n"
        "polyop.collapseDeadStructs mesh\n"
        "update obj\n"
        "format \"STEP|" + spec.name + "|%|%|%\\n\" chosen.numberSet beforeCounts (mcCounts mesh) to:out\n"
        ")\n";
    return {script, level};
}

// tm is obj.objectTransform at Now(), so world offsets follow at().
static std::string EditScript(INode* node, const std::vector<Step>& steps) {
    const std::string h = std::to_string(NodeHandle(node));
    const Matrix3 tm = node->GetObjectTM(Now());
    std::string out =
        "(\n"
        "fn mcCounts mesh = (polyop.getNumVerts mesh as string) + \",\" + (polyop.getNumEdges mesh as string) + \",\" + "
        "(polyop.getNumFaces mesh as string)\n"
        "local obj = getAnimByHandle " + h + "\n"
        "if not isValidNode obj do throw \"the node is no longer in the scene\"\n"
        "if classof obj.baseobject != Editable_Poly do throw \"the base is no longer an Editable_Poly\"\n"
        "local mesh = obj.baseobject\n"
        "local tm = matrix3 " + MxsPoint(tm.GetRow(0)) + " " + MxsPoint(tm.GetRow(1)) + " " +
        MxsPoint(tm.GetRow(2)) + " " + MxsPoint(tm.GetRow(3)) + "\n"
        "local out = stringstream \"\"\n"
        "format \"" + h + "\\n\" to:out\n";
    for (const Step& step : steps) out += step.script;
    return out + "out as string\n)";
}

// Selects the node and shows the last operation's components in the Modify
// panel, switching only what differs: each switch rebuilds the panel.
static std::string ShowScript(Level level) {
    const std::string ui = level == Level::Vertex ? "1" : level == Level::Edge ? "2" : "4";
    return "try (\n"
           "if not (selection.count == 1 and selection[1] == obj) do select obj\n"
           "if getCommandPanelTaskMode() != #modify do max modify mode\n"
           "if modPanel.getCurrentObject() != obj.baseobject do modPanel.setCurrentObject obj.baseobject\n"
           "if subObjectLevel != " + ui + " do subObjectLevel = " + ui + "\n"
           ") catch ()\n"
           "completeredraw()";
}

static nb::tuple CountsTuple(const std::string& text) {
    int v = 0, e = 0, f = 0;
    if (std::sscanf(text.c_str(), "%d,%d,%d", &v, &e, &f) != 3) throw MaxError("unreadable mesh edit counts '" + text + "'");
    return nb::make_tuple(v, e, f);
}

static std::vector<std::string> Split(const std::string& text, char separator) {
    std::vector<std::string> parts(1);
    for (char ch : text) {
        if (ch == separator) parts.emplace_back();
        else if (ch != '\r') parts.back() += ch;
    }
    return parts;
}

static nb::list ParseSteps(const std::string& output) {
    nb::list steps;
    for (const std::string& line : Split(output, '\n')) {
        if (line.rfind("STEP|", 0) != 0) continue;
        const std::vector<std::string> fields = Split(line, '|');
        if (fields.size() != 5) throw MaxError("unreadable mesh edit step '" + line + "'");
        nb::dict step;
        step["op"] = fields[1];
        step["matched"] = std::atoi(fields[2].c_str());
        step["before"] = CountsTuple(fields[3]);
        step["after"] = CountsTuple(fields[4]);
        steps.append(step);
    }
    return steps;
}

static nb::dict Edit(const Poly& p, PolyOpsArg given, const std::string& expected, bool show) {
    const nb::handle ops = given.value;
    const Cage c = ResolveCage(p);
    if (!IsSequence(ops) || nb::len(ops) == 0)
        throw BadParam("edit takes a list of operations: edit([{'op': 'extrude', 'selection': ids, 'amount': 5}])");
    std::vector<Step> steps;
    for (nb::handle item : ops) steps.push_back(OpScript(item, steps.size() + 1));
    if (!expected.empty() && CageToken(c.node) != expected)
        throw BadParam("the cage changed since that token was read; find the IDs and read poly.token again");
    const unsigned long long handle = NodeHandle(c.node);
    const MeshReply reply = RunMeshScript(EditScript(c.node, steps), "MCP mesh edit", expected, handle,
                                          show ? ShowScript(steps.back().level) : std::string());
    nb::dict out;
    out["steps"] = ParseSteps(reply.output);
    out["counts"] = CageCounts(p);
    out["token"] = reply.token;
    return out;
}

// ── Creation and conversion ──────────────────────────────────────

static Poly ConvertToPoly(const Node& n) {
    const std::string h = std::to_string(NodeHandle(Resolve(n)));
    RunMeshScript("(\n"
                  "local obj = getAnimByHandle " + h + "\n"
                  "convertToPoly obj\n"
                  "if classof obj.baseobject != Editable_Poly do throw \"3ds Max could not convert it to an Editable_Poly\"\n"
                  "\"" + h + "\\n\"\n"
                  ")",
                  "MCP convert to poly");
    return Poly{n.handle};
}

static std::vector<Point3> MeshVerts(nb::handle verts) {
    if (!IsSequence(verts))
        throw BadParam("verts is a list of (x, y, z) world positions");
    std::vector<Point3> out;
    for (nb::handle item : verts) {
        const std::string what = "verts[" + std::to_string(out.size()) + "]";
        out.push_back(ToPoint3(item, what));
        if (!Finite(out.back())) throw BadParam(what + " must be finite");
    }
    if (out.size() < 3 || out.size() > 50000) throw BadParam("create_mesh takes 3 to 50000 vertices");
    return out;
}

static std::vector<std::vector<int>> MeshFaces(nb::handle faces, size_t vertCount) {
    if (!IsSequence(faces))
        throw BadParam("faces is a list of polygons, each a list of 1-based vertex IDs");
    std::vector<std::vector<int>> out;
    size_t corners = 0;
    for (nb::handle item : faces) {
        const std::string what = "faces[" + std::to_string(out.size()) + "]";
        std::vector<int> ids;
        try {
            ids = nb::cast<std::vector<int>>(item);
        } catch (const nb::cast_error&) {
            throw BadParam(what + " must be a list of vertex IDs");
        }
        std::vector<int> distinct = ids;
        std::sort(distinct.begin(), distinct.end());
        if (ids.size() < 3 || std::unique(distinct.begin(), distinct.end()) != distinct.end())
            throw BadParam(what + " needs at least three distinct vertex IDs");
        if (distinct.front() < 1 || size_t(distinct.back()) > vertCount)
            throw BadParam(what + " uses a vertex ID outside 1-" + std::to_string(vertCount));
        corners += ids.size();
        out.push_back(std::move(ids));
    }
    if (out.empty() || out.size() > 50000) throw BadParam("create_mesh takes 1 to 50000 faces");
    if (corners > 500000) throw BadParam("create_mesh takes at most 500000 face corners in all");
    return out;
}

static Node CreateMesh(const std::string& name, MeshPointsArg verts, MeshPolygonsArg faces) {
    RequireMain();
    if (name.find_first_not_of(" \t") == std::string::npos) throw BadParam("create_mesh needs a name");
    if (!HandlerHelpers::CollectNodesByName(name).empty())
        throw BadParam("a node named '" + name + "' already exists; choose another name");
    const std::vector<Point3> points = MeshVerts(verts.value);
    const std::vector<std::vector<int>> polygons = MeshFaces(faces.value, points.size());
    Box3 box;
    for (const Point3& p : points) box += p;
    const Point3 center = box.Center();
    std::string pointList, faceList;
    for (const Point3& p : points) pointList += (pointList.empty() ? "" : ",") + MxsPoint(p - center);
    for (const auto& polygon : polygons) {
        std::string ids;
        for (int id : polygon) ids += (ids.empty() ? "" : ",") + std::to_string(id);
        faceList += (faceList.empty() ? "#(" : ",#(") + ids + ")";
    }
    const MeshReply reply = RunMeshScript(
        "(\n"
        "local obj = Editable_Mesh name:" + MxsString(name) + " pos:" + MxsPoint(center) + "\n"
        "convertToPoly obj\n"
        "local mesh = obj.baseobject\n"
        "for p in #(" + pointList + ") do polyop.createVert mesh p\n"
        "local faces = #(" + faceList + ")\n"
        "for i = 1 to faces.count do (\n"
        "local id = polyop.createPolygon mesh faces[i]\n"
        "if id == undefined or id == 0 do throw (\"faces[\" + (i - 1) as string + "
        "\"] could not be created; check its winding against its neighbours\")\n"
        ")\n"
        "update obj\n"
        "(formattedPrint ((getHandleByAnim obj) as integer64) format:\"d\") + \"\\n\"\n"
        ")",
        "MCP create mesh");
    return Node{AnimHandle(reply.handle)};
}

// ── capture and pick ─────────────────────────────────────────────

static Point3 JsonPoint(const json& value) {
    if (value.type() != json::value_t::array || value.size() != 3) throw MaxError("the agent viewport returned a bad point");
    return Point3(value[0].get<float>(), value[1].get<float>(), value[2].get<float>());
}

static nb::dict Capture(const Poly& p, const std::string& levelName, const std::optional<std::vector<int>>& ids) {
    const Cage c = ResolveCage(p);
    const Level level = ParseLevel(levelName);
    const std::vector<int> slots = LiveSlots(*c.mesh, level, ids ? Slots(*c.mesh, level, *ids) : AllSlots(*c.mesh, level));
    if (slots.size() > 100)
        throw BadParam(std::to_string(slots.size()) + " " + Plural(level) + " are too many to label; pass at most 100 IDs from find()");
    const std::vector<Point3> points = CagePoints(c, true);
    const std::string prefix(1, "VEF"[int(level)]);
    json labels = json::array();
    for (int i : slots) {
        const Point3 at = MeanOf(points, ComponentVerts(*c.mesh, level, i));
        labels.push_back({{"point", json::array({at.x, at.y, at.z})}, {"text", prefix + std::to_string(i + 1)}});
    }
    const json reply = NativeJson("native:agent_viewport", {{"action", "capture"}, {"source", "agent"}, {"labels", labels}});
    nb::dict out;
    out["file"] = ToPython(reply.value("file", json()));
    out["view"] = ToPython(reply.value("agent_viewport", json::object()).value("view_token", json()));
    out["width"] = ToPython(reply.value("width", json()));
    out["height"] = ToPython(reply.value("height", json()));
    out["labels"] = labels.size();
    return out;
}

struct Px { double x = 0, y = 0; };

static double SegmentDistance(Px p, Px a, Px b, Px& closest) {
    const double dx = b.x - a.x, dy = b.y - a.y, len2 = dx * dx + dy * dy;
    const double t = len2 == 0 ? 0 : std::clamp(((p.x - a.x) * dx + (p.y - a.y) * dy) / len2, 0.0, 1.0);
    closest = {a.x + t * dx, a.y + t * dy};
    return std::hypot(p.x - closest.x, p.y - closest.y);
}

// Distance to a polygon's outline, or 0 inside it (even-odd, so concave
// interiors count). Geometry only: says nothing about visibility.
static double PolygonDistance(Px p, const std::vector<Px>& polygon, Px& closest) {
    double best = std::numeric_limits<double>::infinity();
    bool inside = false;
    for (size_t i = 0; i < polygon.size(); ++i) {
        const Px a = polygon[i], b = polygon[(i + 1) % polygon.size()];
        Px on;
        const double d = SegmentDistance(p, a, b, on);
        if (d < best) { best = d; closest = on; }
        if ((a.y > p.y) != (b.y > p.y) && p.x < a.x + (p.y - a.y) * (b.x - a.x) / (b.y - a.y)) inside = !inside;
    }
    if (inside) { closest = p; return 0; }
    return best;
}

static double ScreenDistance(Px p, const std::vector<Px>& pixels, Level level) {
    Px closest;
    if (level == Level::Vertex) return std::hypot(p.x - pixels[0].x, p.y - pixels[0].y);
    if (level == Level::Edge) return SegmentDistance(p, pixels[0], pixels[1], closest);
    return PolygonDistance(p, pixels, closest);
}

static float WorldSegment(const Point3& p, const Point3& a, const Point3& b, Point3& closest) {
    const Point3 d = b - a;
    const float len2 = DotProd(d, d);
    const float t = len2 == 0 ? 0.f : std::clamp(DotProd(p - a, d) / len2, 0.f, 1.f);
    closest = a + d * t;
    return Length(p - closest);
}

struct SurfaceMatch {
    float distance;
    bool approximate;
};

// How far a base component lies from the evaluated surface hit. A face uses
// its Newell plane inside its outline, else its outline; a nonplanar or
// degenerate face gives an approximate distance.
static SurfaceMatch SurfaceDistance(const Point3& hit, const std::vector<Point3>& pts, Level level) {
    if (level == Level::Vertex) return {Length(hit - pts[0]), false};
    float outline = kInf;
    Point3 on;
    for (size_t i = 0; i < (level == Level::Edge ? 1 : pts.size()); ++i)
        outline = std::min(outline, WorldSegment(hit, pts[i], pts[(i + 1) % pts.size()], on));
    if (level == Level::Edge) return {outline, false};
    Point3 normal(0, 0, 0), center(0, 0, 0);
    for (size_t i = 0; i < pts.size(); ++i) {
        normal += CrossProd(pts[i] - pts[0], pts[(i + 1) % pts.size()] - pts[0]);
        center += pts[i];
    }
    const float len = Length(normal);
    if (len < 1e-20f) return {outline, true};
    normal /= len;
    center /= float(pts.size());
    float scale = 0;
    for (const Point3& v : pts) scale = std::max(scale, Length(v - center));
    bool approximate = false;
    for (const Point3& v : pts) approximate |= std::fabs(DotProd(v - center, normal)) > std::max(scale * 1e-6f, 1e-12f);
    const float height = DotProd(hit - center, normal);
    const Point3 projected = hit - normal * height;
    // The polygon in the plane of the two axes the normal leans on least.
    const int drop = std::fabs(normal.x) >= std::fabs(normal.y) && std::fabs(normal.x) >= std::fabs(normal.z) ? 0
                     : std::fabs(normal.y) >= std::fabs(normal.z) ? 1 : 2;
    const int u = drop == 0 ? 1 : 0, v = drop == 2 ? 1 : 2;
    std::vector<Px> polygon;
    for (const Point3& q : pts) polygon.push_back({q[u], q[v]});
    Px closest;
    if (PolygonDistance({projected[u], projected[v]}, polygon, closest) == 0) return {std::fabs(height), approximate};
    return {outline, approximate};
}

// Agent-viewport pixels of the given vertices (the others stay empty); each
// request is refused if the view no longer matches the capture.
static std::vector<std::optional<Px>> Project(const std::vector<Point3>& points, const std::vector<int>& verts,
                                              const std::string& view) {
    constexpr size_t kChunk = 2000;  // the bridge's limit per request
    std::vector<std::optional<Px>> out(points.size());
    for (size_t start = 0; start < verts.size(); start += kChunk) {
        const size_t end = std::min(verts.size(), start + kChunk);
        json batch = json::array();
        for (size_t k = start; k < end; ++k)
            batch.push_back(json::array({points[verts[k]].x, points[verts[k]].y, points[verts[k]].z}));
        const json reply = NativeJson("native:agent_viewport",
            {{"action", "project"}, {"source", "agent"}, {"points", batch}, {"expected_view", view}});
        const json pixels = reply.value("pixels", json()), details = reply.value("projections", json());
        if (pixels.type() != json::value_t::array || pixels.size() != end - start)
            throw MaxError("the agent viewport returned an incomplete projection; capture and pick again");
        for (size_t k = start; k < end; ++k) {
            const json& pixel = pixels[k - start];
            const bool behind = details.type() == json::value_t::array && details.size() == pixels.size() &&
                                details[k - start].value("in_front", true) == false;
            if (pixel.type() == json::value_t::array && pixel.size() == 2 && !behind)
                out[verts[k]] = Px{pixel[0].get<double>(), pixel[1].get<double>()};
        }
    }
    return out;
}

struct Candidate {
    std::array<double, 3> key;  // ranked by key, then ID
    int slot;
    nb::dict row;
};

static nb::dict Pick(const Poly& p, double x, double y, const std::string& view, const std::string& levelName,
                     const std::optional<std::vector<int>>& ids, double tolerance, int limit, bool preferSurface) {
    if (!(x >= 0 && x <= 1 && y >= 0 && y <= 1)) throw BadParam("x and y are image coordinates from 0 to 1, top-left origin");
    if (view.empty()) throw BadParam("view is the view token of the agent viewport capture you are looking at");
    if (!(tolerance >= 0 && tolerance <= 0.25)) throw BadParam("tolerance is a fraction of the shorter image side, 0 to 0.25");
    if (limit < 1 || limit > 100) throw BadParam("limit must be 1 to 100");
    const Cage c = ResolveCage(p);
    const MNMesh& m = *c.mesh;
    const Level level = ParseLevel(levelName);
    const std::vector<int> slots = LiveSlots(m, level, ids ? Slots(m, level, *ids) : AllSlots(m, level));

    const json surface = NativeJson("native:agent_viewport",
        {{"action", "pick"}, {"source", "agent"}, {"x", x}, {"y", y}, {"expected_view", view}});
    const int width = surface.value("width", 0), height = surface.value("height", 0);
    if (width < 2 || height < 2) throw MaxError("the agent viewport reported no usable size");
    const json hit = surface.value("hit", json());
    const bool onTarget = hit.is_object() && hit.value("handle", 0ULL) == NodeHandle(c.node);
    const Point3 hitPoint = onTarget ? JsonPoint(hit.at("point")) : Point3(0, 0, 0);
    const Point3 hitNormal = onTarget ? JsonPoint(hit.at("normal")) : Point3(0, 0, 0);

    std::vector<int> needed;
    for (int i : slots)
        for (int v : ComponentVerts(m, level, i)) needed.push_back(v);
    std::sort(needed.begin(), needed.end());
    needed.erase(std::unique(needed.begin(), needed.end()), needed.end());
    const std::vector<Point3> points = CagePoints(c, true);
    const std::vector<std::optional<Px>> pixels = Project(points, needed, view);

    const Px cursor{x * (width - 1), y * (height - 1)};
    const double threshold = tolerance * std::min(width, height);
    std::vector<Candidate> ranked;
    int unprojectable = 0;
    for (int i : slots) {
        const std::vector<int> verts = ComponentVerts(m, level, i);
        std::vector<Px> screen;
        for (int v : verts)
            if (pixels[v]) screen.push_back(*pixels[v]);
        if (screen.size() != verts.size()) { ++unprojectable; continue; }
        const double distance = ScreenDistance(cursor, screen, level);
        if (distance > threshold) continue;
        Candidate cand{{distance, 0, 0}, i, nb::dict()};
        cand.row["id"] = i + 1;
        cand.row["vertices"] = IdList(verts);
        cand.row["center"] = FromPoint3(MeanOf(points, verts));
        cand.row["distance"] = distance;
        const Point3 normal = level == Level::Face ? FaceNormal(points, *m.F(i)) : Point3(0, 0, 0);
        if (level == Level::Face) cand.row["normal"] = FromPoint3(normal);
        if (onTarget) {
            std::vector<Point3> world;
            for (int v : verts) world.push_back(points[v]);
            const SurfaceMatch match = SurfaceDistance(hitPoint, world, level);
            cand.row["surface_distance"] = match.distance;
            if (level == Level::Face) cand.row["approximate"] = match.approximate;
            const double alignment = DotProd(normal, hitNormal);
            cand.key = preferSurface ? std::array<double, 3>{match.distance, distance, -alignment}
                                     : std::array<double, 3>{distance, match.distance, -alignment};
        }
        ranked.push_back(std::move(cand));
    }
    std::sort(ranked.begin(), ranked.end(),
              [](const Candidate& a, const Candidate& b) { return std::tie(a.key, a.slot) < std::tie(b.key, b.slot); });

    nb::list candidates;
    for (size_t k = 0; k < ranked.size() && k < size_t(limit); ++k) candidates.append(ranked[k].row);
    nb::dict out;
    out["candidates"] = candidates;
    out["matched"] = ranked.size();
    nb::object hitOut = nb::none();
    if (hit.is_object()) {
        nb::dict h;
        h["node"] = NodeOrNone(hit.value("handle", 0ULL));
        h["point"] = FromPoint3(JsonPoint(hit.at("point")));
        h["normal"] = FromPoint3(JsonPoint(hit.at("normal")));
        hitOut = h;
    }
    out["hit"] = hitOut;
    out["hit_on_target"] = onTarget;
    out["unprojectable"] = unprojectable;
    out["tolerance_pixels"] = threshold;
    out["token"] = CageToken(c.node);
    return out;
}

// ── mesh_qa: topology of the evaluated triangles ─────────────────
// Connectivity is by vertex index, without welding: vertices that share a
// position but not an index are not connected.

enum Issue { kBoundary, kNonManifold, kWinding, kDegenerate, kDuplicate, kIsolated, kIssues };
const char* const kIssueNames[kIssues] = {"boundary_edges", "non_manifold_edges", "winding_conflicts",
                                          "degenerate_faces", "duplicate_faces", "isolated_vertices"};

struct Findings {
    size_t limit;
    std::array<size_t, kIssues> counts{};
    std::array<nb::list, kIssues> samples;

    // Counts every finding; builds its sample row only while there is room.
    template <typename F>
    void Record(Issue kind, F&& row) {
        if (counts[kind]++ < limit) samples[kind].append(row());
    }
};

struct FaceSets {
    std::vector<int> parent, size;
    explicit FaceSets(size_t n) : parent(n), size(n, 1) { std::iota(parent.begin(), parent.end(), 0); }
    int Root(int i) {
        while (parent[i] != i) i = parent[i] = parent[parent[i]];
        return i;
    }
    void Join(int a, int b) {
        a = Root(a);
        b = Root(b);
        if (a == b) return;
        if (size[a] < size[b]) std::swap(a, b);
        parent[b] = a;
        size[a] += size[b];
    }
};

struct Incidence {
    int edge;
    int face;
    bool forward;  // the face runs the edge from its lower vertex index
};

// Unique edges in first-use order and every face's use of them.
struct EdgeTable {
    std::unordered_map<uint64_t, int> index;
    std::vector<std::pair<int, int>> ends;
    std::vector<Incidence> uses;
    std::vector<size_t> offsets;  // after Group: edge e's uses are uses[offsets[e]..offsets[e + 1])

    void Add(int u, int v, int face) {
        auto [it, fresh] = index.try_emplace(EdgeKey(u, v), int(ends.size()));
        if (fresh) ends.push_back({std::min(u, v), std::max(u, v)});
        uses.push_back({it->second, face, u < v});
    }

    // A stable counting sort: each edge's uses stay in face order.
    void Group() {
        offsets.assign(ends.size() + 1, 0);
        for (const Incidence& use : uses) ++offsets[use.edge + 1];
        for (size_t e = 0; e < ends.size(); ++e) offsets[e + 1] += offsets[e];
        std::vector<size_t> next(offsets.begin(), offsets.end() - 1);
        std::vector<Incidence> sorted(uses.size());
        for (const Incidence& use : uses) sorted[next[use.edge]++] = use;
        uses.swap(sorted);
    }
};

struct TripleHash {
    size_t operator()(const std::array<int, 3>& t) const {
        uint64_t h = uint32_t(t[0]);
        h = h * 0x9E3779B97F4A7C15ULL ^ uint32_t(t[1]);
        h = h * 0x9E3779B97F4A7C15ULL ^ uint32_t(t[2]);
        return size_t(h ^ (h >> 29));
    }
};

struct Qa {
    const TriMesh& mesh;
    Findings findings;
    double epsilon;
    std::vector<double> areas;
    std::vector<char> degenerate, open, nonManifold, winding;
    EdgeTable edges;
    FaceSets sets;

    Qa(const TriMesh& m, size_t limit, double eps)
        : mesh(m), findings{limit}, epsilon(eps), areas(m.faces.size()), degenerate(m.faces.size()),
          open(m.faces.size()), nonManifold(m.faces.size()), winding(m.faces.size()), sets(m.faces.size()) {}

    nb::object Mean(std::initializer_list<int> ids) const {
        double x = 0, y = 0, z = 0;
        for (int i : ids) {
            x += mesh.verts[i].x;
            y += mesh.verts[i].y;
            z += mesh.verts[i].z;
        }
        const double n = double(ids.size());
        return nb::make_tuple(x / n, y / n, z / n);
    }
};

static double TriangleArea(const Point3& a, const Point3& b, const Point3& c) {
    const double abx = b.x - a.x, aby = b.y - a.y, abz = b.z - a.z;
    const double acx = c.x - a.x, acy = c.y - a.y, acz = c.z - a.z;
    const double x = aby * acz - abz * acy, y = abz * acx - abx * acz, z = abx * acy - aby * acx;
    return std::sqrt(x * x + y * y + z * z) * 0.5;
}

// Degenerate and duplicate triangles, and every face's edges.
static void ScanFaces(Qa& qa) {
    std::unordered_map<std::array<int, 3>, int, TripleHash> first;
    first.reserve(qa.mesh.faces.size());
    for (int f = 0; f < int(qa.mesh.faces.size()); ++f) {
        const auto [i, j, k] = qa.mesh.faces[f];
        const double area = TriangleArea(qa.mesh.verts[i], qa.mesh.verts[j], qa.mesh.verts[k]);
        qa.areas[f] = area;
        const bool distinct = i != j && j != k && i != k;
        if (!distinct || area <= qa.epsilon) {
            qa.degenerate[f] = 1;
            qa.findings.Record(kDegenerate, [&] {
                nb::dict row;
                row["face"] = f + 1;
                row["vertices"] = nb::make_tuple(i + 1, j + 1, k + 1);
                row["center"] = qa.Mean({i, j, k});
                row["area"] = area;
                row["reason"] = distinct ? "area_at_or_below_epsilon" : "repeated_vertex";
                return row;
            });
        }
        std::array<int, 3> signature{i, j, k};
        std::sort(signature.begin(), signature.end());
        const auto [it, fresh] = first.try_emplace(signature, f);
        if (!fresh) {
            qa.findings.Record(kDuplicate, [&] {
                nb::dict row;
                row["face"] = f + 1;
                row["other_face"] = it->second + 1;
                row["vertices"] = nb::make_tuple(i + 1, j + 1, k + 1);
                row["center"] = qa.Mean({i, j, k});
                return row;
            });
        }
        // A repeated vertex can walk one edge twice: each face uses an edge once.
        std::vector<uint64_t> seen;
        for (const auto& [u, v] : {std::pair{i, j}, std::pair{j, k}, std::pair{k, i}}) {
            if (u == v || std::find(seen.begin(), seen.end(), EdgeKey(u, v)) != seen.end()) continue;
            seen.push_back(EdgeKey(u, v));
            qa.edges.Add(u, v, f);
        }
    }
}

// Open, non-manifold and opposite-winding edges; joins faces across edges.
static void ScanEdges(Qa& qa) {
    qa.edges.Group();
    const size_t limit = qa.findings.limit;
    for (size_t e = 0; e < qa.edges.ends.size(); ++e) {
        const Incidence* use = qa.edges.uses.data() + qa.edges.offsets[e];
        const size_t count = qa.edges.offsets[e + 1] - qa.edges.offsets[e];
        Issue kind;
        if (count == 2) {
            qa.sets.Join(use[0].face, use[1].face);
            // Neighbours run a shared edge in opposite directions when their winding agrees.
            if (use[0].forward != use[1].forward || qa.degenerate[use[0].face] || qa.degenerate[use[1].face]) continue;
            kind = kWinding;
            qa.winding[use[0].face] = qa.winding[use[1].face] = 1;
        } else if (count == 1) {
            kind = kBoundary;
            qa.open[use[0].face] = 1;
        } else {
            kind = kNonManifold;
            for (size_t k = 0; k < count; ++k) {
                qa.sets.Join(use[0].face, use[k].face);
                qa.nonManifold[use[k].face] = 1;
            }
        }
        qa.findings.Record(kind, [&] {
            const auto [a, b] = qa.edges.ends[e];
            nb::list faces;
            for (size_t k = 0; k < count && k < limit; ++k) faces.append(use[k].face + 1);
            nb::dict row;
            row["vertices"] = nb::make_tuple(a + 1, b + 1);
            row["center"] = qa.Mean({a, b});
            row["faces"] = faces;
            row["face_count"] = count;
            return row;
        });
    }
}

// Edge-connected pieces in order of their first face, with up to limit samples.
static nb::dict Pieces(Qa& qa) {
    struct Piece {
        int firstFace;
        int faces = 0;
        double area = 0;
        bool open = false, nonManifold = false, winding = false, degenerate = false;
    };
    std::unordered_map<int, size_t> byRoot;
    std::vector<Piece> pieces;
    for (int f = 0; f < int(qa.mesh.faces.size()); ++f) {
        const auto [it, fresh] = byRoot.try_emplace(qa.sets.Root(f), pieces.size());
        if (fresh) pieces.push_back(Piece{f});
        Piece& piece = pieces[it->second];
        ++piece.faces;
        piece.area += qa.areas[f];
        piece.open |= qa.open[f] != 0;
        piece.nonManifold |= qa.nonManifold[f] != 0;
        piece.winding |= qa.winding[f] != 0;
        piece.degenerate |= qa.degenerate[f] != 0;
    }
    nb::list samples;
    for (size_t k = 0; k < pieces.size() && k < qa.findings.limit; ++k) {
        const Piece& piece = pieces[k];
        const auto [i, j, v] = qa.mesh.faces[piece.firstFace];
        nb::dict row;
        row["first_face"] = piece.firstFace + 1;
        row["center"] = qa.Mean({i, j, v});
        row["faces"] = piece.faces;
        row["area"] = piece.area;
        row["open"] = piece.open;
        row["non_manifold"] = piece.nonManifold;
        row["winding_conflict"] = piece.winding;
        row["degenerate"] = piece.degenerate;
        samples.append(row);
    }
    nb::dict out;
    out["count"] = pieces.size();
    out["samples"] = samples;
    return out;
}

static void ScanIsolated(Qa& qa) {
    std::vector<char> used(qa.mesh.verts.size());
    for (const auto& f : qa.mesh.faces) used[f[0]] = used[f[1]] = used[f[2]] = 1;
    for (int v = 0; v < int(used.size()); ++v)
        if (!used[v]) qa.findings.Record(kIsolated, [&] {
            nb::dict row;
            row["vertex"] = v + 1;
            row["point"] = FromPoint3(qa.mesh.verts[v]);
            return row;
        });
}

static nb::dict MeshQa(const Node& n, int limit, double areaEpsilon) {
    if (limit < 1 || limit > 100) throw BadParam("limit must be 1 to 100 samples per issue");
    if (!std::isfinite(areaEpsilon) || areaEpsilon < 0) throw BadParam("area_epsilon must be 0 (automatic) or a positive area");
    INode* node = Resolve(n);
    const TriMesh mesh = RequireMesh(node, Now(), true);
    CheckMesh(mesh, node);
    Point3 lo(kInf, kInf, kInf), hi(-kInf, -kInf, -kInf);
    for (const Point3& p : mesh.verts) {
        lo = Point3(std::min(lo.x, p.x), std::min(lo.y, p.y), std::min(lo.z, p.z));
        hi = Point3(std::max(hi.x, p.x), std::max(hi.y, p.y), std::max(hi.z, p.z));
    }
    // Translation independent: tiny triangles at the bbox's scale are degenerate.
    const double diagonal2 = mesh.verts.empty() ? 0.0 : double(LengthSquared(hi - lo));
    const double epsilon = areaEpsilon > 0 ? areaEpsilon : std::max(diagonal2 * 1e-12, 1e-24);

    Qa qa(mesh, size_t(limit), epsilon);
    ScanFaces(qa);
    ScanEdges(qa);
    ScanIsolated(qa);
    nb::dict issues;
    for (int kind = 0; kind < kIssues; ++kind) {
        nb::dict issue;
        issue["count"] = qa.findings.counts[kind];
        issue["samples"] = qa.findings.samples[kind];
        issues[kIssueNames[kind]] = issue;
    }
    nb::dict out;
    out["vertices"] = mesh.verts.size();
    out["triangles"] = mesh.faces.size();
    out["edges"] = qa.edges.ends.size();
    out["bounds"] = mesh.verts.empty() ? nb::none() : nb::object(nb::make_tuple(FromPoint3(lo), FromPoint3(hi)));
    out["area"] = std::accumulate(qa.areas.begin(), qa.areas.end(), 0.0);
    out["area_epsilon"] = epsilon;
    out["issues"] = issues;
    out["components"] = Pieces(qa);
    return out;
}

// ── contacts ─────────────────────────────────────────────────────

struct Aabb {
    Point3 lo{kInf, kInf, kInf}, hi{-kInf, -kInf, -kInf};
    void Grow(const Point3& p) {
        lo = Point3(std::min(lo.x, p.x), std::min(lo.y, p.y), std::min(lo.z, p.z));
        hi = Point3(std::max(hi.x, p.x), std::max(hi.y, p.y), std::max(hi.z, p.z));
    }
};

static float BoxDistance2(const Aabb& b, const Point3& p) {
    float d2 = 0;
    for (int a = 0; a < 3; ++a) {
        const float d = std::max({b.lo[a] - p[a], 0.f, p[a] - b.hi[a]});
        d2 += d * d;
    }
    return d2;
}

// Whether the ray o + t * dir, 0 <= t <= length, meets the box; inv is 1 / dir.
static bool RayMeetsBox(const Point3& o, const Point3& inv, const Aabb& b, float length) {
    float t0 = 0, t1 = length;
    for (int a = 0; a < 3; ++a) {
        float ta = (b.lo[a] - o[a]) * inv[a], tb = (b.hi[a] - o[a]) * inv[a];
        if (ta > tb) std::swap(ta, tb);
        // A NaN (0 * inf on a parallel face) leaves the bounds as they are.
        t0 = std::max(t0, ta);
        t1 = std::min(t1, tb);
        if (t0 > t1) return false;
    }
    return true;
}

// Möller-Trumbore, both sides: the ray parameter of the hit in t.
static bool RayTriangle(const Point3& o, const Point3& dir, const Point3& a, const Point3& b, const Point3& c, float& t) {
    const Point3 e1 = b - a, e2 = c - a, pv = CrossProd(dir, e2);
    const float det = DotProd(e1, pv);
    if (std::fabs(det) < 1e-12f) return false;
    const float inv = 1.0f / det;
    const Point3 s = o - a;
    const float u = DotProd(s, pv) * inv;
    if (u < 0 || u > 1) return false;
    const Point3 q = CrossProd(s, e1);
    const float v = DotProd(dir, q) * inv;
    if (v < 0 || u + v > 1) return false;
    t = DotProd(e2, q) * inv;
    return true;
}

static float Ratio(float num, float den) { return den != 0 ? num / den : 0.f; }

// Ericson's closest point on a triangle, guarded for degenerate ones.
static Point3 ClosestOnTriangle(const Point3& p, const Point3& a, const Point3& b, const Point3& c) {
    const Point3 ab = b - a, ac = c - a, ap = p - a;
    const float d1 = DotProd(ab, ap), d2 = DotProd(ac, ap);
    if (d1 <= 0 && d2 <= 0) return a;
    const Point3 bp = p - b;
    const float d3 = DotProd(ab, bp), d4 = DotProd(ac, bp);
    if (d3 >= 0 && d4 <= d3) return b;
    const float vc = d1 * d4 - d3 * d2;
    if (vc <= 0 && d1 >= 0 && d3 <= 0) return a + ab * Ratio(d1, d1 - d3);
    const Point3 cp = p - c;
    const float d5 = DotProd(ab, cp), d6 = DotProd(ac, cp);
    if (d6 >= 0 && d5 <= d6) return c;
    const float vb = d5 * d2 - d1 * d6;
    if (vb <= 0 && d2 >= 0 && d6 <= 0) return a + ac * Ratio(d2, d2 - d6);
    const float va = d3 * d6 - d5 * d4;
    if (va <= 0 && d4 - d3 >= 0 && d5 - d6 >= 0) return b + (c - b) * Ratio(d4 - d3, (d4 - d3) + (d5 - d6));
    const float sum = va + vb + vc;
    return a + ab * Ratio(vb, sum) + ac * Ratio(vc, sum);
}

// A bounding volume tree over one world mesh's triangles.
class TriTree {
public:
    explicit TriTree(const TriMesh& mesh) : mesh_(mesh), order_(mesh.faces.size()) {
        std::iota(order_.begin(), order_.end(), 0);
        std::vector<Point3> centers(order_.size());
        for (size_t f = 0; f < centers.size(); ++f)
            centers[f] = (Corner(int(f), 0) + Corner(int(f), 1) + Corner(int(f), 2)) / 3.0f;
        if (!order_.empty()) Build(centers, 0, int(order_.size()));
    }

    // Distance from p to the nearest point of the surface.
    float Distance(const Point3& p) const {
        float best2 = kInf;
        int stack[kStack], top = 0;
        if (!cells_.empty()) stack[top++] = 0;
        while (top) {
            const Cell& cell = cells_[stack[--top]];
            if (BoxDistance2(cell.box, p) >= best2) continue;
            if (cell.left < 0) {
                for (int k = cell.first; k < cell.first + cell.count; ++k) {
                    const int f = order_[k];
                    best2 = std::min(best2, LengthSquared(p - ClosestOnTriangle(p, Corner(f, 0), Corner(f, 1), Corner(f, 2))));
                }
                continue;
            }
            const bool leftNearer = BoxDistance2(cells_[cell.left].box, p) < BoxDistance2(cells_[cell.right].box, p);
            stack[top++] = leftNearer ? cell.right : cell.left;  // the nearer child is searched first
            stack[top++] = leftNearer ? cell.left : cell.right;
        }
        return std::sqrt(best2);
    }

    // Whether p is inside the closed surface: an odd number of distinct
    // crossings along two fixed, unrelated directions.
    bool Inside(const Point3& p, float eps) const {
        static const Point3 kDirs[2] = {Unit(Point3(0.5773502f, 0.5812310f, 0.5733181f)),
                                        Unit(Point3(-0.6112f, 0.2317f, 0.7567f))};
        return Crossings(p, kDirs[0], eps) % 2 == 1 && Crossings(p, kDirs[1], eps) % 2 == 1;
    }

    // Distance from a to the first surface hit on the segment a-b.
    std::optional<float> FirstHit(const Point3& a, const Point3& b) const {
        const float length = Length(b - a);
        if (length == 0) return std::nullopt;
        const Point3 dir = (b - a) / length;
        float best = kInf;
        Along(a, dir, length, [&](int f) {
            float t;
            if (RayTriangle(a, dir, Corner(f, 0), Corner(f, 1), Corner(f, 2), t) && t >= 0 && t <= length) best = std::min(best, t);
        });
        if (best == kInf) return std::nullopt;
        return best;
    }

private:
    static constexpr int kLeaf = 4;
    static constexpr int kStack = 64;  // depth of a median-split tree, with room

    struct Cell {
        Aabb box;
        int left = -1, right = -1, first = 0, count = 0;
    };

    const Point3& Corner(int face, int k) const { return mesh_.verts[mesh_.faces[face][k]]; }

    int Build(const std::vector<Point3>& centers, int first, int count) {
        const int id = int(cells_.size());
        cells_.push_back(Cell{});
        Aabb box, spread;
        for (int k = first; k < first + count; ++k) {
            for (int c = 0; c < 3; ++c) box.Grow(Corner(order_[k], c));
            spread.Grow(centers[order_[k]]);
        }
        cells_[id].box = box;
        if (count <= kLeaf) {
            cells_[id].first = first;
            cells_[id].count = count;
            return id;
        }
        const Point3 extent = spread.hi - spread.lo;
        const int axis = extent.x >= extent.y && extent.x >= extent.z ? 0 : extent.y >= extent.z ? 1 : 2;
        const int mid = first + count / 2;
        std::nth_element(order_.begin() + first, order_.begin() + mid, order_.begin() + first + count,
                         [&](int a, int b) { return centers[a][axis] < centers[b][axis]; });
        const int left = Build(centers, first, mid - first);
        const int right = Build(centers, mid, first + count - mid);
        cells_[id].left = left;
        cells_[id].right = right;
        return id;
    }

    template <typename F>
    void Along(const Point3& origin, const Point3& dir, float length, F&& visit) const {
        const Point3 inv(1.0f / dir.x, 1.0f / dir.y, 1.0f / dir.z);
        int stack[kStack], top = 0;
        if (!cells_.empty()) stack[top++] = 0;
        while (top) {
            const Cell& cell = cells_[stack[--top]];
            if (!RayMeetsBox(origin, inv, cell.box, length)) continue;
            if (cell.left < 0) {
                for (int k = cell.first; k < cell.first + cell.count; ++k) visit(order_[k]);
                continue;
            }
            stack[top++] = cell.left;
            stack[top++] = cell.right;
        }
    }

    // Surface crossings past eps along a ray, hits closer than eps counted once.
    int Crossings(const Point3& origin, const Point3& dir, float eps) const {
        std::vector<float> hits;
        Along(origin, dir, kInf, [&](int f) {
            float t;
            if (RayTriangle(origin, dir, Corner(f, 0), Corner(f, 1), Corner(f, 2), t) && t > eps) hits.push_back(t);
        });
        std::sort(hits.begin(), hits.end());
        int count = 0;
        float last = -kInf;
        for (float t : hits)
            if (t - last > eps) {
                ++count;
                last = t;
            }
        return count;
    }

    const TriMesh& mesh_;
    std::vector<int> order_;
    std::vector<Cell> cells_;
};

// A closed mesh has no edge used by exactly one triangle.
static bool Closed(const TriMesh& mesh) {
    std::unordered_map<uint64_t, int> uses;
    uses.reserve(mesh.faces.size() * 2);
    for (const auto& f : mesh.faces)
        for (int k = 0; k < 3; ++k)
            if (f[k] != f[(k + 1) % 3]) ++uses[EdgeKey(f[k], f[(k + 1) % 3])];
    return std::none_of(uses.begin(), uses.end(), [](const auto& use) { return use.second == 1; });
}

// One measurable mesh. Its tree is built when a pair first needs it.
struct Body {
    INode* node;
    TriMesh mesh;
    Aabb box;
    bool closed;
    bool inAgainst;
    bool measured = false;
    std::unique_ptr<TriTree> tree;

    const TriTree& Tree() {
        if (!tree) tree = std::make_unique<TriTree>(mesh);
        return *tree;
    }
};

struct Thresholds {
    float tol, nearGap, eps;
};

// What one mesh's vertices and edges show against another's surface.
struct Side {
    float gap = kInf;  // nearest outside vertex to the surface
    std::optional<Point3> gapAt;
    float depth = 0;  // deepest inside vertex to the surface
    std::optional<Point3> depthAt;
    int inside = 0;
    int crossings = 0;  // edges clear of the surface at both ends that pass through it
    std::optional<Point3> crossAt;
};

static bool InBox(const Point3& p, const Aabb& b, float pad) {
    return p.x >= b.lo.x - pad && p.x <= b.hi.x + pad && p.y >= b.lo.y - pad && p.y <= b.hi.y + pad &&
           p.z >= b.lo.z - pad && p.z <= b.hi.z + pad;
}

static bool StrictlyIn(const Point3& p, const Aabb& b) {
    return p.x > b.lo.x && p.x < b.hi.x && p.y > b.lo.y && p.y < b.hi.y && p.z > b.lo.z && p.z < b.hi.z;
}

static bool BoxesMeet(const Aabb& a, const Aabb& b, float pad) {
    return a.lo.x - pad <= b.hi.x && b.lo.x - pad <= a.hi.x && a.lo.y - pad <= b.hi.y && b.lo.y - pad <= a.hi.y &&
           a.lo.z - pad <= b.hi.z && b.lo.z - pad <= a.hi.z;
}

// a's vertices and edges measured against b's surface. Only vertices within
// near_gap of b's bounds are measured; the others are clear of it.
static Side Measure(const Body& a, Body& b, const Thresholds& th) {
    const TriTree& tree = b.Tree();
    Side side;
    const size_t n = a.mesh.verts.size();
    std::vector<float> dist(n, -1);
    std::vector<char> inside(n, 0);
    for (size_t i = 0; i < n; ++i) {
        const Point3& p = a.mesh.verts[i];
        if (!InBox(p, b.box, th.nearGap)) continue;
        const float d = tree.Distance(p);
        dist[i] = d;
        inside[i] = b.closed && d > th.tol && StrictlyIn(p, b.box) && tree.Inside(p, th.eps);
        if (inside[i]) {
            ++side.inside;
            if (d > side.depth) { side.depth = d; side.depthAt = p; }
        } else if (d < side.gap) {
            side.gap = d;
            side.gapAt = p;
        }
    }
    // No vertex inside: parts that overlap with coplanar faces (two boxes
    // sharing their side planes) put every vertex ON the other surface and
    // every edge IN it, so neither test sees the overlap. Face centers do.
    if (side.inside == 0 && b.closed) {
        for (const auto& f : a.mesh.faces) {
            const Point3 p = (a.mesh.verts[f[0]] + a.mesh.verts[f[1]] + a.mesh.verts[f[2]]) / 3.0f;
            if (!StrictlyIn(p, b.box)) continue;
            const float d = tree.Distance(p);
            if (d <= th.tol || !tree.Inside(p, th.eps)) continue;
            ++side.inside;
            if (d > side.depth) { side.depth = d; side.depthAt = p; }
        }
    }
    const auto clear = [&](int i) { return (dist[i] < 0 || dist[i] > th.tol) && !inside[i]; };
    std::unordered_set<uint64_t> tested;
    for (const auto& f : a.mesh.faces) {
        for (int k = 0; k < 3; ++k) {
            const int i1 = f[k], i2 = f[(k + 1) % 3];
            if (i1 == i2 || !clear(i1) || !clear(i2)) continue;
            const Point3 &p1 = a.mesh.verts[i1], &p2 = a.mesh.verts[i2];
            Aabb span;
            span.Grow(p1);
            span.Grow(p2);
            if (!BoxesMeet(span, b.box, 0) || !tested.insert(EdgeKey(i1, i2)).second) continue;
            if (const std::optional<float> t = tree.FirstHit(p1, p2)) {
                ++side.crossings;
                if (!side.crossAt) side.crossAt = p1 + Unit(p2 - p1) * *t;
            }
        }
    }
    return side;
}

const char* const kStatuses[] = {"penetrating", "intersecting", "touching", "near_gap", "separate"};

// The most severe status first: penetration over crossing, contact over gap.
static int Classify(float depth, int crossings, float gap, const Thresholds& th) {
    if (depth > th.tol) return 0;
    if (crossings > 0) return 1;
    if (gap <= th.tol) return 2;
    if (gap <= th.nearGap) return 3;
    return 4;
}

static nb::object PointOrNone(const std::optional<Point3>& p) { return p ? FromPoint3(*p) : nb::none(); }

struct PairRow {
    int status;
    float depth, gap;
    nb::dict row;
};

static PairRow MeasurePair(Body& a, Body& b, const Thresholds& th) {
    const Side ab = Measure(a, b, th), ba = Measure(b, a, th);
    a.measured = b.measured = true;
    const float depth = std::max(ab.depth, ba.depth), gap = std::min(ab.gap, ba.gap);
    const int crossings = ab.crossings + ba.crossings;
    PairRow out{Classify(depth, crossings, gap, th), depth, gap, nb::dict()};
    out.row["a"] = Wrap(a.node);
    out.row["b"] = Wrap(b.node);
    out.row["status"] = kStatuses[out.status];
    out.row["depth"] = depth;
    out.row["gap"] = gap == kInf ? nb::none() : nb::object(nb::float_(gap));
    out.row["inside_vertices"] = ab.inside + ba.inside;
    out.row["edge_crossings"] = crossings;
    out.row["depth_point"] = PointOrNone(ab.depth >= ba.depth ? ab.depthAt : ba.depthAt);
    out.row["gap_point"] = PointOrNone(ab.gap <= ba.gap ? ab.gapAt : ba.gapAt);
    out.row["crossing_point"] = PointOrNone(ab.crossAt ? ab.crossAt : ba.crossAt);
    return out;
}

static std::vector<INode*> UniqueNodes(nb::handle nodes, const char* what) {
    std::vector<INode*> out;
    for (INode* node : NodeList(nodes, what))
        if (std::find(out.begin(), out.end(), node) == out.end()) out.push_back(node);
    return out;
}

static Thresholds ContactThresholds(double tolerance, double nearGap) {
    if (!std::isfinite(tolerance) || tolerance < 0 || !std::isfinite(nearGap) || nearGap < 0)
        throw BadParam("tolerance and near_gap are distances, 0 for the defaults (0.1 mm and 10 mm)");
    if (tolerance > 0 && nearGap > 0 && nearGap < tolerance) throw BadParam("near_gap must be at least tolerance");
    const double mmPerUnit = GetSystemUnitScale(UNITS_MILLIMETERS);
    const float tol = float(tolerance > 0 ? tolerance : 0.1 / mmPerUnit);
    const float gap = std::max(tol, float(nearGap > 0 ? nearGap : 10.0 / mmPerUnit));
    return {tol, gap, tol * 0.01f};
}

static nb::dict Contacts(NodesArg nodes, NodesOrNone against, double tolerance, double nearGap, bool includeSeparate) {
    RequireMain();
    const Thresholds th = ContactThresholds(tolerance, nearGap);
    const std::vector<INode*> setA = UniqueNodes(nodes, "contacts");
    const std::vector<INode*> setB = against.is_none() ? std::vector<INode*>() : UniqueNodes(against, "contacts against");
    for (INode* node : setB)
        if (std::find(setA.begin(), setA.end(), node) != setA.end())
            throw BadParam("'" + NodeName(node) + "' is in both nodes and against; each node goes in one of them");

    const TimeValue t = Now();
    std::vector<std::unique_ptr<Body>> bodies;
    nb::list skipped;
    std::string skippedText;
    const auto add = [&](INode* node, bool inAgainst) {
        std::optional<TriMesh> mesh = EvalMesh(node, t, true);
        if (mesh && !mesh->faces.empty()) {
            CheckMesh(*mesh, node);
            auto body = std::make_unique<Body>(Body{node, std::move(*mesh), Aabb(), false, inAgainst});
            for (const Point3& p : body->mesh.verts) body->box.Grow(p);
            body->closed = Closed(body->mesh);
            bodies.push_back(std::move(body));
            return;
        }
        nb::dict row;
        row["node"] = Wrap(node);
        row["reason"] = mesh ? "empty_mesh" : "not_mesh";
        row["class"] = ClassOf(node);
        skipped.append(row);
        skippedText += (skippedText.empty() ? "; skipped " : ", ") + NodeName(node) + " (" + (mesh ? "empty" : ClassOf(node)) + ")";
    };
    for (INode* node : setA) add(node, false);
    for (INode* node : setB) add(node, true);

    const auto inB = std::count_if(bodies.begin(), bodies.end(), [](const auto& b) { return b->inAgainst; });
    if (!setB.empty() && (inB == 0 || inB == std::ptrdiff_t(bodies.size())))
        throw BadParam("contacts needs a mesh in nodes and one in against" + skippedText);
    if (bodies.size() < 2) throw BadParam("contacts needs at least two meshes" + skippedText);

    std::vector<std::pair<Body*, Body*>> pairs;
    for (size_t i = 0; i < bodies.size(); ++i)
        for (size_t j = i + 1; j < bodies.size(); ++j) {
            Body &a = *bodies[i], &b = *bodies[j];
            const bool wanted = setB.empty() || a.inAgainst != b.inAgainst;
            if (wanted && BoxesMeet(a.box, b.box, th.nearGap)) pairs.push_back({&a, &b});
        }
    constexpr size_t kMaxPairs = 5000;
    if (pairs.size() > kMaxPairs)
        throw BadParam(std::to_string(pairs.size()) + " pairs to measure is more than " + std::to_string(kMaxPairs) +
                       "; pass fewer nodes, or check them against a few with against=");

    std::vector<PairRow> rows;
    for (auto [a, b] : pairs) rows.push_back(MeasurePair(*a, *b, th));
    std::sort(rows.begin(), rows.end(), [](const PairRow& x, const PairRow& y) {
        return std::tie(x.status, y.depth, x.gap) < std::tie(y.status, x.depth, y.gap);
    });

    std::array<int, std::size(kStatuses)> counts{};
    nb::list shown;
    for (const PairRow& r : rows) {
        ++counts[r.status];
        if (includeSeparate || r.status != 4) shown.append(r.row);
    }
    nb::dict summary;
    for (size_t s = 0; s < counts.size(); ++s) summary[kStatuses[s]] = counts[s];
    nb::list openMeshes;
    for (const auto& body : bodies)
        if (body->measured && !body->closed) openMeshes.append(Wrap(body->node));
    nb::dict out;
    out["tolerance"] = th.tol;
    out["near_gap"] = th.nearGap;
    out["summary"] = summary;
    out["pairs"] = shown;
    out["open_meshes"] = openMeshes;
    out["skipped"] = skipped;
    return out;
}

// ── Binding ──────────────────────────────────────────────────────

void BindMesh(nb::module_& m) {
    nb::class_<Poly>(m, "Poly",
        "An Editable Poly base cage beneath the modifiers: 1-based vertex, edge and face IDs as polyop numbers them; "
        "positions are world space unless world=False.")
        .def_prop_ro("node", [](const Poly& p) { Resolve(Node{p.node}); return Node{p.node}; }, "The node whose base this is.")
        .def_prop_ro("counts", &CageCounts,
            "(vertices, edges, faces), counting deleted slots not yet collapsed, as polyop does.")
        .def_prop_ro("token", [](const Poly& p) { return CageToken(ResolveCage(p).node); },
            "Hash of the cage and its transform: edit(ops, expected=token) refuses IDs read before the mesh changed.")
        .def_prop_ro("instances", &Instances, "Nodes sharing this base, this one first; an edit changes them all.")
        .def("verts", &CageVerts, "ids"_a = nb::none(), "world"_a = true,
            "Vertex positions, all or of the given IDs; None for a deleted slot.")
        .def("edges", &CageEdges, "ids"_a = nb::none(), "(v1, v2) vertex IDs of each edge, all or of the given IDs.")
        .def("faces", &CageFaces, "ids"_a = nb::none(), "Vertex IDs of each face in winding order, all or of the given IDs.")
        .def("centers", &CageCenters, "level"_a, "ids"_a = nb::none(), "world"_a = true,
            "Centers (mean vertex position) of 'vertex', 'edge' or 'face' components, all or of the given IDs.")
        .def("normals", &CageNormals, "ids"_a = nb::none(), "world"_a = true,
            "Unit face normals (Newell; (0, 0, 0) for a degenerate face), all or of the given face IDs.")
        .def("find", &Find, "level"_a, "ids"_a = nb::none(), "current"_a = false, "bbox"_a.none() = nb::none(),
            "near"_a.none() = nb::none(), "radius"_a = nb::none(), "normal"_a.none() = nb::none(), "angle"_a = nb::none(),
            "boundary"_a = nb::none(), "sharp"_a = nb::none(),
            "IDs passing every filter given: among ids, selected (current), world center in bbox or within radius of "
            "near, face normal within angle (default 10) of normal, open edge (boundary), edge whose faces meet at "
            "sharp degrees or more: find('face', normal=(0, 0, 1)), find('edge', sharp=30).")
        .def("edit", &Edit, "ops"_a, "expected"_a = "", "show"_a = false,
            "Run ops as one undo step, rolled back if one fails: edit([{'op': 'extrude', 'selection': ids, "
            "'amount': 10}], expected=token). Each op has op, level ('vertex'/'edge'/'face', defaults to the op's "
            "own), selection (IDs, 'current' (default) or 'all') and values: select; move offset; scale factors, "
            "pivot; extrude amount, mode ('group'/'local'/'polygon'); inset amount, mode; bevel amount, outline, mode; "
            "chamfer amount, segments; connect segments, pinch, slide; bridge segments; cap; delete; relax amount "
            "(0-1), iterations. Offsets, factors and pivots are world space, amounts local units. Returns "
            "{'steps', 'counts', 'token'}; topology edits renumber, so find IDs again. show=True shows the result in "
            "the Modify panel. Keys: {'steps', 'counts', 'token'}.")
        .def("capture", &Capture, "level"_a, "ids"_a = nb::none(),
            "Capture the AGENT VIEWPORT with up to 100 components labeled at their centers (F12, E3, V7); "
            "pass 'view' to pick(). Keys: {'file', 'view', 'width', 'height', 'labels'}.")
        .def("pick", &Pick, "x"_a, "y"_a, "view"_a, "level"_a = "edge", "ids"_a = nb::none(), "tolerance"_a = 0.025,
            "limit"_a = 5, "prefer_surface"_a = true,
            "Components near image point x, y (0-1, top-left) of the agent viewport capture whose token is view, "
            "within tolerance (fraction of the shorter side); ranked by distance to the cursor's surface hit when it "
            "is on this node and prefer_surface, else on screen. Projection does not test visibility. candidates are "
            "{'id', 'vertices', 'center', 'distance', ...} nearest first; hit is the surface point under the cursor, or None. "
            "Keys: {'candidates', 'matched', 'hit', 'hit_on_target', 'unprojectable', 'tolerance_pixels', 'token'}.")
        .def("__repr__", &PolyRepr)
        .def("__eq__", [](const Poly& a, nb::handle b) { return nb::isinstance<Poly>(b) && a.node == nb::cast<Poly>(b).node; }, nb::arg("other").none())
        .def("__hash__", [](const Poly& p) { return (Py_hash_t)p.node; });

    AddProperty<Node>("poly", [](const Node& n) { ResolveCage(Poly{n.handle}); return Poly{n.handle}; },
        "The Editable Poly base cage beneath the modifiers (Poly), for component IDs and edits; BadParam for other bases.");
    AddMethod<Node>("convert_to_poly", &ConvertToPoly,
        "Collapse the modifier stack into an Editable Poly (MAXScript convertToPoly) as one undo step; returns node.poly.");
    AddMethod<Node>("verts", &NodeVerts, "world"_a = true,
        "Vertex positions of the evaluated, triangulated mesh (after modifiers), world space unless world=False; "
        "its IDs are not the cage's.");
    AddMethod<Node>("faces", &NodeFaces,
        "Triangles of the evaluated mesh as 1-based vertex IDs into verts(): "
        "create_mesh('Copy', n.verts(), n.faces()) copies it.");
    AddMethod<Node>("mesh_qa", &MeshQa, "limit"_a = 12, "area_epsilon"_a = 0.0,
        "Topology report of the evaluated mesh: boundary, non-manifold and winding-conflict edges, degenerate and "
        "duplicate triangles, isolated vertices and edge-connected components, each counted with up to limit samples; "
        "area_epsilon 0 scales with the bbox. IDs are the evaluated mesh's. issues holds boundary_edges, "
        "non_manifold_edges, winding_conflicts, degenerate_faces, duplicate_faces and isolated_vertices, each "
        "{'count', 'samples'}, as is components. "
        "Keys: {'vertices', 'triangles', 'edges', 'bounds', 'area', 'area_epsilon', 'issues', 'components'}.");
    AddMethod<Node>("orientation", &Orientation,
        "World orientation: pivot, unit local axes (x, y, z), matrix rows, bbox center and size, pivot_to_center. "
        "Keys: {'pivot', 'axes', 'matrix', 'center', 'size', 'pivot_to_center'}.");

    m.def("create_mesh", &CreateMesh, "name"_a, "verts"_a, "faces"_a,
        "Create an Editable Poly from world vertices and polygons of 1-based vertex IDs, counterclockwise seen from "
        "outside, as one undo step: create_mesh('Tri', [(0, 0, 0), (10, 0, 0), (0, 10, 0)], [(1, 2, 3)]).");
    m.def("contacts", &Contacts, "nodes"_a, "against"_a.none() = nb::none(), "tolerance"_a = 0.0, "near_gap"_a = 0.0,
        "include_separate"_a = false,
        "Evaluated meshes that penetrate, intersect, touch or nearly touch, worst first: contacts(legs, "
        "against=[floor, seat]) checks each leg against those only. tolerance and near_gap default to 0.1 mm and "
        "10 mm in scene units; depth is a lower and gap an upper bound, both from vertices. Nodes without a mesh "
        "are listed in 'skipped'. pairs are {'a', 'b', 'status', 'depth', 'gap', ...} and summary counts them by status. "
        "Keys: {'tolerance', 'near_gap', 'summary', 'pairs', 'open_meshes', 'skipped'}.");
}

} // namespace MX_ABI_TAG
} // namespace mx
