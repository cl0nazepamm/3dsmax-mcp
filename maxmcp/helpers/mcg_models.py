"""Typed MCP contracts for structured MCG edits and acceptance tests."""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    StrictBool,
    StrictInt,
    StrictStr,
)


NodeId: TypeAlias = Annotated[StrictInt, Field(ge=0)]
SourcePort: TypeAlias = Annotated[StrictInt, Field(ge=0, le=1)]
DestinationPort: TypeAlias = Annotated[StrictInt, Field(ge=0)]
VectorValue: TypeAlias = Annotated[list[FiniteFloat], Field(min_length=2, max_length=4)]
Dimensions3: TypeAlias = Annotated[list[FiniteFloat], Field(min_length=3, max_length=3)]
ParameterValue: TypeAlias = StrictBool | StrictInt | FiniteFloat | StrictStr | VectorValue


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MCGAddNode(_StrictModel):
    """Add one operator or group; id is required so later operations can reference it."""
    op: Literal["add_node"]
    id: NodeId
    operator: str | None = None
    groupnode: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    members: list[NodeId] | None = None


class MCGRemoveNode(_StrictModel):
    """Remove a node plus all of its connections and group memberships."""
    op: Literal["remove_node"]
    id: NodeId


class MCGSetNode(_StrictModel):
    """Set XML attributes or a group membership list without changing node identity."""
    op: Literal["set_node"]
    id: NodeId
    attributes: dict[str, Any] = Field(default_factory=dict)
    members: list[NodeId] | None = None


class MCGReplaceOperator(_StrictModel):
    """Replace one node's operator while retaining its node id and connections."""
    op: Literal["replace_operator"]
    id: NodeId
    operator: str


class MCGConnect(_StrictModel):
    """Connect nodes; source_port accepts value/function and dest_port accepts an input name."""
    op: Literal["connect"]
    source_node: NodeId
    source_port: SourcePort | StrictStr = 0
    dest_node: NodeId
    dest_port: DestinationPort | StrictStr


class MCGDisconnect(_StrictModel):
    """Remove one exact numeric connection."""
    op: Literal["disconnect"]
    source_node: NodeId
    source_port: SourcePort = 0
    dest_node: NodeId
    dest_port: DestinationPort


class MCGSetMeta(_StrictModel):
    """Set displayName, description, category, or other non-identity metadata."""
    op: Literal["set_meta"]
    values: dict[str, str]


MCGPatchOperation: TypeAlias = Annotated[
    MCGAddNode
    | MCGRemoveNode
    | MCGSetNode
    | MCGReplaceOperator
    | MCGConnect
    | MCGDisconnect
    | MCGSetMeta,
    Field(discriminator="op"),
]


class MCGExpectedResult(_StrictModel):
    """Semantic geometry/modifier output expectations."""
    dimensions: Dimensions3 | None = None
    center: Dimensions3 | None = None
    num_vertices: Annotated[StrictInt, Field(ge=0)] | None = None
    num_faces: Annotated[StrictInt, Field(ge=0)] | None = None
    changed: StrictBool | None = None


class MCGVerificationSpec(_StrictModel):
    """Disposable instance parameters, expected mesh proof, and numeric tolerance."""
    parameters: dict[str, ParameterValue] = Field(
        default_factory=dict
    )
    expect: MCGExpectedResult = Field(default_factory=MCGExpectedResult)
    tolerance: Annotated[FiniteFloat, Field(gt=0)] = 1e-4
    allow_empty: StrictBool = False


__all__ = [
    "MCGExpectedResult",
    "MCGPatchOperation",
    "MCGVerificationSpec",
]
