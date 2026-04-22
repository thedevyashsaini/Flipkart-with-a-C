from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.demand_generator import DemandGenerator
from app.models import DemandState, SimulationState, World
from app.simulator_core import SimulatorCore

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WORLD_PATH = Path(__file__).parent / "data" / "world.json"
demand_generator: DemandGenerator | None = None
simulator_core: SimulatorCore | None = None


class SimulationStartRequest(BaseModel):
    seed: int = Field(ge=0)
    tick: int = Field(ge=0, default=0)


class SimulationClearResponse(BaseModel):
    ok: bool
    seed: int = Field(ge=0)
    tick: int = Field(ge=0)


def load_world() -> World:
    raw = json.loads(WORLD_PATH.read_text(encoding="utf-8"))
    return World.model_validate(raw)


@app.on_event("startup")
def startup() -> None:
    global demand_generator
    global simulator_core
    world_data = load_world()
    demand_generator = DemandGenerator(world=world_data, seed=42, tick_interval_sec=1.0, window_size=600)
    demand_generator.start()
    simulator_core = SimulatorCore(world=world_data, demand_generator=demand_generator, tick_interval_sec=1.0)
    simulator_core.start()

@app.get("/")
def root():
    return {"message": "Hello, FastAPI with uv!"}


@app.get("/world", response_model=World)
def world() -> World:
    return load_world()


@app.get("/demand/state", response_model=DemandState)
def demand_state() -> DemandState:
    if demand_generator is None:
        return DemandState(seed=0, tick=0, running=False, total_generated=0, in_window_shipments=0, within_region=[], cross_region=[])
    return demand_generator.get_state()


@app.get("/demand/stream")
async def demand_stream(request: Request) -> StreamingResponse:
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break

            if demand_generator is None:
                payload = DemandState(
                    seed=0,
                    tick=0,
                    running=False,
                    total_generated=0,
                    in_window_shipments=0,
                    within_region=[],
                    cross_region=[],
                )
            else:
                payload = demand_generator.get_state()

            yield f"data: {payload.model_dump_json()}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/sim/state", response_model=SimulationState)
def simulation_state() -> SimulationState:
    if simulator_core is None:
        return SimulationState(
            tick=0,
            running=False,
            failed=False,
            failure_reason="",
            full_nodes=0,
            failed_node_names=[],
            generated_shipments_tick=0,
            blocked_admission_tick=0,
            moved_shipments_tick=0,
            moved_load_tick=0,
            queued_shipments=0,
            in_transit_shipments=0,
            holding_shipments=0,
            consumed_shipments=0,
            node_stats={},
            edge_stats={},
            recent_logs=[],
        )
    return simulator_core.get_state()


@app.get("/sim/stream")
async def simulation_stream(request: Request) -> StreamingResponse:
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break

            if simulator_core is None:
                payload = SimulationState(
                    tick=0,
                    running=False,
                    failed=False,
                    failure_reason="",
                    full_nodes=0,
                    failed_node_names=[],
                    generated_shipments_tick=0,
                    blocked_admission_tick=0,
                    moved_shipments_tick=0,
                    moved_load_tick=0,
                    queued_shipments=0,
                    in_transit_shipments=0,
                    holding_shipments=0,
                    consumed_shipments=0,
                    node_stats={},
                    edge_stats={},
                    recent_logs=[],
                )
            else:
                payload = simulator_core.get_state()

            yield f"data: {payload.model_dump_json()}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/sim/start")
def simulation_start(payload: SimulationStartRequest | None = None) -> dict[str, bool]:
    seed = payload.seed if payload is not None else (demand_generator.get_state().seed if demand_generator is not None else 42)
    tick = payload.tick if payload is not None else 0

    if demand_generator is not None:
        demand_generator.configure(seed=seed, start_tick=0)
    if simulator_core is not None:
        simulator_core.configure(start_tick=tick)
    if demand_generator is not None:
        demand_generator.set_running(True)
    if simulator_core is not None:
        simulator_core.set_running(True)
    return {"ok": True}


@app.post("/sim/pause")
def simulation_pause() -> dict[str, bool]:
    if demand_generator is not None:
        demand_generator.set_running(False)
    if simulator_core is not None:
        simulator_core.set_running(False)
    return {"ok": True}


@app.post("/sim/clear")
def simulation_clear() -> SimulationClearResponse:
    next_seed = random.randint(1, 999_999_999)
    if demand_generator is not None:
        demand_generator.configure(seed=next_seed, start_tick=0)
    if simulator_core is not None:
        simulator_core.clear()
    return SimulationClearResponse(ok=True, seed=next_seed, tick=0)


@app.post("/demand/inject/{node_id}")
def demand_inject(node_id: str) -> dict[str, int | bool | str]:
    if demand_generator is None:
        raise HTTPException(status_code=503, detail="Demand generator unavailable")

    injected = demand_generator.inject_destination_spike(node_id)
    if injected <= 0:
        raise HTTPException(status_code=404, detail=f"Unknown destination node: {node_id}")

    return {"ok": True, "node_id": node_id, "injected": injected}
