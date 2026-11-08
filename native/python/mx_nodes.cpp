// Scene nodes: identity, transform, hierarchy, visibility, bounds, creation,
// cloning and selection.
#include "mx_common.h"

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

static nb::object GetPos(const Node& n) { return FromPoint3(Resolve(n)->GetNodeTM(Now()).GetTrans()); }

static void SetPos(const Node& n, nb::handle value) {
    INode* node = Resolve(n);
    const Point3 p = ToPoint3(value, "pos");
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

static nb::object GetRot(const Node& n) {
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
    return nb::make_tuple(RadToDeg(angles[0]), RadToDeg(angles[1]), RadToDeg(angles[2]));
}

static void SetRot(const Node& n, nb::handle value) {
    INode* node = Resolve(n);
    const auto deg = Floats(value, 3, 3, "rot");
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

static nb::object GetScale(const Node& n) {
    Control* sc = Controller(Resolve(n), 2);
    ScaleValue s(Point3(1, 1, 1));
    Interval valid = FOREVER;
    if (sc) sc->GetValue(Now(), &s, valid, CTRL_ABSOLUTE);
    return FromPoint3(s.s);
}

static void SetScale(const Node& n, nb::handle value) {
    INode* node = Resolve(n);
    Control* sc = Controller(node, 2);
    if (!sc) throw MaxError("'" + NodeName(node) + "' has no scale track");
    Point3 p = nb::isinstance<nb::float_>(value) || nb::isinstance<nb::int_>(value)
        ? Point3(1, 1, 1) * nb::cast<float>(value) : ToPoint3(value, "scale");
    ScaleValue s(p);
    sc->SetValue(Now(), &s, 1, CTRL_ABSOLUTE);
}

// World bounding box of the evaluated geometry, or None for nodes without any.
static nb::object BBox(const Node& n) {
    INode* node = Resolve(n);
    const TimeValue t = Now();
    ObjectState os = node->EvalWorldState(t);
    if (!os.obj) return nb::none();
    Box3 box;
    Matrix3 tm = node->GetObjectTM(t);
    os.obj->GetDeformBBox(t, box, &tm, FALSE);
    if (box.IsEmpty()) return nb::none();
    return nb::make_tuple(FromPoint3(box.pmin), FromPoint3(box.pmax));
}

// (vertices, faces) of the evaluated mesh, triangulated, or None.
static nb::object MeshCounts(const Node& n) {
    INode* node = Resolve(n);
    const TimeValue t = Now();
    ObjectState os = node->EvalWorldState(t);
    if (!os.obj || os.obj->SuperClassID() != GEOMOBJECT_CLASS_ID || !os.obj->CanConvertToType(triObjectClassID))
        return nb::none();
    TriObject* tri = static_cast<TriObject*>(os.obj->ConvertToType(t, triObjectClassID));
    if (!tri) return nb::none();
    const int verts = tri->GetMesh().getNumVerts();
    const int faces = tri->GetMesh().getNumFaces();
    if (tri != os.obj) tri->MaybeAutoDelete();
    return nb::make_tuple(verts, faces);
}

// ── Creation ─────────────────────────────────────────────────────

static Node Create(const std::string& cls, const std::string& name, nb::handle pos, nb::kwargs params) {
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
        created->DeleteThis();
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
        if (!pos.is_none()) {
            Matrix3 tm;
            tm.IdentityMatrix();
            tm.SetTrans(ToPoint3(pos, "pos"));
            node->SetNodeTM(t, tm);
        }
        for (auto [key, value] : params) SetParam(object, nb::cast<std::string>(key), value, t);
    } catch (...) {
        ip->DeleteNode(node, FALSE);  // a failed create leaves nothing behind
        throw;
    }
    return Wrap(node);
}

static Node Clone(const Node& n, const std::string& mode, const std::string& name, nb::handle offset) {
    INode* node = Resolve(n);
    CloneType type;
    if (mode == "copy") type = NODE_COPY;
    else if (mode == "instance") type = NODE_INSTANCE;
    else if (mode == "reference") type = NODE_REFERENCE;
    else throw BadParam("clone mode must be 'copy', 'instance' or 'reference', not '" + mode + "'");
    Point3 shift = offset.is_none() ? Point3(0, 0, 0) : ToPoint3(offset, "offset");
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

void BindNodes(nb::module_& m) {
    nb::class_<Node>(m, "Node", "A scene node, held by handle; NotFound once deleted.")
        .def_prop_ro("handle", [](const Node& n) { return (unsigned long long)n.handle; })
        .def_prop_rw("name",
            [](const Node& n) { return NodeName(Resolve(n)); },
            [](const Node& n, const std::string& name) { Resolve(n)->SetName(Utf8ToWide(name).c_str()); })
        .def_prop_ro("class_name", [](const Node& n) { return ClassOf(Resolve(n)); },
            "Class of the base object: Sphere, Editable_Poly, ...")
        .def_prop_rw("pos", &GetPos, &SetPos, "World position of the pivot (x, y, z).")
        .def_prop_rw("rot", &GetRot, &SetRot,
            "Rotation track as Euler degrees (x, y, z), relative to the parent; keeps winding past 360.")
        .def_prop_rw("scale", &GetScale, &SetScale, "Scale track (x, y, z), or one number for all axes.")
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
        })
        .def_prop_rw("hidden",
            [](const Node& n) { return Resolve(n)->IsHidden() != 0; },
            [](const Node& n, bool hidden) { Resolve(n)->Hide(hidden ? TRUE : FALSE); })
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
        })
        .def("__hash__", [](const Node& n) { return (Py_hash_t)n.handle; });

    m.def("node", &FindNode, "key"_a, "Node by name, handle or Node; NotFound or Ambiguous otherwise.");
    m.def("nodes", [](const std::optional<std::string>& pattern) {
        RequireMain();
        std::vector<Node> out;
        for (INode* node : AllNodes())
            if (!pattern || HandlerHelpers::WildcardMatch(NodeName(node), *pattern)) out.push_back(Wrap(node));
        return out;
    }, "pattern"_a = nb::none(), "Scene nodes, optionally filtered by a wildcard name: nodes('Wall*').");
    m.def("create", &Create, "cls"_a, "name"_a = "", "pos"_a = nb::none(), "params"_a,
          "Create an object (geometry, shape, helper, light, camera) with parameters: "
          "create('Sphere', 'Ball', pos=(0, 0, 10), radius=10, segs=32).");
    m.def("delete", [](nb::handle nodes) {
        for (INode* node : NodeList(nodes, "delete")) GetCOREInterface()->DeleteNode(node, FALSE);
    }, "nodes"_a, "Delete a node or a list of nodes.");
    m.def("select", [](nb::handle nodes, bool add) {
        Interface* ip = GetCOREInterface();
        INodeTab tab;
        for (INode* node : NodeList(nodes, "select")) tab.AppendNode(node);
        if (!add) ip->ClearNodeSelection(FALSE);
        ip->SelectNodeTab(tab, TRUE, FALSE);
    }, "nodes"_a, "add"_a = false, "Select nodes, replacing the selection unless add=True.");
    m.def("selection", [] {
        RequireMain();
        Interface* ip = GetCOREInterface();
        std::vector<Node> out;
        for (int i = 0; i < ip->GetSelNodeCount(); ++i) out.push_back(Wrap(ip->GetSelNode(i)));
        return out;
    }, "Selected nodes.");
}

} // namespace MX_ABI_TAG
} // namespace mx
