from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ShipmentPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SKUClass(str, Enum):
    STANDARD = "standard"
    PERISHABLE = "perishable"
    ELECTRONICS = "electronics"
    PHARMA = "pharma"


class ShipmentDemand(BaseModel):
    id: str
    source: str
    destination: str
    source_region: str
    destination_region: str
    load: float = Field(gt=0)
    priority: ShipmentPriority
    deadline_tick: int = Field(ge=0)
    sku_class: SKUClass
    created_tick: int = Field(ge=0)


class DemandLaneSummary(BaseModel):
    source_region: str
    destination_region: str
    shipment_count: int = Field(ge=0)
    total_load: float = Field(ge=0)
    avg_load: float = Field(ge=0)


class DemandState(BaseModel):
    seed: int = Field(ge=0)
    tick: int = Field(ge=0)
    running: bool = False
    total_generated: int = Field(ge=0)
    in_window_shipments: int = Field(ge=0)
    within_region: list[DemandLaneSummary]
    cross_region: list[DemandLaneSummary]
