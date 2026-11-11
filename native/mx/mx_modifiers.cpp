// Modifier stacks: list, add with parameters, enable, rename, delete.
#include "mx_common.h"

namespace mx {
inline namespace MX_ABI_TAG {

// Object-space modifiers, top of the stack first (as the modifier panel lists them).
static std::vector<Modifier*> Stack(INode* node) {
    std::vector<Modifier*> mods;
    Object* object = node->GetObjectRef();
    while (object && object->SuperClassID() == GEN_DERIVOB_CLASS_ID) {
        auto* derived = static_cast<IDerivedObject*>(object);
        for (int i = 0; i < derived->NumModifiers(); ++i) mods.push_back(derived->GetModifier(i));
        object = derived->GetObjRef();
    }
    return mods;
}

static Modifier* ResolveModifier(const ModifierRef& ref, INode** owner = nullptr) {
    INode* node = Resolve(Node{ref.node});
    for (Modifier* mod : Stack(node)) {
        if (Animatable::GetHandleByAnim(mod) == ref.mod) {
            if (owner) *owner = node;
            return mod;
        }
    }
    throw NotFound("modifier #" + std::to_string(ref.mod) + " is no longer on '" + NodeName(node) + "'");
}

std::vector<ModifierRef> NodeModifiers(const Node& n) {
    std::vector<ModifierRef> out;
    for (Modifier* mod : Stack(Resolve(n))) out.push_back(ModifierRef{n.handle, Animatable::GetHandleByAnim(mod)});
    return out;
}

ModifierRef AddModifier(const Node& n, const std::string& cls, const std::string& name, nb::kwargs params) {
    INode* node = Resolve(n);
    Modifier* mod = nullptr;
    try {
        mod = ModifierHelpers::CreateModifier(cls);
    } catch (const std::runtime_error&) {
        throw NotFound("no modifier class '" + cls + "'." + Suggest(cls, ClassNames({OSM_CLASS_ID})));
    }
    if (!name.empty()) mod->SetName(MSTR(Utf8ToWide(name).c_str()));
    if (GetCOREInterface7()->AddModifier(*node, *mod) != Interface7::kRES_SUCCESS) {
        mod->DeleteThis();
        throw MaxError("3ds Max could not add " + WithArticle(cls) + " to '" + NodeName(node) + "'");
    }
    // Parameters after the modifier is applied: some initialize on apply.
    NoAnimate noAnimate;
    const TimeValue t = Now();
    try {
        for (auto [key, value] : params) SetParam(mod, nb::cast<std::string>(key), value, t);
    } catch (...) {
        GetCOREInterface7()->DeleteModifier(*node, *mod);  // a failed add leaves nothing behind
        throw;
    }
    return ModifierRef{n.handle, Animatable::GetHandleByAnim(mod)};
}

static std::string ModifierName(Modifier* mod) { return WideToUtf8(mod->GetName(false).data()); }

void BindModifiers(nb::module_& m) {
    nb::class_<ModifierRef>(m, "Modifier", "A modifier on a node's stack: m.params.angle = 30, m.enabled = False.")
        .def_prop_rw("name",
            [](const ModifierRef& r) { return ModifierName(ResolveModifier(r)); },
            [](const ModifierRef& r, const std::string& name) {
                ResolveModifier(r)->SetName(MSTR(Utf8ToWide(name).c_str()));
            },
            "The modifier's name in the stack; assigning renames it.")
        .def_prop_ro("class_name", [](const ModifierRef& r) { return HandlerHelpers::MaxScriptVisibleClassName(ResolveModifier(r)); },
            "MAXScript class: Bend, TurboSmooth, Edit_Poly, ...")
        .def_prop_rw("enabled",
            [](const ModifierRef& r) { return ResolveModifier(r)->IsEnabled() != 0; },
            [](const ModifierRef& r, bool on) {
                Modifier* mod = ResolveModifier(r);
                if (on) mod->EnableMod(); else mod->DisableMod();
            },
            "Whether the modifier is on, in the viewports and in renders.")
        .def_prop_ro("params", [](const ModifierRef& r) { ResolveModifier(r); return Params{r.node, r.mod}; },
            "The modifier's parameters.")
        .def_prop_ro("node", [](const ModifierRef& r) { ResolveModifier(r); return Node{r.node}; },
            "The node whose stack this modifier was read from; an instanced modifier is on others too.")
        .def("delete", [](const ModifierRef& r) {
            INode* node = nullptr;
            Modifier* mod = ResolveModifier(r, &node);
            GetCOREInterface7()->DeleteModifier(*node, *mod);
        }, "Remove the modifier from the stack.")
        .def("__repr__", [](const ModifierRef& r) {
            INode* node = HandlerHelpers::FindNodeByHandle(r.node);
            if (!InScene(node)) return std::string("<Modifier #") + std::to_string(r.mod) + " deleted>";
            for (Modifier* mod : Stack(node))
                if (Animatable::GetHandleByAnim(mod) == r.mod)
                    return "<Modifier '" + ModifierName(mod) + "' " + HandlerHelpers::MaxScriptVisibleClassName(mod) +
                           " on '" + NodeName(node) + "'>";
            return std::string("<Modifier #") + std::to_string(r.mod) + " removed>";
        })
        .def("__eq__", [](const ModifierRef& a, nb::handle b) {
            return nb::isinstance<ModifierRef>(b) && a.mod == nb::cast<ModifierRef>(b).mod;
        }, nb::arg("other").none())
        .def("__hash__", [](const ModifierRef& r) { return (Py_hash_t)r.mod; });
}

} // namespace MX_ABI_TAG
} // namespace mx
