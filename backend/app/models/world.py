from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class NodeType(str, Enum):
    WAREHOUSE = "warehouse"
    HUB = "hub"
    CITY = "city"
    AIRPORT = "airport"


class Node(BaseModel):
    id: str
    name: str
    type: NodeType
    region: str
    capacity: float = Field(gt=0)
    x: float
    y: float


class Edge(BaseModel):
    id: str
    source: str
    target: str
    base_eta: int = Field(ge=1)
    base_cost: float = Field(ge=0)


class World(BaseModel):
    nodes: list[Node]
    edges: list[Edge]
