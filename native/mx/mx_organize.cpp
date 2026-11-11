// Scene organization: layers, groups, named selection sets and State Sets.
//
// Layers are held by anim handle and groups by their head node's handle.
// Named selection sets have no handle, so they are held by name. State Sets
// live in a .NET plugin without a C++ API; they are read through MAXScript as
// snapshots.
#include "mx_common.h"

#include <ilayermanager.h>
#include <INamedSelectionSetManager.h>

#include <cstdlib>
#include <unordered_map>

namespace mx {
inline namespace MX_ABI_TAG {

using json = nlohmann::json;

static std::string Counted(size_t n, const char* noun) {
    return std::to_string(n) + " " + noun + (n == 1 ? "" : "s");
}

static INodeTab ToTab(const std::vector<INode*>& nodes) {
    INodeTab tab;
    for (INode* node : nodes) tab.AppendNode(node);
    return tab;
}

static std::vector<Node> WrapAll(const std::vector<INode*>& nodes) {
    std::vector<Node> out;
    for (INode* node : nodes) out.push_back(Wrap(node));
    return out;
}

// ── Layers ───────────────────────────────────────────────────────

struct LayerRef { AnimHandle layer = 0; };

static ILayerManagerEx* LayerManager() {
    RequireMain();
    ILayerManager* manager = GetCOREInterface13()->GetLayerManager();
    if (!manager) throw MaxError("3ds Max's layer manager is not available");
    return static_cast<ILayerManagerEx*>(manager);  // the SDK's documented way to the Ex interface
}

static std::string LayerName(ILayer* layer) { return WideToUtf8(layer->GetName().data()); }
static LayerRef WrapLayer(ILayer* layer) { return LayerRef{Animatable::GetHandleByAnim(layer)}; }

static std::vector<ILayer*> AllLayers() {
    ILayerManagerEx* manager = LayerManager();
    std::vector<ILayer*> out;
    for (int i = 0; i < manager->GetLayerCount(); ++i)
        if (ILayer* layer = manager->GetLayer(i)) out.push_back(layer);
    return out;
}

// The live layer, or null. A deleted layer can outlive its deletion in the
// undo buffer, so it must also still be in the layer manager.
static ILayer* LiveLayer(AnimHandle handle) {
    const std::vector<ILayer*> all = AllLayers();
    Animatable* anim = Animatable::GetAnimByHandle(handle);
    if (!anim || anim->SuperClassID() != LAYER_CLASS_ID) return nullptr;
    ILayer* layer = static_cast<ILayer*>(anim);
    return std::find(all.begin(), all.end(), layer) != all.end() ? layer : nullptr;
}

static ILayer* ResolveLayer(const LayerRef& r) {
    ILayer* layer = LiveLayer(r.layer);
    if (!layer) throw NotFound("layer #" + std::to_string(r.layer) + " was deleted");
    return layer;
}

// Layer names are unique ignoring case (ILayerManager::CreateLayer).
static ILayer* LayerByName(const std::string& name) {
    const std::wstring wanted = Utf8ToWide(name);
    for (ILayer* layer : AllLayers())
        if (_wcsicmp(layer->GetName().data(), wanted.c_str()) == 0) return layer;
    return nullptr;
}

static ILayer* RequireLayer(const std::string& name) {
    if (ILayer* layer = LayerByName(name)) return layer;
    std::vector<std::string> names;
    for (ILayer* layer : AllLayers()) names.push_back(LayerName(layer));
    throw NotFound("no layer named '" + name + "'." + Suggest(name, names) + " Create it with mx.layer('" + name +
                   "', create=True).");
}

// A Layer or a layer name.
static ILayer* FindLayer(nb::handle key) {
    if (nb::isinstance<LayerRef>(key)) return ResolveLayer(nb::cast<LayerRef>(key));
    if (!nb::isinstance<nb::str>(key)) throw BadParam("expected a Layer or a layer name, not " + TypeNameOf(key));
    return RequireLayer(nb::cast<std::string>(key));
}

// Nodes on the layer itself, not on its child layers.
static std::vector<INode*> LayerNodes(ILayer* layer) {
    Tab<INode*> nodes;
    if (auto* props = static_cast<ILayerProperties*>(layer->GetInterface(LAYERPROPERTIES_INTERFACE))) props->Nodes(nodes);
    std::vector<INode*> out;
    for (int i = 0; i < nodes.Count(); ++i)
        if (InScene(nodes[i])) out.push_back(nodes[i]);
    return out;
}

static ILayer* NodeLayer(INode* node) {
    ReferenceTarget* ref = node->GetReference(NODE_LAYER_REF);
    if (!ref || ref->SuperClassID() != LAYER_CLASS_ID) throw MaxError(Quoted(node) + " has no layer");
    return static_cast<ILayer*>(ref);
}

static void MoveToLayer(ILayer* layer, const std::vector<INode*>& nodes) {
    std::vector<std::string> kept;
    for (INode* node : nodes) {
        layer->AddToLayer(node);
        if (NodeLayer(node) != layer) kept.push_back(Quoted(node));
    }
    if (!kept.empty()) throw MaxError("3ds Max kept " + Join(kept) + " off layer '" + LayerName(layer) + "'");
}

static LayerRef GetLayer(const std::optional<std::string>& name, bool create) {
    ILayerManagerEx* manager = LayerManager();
    if (!name) {
        if (create) throw BadParam("create=True needs a layer name");
        ILayer* current = manager->GetCurrentLayer();
        if (!current) throw MaxError("3ds Max has no current layer");
        return WrapLayer(current);
    }
    if (!create) return WrapLayer(RequireLayer(*name));
    if (ILayer* existing = LayerByName(*name)) return WrapLayer(existing);
    if (name->empty()) throw BadParam("a layer needs a name");
    ILayer* layer = manager->CreateLayer(MSTR(Utf8ToWide(*name).c_str()));
    if (!layer) throw MaxError("3ds Max could not create layer '" + *name + "'");
    return WrapLayer(layer);
}

static void RenameLayer(const LayerRef& r, const std::string& name) {
    ILayer* layer = ResolveLayer(r);
    const std::string old = LayerName(layer);
    if (layer == LayerManager()->GetRootLayer()) throw BadParam("layer '0' cannot be renamed");
    if (name.empty()) throw BadParam("a layer name cannot be empty");
    ILayer* existing = LayerByName(name);
    if (existing && existing != layer) throw BadParam("a layer named '" + LayerName(existing) + "' already exists");
    layer->SetName(MSTR(Utf8ToWide(name).c_str()));
    if (LayerName(layer) != name) throw MaxError("3ds Max did not rename layer '" + old + "' to '" + name + "'");
}

static AsColor LayerColor(const LayerRef& r) {
    const DWORD c = ResolveLayer(r)->GetWireColor();
    return {GetRValue(c) / 255.0f, GetGValue(c) / 255.0f, GetBValue(c) / 255.0f};
}

static void SetLayerColor(const LayerRef& r, ColorArg color) {
    const nb::handle value = color.value;
    ILayer* layer = ResolveLayer(r);
    const std::vector<float> rgb = Floats(value, 3, 3, "color");
    if (std::any_of(rgb.begin(), rgb.end(), [](float x) { return x < 0.0f || x > 1.0f; }))
        throw BadParam("color takes 0-1 floats (divide 0-255 values by 255), got " + ReprOf(value));
    auto byte = [](float x) { return (BYTE)std::lround(x * 255.0f); };
    layer->SetWireColor(RGB(byte(rgb[0]), byte(rgb[1]), byte(rgb[2])));
}

static std::optional<LayerRef> LayerParent(const LayerRef& r) {
    ILayer* parent = ResolveLayer(r)->GetParentLayer();
    if (!parent) return std::nullopt;
    return WrapLayer(parent);
}

static void SetLayerParent(const LayerRef& r, nb::handle value) {
    ILayer* layer = ResolveLayer(r);
    ILayer* parent = value.is_none() ? nullptr : FindLayer(value);
    if (layer->GetParentLayer() == parent) return;
    for (ILayer* above = parent; above; above = above->GetParentLayer())
        if (above == layer)
            throw BadParam("layer '" + LayerName(layer) + "' cannot go under itself or one of its child layers");
    layer->SetParentLayer(parent);
    if (layer->GetParentLayer() != parent)
        throw MaxError("3ds Max refused to move layer '" + LayerName(layer) + "' " +
                       (parent ? "under '" + LayerName(parent) + "'" : std::string("to the top level")));
}

static std::vector<LayerRef> LayerChildren(const LayerRef& r) {
    ILayer* layer = ResolveLayer(r);
    std::vector<LayerRef> out;
    for (int i = 0; i < layer->GetNumOfChildLayers(); ++i)
        if (ILayer* child = layer->GetChildLayer(i)) out.push_back(WrapLayer(child));
    return out;
}

static void SetCurrentLayer(const LayerRef& r, bool on) {
    ILayer* layer = ResolveLayer(r);
    ILayerManagerEx* manager = LayerManager();
    const bool current = manager->GetCurrentLayer() == layer;
    if (on == current) return;
    if (!on) throw BadParam("a layer stops being current when another one becomes current: mx.layer('0').current = True");
    manager->SetCurrentLayer(layer);
    if (manager->GetCurrentLayer() != layer)
        throw MaxError("3ds Max did not make '" + LayerName(layer) + "' the current layer");
}

static void DeleteLayer(const LayerRef& r) {
    ILayer* layer = ResolveLayer(r);
    ILayerManagerEx* manager = LayerManager();
    const std::string name = LayerName(layer);
    if (layer == manager->GetRootLayer()) throw BadParam("layer '0' cannot be deleted");
    if (layer == manager->GetCurrentLayer())
        throw BadParam("'" + name + "' is the current layer; make another one current first: mx.layer('0').current = True");
    if (const size_t count = LayerNodes(layer).size())
        throw BadParam("layer '" + name + "' still holds " + Counted(count, "node") + "; move them first: mx.layer('0').add("
                       "mx.layer('" + name + "').nodes)");
    if (manager->DoesLayerHierarchyContainNodes(layer))
        throw BadParam("a child layer of '" + name + "' still holds nodes; move them or delete that layer first");
    if (!manager->DeleteLayer(layer)) throw MaxError("3ds Max refused to delete layer '" + name + "'");
}

static std::string LayerRepr(const LayerRef& r) {
    ILayer* layer = LiveLayer(r.layer);
    if (!layer) return "<Layer #" + std::to_string(r.layer) + " deleted>";
    std::string out = "<Layer '" + LayerName(layer) + "' " + Counted(LayerNodes(layer).size(), "node");
    if (layer == LayerManager()->GetCurrentLayer()) out += " current";
    if (layer->IsHidden(false)) out += " hidden";
    if (layer->IsFrozen(false)) out += " frozen";
    return out + ">";
}

// On/off layer properties, each the layer's own state.
struct LayerFlag {
    const char* name;
    bool (*get)(ILayer*);
    void (*set)(ILayer*, bool);
    const char* doc;
};

static const LayerFlag kLayerFlags[] = {
    {"hidden", [](ILayer* l) { return l->IsHidden(false); }, [](ILayer* l, bool on) { l->Hide(on); },
     "Hidden; a hidden parent layer also hides it."},
    {"frozen", [](ILayer* l) { return l->IsFrozen(false); }, [](ILayer* l, bool on) { l->Freeze(on); },
     "Frozen; a frozen parent layer also freezes it."},
    {"renderable", [](ILayer* l) { return l->Renderable(); }, [](ILayer* l, bool on) { l->SetRenderable(on); },
     "Whether its objects render."},
    {"box_mode", [](ILayer* l) { return l->GetBoxMode(); }, [](ILayer* l, bool on) { l->BoxMode(on); },
     "Display its objects as bounding boxes."},
    {"backface_cull", [](ILayer* l) { return l->GetBackCull(); }, [](ILayer* l, bool on) { l->BackCull(on); },
     "Hide back faces in the viewports."},
    {"all_edges", [](ILayer* l) { return l->GetAllEdges(); }, [](ILayer* l, bool on) { l->AllEdges(on); },
     "Show every edge, hidden ones included."},
    {"vertex_ticks", [](ILayer* l) { return l->GetVertTicks(); }, [](ILayer* l, bool on) { l->VertTicks(on); },
     "Show vertices as ticks."},
    {"trajectory", [](ILayer* l) { return l->GetTrajectory(); }, [](ILayer* l, bool on) { l->Trajectory(on); },
     "Show motion trajectories."},
    {"xray", [](ILayer* l) { return l->HasXRayMtl(); }, [](ILayer* l, bool on) { l->XRayMtl(on); },
     "See-through display."},
    {"cast_shadows", [](ILayer* l) { return l->CastShadows(); }, [](ILayer* l, bool on) { l->SetCastShadows(on); },
     "Its objects cast shadows."},
    {"receive_shadows", [](ILayer* l) { return l->RcvShadows(); }, [](ILayer* l, bool on) { l->SetRcvShadows(on); },
     "Its objects receive shadows."},
    {"primary_visibility", [](ILayer* l) { return l->GetPrimaryVisibility(); },
     [](ILayer* l, bool on) { l->SetPrimaryVisibility(on); }, "Visible to the camera."},
    {"secondary_visibility", [](ILayer* l) { return l->GetSecondaryVisibility(); },
     [](ILayer* l, bool on) { l->SetSecondaryVisibility(on); }, "Visible to reflections and refractions."},
};

static void BindLayers(nb::module_& m) {
    nb::class_<LayerRef> cls(m, "Layer", "A scene layer: layer.add(nodes), layer.hidden = True, layer.parent = 'Building'.");
    cls.def_prop_rw("name", [](const LayerRef& r) { return LayerName(ResolveLayer(r)); }, &RenameLayer,
            "The layer's name; assigning renames it (names are unique, ignoring case).")
        .def_prop_rw("color", &LayerColor, &SetLayerColor, "Wire color (r, g, b), 0-1.")
        .def_prop_rw("parent", &LayerParent, &SetLayerParent, nb::for_setter(nb::arg("parent").none()),
            "Parent layer or None; assign a Layer, a layer name or None (top level).")
        .def_prop_ro("children", &LayerChildren, "Child layers.")
        .def_prop_ro("nodes", [](const LayerRef& r) { return WrapAll(LayerNodes(ResolveLayer(r))); },
            "Nodes on this layer, not on its child layers; mx.select(layer.nodes) selects them.")
        .def_prop_rw("current", [](const LayerRef& r) { return LayerManager()->GetCurrentLayer() == ResolveLayer(r); },
            &SetCurrentLayer, "Whether new objects go on this layer; assign True to make it current.")
        .def("add", [](const LayerRef& r, NodesArg nodes) { MoveToLayer(ResolveLayer(r), NodeList(nodes, "layer.add")); },
            "nodes"_a, "Move nodes onto this layer: layer.add(mx.nodes('Wall*')).")
        .def("delete", &DeleteLayer, "Delete the layer; it must hold no nodes and not be current.")
        .def("__repr__", &LayerRepr)
        .def("__eq__", [](const LayerRef& a, nb::handle b) {
            return nb::isinstance<LayerRef>(b) && a.layer == nb::cast<LayerRef>(b).layer;
        }, nb::arg("other").none())
        .def("__hash__", [](const LayerRef& r) { return (Py_hash_t)r.layer; });
    for (const LayerFlag& flag : kLayerFlags)
        cls.def_prop_rw(flag.name,
            [get = flag.get](const LayerRef& r) { return get(ResolveLayer(r)); },
            [set = flag.set](const LayerRef& r, bool on) { set(ResolveLayer(r), on); },
            flag.doc);

    m.def("layer", &GetLayer, "name"_a = nb::none(), "create"_a = false,
          "Layer by name, ignoring case; no name: the current layer. layer('Walls', create=True) makes a missing one.");
    m.def("layers", [] {
        std::vector<LayerRef> out;
        for (ILayer* layer : AllLayers()) out.push_back(WrapLayer(layer));
        return out;
    }, "Every layer, in the layer manager's order.");

    AddProperty<Node>("layer",
        [](const Node& n) { return WrapLayer(NodeLayer(Resolve(n))); },
        [](const Node& n, nb::handle layer) { MoveToLayer(FindLayer(layer), {Resolve(n)}); },
        "The node's layer; assign a Layer or a layer name to move it: node.layer = 'Walls'.");
}

// ── Groups ───────────────────────────────────────────────────────
// A group is a head node (a dummy) whose member nodes move with it. Members
// can sit in a hierarchy below the head; a nested group is one member.

struct GroupRef : Node {};

// The group head a member belongs to, or null.
static INode* GroupOf(INode* node) {
    if (!node->IsGroupMember()) return nullptr;
    for (INode* parent = node->GetParentNode(); parent && !parent->IsRootNode(); parent = parent->GetParentNode())
        if (parent->IsGroupHead()) return parent;
    return nullptr;
}

// Parents come before their children; `nested` also enters nested groups.
static void CollectMembers(INode* parent, std::vector<INode*>& out, bool nested) {
    for (int i = 0; i < parent->NumberOfChildren(); ++i) {
        INode* child = parent->GetChildNode(i);
        if (!child->IsGroupMember()) continue;
        out.push_back(child);
        if (nested || !child->IsGroupHead()) CollectMembers(child, out, nested);
    }
}

static std::vector<INode*> Members(INode* head, bool nested = false) {
    std::vector<INode*> out;
    CollectMembers(head, out, nested);
    return out;
}

static GroupRef WrapGroup(INode* head) { return GroupRef{Wrap(head)}; }

// A nested group head as a Group, any other member as a Node.
static nb::object WrapMember(INode* node) {
    return node->IsGroupHead() ? nb::cast(WrapGroup(node)) : nb::cast(Wrap(node));
}

static INode* ResolveGroup(const GroupRef& g) {
    INode* head = Resolve(g);
    if (!head->IsGroupHead()) throw NotFound(Quoted(head) + " is no longer a group");
    return head;
}

static GroupRef ToGroup(nb::handle key) {
    INode* node = Resolve(FindNode(key));
    if (node->IsGroupHead()) return WrapGroup(node);
    INode* group = GroupOf(node);
    throw BadParam(Quoted(node) + " is not a group head" + (group ? "; it is in group " + Quoted(group) + " (node.group)" : ""));
}

static GroupRef CreateGroup(NodesArg nodes, const std::string& name) {
    INodeTab tab = ToTab(NodeList(nodes, "group"));
    Interface* ip = GetCOREInterface();
    MSTR groupName(name.empty() ? L"Group" : Utf8ToWide(name).c_str());
    if (name.empty()) ip->MakeNameUnique(groupName);
    NoAnimate noAnimate;
    // Always named: without a name, GroupNodes asks the user for one.
    INode* head = ip->GroupNodes(&tab, &groupName, FALSE);
    if (!head) throw MaxError("3ds Max could not group the nodes");
    return WrapGroup(head);
}

static void SetGroupOpen(INode* head, bool open) {
    if ((head->IsOpenGroupHead() != 0) == open) return;
    INodeTab tab;
    tab.AppendNode(head);
    if (open) GetCOREInterface()->OpenGroup(&tab, FALSE);
    else GetCOREInterface()->CloseGroup(&tab, FALSE);
    if ((head->IsOpenGroupHead() != 0) != open)
        throw MaxError(std::string("3ds Max did not ") + (open ? "open" : "close") + " group " + Quoted(head));
}

// Throws `message` plus the nodes whose membership in `head` is not `wanted`.
static void CheckMembers(INode* head, const std::vector<INode*>& nodes, bool wanted, const std::string& message) {
    std::vector<std::string> wrong;
    for (INode* node : nodes)
        if ((GroupOf(node) == head) != wanted) wrong.push_back(Quoted(node));
    if (!wrong.empty()) throw MaxError(message + Join(wrong));
}

static bool IsAncestor(INode* node, INode* of) {
    for (INode* parent = of->GetParentNode(); parent; parent = parent->GetParentNode())
        if (parent == node) return true;
    return false;
}

static void AddToGroup(const GroupRef& g, NodesArg nodes) {
    INode* head = ResolveGroup(g);
    std::vector<INode*> adding;
    for (INode* node : NodeList(nodes, "group.add")) {
        if (node == head) throw BadParam("a group cannot join itself");
        if (IsAncestor(node, head)) throw BadParam(Quoted(node) + " is above group " + Quoted(head) + ", so it cannot join it");
        INode* group = GroupOf(node);
        if (group == head) continue;
        if (group)
            throw BadParam(Quoted(node) + " is in group " + Quoted(group) + "; take it out first: node.group.remove(node)");
        adding.push_back(node);
    }
    if (adding.empty()) return;
    INodeTab tab = ToTab(adding);
    NoAnimate noAnimate;
    if (!GetCOREInterface()->AttachNodesToGroup(tab, *head))
        throw MaxError("3ds Max could not add the nodes to group " + Quoted(head));
    CheckMembers(head, adding, true, "3ds Max did not add these to group " + Quoted(head) + ": ");
}

static void RemoveFromGroup(const GroupRef& g, NodesArg nodes) {
    INode* head = ResolveGroup(g);
    const std::string name = Quoted(head);
    const std::vector<INode*> removing = NodeList(nodes, "group.remove");
    for (INode* node : removing)
        if (GroupOf(node) != head) throw BadParam(Quoted(node) + " is not in group " + name);
    INodeTab tab = ToTab(removing);
    NoAnimate noAnimate;
    // Max detaches only from open groups: a closed one is opened for it and closed again.
    const bool closed = !head->IsOpenGroupHead();
    if (closed) SetGroupOpen(head, true);
    GetCOREInterface()->DetachNodesFromGroup(tab);
    // Look the head up again: Max may delete a group that lost its last member.
    INode* still = HandlerHelpers::FindNodeByHandle(g.handle);
    if (closed && InScene(still) && still->IsGroupHead()) SetGroupOpen(still, false);
    CheckMembers(head, removing, false, "3ds Max left these in group " + name + ": ");
}

static nb::typed<nb::list, Node> Ungroup(const GroupRef& g) {
    INode* head = ResolveGroup(g);
    const std::string name = Quoted(head);
    std::vector<AnimHandle> members;
    for (INode* node : Members(head)) members.push_back(Animatable::GetHandleByAnim(node));
    INodeTab tab;
    tab.AppendNode(head);
    {
        NoAnimate noAnimate;
        GetCOREInterface()->UngroupNodes(&tab);
    }
    INode* still = HandlerHelpers::FindNodeByHandle(g.handle);
    if (InScene(still) && still->IsGroupHead()) throw MaxError("3ds Max did not ungroup " + name);
    nb::list out;
    for (AnimHandle handle : members)
        if (INode* node = HandlerHelpers::FindNodeByHandle(handle); InScene(node)) out.append(WrapMember(node));
    return out;
}

// Deleting only the head would leave its members at the root still flagged as
// group members; MAXScript's delete also takes a closed group's members.
// Members go first, by handle: Max deletes a group head once its last member
// is gone, so a saved pointer to a nested head would dangle.
static void DeleteGroup(const GroupRef& g) {
    std::vector<AnimHandle> handles;
    for (INode* node : Members(ResolveGroup(g), true)) handles.push_back(Animatable::GetHandleByAnim(node));
    handles.insert(handles.begin(), g.handle);
    Interface* ip = GetCOREInterface();
    for (auto it = handles.rbegin(); it != handles.rend(); ++it)
        if (INode* node = HandlerHelpers::FindNodeByHandle(*it); InScene(node)) ip->DeleteNode(node, FALSE);
}

static std::string GroupRepr(const GroupRef& g) {
    RequireMain();
    INode* head = HandlerHelpers::FindNodeByHandle(g.handle);
    if (!InScene(head) || !head->IsGroupHead()) return "<Group #" + std::to_string(g.handle) + " deleted>";
    return "<Group " + Quoted(head) + " " + Counted(Members(head).size(), "member") +
           (head->IsOpenGroupHead() ? " open>" : " closed>");
}

static void BindGroups(nb::module_& m) {
    nb::class_<GroupRef, Node>(m, "Group",
        "A group: a Node whose members move with it. Group(node) wraps an existing group head (Node, name or handle).")
        .def("__init__", [](GroupRef* self, nb::handle node) { new (self) GroupRef(ToGroup(node)); }, "node"_a,
            "The group headed by node (a Node, name or handle): mx.Group('Chairs').")
        .def_prop_ro("members", [](const GroupRef& g) -> nb::typed<nb::list, Node> {
            nb::list out;
            for (INode* node : Members(ResolveGroup(g))) out.append(WrapMember(node));
            return out;
        }, "Member nodes; a nested group is one member, as a Group.")
        .def_prop_rw("open", [](const GroupRef& g) { return ResolveGroup(g)->IsOpenGroupHead() != 0; },
            [](const GroupRef& g, bool open) { NoAnimate noAnimate; SetGroupOpen(ResolveGroup(g), open); },
            "Whether the group is open, so its members can be picked and edited one by one.")
        .def("add", &AddToGroup, "nodes"_a, "Add nodes to the group: group.add(mx.node('Lamp')).")
        .def("remove", &RemoveFromGroup, "nodes"_a, "Take nodes out of the group; they move to the scene root.")
        .def("ungroup", &Ungroup, "Dissolve the group (its head is deleted); returns the former members.")
        .def("delete", &DeleteGroup, "Delete the group with all its members, nested groups included; ungroup() keeps them.")
        .def("__repr__", &GroupRepr);

    m.def("group", &CreateGroup, "nodes"_a, "name"_a = "",
          "Group nodes under a new group head: group(mx.nodes('Chair*'), 'Chairs').");
    m.def("groups", [] {
        RequireMain();
        std::vector<GroupRef> out;
        for (INode* node : AllNodes())
            if (node->IsGroupHead()) out.push_back(WrapGroup(node));
        return out;
    }, "Every group in the scene, nested ones included.");

    AddProperty<Node>("group", [](const Node& n) -> std::optional<GroupRef> {
        INode* group = GroupOf(Resolve(n));
        if (!group) return std::nullopt;
        return WrapGroup(group);
    }, "The group this node is in, or None.");
}

// ── Named selection sets ─────────────────────────────────────────

struct SelectionSetRef { std::string name; };

static INamedSelectionSetManager* NamedSets() {
    RequireMain();
    INamedSelectionSetManager* sets = INamedSelectionSetManager::GetInstance();
    if (!sets) throw MaxError("3ds Max's named selection sets are not available");
    return sets;
}

static std::string SetName(int index) {
    const MCHAR* name = NamedSets()->GetNamedSelSetName(index);
    return name ? WideToUtf8(name) : std::string();
}

static std::vector<std::string> SetNames() {
    std::vector<std::string> names;
    for (int i = 0; i < NamedSets()->GetNumNamedSelSets(); ++i) names.push_back(SetName(i));
    return names;
}

// The set's index: the exact name, else the only one that differs in case; -1 if none.
static int FindSet(const std::string& name) {
    INamedSelectionSetManager* sets = NamedSets();
    const std::wstring wanted = Utf8ToWide(name);
    int caseless = -1, matches = 0;
    for (int i = 0; i < sets->GetNumNamedSelSets(); ++i) {
        const MCHAR* setName = sets->GetNamedSelSetName(i);
        if (!setName) continue;
        if (wanted == setName) return i;
        if (_wcsicmp(setName, wanted.c_str()) == 0) {
            caseless = i;
            ++matches;
        }
    }
    if (matches > 1)
        throw Ambiguous(std::to_string(matches) + " selection sets are named '" + name + "' in different cases; give the exact name");
    return caseless;
}

static int RequireSet(const std::string& name) {
    const int index = FindSet(name);
    if (index < 0)
        throw NotFound("no selection set named '" + name + "'." + Suggest(name, SetNames()) +
                       " Create it with mx.selection_set('" + name + "', nodes).");
    return index;
}

static std::vector<INode*> SetNodes(int index) {
    INamedSelectionSetManager* sets = NamedSets();
    std::vector<INode*> out;
    for (int i = 0; i < sets->GetNamedSelSetItemCount(index); ++i)
        if (INode* node = sets->GetNamedSelSetItem(index, i); InScene(node)) out.push_back(node);
    return out;
}

static SelectionSetRef GetSelectionSet(const std::string& name, NodesOrNone nodes) {
    if (nodes.is_none()) return SelectionSetRef{SetName(RequireSet(name))};
    if (name.empty()) throw BadParam("a selection set needs a name");
    INodeTab tab = ToTab(NodeList(nodes, "selection_set"));
    INamedSelectionSetManager* sets = NamedSets();
    const int index = FindSet(name);
    MSTR setName(Utf8ToWide(name).c_str());
    const BOOL ok = index >= 0 ? sets->ReplaceNamedSelSet(tab, index) : sets->AddNewNamedSelSet(tab, setName);
    if (!ok) throw MaxError("3ds Max could not store selection set '" + name + "'");
    return SelectionSetRef{SetName(RequireSet(name))};
}

static void SetSetNodes(const SelectionSetRef& s, NodesArg nodes) {
    INodeTab tab = ToTab(NodeList(nodes, "selection set nodes"));
    if (!NamedSets()->ReplaceNamedSelSet(tab, RequireSet(s.name)))
        throw MaxError("3ds Max could not replace the nodes of selection set '" + s.name + "'");
}

static void RenameSet(SelectionSetRef& s, const std::string& name) {
    const int index = RequireSet(s.name);
    if (name.empty()) throw BadParam("a selection set name cannot be empty");
    const int other = FindSet(name);
    if (other >= 0 && other != index) throw BadParam("a selection set named '" + SetName(other) + "' already exists");
    MSTR setName(Utf8ToWide(name).c_str());
    if (!NamedSets()->SetNamedSelSetName(index, setName))
        throw MaxError("3ds Max did not rename selection set '" + s.name + "'");
    s.name = name;
}

static std::string SetRepr(const SelectionSetRef& s) {
    const int index = FindSet(s.name);
    if (index < 0) return "<SelectionSet '" + s.name + "' deleted>";
    return "<SelectionSet '" + s.name + "' " + Counted(SetNodes(index).size(), "node") + ">";
}

static void BindSelectionSets(nb::module_& m) {
    nb::class_<SelectionSetRef>(m, "SelectionSet",
        "A named selection set, held by name: s.nodes = nodes replaces it; mx.select(s.nodes) selects it.")
        .def_prop_rw("name", [](const SelectionSetRef& s) { return SetName(RequireSet(s.name)); }, &RenameSet,
            "The set's name; assigning renames it.")
        .def_prop_rw("nodes", [](const SelectionSetRef& s) { return WrapAll(SetNodes(RequireSet(s.name))); }, &SetSetNodes,
            "Nodes in the set; assigning replaces them.")
        .def("delete", [](const SelectionSetRef& s) {
            if (!NamedSets()->RemoveNamedSelSet(RequireSet(s.name)))
                throw MaxError("3ds Max could not delete selection set '" + s.name + "'");
        }, "Delete the set; its nodes stay in the scene.")
        .def("__len__", [](const SelectionSetRef& s) { return SetNodes(RequireSet(s.name)).size(); })
        .def("__repr__", &SetRepr)
        .def("__eq__", [](const SelectionSetRef& a, nb::handle b) {
            return nb::isinstance<SelectionSetRef>(b) && a.name == nb::cast<SelectionSetRef>(b).name;
        }, nb::arg("other").none())
        .def("__hash__", [](const SelectionSetRef& s) { return (Py_hash_t)std::hash<std::string>()(s.name); });

    m.def("selection_set", &GetSelectionSet, "name"_a, "nodes"_a.none() = nb::none(),
          "Named selection set by name; with nodes, create or replace it: selection_set('Lights', mx.nodes('Light*')).");
    m.def("selection_sets", [] {
        std::vector<SelectionSetRef> out;
        for (const std::string& name : SetNames()) out.push_back(SelectionSetRef{name});
        return out;
    }, "Every named selection set.");
}

// ── State Sets ───────────────────────────────────────────────────

using FrameRange = std::pair<double, double>;

struct StateSetInfo {
    std::string name;
    std::optional<Node> camera;
    std::optional<FrameRange> render_range;
    std::optional<FrameRange> anim_range;
    bool lock_camera_to_range = false;
};

// The Master state set's children as a JSON array of {name, camera (the
// camera INode's address as a decimal string; "0" = none), render and anim
// ([start, end] ticks or null), lock}; "null" when State Sets is unavailable.
// State Sets returns the camera as a .NET wrapper (Autodesk.Max.Wrappers.INode)
// around a raw pointer it does not clear when the camera is deleted: reading
// the wrapper's Handle then dereferences freed memory and .NET fail-fasts Max.
// Only the wrapper's INativeObject.NativePointer (an explicit interface
// member, read through reflection) is taken here; the address is matched
// against live scene nodes in C++ before anything touches it.
static const char* const kStateSetsScript = R"MXS(
(
    fn rangeJson r = try ("[" + ((r.Start as integer) as string) + "," + ((r.End as integer) as string) + "]") catch ("null")
    fn cameraAddress ss = (
        local cam = try (ss.ActiveViewportCamera) catch (undefined)
        if cam == undefined then "0" else try (
            local np = ((cam.GetType()).GetInterface "Autodesk.Max.INativeObject").GetProperty "NativePointer"
            trimRight ((np.GetValue cam undefined) as string) "P"
        ) catch ("0")
    )
    local master = try ((dotNetClass "Autodesk.Max.StateSets.Plugin").Instance.EntityManager.RootEntity.MasterStateSet) catch (undefined)
    if master == undefined then "null" else (
        local text = "["
        for i = 0 to master.Children.Count - 1 do (
            local ss = master.Children.Item[i]
            local locked = try (ss.IsLockingCameraAnimationToRange == true) catch (false)
            if i > 0 do text += ","
            text += "{\"name\":" + (__mxQuote ss.Name)
            text += ",\"camera\":\"" + (cameraAddress ss) + "\""
            text += ",\"render\":" + (rangeJson (try (ss.RenderRange) catch (undefined)))
            text += ",\"anim\":" + (rangeJson (try (ss.AnimationRange) catch (undefined)))
            text += ",\"lock\":" + (if locked then "true" else "false") + "}"
        )
        text + "]"
    )
)
)MXS";

static std::optional<FrameRange> TicksRange(const json& value) {
    if (value.type() != json::value_t::array || value.size() != 2) return std::nullopt;
    return FrameRange{TicksToFrame((TimeValue)value[0].get<long long>()), TicksToFrame((TimeValue)value[1].get<long long>())};
}

// Live nodes by address: the INode and both of its InterfaceServer bases, one
// of which the .NET wrapper reports.
static void CollectAddresses(INode* parent, std::unordered_map<uintptr_t, INode*>& out) {
    for (int i = 0; i < parent->NumberOfChildren(); ++i) {
        INode* child = parent->GetChildNode(i);
        out[reinterpret_cast<uintptr_t>(child)] = child;
        out[reinterpret_cast<uintptr_t>(static_cast<InterfaceServer*>(static_cast<Animatable*>(child)))] = child;
        out[reinterpret_cast<uintptr_t>(static_cast<InterfaceServer*>(static_cast<FPMixinInterface*>(child)))] = child;
        CollectAddresses(child, out);
    }
}

static std::vector<StateSetInfo> StateSets() {
    const json reply = NativeJson("maxscript", {{"code", std::string("(\n") + kMxsQuote + kStateSetsScript + "\n)"}});
    if (reply.is_null()) throw MaxError("3ds Max's State Sets system is not available");
    if (reply.type() != json::value_t::array)
        throw MaxError("State Sets returned data _mx cannot read: " + reply.dump().substr(0, 160));
    std::unordered_map<uintptr_t, INode*> live;
    CollectAddresses(GetCOREInterface()->GetRootNode(), live);
    std::vector<StateSetInfo> out;
    for (const json& item : reply) {
        StateSetInfo s;
        s.name = item.value("name", "");
        // A deleted camera's address is not among the live nodes, so it is
        // never dereferenced; the set then reports no camera.
        const uintptr_t address = std::strtoull(item.value("camera", std::string("0")).c_str(), nullptr, 10);
        if (auto it = live.find(address); address && it != live.end()) s.camera = Wrap(it->second);
        s.render_range = TicksRange(item.value("render", json()));
        s.anim_range = TicksRange(item.value("anim", json()));
        s.lock_camera_to_range = item.value("lock", false);
        out.push_back(std::move(s));
    }
    return out;
}

static std::vector<StateSetInfo> CameraSequence() {
    std::vector<StateSetInfo> out;
    for (StateSetInfo& s : StateSets())
        if (s.camera) out.push_back(std::move(s));
    // Sets without a render range have no place on the timeline: they go last.
    std::stable_sort(out.begin(), out.end(), [](const StateSetInfo& a, const StateSetInfo& b) {
        if (!a.render_range || !b.render_range) return a.render_range.has_value() && !b.render_range.has_value();
        return a.render_range->first < b.render_range->first;
    });
    return out;
}

static std::string FrameText(double frame) {
    if (frame == std::floor(frame) && std::abs(frame) < 1e9) return std::to_string((long long)frame);
    return ReprOf(nb::float_(frame));
}

static std::string StateSetRepr(const StateSetInfo& s) {
    RequireMain();
    std::string camera = " no camera";
    if (s.camera) {
        INode* node = HandlerHelpers::FindNodeByHandle(s.camera->handle);
        camera = InScene(node) ? " camera " + Quoted(node) : " camera #" + std::to_string(s.camera->handle);
    }
    const std::string range = s.render_range ? " " + FrameText(s.render_range->first) + "-" + FrameText(s.render_range->second) : "";
    return "<StateSet '" + s.name + "'" + camera + range + ">";
}

static void BindStateSets(nb::module_& m) {
    nb::class_<StateSetInfo>(m, "StateSet",
        "A State Set (a snapshot): name, camera, render_range, anim_range, lock_camera_to_range.")
        .def_ro("name", &StateSetInfo::name, "The State Set's name.")
        .def_prop_ro("camera", [](const StateSetInfo& s) { return s.camera; }, "The state set's camera, or None.")
        .def_ro("render_range", &StateSetInfo::render_range, "(start, end) render frames, or None.")
        .def_ro("anim_range", &StateSetInfo::anim_range, "(start, end) animation frames, or None.")
        .def_ro("lock_camera_to_range", &StateSetInfo::lock_camera_to_range,
            "Whether the camera's animation is locked to the range.")
        .def("__repr__", &StateSetRepr);

    m.def("state_sets", &StateSets, "The top-level State Sets with their cameras and ranges (snapshots).");
    m.def("camera_sequence", &CameraSequence,
          "The State Sets that have a camera, by render start frame (sets without a render range last).");
}

void BindOrganize(nb::module_& m) {
    BindLayers(m);
    BindGroups(m);
    BindSelectionSets(m);
    BindStateSets(m);
}

} // namespace MX_ABI_TAG
} // namespace mx
