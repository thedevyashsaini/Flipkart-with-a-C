from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SimulationLog(BaseModel):
    log_id: int = Field(ge=1)
    tick: int = Field(ge=0)
    message: str
    counterfactual_diff: bool = False
    counterfactual_agent: str | None = None
    counterfactual_summary: str | None = None
    counterfactual_context: dict[str, Any] | None = None


class NodeSimulationStat(BaseModel):
    queued_shipments: int = Field(ge=0)
    inbound_shipments: int = Field(ge=0)
    outbound_shipments: int = Field(ge=0)
    holding_shipments: int = Field(ge=0)
    consumed_shipments_tick: int = Field(ge=0)
    active_load_tons: float = Field(ge=0)
    capacity: float = Field(ge=0)


class EdgeSimulationStat(BaseModel):
    dispatched_shipments_tick: int = Field(ge=0)
    dispatched_load_tick: float = Field(ge=0)
    in_transit_shipments: int = Field(ge=0)
    in_transit_load: float = Field(ge=0)
    avg_remaining_ticks: float = Field(ge=0)
    min_remaining_ticks: int = Field(ge=0)
    max_remaining_ticks: int = Field(ge=0)


class SimulationState(BaseModel):
    agent_mode: str = "without_c"
    tick: int = Field(ge=0)
    running: bool = False
    failed: bool = False
    failure_reason: str = ""
    full_nodes: int = Field(ge=0)
    failed_node_names: list[str]
    generated_shipments_tick: int = Field(ge=0)
    blocked_admission_tick: int = Field(ge=0)
    moved_shipments_tick: int = Field(ge=0)
    moved_load_tick: float = Field(ge=0)
    queued_shipments: int = Field(ge=0)
    in_transit_shipments: int = Field(ge=0)
    holding_shipments: int = Field(ge=0)
    consumed_shipments: int = Field(ge=0)
    additional_cost: float = Field(ge=0)
    additional_cost_threshold: float = Field(ge=0)
    node_stats: dict[str, NodeSimulationStat]
    edge_stats: dict[str, EdgeSimulationStat]
    recent_logs: list[SimulationLog]
