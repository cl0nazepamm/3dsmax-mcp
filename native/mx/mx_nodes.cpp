// Scene nodes: identity, transform, hierarchy, visibility, bounds, creation,
// cloning and selection.
#include "mx_common.h"
#include "mx_collection.h"

#include <triobj.h>

namespace mx {
inline namespace MX_ABI_TAG {

static std::string Repr(const Node& n) {
    INode* node = HandlerHelpers::FindNodeByHandle(n.handle);
    if (!InScene(node)) return "<Node #" + std::to_string(n.handle) + " deleted>";
    return "<Node '" + NodeName(node) + "' #" + std::to_string(n.handle) + " " + ClassOf(node) + ">";
}

// ── Transform ────────────────────────────────────────────────────
// pos is the pivot's world position. rot and scale are the node's own
// rotation and scale tracks (relative to its parent; the world for unparented
// nodes). An Euler rotation keeps its winding: rot = (0, 720, 0) is two turns.

static Control* Controller(INode* node, int part) {
    Control* tm = node->GetTMController();
    if (!tm) throw MaxError("'" + NodeName(node) + "' has no transform controller");
    Control* c = part == 0 ? tm->GetPositionController() : part == 1 ? tm->GetRotationController() : tm->GetScaleController();
    return c;
}

static AsPoint GetPos(const Node& n) { return {Resolve(n)->GetNodeTM(Now()).GetTrans()}; }

static void SetPos(const Node& n, PointArg value) {
    INode* node = Resolve(n);
    const Point3 p = ToPoint3(value.value, "pos");
    const TimeValue t = Now();
    INode* parent = node->GetParentNode();
    Control* pc = Controller(node, 0);
    // Unparented: the position track is the world position; set it directly.
    if (pc && parent && parent->IsRootNode()) {
        Point3 v = p;
        pc->SetValue(t, &v, 1, CTRL_ABSOLUTE);
        return;
    }
    Matrix3 tm = node->GetNodeTM(t);
    tm.SetTrans(p);
    node->SetNodeTM(t, tm);
}

static Control* EulerAxis(Control* rc, int axis) {
    if (!rc) return nullptr;
    return axis == 0 ? rc->GetXController() : axis == 1 ? rc->GetYController() : rc->GetZController();
}

static AsPoint GetRot(const Node& n) {
    INode* node = Resolve(n);
    Control* rc = Controller(node, 1);
    const TimeValue t = Now();
    float angles[3] = {0, 0, 0};
    if (EulerAxis(rc, 0) && EulerAxis(rc, 1) && EulerAxis(rc, 2)) {
        for (int axis = 0; axis < 3; ++axis) {
            Interval valid = FOREVER;
            EulerAxis(rc, axis)->GetValue(t, &angles[axis], valid, CTRL_ABSOLUTE);
        }
    } else if (rc) {
        Quat q;
        Interval valid = FOREVER;
        rc->GetValue(t, &q, valid, CTRL_ABSOLUTE);
        QuatToEuler(q, angles, EULERTYPE_XYZ);
    }
    return {Point3(RadToDeg(angles[0]), RadToDeg(angles[1]), RadToDeg(angles[2]))};
}

static void SetRot(const Node& n, PointArg value) {
    INode* node = Resolve(n);
    const auto deg = Floats(value.value, 3, 3, "rot");
    Control* rc = Controller(node, 1);
    const TimeValue t = Now();
    if (EulerAxis(rc, 0) && EulerAxis(rc, 1) && EulerAxis(rc, 2)) {
        for (int axis = 0; axis < 3; ++axis) {
            float v = DegToRad(deg[axis]);
            EulerAxis(rc, axis)->SetValue(t, &v, 1, CTRL_ABSOLUTE);
        }
        return;
    }
    if (!rc) throw MaxError("'" + NodeName(node) + "' has no rotation track");
    float angles[3] = {DegToRad(deg[0]), DegToRad(deg[1]), DegToRad(deg[2])};
    Quat q;
    EulerToQuat(angles, q, EULERTYPE_XYZ);
    rc->SetValue(t, &q, 1, CTRL_ABSOLUTE);
}

static AsPoint GetScale(const Node& n) {
    Control* sc = Controller(Resolve(n), 2);
    ScaleValue s(Point3(1, 1, 1));
    Interval valid = FOREVER;
    if (sc) sc->GetValue(Now(), &s, valid, CTRL_ABSOLUTE);
    return {s.s};
}

static void SetScale(const Node& n, ScaleArg scale) {
    const nb::handle value = scale.value;
    INode* node = Resolve(n);
    Control* sc = Controller(node, 2);
    if (!sc) throw MaxError("'" + NodeName(node) + "' has no scale track");
    Point3 p = nb::isinstance<nb::float_>(value) || nb::isinstance<nb::int_>(value)
        ? Point3(1, 1, 1) * nb::cast<float>(value) : ToPoint3(value, "scale");
    ScaleValue s(p);
    sc->SetValue(Now(), &s, 1, CTRL_ABSOLUTE);
}

// The world matrix as four rows: the node's X, Y and Z axes, then its position.
static AsMatrix GetTransform(const Node& n) { return {Resolve(n)->GetNodeTM(Now())}; }

// A controller that owns part of the transform (look-at, path, script) keeps
// its part; saying so beats a silent no-op.
static void ApplyTM(INode* node, const Matrix3& wanted, const char* what) {
    const TimeValue t = Now();
    node->SetNodeTM(t, wanted);
    const Matrix3 actual = node->GetNodeTM(t);
    for (int r = 0; r < 4; ++r) {
        const Point3 d = actual.GetRow(r) - wanted.GetRow(r);
        const float tolerance = 1e-3f * std::max(1.0f, Length(wanted.GetRow(r)));
        if (Length(d) > tolerance)
            throw MaxError(std::string(what) + ": a controller on '" + NodeName(node) +
                           "' drives its transform, so the value did not apply");
    }
}

static void SetTransform(const Node& n, MatrixArg value) {
    INode* node = Resolve(n);
    ApplyTM(node, ToMatrix3(value.value, "transform"), "transform");
}

// Turn the node so its -Z axis (the way lights and cameras look) points at a
// world point; position and scale stay.
static void LookAt(const Node& n, PointArg target, std::optional<PointArg> up) {
    INode* node = Resolve(n);
    const Matrix3 now = node->GetNodeTM(Now());
    const Point3 from = now.GetTrans();
    Point3 back = from - ToPoint3(target.value, "target");  // +Z points away from the target
    if (Length(back) < 1e-6f) throw BadParam("look_at: the target is the node's own position");
    back = Normalize(back);
    Point3 hint = up ? ToPoint3(up->value, "up") : (std::fabs(back.z) > 0.999f ? Point3(0, 1, 0) : Point3(0, 0, 1));
    Point3 x = CrossProd(hint, back);
    if (Length(x) < 1e-6f) throw BadParam("look_at: up is parallel to the direction");
    x = Normalize(x);
    const Point3 y = Normalize(CrossProd(back, x));
    Matrix3 tm(TRUE);
    tm.SetRow(0, x * Length(now.GetRow(0)));
    tm.SetRow(1, y * Length(now.GetRow(1)));
    tm.SetRow(2, back * Length(now.GetRow(2)));
    tm.SetRow(3, from);
    ApplyTM(node, tm, "look_at");
}

// World bounding box of the evaluated geometry, or None for nodes without any.
static std::optional<AsBox> BBox(const Node& n) {
    INode* node = Resolve(n);
    const TimeValue t = Now();
    ObjectState os = node->EvalWorldState(t);
    if (!os.obj) return std::nullopt;
    Box3 box;
    Matrix3 tm = node->GetObjectTM(t);
    os.obj->GetDeformBBox(t, box, &tm, FALSE);
    if (box.IsEmpty()) return std::nullopt;
    return AsBox{box};
}

// (vertices, faces) of the evaluated mesh, triangulated, or None.
static std::optional<std::pair<int, int>> MeshCounts(const Node& n) {
    INode* node = Resolve(n);
    const TimeValue t = Now();
    ObjectState os = node->EvalWorldState(t);
    if (!os.obj || os.obj->SuperClassID() != GEOMOBJECT_CLASS_ID || !os.obj->CanConvertToType(triObjectClassID))
        return std::nullopt;
    TriObject* tri = static_cast<TriObject*>(os.obj->ConvertToType(t, triObjectClassID));
    if (!tri) return std::nullopt;
    const int verts = tri->GetMesh().getNumVerts();
    const int faces = tri->GetMesh().getNumFaces();
    if (tri != os.obj) tri->MaybeAutoDelete();
    return std::make_pair(verts, faces);
}

// ── Creation ─────────────────────────────────────────────────────

static Node Create(const std::string& cls, const std::string& name, std::optional<PointArg> pos, nb::kwargs params) {
    RequireMain();
    // Lookups stay inside these superclasses: several classes share a name
    // with a class of another kind, and creating the wrong one breaks the casts.
    ClassDesc* cd = FindClass(cls, {GEOMOBJECT_CLASS_ID, SHAPE_CLASS_ID, HELPER_CLASS_ID, LIGHT_CLASS_ID, CAMERA_CLASS_ID});
    if (!cd)
        throw NotFound("no creatable class '" + cls + "'." + Suggest(cls, ClassNames({GEOMOBJECT_CLASS_ID, SHAPE_CLASS_ID,
                                                                                       HELPER_CLASS_ID, LIGHT_CLASS_ID, CAMERA_CLASS_ID})));

    Interface* ip = GetCOREInterface();
    auto* created = static_cast<Animatable*>(ip->CreateInstance(cd->SuperClassID(), cd->ClassID()));
    if (!created) throw MaxError("3ds Max could not create a " + cls);
    if (created->SuperClassID() != cd->SuperClassID() || created->ClassID() != cd->ClassID()) {
        FreeUnused(created);
        throw MaxError("3ds Max created a different class than " + cls);
    }
    Object* object = static_cast<Object*>(created);
    INode* node = ip->CreateObjectNode(object);
    if (!node) throw MaxError("3ds Max could not create a node for the " + cls);

    MSTR nodeName = name.empty() ? MSTR(cd->ClassName()) : MSTR(Utf8ToWide(name).c_str());
    if (name.empty()) ip->MakeNameUnique(nodeName);
    node->SetName(nodeName.data());

    NoAnimate noAnimate;
    const TimeValue t = Now();
    try {
        if (pos) {
            Matrix3 tm;
            tm.IdentityMatrix();
            tm.SetTrans(ToPoint3(pos->value, "pos"));
            node->SetNodeTM(t, tm);
        }
        for (auto [key, value] : params) SetParam(object, nb::cast<std::string>(key), value, t);
    } catch (...) {
        ip->DeleteNode(node, FALSE);  // a failed create leaves nothing behind
        throw;
    }
    return Wrap(node);
}

static Node Clone(const Node& n, const std::string& mode, const std::string& name, std::optional<PointArg> offset) {
    INode* node = Resolve(n);
    CloneType type;
    if (mode == "copy") type = NODE_COPY;
    else if (mode == "instance") type = NODE_INSTANCE;
    else if (mode == "reference") type = NODE_REFERENCE;
    else throw BadParam("clone mode must be 'copy', 'instance' or 'reference', not '" + mode + "'");
    Point3 shift = offset ? ToPoint3(offset->value, "offset") : Point3(0, 0, 0);
    INodeTab nodes, sources, clones;
    nodes.AppendNode(node);
    NoAnimate noAnimate;
    if (!GetCOREInterface()->CloneNodes(nodes, shift, true, type, &sources, &clones))
        throw MaxError("3ds Max could not clone '" + NodeName(node) + "'");
    INode* clone = nullptr;
    for (int i = 0; i < sources.Count() && i < clones.Count(); ++i)
        if (sources[i] == node) clone = clones[i];
    if (!clone) throw MaxError("3ds Max did not report the clone of '" + NodeName(node) + "'");
    if (!name.empty()) clone->SetName(Utf8ToWide(name).c_str());
    return Wrap(clone);
}

// ── Binding ──────────────────────────────────────────────────────

Track MakeTransformTrack(const Node& n, const std::string& path);

struct Nodes {};
struct Selection {};

static std::vector<Node> SelectedNodes() {
    RequireMain();
    Interface* ip = GetCOREInterface();
    std::vector<Node> out;
    for (int i = 0; i < ip->GetSelNodeCount(); ++i) out.push_back(Wrap(ip->GetSelNode(i)));
    return out;
}

static void SelectNodes(NodesArg nodes, const char* what, bool replace) {
    Interface* ip = GetCOREInterface();
    INodeTab tab;
    for (INode* node : NodeList(nodes, what, true)) tab.AppendNode(node);
    if (replace) ip->ClearNodeSelection(FALSE);
    ip->SelectNodeTab(tab, TRUE, FALSE);
}

void BindNodes(nb::module_& m) {
    nb::class_<Node>(m, "Node", "A scene node, held by handle; NotFound once deleted.")
        .def_prop_ro("handle", [](const Node& n) { return (unsigned long long)n.handle; },
            "The node's animatable handle. In pymxs: rt.getAnimByHandle(node.handle), not maxOps.getNodeByHandle.")
        .def_prop_rw("name",
            [](const Node& n) { return NodeName(Resolve(n)); },
            [](const Node& n, const std::string& name) { Resolve(n)->SetName(Utf8ToWide(name).c_str()); },
            "The node's name; assigning renames it. Names need not be unique: hold the node, or its handle.")
        .def_prop_ro("class_name", [](const Node& n) { return ClassOf(Resolve(n)); },
            "Class of the base object: Sphere, Editable_Poly, ...")
        .def_prop_rw("pos", &GetPos, &SetPos, "World position of the pivot (x, y, z).")
        .def_prop_rw("rot", &GetRot, &SetRot,
            "Rotation track as Euler degrees (x, y, z), relative to the parent; keeps winding past 360.")
        .def_prop_rw("scale", &GetScale, &SetScale, "Scale track (x, y, z), or one number for all axes.")
        .def_prop_rw("transform", &GetTransform, &SetTransform,
            "World matrix as four rows of (x, y, z): the node's X, Y and Z axes, then its position. Setting it "
            "raises when a controller keeps part of the transform.")
        .def("look_at", &LookAt, "target"_a, "up"_a = nb::none(),
            "Turn the node so its -Z axis, the way cameras and lights look, points at a world point: "
            "cam.look_at((0, 0, 100)). Position and scale stay; up defaults to world Z.")
        .def_prop_ro("bbox", &BBox, "World bounding box ((min), (max)) of the evaluated geometry, or None.")
        .def_prop_ro("mesh_counts", &MeshCounts, "(vertices, faces) of the evaluated, triangulated mesh, or None.")
        .def_prop_ro("params", [](const Node& n) { Resolve(n); return Params{n.handle, 0}; },
            "The base object's parameters: node.params.radius = 10.")
        .def_prop_rw("parent",
            [](const Node& n) -> std::optional<Node> {
                INode* parent = Resolve(n)->GetParentNode();
                if (!parent || parent->IsRootNode()) return std::nullopt;
                return Wrap(parent);
            },
            [](const Node& n, std::optional<Node> parent) {
                INode* node = Resolve(n);
                INode* target = parent ? Resolve(*parent) : GetCOREInterface()->GetRootNode();
                target->AttachChild(node, 1);
            },
            "Parent node or None; reparenting keeps the world transform.")
        .def_prop_ro("children", [](const Node& n) {
            INode* node = Resolve(n);
            std::vector<Node> out;
            for (int i = 0; i < node->NumberOfChildren(); ++i) out.push_back(Wrap(node->GetChildNode(i)));
            return out;
        }, "The nodes parented to this one, one level down.")
        .def_prop_rw("hidden",
            [](const Node& n) { return Resolve(n)->IsHidden() != 0; },
            [](const Node& n, bool hidden) { Resolve(n)->Hide(hidden ? TRUE : FALSE); },
            "Whether the node is hidden; assign to hide or show it.")
        .def_prop_ro("layer", [](const Node& n) { return HandlerHelpers::NodeLayerName(Resolve(n)); })
        .def_prop_ro("exists", [](const Node& n) {
            RequireMain();
            return InScene(HandlerHelpers::FindNodeByHandle(n.handle));
        }, "False once the node is deleted.")
        .def_prop_ro("modifiers", &NodeModifiers, "Modifier stack, top first.")
        .def("add_modifier", &AddModifier, "cls"_a, "name"_a = "", "params"_a,
            "Add a modifier on top of the stack: add_modifier('Bend', angle=30, direction=90).")
        .def_prop_rw("material", &NodeMaterial, &SetNodeMaterial, "The node's material, or None.")
        .def("track", &MakeTransformTrack, "path"_a,
            "Transform track: 'pos', 'pos.x'..'pos.z', 'rot.x'..'rot.z' (degrees), 'scale'.")
        .def("clone", &Clone, "mode"_a = "copy", "name"_a = "", "offset"_a = nb::none(),
            "Copy, instance or reference the node (and its children), moved by offset.")
        .def("delete", [](const Node& n) { GetCOREInterface()->DeleteNode(Resolve(n), FALSE); }, "Delete the node.")
        .def("__repr__", &Repr)
        .def("__eq__", [](const Node& a, nb::handle b) {
            return nb::isinstance<Node>(b) && a.handle == nb::cast<Node>(b).handle;
        }, nb::arg("other").none())
        .def("__hash__", [](const Node& n) { return (Py_hash_t)n.handle; });

    BindCollection<Nodes, Node, NodeArg>(m, "nodes", "Nodes",
        "The scene's nodes. nodes['Ball'] is one by name, handle or Node; nodes('Wall*', cls='Box', hidden=False) "
        "lists those every filter matches; nodes.new('Sphere', 'Ball', radius=10) creates one and "
        "nodes.remove(nodes) deletes.",
        [] {
            RequireMain();
            std::vector<Node> out;
            for (INode* node : AllNodes()) out.push_back(Wrap(node));
            return out;
        },
        [](const NodeArg& key) { return FindNode(key); })
        .def("__call__", [](const Nodes&, const std::optional<std::string>& pattern,
                            const std::optional<std::string>& cls, const std::optional<std::string>& layer,
                            bool rootsOnly, const std::optional<std::string>& uses, nb::kwargs props) {
            return FindNodes(pattern, cls, layer, rootsOnly, uses, props);
        }, "pattern"_a = nb::none(), "cls"_a = nb::none(), "layer"_a = nb::none(), "roots_only"_a = false,
           "uses"_a = nb::none(), "props"_a,
           "Nodes matching every filter, all of them without one: nodes('Wall*', cls='Box', layer='Walls', "
           "uses='TurboSmooth', hidden=False, height=50). Keywords compare node flags (hidden, frozen, renderable, "
           "cast_shadows, material, ...) or base object parameters; numbers within 1e-4, text ignoring case.")
        .def("new", [](const Nodes&, const std::string& cls, const std::string& name, std::optional<PointArg> pos,
                       nb::kwargs params) { return Create(cls, name, pos, params); },
             "cls"_a, "name"_a = "", "pos"_a = nb::none(), "params"_a,
             "Create an object (geometry, shape, helper, light, camera) with parameters: "
             "nodes.new('Sphere', 'Ball', pos=(0, 0, 10), radius=10, segs=32).")
        .def("remove", [](const Nodes&, NodesArg nodes) {
            for (INode* node : NodeList(nodes, "nodes.remove", true)) GetCOREInterface()->DeleteNode(node, FALSE);
        }, "nodes"_a, "Delete a node or a list of nodes; an empty list deletes nothing.");

    BindCollection<Selection, Node, NodeArg>(m, "selection", "Selection",
        "The selected nodes. selection() lists them, `node in selection` and len(selection) read the state; "
        "selection.set(nodes) selects these and no others, add and remove change it, clear() empties it.",
        &SelectedNodes,
        [](const NodeArg& key) {
            const Node node = FindNode(key);
            INode* live = Resolve(node);
            if (!live->Selected()) throw NotFound(Quoted(live) + " is not selected");
            return node;
        })
        .def("__call__", [](const Selection&, const std::optional<std::string>& pattern) {
            std::vector<Node> out;
            for (const Node& node : SelectedNodes())
                if (!pattern || HandlerHelpers::WildcardMatch(NodeName(Resolve(node)), *pattern)) out.push_back(node);
            return out;
        }, "pattern"_a = nb::none(), "The selected nodes, or those a wildcard name matches: selection('Wall*').")
        .def("set", [](const Selection&, NodesArg nodes) { SelectNodes(nodes, "selection.set", true); }, "nodes"_a,
             "Select these nodes and no others; set([]) clears the selection.")
        .def("add", [](const Selection&, NodesArg nodes) { SelectNodes(nodes, "selection.add", false); }, "nodes"_a,
             "Add nodes to the selection.")
        .def("remove", [](const Selection&, NodesArg nodes) {
            for (INode* node : NodeList(nodes, "selection.remove", true)) GetCOREInterface()->DeSelectNode(node);
        }, "nodes"_a, "Take nodes out of the selection; they stay in the scene.")
        .def("clear", [](const Selection&) {
            RequireMain();
            GetCOREInterface()->ClearNodeSelection(FALSE);
        }, "Deselect everything.");
}

} // namespace MX_ABI_TAG
} // namespace mx
