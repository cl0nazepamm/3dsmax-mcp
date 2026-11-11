"""The node a script holds: one shape, wherever the script runs.

Inside 3ds Max the mx helpers return sdk nodes (sdk.Node, bound from the SDK). Outside
it there is no sdk, and they return the Node below, which reads and edits through the
guarded tools. It offers a part of what sdk.Node does, under the same names and with the
same meaning, and nothing sdk.Node lacks, so a script that keeps to these members runs
the same in both places. NODE_MEMBERS names them: the sdk lint (mx/_reference.py) checks
that sdk.Node has every one, and that this class has no other.

Point3 and Box are the same idea for the values a node gives: tuples with names, as
sdk.Point3 and sdk.Box are.
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

from ._runtime import MaxToolError, call

# What a node offers in both places. Properties, then methods.
# Rotation and scale are left out on purpose: sdk reads the node's own tracks, relative to
# its parent, where the tools report the world matrix taken apart, and the two differ for a
# parented or wound node. Outside 3ds Max they are mx.tools.transform_object's to change.
NODE_MEMBERS = ("handle", "name", "exists", "class_name", "pos", "bbox", "hidden", "delete")


class Point3(tuple):
    """A point or vector (x, y, z): a tuple with names; + and - per component, * and / by a number."""

    __slots__ = ()

    def __new__(cls, x: Any = 0.0, y: float = 0.0, z: float = 0.0) -> "Point3":
        if isinstance(x, (list, tuple)):
            x, y, z = x
        return super().__new__(cls, (float(x), float(y), float(z)))

    x = property(lambda self: self[0], doc="The x component.")
    y = property(lambda self: self[1], doc="The y component.")
    z = property(lambda self: self[2], doc="The z component.")

    def __add__(self, other: Sequence[float]) -> "Point3":  # type: ignore[override]
        ox, oy, oz = other
        return Point3(self[0] + ox, self[1] + oy, self[2] + oz)

    __radd__ = __add__

    def __sub__(self, other: Sequence[float]) -> "Point3":
        ox, oy, oz = other
        return Point3(self[0] - ox, self[1] - oy, self[2] - oz)

    def __rsub__(self, other: Sequence[float]) -> "Point3":
        ox, oy, oz = other
        return Point3(ox - self[0], oy - self[1], oz - self[2])

    def __mul__(self, factor: float) -> "Point3":  # type: ignore[override]
        return Point3(self[0] * factor, self[1] * factor, self[2] * factor)

    __rmul__ = __mul__  # type: ignore[assignment]

    def __truediv__(self, divisor: float) -> "Point3":
        return Point3(self[0] / divisor, self[1] / divisor, self[2] / divisor)

    def __neg__(self) -> "Point3":
        return Point3(-self[0], -self[1], -self[2])

    def __repr__(self) -> str:
        return f"Point3({self[0]:g}, {self[1]:g}, {self[2]:g})"


class Box(tuple):
    """An axis-aligned box as (min, max), two Point3: low, high = node.bbox unpacks it."""

    __slots__ = ()

    def __new__(cls, low: Sequence[float], high: Sequence[float]) -> "Box":
        return super().__new__(cls, (Point3(low), Point3(high)))

    min = property(lambda self: self[0], doc="The corner with the smallest x, y and z.")
    max = property(lambda self: self[1], doc="The corner with the largest x, y and z.")
    center = property(lambda self: (self[0] + self[1]) / 2, doc="The middle of the box.")
    size = property(lambda self: self[1] - self[0], doc="Its extent along x, y and z.")

    def __repr__(self) -> str:
        return f"Box({self[0]!r}, {self[1]!r})"


def _vector(value: Any, what: str) -> str:
    """(x, y, z) as the MAXScript point the property tools take."""
    try:
        x, y, z = (float(part) for part in value)
    except (TypeError, ValueError):
        raise ValueError(f"{what} takes (x, y, z), not {value!r}") from None
    return f"[{x!r},{y!r},{z!r}]"


class Node:
    """A scene node, held by handle (by name when a tool gave none); see the module text.

    node.pos = (0, 0, 10); node.name = 'Wall'; low, high = node.bbox; node.delete().
    Everything else a node can do is a tool that takes it: mx.add_modifier(node, 'Bend'),
    mx.material(node, ...), mx.params(node), mx.tools.transform_object(handle=node.handle, ...).
    """

    __slots__ = ("handle", "_name")

    def __init__(self, name: str, handle: int | None = None, data: dict[str, Any] | None = None):
        self._name = name
        self.handle = int(handle) if handle not in (None, "", 0) else None

    def __repr__(self) -> str:
        return f"Node({self._name!r}, handle={self.handle})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Node) and (self.handle, self._name) == (other.handle, other._name)

    def __hash__(self) -> int:
        return hash((self.handle, self._name))

    def _target(self) -> dict[str, Any]:
        return {"handle": self.handle} if self.handle else {"name": self._name}

    def _info(self) -> dict[str, Any]:
        info = call("inspect_object", **self._target())
        self._name = str(info.get("name") or self._name)
        return info

    def _set(self, property: str, value: str) -> None:  # noqa: A002 - tool argument name
        call("set_object_property", **self._target(), property=property, value=value)

    @property
    def name(self) -> str:
        """The node's name; assigning renames it. Names need not be unique: hold the node."""
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        self._set("name", str(value))
        self._name = str(value)

    @property
    def exists(self) -> bool:
        """False once the node is deleted."""
        try:
            self._info()
        except MaxToolError as error:
            if error.code in ("NOT_FOUND", "BAD_PARAM", "INVALID_PARAM"):
                return False
            raise
        return True

    @property
    def class_name(self) -> str:
        """Class of the base object: Sphere, Editable_Poly, ..."""
        return str(self._info()["baseObject"])

    @property
    def pos(self) -> Point3:
        """World position of the pivot (x, y, z)."""
        return Point3(self._info()["position"])

    @pos.setter
    def pos(self, value: Sequence[float]) -> None:
        self._set("pos", _vector(value, "pos"))

    @property
    def bbox(self) -> Box | None:
        """World bounding box ((min), (max)) of the evaluated geometry, or None."""
        box = self._info().get("boundingBox")
        return Box(box["min"], box["max"]) if box else None

    @property
    def hidden(self) -> bool:
        """Whether the node is hidden; assign to hide or show it."""
        return bool(self._info()["isHidden"])

    @hidden.setter
    def hidden(self, value: bool) -> None:
        self._set("isHidden", "true" if value else "false")

    def delete(self) -> None:
        """Delete the node."""
        if self.handle:
            call("delete_objects", handles=[self.handle])
        else:
            call("delete_objects", names=[self._name])


def to_node(result: Any) -> Any:
    """The node a tool result or entry names (`name`, and `handle` when it has one).

    Inside 3ds Max this is the sdk node; outside, a Node.
    """
    if isinstance(result, Node) or (_sdk_node is not None and isinstance(result, _sdk_node)):
        return result
    if not isinstance(result, dict) or not result.get("name"):
        raise ValueError(f"result has no node name: {result!r}")
    node = Node(str(result["name"]), result.get("handle"))
    if _to_sdk is not None:
        return _to_sdk(node.handle if node.handle else node.name)
    return node


# Inside 3ds Max: sdk's Node type and its lookup by handle or name (use_sdk).
_sdk_node: type | None = None
_to_sdk: Callable[[Any], Any] | None = None


def use_sdk(sdk: Any) -> None:
    """Make the helpers return sdk nodes (sdk is the _mx module), or Nodes again with None."""
    global _sdk_node, _to_sdk
    if sdk is None:
        _sdk_node, _to_sdk = None, None
    else:
        # An _mx older than its collections looks a node up with node(key).
        nodes = getattr(sdk, "nodes", None)
        _sdk_node, _to_sdk = sdk.Node, nodes.__getitem__ if hasattr(type(nodes), "__getitem__") else sdk.node
