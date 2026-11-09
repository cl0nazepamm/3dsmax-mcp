// _mx: 3ds Max for agents, bound straight from the SDK with nanobind.
// The pieces live in mx_*.cpp; mx_common.h states the shared rules.
#include "mx_common.h"

namespace mx {
inline namespace MX_ABI_TAG {

static PyObject* g_max_error = nullptr;
static PyObject* g_not_found = nullptr;
static PyObject* g_ambiguous = nullptr;
static PyObject* g_bad_param = nullptr;
static PyObject* g_unknown_param = nullptr;
static PyObject* g_wrong_thread = nullptr;

static void TranslateErrors(const std::exception_ptr& error, void*) {
    try {
        std::rethrow_exception(error);
    } catch (const UnknownParam& e) {
        PyErr_SetString(g_unknown_param, e.what());
    } catch (const NotFound& e) {
        PyErr_SetString(g_not_found, e.what());
    } catch (const Ambiguous& e) {
        PyErr_SetString(g_ambiguous, e.what());
    } catch (const BadParam& e) {
        PyErr_SetString(g_bad_param, e.what());
    } catch (const WrongThread& e) {
        PyErr_SetString(g_wrong_thread, e.what());
    } catch (const MaxError& e) {
        PyErr_SetString(g_max_error, e.what());
    }
}

static void Bind(nb::module_& m) {
    m.doc() = "3ds Max for agents: nodes, parameters, modifiers, materials and animation, bound from the SDK.";

    // Python exception types, each also a builtin error where one fits
    // (UnknownParam is an AttributeError, so hasattr() works on Params).
    auto exception = [&](const char* name, nb::handle base, nb::handle builtin, const char* doc) {
        nb::object bases = builtin ? nb::object(nb::make_tuple(base, builtin)) : nb::borrow(base);
        PyObject* type = PyErr_NewExceptionWithDoc(name, doc, bases.ptr(), nullptr);
        if (!type) throw nb::python_error();
        m.attr(std::strchr(name, '.') + 1) = nb::steal(type);
        return type;
    };
    g_max_error = exception("_mx.MaxError", PyExc_Exception, nb::handle(), "Base of every _mx error.");
    g_not_found = exception("_mx.NotFound", g_max_error, PyExc_LookupError, "A node, class or object does not exist.");
    g_ambiguous = exception("_mx.Ambiguous", g_max_error, PyExc_LookupError,
                            "A name matches several nodes or classes; pass a handle.");
    g_bad_param = exception("_mx.BadParam", g_max_error, PyExc_ValueError,
                            "A value has the wrong type or 3ds Max refused it.");
    g_unknown_param = exception("_mx.UnknownParam", g_max_error, PyExc_AttributeError, "No parameter of that name.");
    g_wrong_thread = exception("_mx.WrongThread", g_max_error, nb::handle(), "Called off 3ds Max's main thread.");
    nb::register_exception_translator(TranslateErrors);

    m.attr("BUILD") = MX_BUILD_ID;
    m.attr("MAX_VERSION") = MAX_SDK_VERSION;

    BindParams(m);
    BindNodes(m);
    BindTracks(m);
    BindModifiers(m);
    BindMaterials(m);
    BindNative(m);
    BindOrganize(m);
    BindViewport(m);
    BindScene(m);
    BindFiles(m);
    BindMesh(m);
    BindLighting(m);
    BindSolids(m);
    BindCurves(m);
    BindAnimation(m);

    m.def("redraw", [] { RequireMain(); GetCOREInterface()->RedrawViews(GetCOREInterface()->GetTime()); },
          "Redraw the viewports.");
}

} // namespace MX_ABI_TAG
} // namespace mx

NB_MODULE(_mx, m) { mx::Bind(m); }
