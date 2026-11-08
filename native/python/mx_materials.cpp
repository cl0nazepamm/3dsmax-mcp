// Materials: create with parameters, assign, list the scene's.
#include "mx_common.h"

namespace mx {
inline namespace MX_ABI_TAG {

static Mtl* ResolveMaterial(const MaterialRef& ref) {
    RequireMain();
    Animatable* anim = Animatable::GetAnimByHandle(ref.mtl);
    if (!anim || anim->SuperClassID() != MATERIAL_CLASS_ID)
        throw NotFound("material #" + std::to_string(ref.mtl) + " was deleted");
    return static_cast<Mtl*>(anim);
}

static MaterialRef Wrap(Mtl* mtl) { return MaterialRef{Animatable::GetHandleByAnim(mtl)}; }

std::optional<MaterialRef> NodeMaterial(const Node& n) {
    Mtl* mtl = Resolve(n)->GetMtl();
    if (!mtl) return std::nullopt;
    return Wrap(mtl);
}

void SetNodeMaterial(const Node& n, std::optional<MaterialRef> material) {
    INode* node = Resolve(n);
    node->SetMtl(material ? ResolveMaterial(*material) : nullptr);
}

static MaterialRef CreateMaterial(const std::string& cls, const std::string& name, nb::kwargs params) {
    RequireMain();
    // The lookup stays inside MATERIAL_CLASS_ID: "Physical" is also a camera,
    // and treating that as a Mtl crashes 3ds Max.
    ClassDesc* cd = FindClass(cls, {MATERIAL_CLASS_ID});
    if (!cd) throw NotFound("no material class '" + cls + "'." + Suggest(cls, ClassNames({MATERIAL_CLASS_ID})));
    auto* created = static_cast<Animatable*>(GetCOREInterface()->CreateInstance(MATERIAL_CLASS_ID, cd->ClassID()));
    if (!created || created->SuperClassID() != MATERIAL_CLASS_ID) {
        if (created) created->DeleteThis();
        throw MaxError("3ds Max could not create " + WithArticle(cls));
    }
    Mtl* mtl = static_cast<Mtl*>(created);
    mtl->SetName(MSTR(name.empty() ? cd->ClassName() : Utf8ToWide(name).c_str()));
    NoAnimate noAnimate;
    const TimeValue t = Now();
    try {
        for (auto [key, value] : params) SetParam(mtl, nb::cast<std::string>(key), value, t);
    } catch (...) {
        mtl->DeleteThis();  // nothing references it yet
        throw;
    }
    return Wrap(mtl);
}

static std::string MaterialName(Mtl* mtl) { return WideToUtf8(mtl->GetName().data()); }

void BindMaterials(nb::module_& m) {
    nb::class_<MaterialRef>(m, "Material", "A material: mat.params.base_color = (1, 0, 0); node.material = mat.")
        .def_prop_rw("name",
            [](const MaterialRef& r) { return MaterialName(ResolveMaterial(r)); },
            [](const MaterialRef& r, const std::string& name) { ResolveMaterial(r)->SetName(Utf8ToWide(name).c_str()); })
        .def_prop_ro("class_name", [](const MaterialRef& r) { return HandlerHelpers::MaxScriptVisibleClassName(ResolveMaterial(r)); })
        .def_prop_ro("params", [](const MaterialRef& r) { ResolveMaterial(r); return Params{0, r.mtl}; },
            "The material's parameters.")
        .def("__repr__", [](const MaterialRef& r) {
            Animatable* anim = Animatable::GetAnimByHandle(r.mtl);
            if (!anim || anim->SuperClassID() != MATERIAL_CLASS_ID)
                return "<Material #" + std::to_string(r.mtl) + " deleted>";
            Mtl* mtl = static_cast<Mtl*>(anim);
            return "<Material '" + MaterialName(mtl) + "' " + HandlerHelpers::MaxScriptVisibleClassName(mtl) + ">";
        })
        .def("__eq__", [](const MaterialRef& a, nb::handle b) {
            return nb::isinstance<MaterialRef>(b) && a.mtl == nb::cast<MaterialRef>(b).mtl;
        })
        .def("__hash__", [](const MaterialRef& r) { return (Py_hash_t)r.mtl; });

    m.def("material", &CreateMaterial, "cls"_a = "PhysicalMaterial", "name"_a = "", "params"_a,
          "Create a material with parameters: material('PhysicalMaterial', 'Red', base_color=(0.8, 0.1, 0.1)).");
    m.def("materials", [] {
        RequireMain();
        std::vector<MaterialRef> out;
        MtlBaseLib* lib = GetCOREInterface()->GetSceneMtls();
        for (int i = 0; lib && i < lib->Count(); ++i) {
            MtlBase* base = (*lib)[i];
            if (base && base->SuperClassID() == MATERIAL_CLASS_ID) out.push_back(Wrap(static_cast<Mtl*>(base)));
        }
        return out;
    }, "Materials used in the scene.");
}

} // namespace MX_ABI_TAG
} // namespace mx
