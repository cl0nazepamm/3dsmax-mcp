// The value types of _mx: Point3, Box, Matrix and Color.
//
// They are Python classes, defined by the source below when the module loads,
// because each one is a tuple: a value unpacks, compares, hashes, serializes
// and passes to any tool exactly as the plain (x, y, z) it used to be, and any
// (x, y, z) passes where one is asked for. The classes add names and a little
// vector math. Their docstrings follow the shape nanobind gives the bound
// classes (a signature line, then the text), so mx.help and the reference read
// them the same way.
#include "mx_common.h"

namespace mx {
inline namespace MX_ABI_TAG {

static const char* const kSource = R"PY(
import math as _math


def _field(index, kind, doc):
    def get(self):
        return tuple.__getitem__(self, index)
    get.__doc__ = "(self) -> " + kind
    return property(get, doc=doc)


def _derived(kind, doc):
    def wrap(function):
        function.__doc__ = "(self) -> " + kind
        return property(function, doc=doc)
    return wrap


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class Point3(tuple):
    """A point or vector (x, y, z). It is a tuple, and any (x, y, z) passes where a Point3 is asked for. + and - work per component with another point, * and / with a number: node.pos + (0, 0, 10), (a - b).length."""

    __slots__ = ()

    def __new__(cls, x=0.0, y=0.0, z=0.0):
        if not _number(x):
            x, y, z = x
        return tuple.__new__(cls, (float(x), float(y), float(z)))

    x = _field(0, "float", "The x component.")
    y = _field(1, "float", "The y component.")
    z = _field(2, "float", "The z component.")

    def __add__(self, other):
        try:
            x, y, z = other
        except (TypeError, ValueError):
            return NotImplemented
        return Point3(self[0] + x, self[1] + y, self[2] + z)

    __radd__ = __add__

    def __sub__(self, other):
        try:
            x, y, z = other
        except (TypeError, ValueError):
            return NotImplemented
        return Point3(self[0] - x, self[1] - y, self[2] - z)

    def __rsub__(self, other):
        try:
            x, y, z = other
        except (TypeError, ValueError):
            return NotImplemented
        return Point3(x - self[0], y - self[1], z - self[2])

    def __mul__(self, factor):
        if not _number(factor):
            return NotImplemented
        return Point3(self[0] * factor, self[1] * factor, self[2] * factor)

    __rmul__ = __mul__

    def __truediv__(self, divisor):
        if not _number(divisor):
            return NotImplemented
        return Point3(self[0] / divisor, self[1] / divisor, self[2] / divisor)

    def __neg__(self):
        return Point3(-self[0], -self[1], -self[2])

    @_derived("float", "Its length.")
    def length(self):
        return _math.sqrt(self[0] * self[0] + self[1] * self[1] + self[2] * self[2])

    def normalized(self):
        """normalized(self) -> _mx.Point3

The same direction at length 1; a zero vector stays zero."""
        length = self.length
        return self / length if length else self

    def dot(self, other):
        """dot(self, other: _mx.Point3) -> float

Dot product with another point or (x, y, z)."""
        x, y, z = other
        return self[0] * x + self[1] * y + self[2] * z

    def cross(self, other):
        """cross(self, other: _mx.Point3) -> _mx.Point3

Cross product with another point or (x, y, z)."""
        x, y, z = other
        return Point3(self[1] * z - self[2] * y, self[2] * x - self[0] * z, self[0] * y - self[1] * x)

    def distance_to(self, other):
        """distance_to(self, other: _mx.Point3) -> float

Distance to another point or (x, y, z)."""
        return (self - other).length


class Box(tuple):
    """An axis-aligned box as (min, max), two Point3: node.bbox. It is a tuple, so low, high = node.bbox unpacks it."""

    __slots__ = ()

    def __new__(cls, low, high):
        return tuple.__new__(cls, (Point3(low), Point3(high)))

    min = _field(0, "_mx.Point3", "The corner with the smallest x, y and z.")
    max = _field(1, "_mx.Point3", "The corner with the largest x, y and z.")

    @_derived("_mx.Point3", "The middle of the box.")
    def center(self):
        return (self[0] + self[1]) / 2

    @_derived("_mx.Point3", "Its extent along x, y and z.")
    def size(self):
        return self[1] - self[0]

    def contains(self, point):
        """contains(self, point: _mx.Point3) -> bool

Whether a point lies inside the box or on its faces."""
        return all(low <= value <= high for low, value, high in zip(self[0], point, self[1]))


class Matrix(tuple):
    """A transform as four rows of Point3, the X, Y and Z axes then the position: node.transform. It is a tuple of its rows, and any four rows of (x, y, z) pass where a Matrix is asked for."""

    __slots__ = ()

    def __new__(cls, x_axis=(1, 0, 0), y_axis=(0, 1, 0), z_axis=(0, 0, 1), pos=(0, 0, 0)):
        if len(x_axis) == 4:
            x_axis, y_axis, z_axis, pos = x_axis
        return tuple.__new__(cls, (Point3(x_axis), Point3(y_axis), Point3(z_axis), Point3(pos)))

    x_axis = _field(0, "_mx.Point3", "The X axis: its direction, and its scale as its length.")
    y_axis = _field(1, "_mx.Point3", "The Y axis: its direction, and its scale as its length.")
    z_axis = _field(2, "_mx.Point3", "The Z axis: its direction, and its scale as its length.")
    pos = _field(3, "_mx.Point3", "The position.")

    def transform_point(self, point):
        """transform_point(self, point: _mx.Point3) -> _mx.Point3

A point given in this transform's own space, in the space the transform is in: node.transform.transform_point((0, 0, 10)) is 10 units up the node's Z, in world space."""
        x, y, z = point
        return self[0] * x + self[1] * y + self[2] * z + self[3]

    def transform_vector(self, vector):
        """transform_vector(self, vector: _mx.Point3) -> _mx.Point3

A direction taken the same way as transform_point(), without the position."""
        x, y, z = vector
        return self[0] * x + self[1] * y + self[2] * z

    def inverse(self):
        """inverse(self) -> _mx.Matrix

The transform that undoes this one: node.transform.inverse().transform_point(p) is world point p in the node's own space. ValueError when the axes span no volume."""
        a, b, c, t = self
        bc, ca, ab = b.cross(c), c.cross(a), a.cross(b)
        det = a.dot(bc)
        if abs(det) < 1e-12:
            raise ValueError("this matrix has no inverse: its axes span no volume")
        rows = [Point3(bc[i] / det, ca[i] / det, ab[i] / det) for i in range(3)]
        return Matrix(rows[0], rows[1], rows[2], -(rows[0] * t[0] + rows[1] * t[1] + rows[2] * t[2]))


class Color(tuple):
    """A color (r, g, b), each 0-1: layer.color. It is a tuple, and any (r, g, b) passes where a Color is asked for."""

    __slots__ = ()

    def __new__(cls, r=0.0, g=0.0, b=0.0):
        if not _number(r):
            r, g, b = r
        return tuple.__new__(cls, (float(r), float(g), float(b)))

    r = _field(0, "float", "Red, 0-1.")
    g = _field(1, "float", "Green, 0-1.")
    b = _field(2, "float", "Blue, 0-1.")

    @_derived("str", "The color as '#rrggbb', each channel clipped to 0-1.")
    def hex(self):
        return "#" + "".join(format(round(min(1.0, max(0.0, channel)) * 255), "02x") for channel in self)
)PY";

// The classes, kept for the module's life.
static PyObject* g_point3 = nullptr;
static PyObject* g_box = nullptr;
static PyObject* g_matrix = nullptr;
static PyObject* g_color = nullptr;

// tuple.__new__(cls, items). The items are already of the right types, so the
// class's own __new__, which converts what a script passes, is skipped.
static nb::object NewValue(PyObject* cls, const nb::tuple& items) {
    if (!cls) throw MaxError("_mx's value types are not loaded");
    const nb::tuple args = nb::make_tuple(items);
    PyObject* made = PyTuple_Type.tp_new(reinterpret_cast<PyTypeObject*>(cls), args.ptr(), nullptr);
    if (!made) throw nb::python_error();
    return nb::steal(made);
}

nb::object MakePoint3(double x, double y, double z) { return NewValue(g_point3, nb::make_tuple(x, y, z)); }

nb::object MakePoint3(const Point3& p) { return MakePoint3((double)p.x, (double)p.y, (double)p.z); }

nb::object MakeBox(const Box3& box) {
    return NewValue(g_box, nb::make_tuple(MakePoint3(box.pmin), MakePoint3(box.pmax)));
}

nb::object MakeMatrix(const Matrix3& tm) {
    return NewValue(g_matrix, nb::make_tuple(MakePoint3(tm.GetRow(0)), MakePoint3(tm.GetRow(1)), MakePoint3(tm.GetRow(2)),
                                             MakePoint3(tm.GetRow(3))));
}

nb::object MakeColor(float r, float g, float b) {
    return NewValue(g_color, nb::make_tuple((double)r, (double)g, (double)b));
}

Matrix3 ToMatrix3(nb::handle value, const std::string& what) {
    if (!nb::isinstance<nb::sequence>(value) || nb::len(value) != 4)
        throw BadParam(what + " is four rows of (x, y, z): the X, Y and Z axes, then the position");
    Matrix3 tm(TRUE);
    for (int row = 0; row < 4; ++row) tm.SetRow(row, ToPoint3(value[row], what + " row"));
    return tm;
}

void BindValues(nb::module_& m) {
    nb::dict scope;
    scope["__builtins__"] = nb::module_::import_("builtins");
    scope["__name__"] = "_mx";
    PyObject* done = PyRun_String(kSource, Py_file_input, scope.ptr(), scope.ptr());
    if (!done) throw nb::python_error();
    Py_DECREF(done);
    const auto publish = [&](const char* name) {
        nb::object cls = scope[name];
        m.attr(name) = cls;
        return cls.release().ptr();
    };
    g_point3 = publish("Point3");
    g_box = publish("Box");
    g_matrix = publish("Matrix");
    g_color = publish("Color");
}

} // namespace MX_ABI_TAG
} // namespace mx
