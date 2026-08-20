"""Neutral, versioned graph interchange models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class GraphNode(BaseModel):
    id: str
    label: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class GraphLink(BaseModel):
    id: str
    source: str
    target: str
    weight: float = 1.0
    attributes: dict[str, Any] = Field(default_factory=dict)


class GraphMeta(BaseModel):
    graph_id: str
    kind: str
    directed: bool = False
    multigraph: bool = False
    node_count: int = 0
    edge_count: int = 0
    generated_at: str | None = None
    pruned: bool = False
    original_node_count: int | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)


class GraphBundle(BaseModel):
    schema_version: Literal["hudoc-graph/v1"] = "hudoc-graph/v1"
    meta: GraphMeta
    nodes: list[GraphNode] = Field(default_factory=list)
    links: list[GraphLink] = Field(default_factory=list)


__all__ = ["GraphBundle", "GraphLink", "GraphMeta", "GraphNode"]
