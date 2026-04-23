from __future__ import annotations

import asyncio
import io
import json
import os
import random
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas
from matplotlib.figure import Figure
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
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
demand_generator: DemandGenerator | None = None
simulator_core: SimulatorCore | None = None
explanation_cache: dict[int, str] = {}


def load_env_file() -> None:
    if not ENV_PATH.exists():
        return

    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


load_env_file()


class StressEventTemplate(BaseModel):
    tick: int = Field(ge=1)
    slot_index: int = Field(ge=0)
    shipment_count: int = Field(ge=1)


class StressProfile(BaseModel):
    id: str
    label: str
    required_nodes: int = Field(ge=1)
    events: list[StressEventTemplate]


STRESS_PROFILES: dict[str, StressProfile] = {
    "profile_3": StressProfile(
        id="profile_3",
        label="Profile 3 - tri-node wave",
        required_nodes=3,
        events=[
            StressEventTemplate(tick=20, slot_index=0, shipment_count=12),
            StressEventTemplate(tick=24, slot_index=1, shipment_count=10),
            StressEventTemplate(tick=28, slot_index=2, shipment_count=10),
            StressEventTemplate(tick=34, slot_index=0, shipment_count=8),
            StressEventTemplate(tick=38, slot_index=2, shipment_count=8),
        ],
    ),
    "profile_4": StressProfile(
        id="profile_4",
        label="Profile 4 - regional cascade",
        required_nodes=4,
        events=[
            StressEventTemplate(tick=18, slot_index=0, shipment_count=12),
            StressEventTemplate(tick=22, slot_index=1, shipment_count=11),
            StressEventTemplate(tick=26, slot_index=2, shipment_count=11),
            StressEventTemplate(tick=30, slot_index=3, shipment_count=10),
            StressEventTemplate(tick=38, slot_index=0, shipment_count=9),
            StressEventTemplate(tick=42, slot_index=2, shipment_count=9),
        ],
    ),
    "profile_5": StressProfile(
        id="profile_5",
        label="Profile 5 - distributed heavy",
        required_nodes=5,
        events=[
            StressEventTemplate(tick=15, slot_index=0, shipment_count=12),
            StressEventTemplate(tick=19, slot_index=1, shipment_count=12),
            StressEventTemplate(tick=23, slot_index=2, shipment_count=11),
            StressEventTemplate(tick=27, slot_index=3, shipment_count=11),
            StressEventTemplate(tick=31, slot_index=4, shipment_count=10),
            StressEventTemplate(tick=40, slot_index=1, shipment_count=9),
            StressEventTemplate(tick=45, slot_index=3, shipment_count=9),
            StressEventTemplate(tick=50, slot_index=0, shipment_count=8),
        ],
    ),
    "profile_fail": StressProfile(
        id="profile_fail",
        label="Profile FAIL - with-c survival test",
        required_nodes=3,
        events=[
            StressEventTemplate(tick=30, slot_index=0, shipment_count=392),
            StressEventTemplate(tick=30, slot_index=1, shipment_count=392),
            StressEventTemplate(tick=30, slot_index=2, shipment_count=392),
        ],
    ),
}


class SimulationStartRequest(BaseModel):
    seed: int = Field(ge=0)
    tick: int = Field(ge=0, default=0)
    agent_mode: str = Field(default="without_c")
    stress_profile_id: str | None = None
    stress_node_ids: list[str] = Field(default_factory=list)


class SimulationAgentModeRequest(BaseModel):
    agent_mode: str = Field(default="without_c")


class SimulationClearResponse(BaseModel):
    ok: bool
    seed: int = Field(ge=0)
    tick: int = Field(ge=0)


class StressProfilesResponse(BaseModel):
    profiles: list[StressProfile]


class ExplainLogRequest(BaseModel):
    log_id: int = Field(ge=1)


def load_world() -> World:
    raw = json.loads(WORLD_PATH.read_text(encoding="utf-8"))
    return World.model_validate(raw)


@app.on_event("startup")
def startup() -> None:
    global demand_generator
    global simulator_core
    world_data = load_world()
    demand_generator = DemandGenerator(world=world_data, seed=42, tick_interval_sec=1.0, window_size=600)
    simulator_core = SimulatorCore(world=world_data, demand_generator=demand_generator, tick_interval_sec=1.0)
    simulator_core.start()

@app.get("/")
def root():
    return {"message": "Hello, FastAPI with uv!"}


@app.get("/world", response_model=World)
def world() -> World:
    return load_world()


@app.get("/stress/profiles", response_model=StressProfilesResponse)
def stress_profiles() -> StressProfilesResponse:
    return StressProfilesResponse(profiles=list(STRESS_PROFILES.values()))


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
            agent_mode="without_c",
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
            additional_cost=0,
            additional_cost_threshold=0,
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
                    agent_mode="without_c",
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
                    additional_cost=0,
                    additional_cost_threshold=0,
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
    explanation_cache.clear()
    seed = payload.seed if payload is not None else (demand_generator.get_state().seed if demand_generator is not None else 42)
    tick = payload.tick if payload is not None else 0
    agent_mode = payload.agent_mode if payload is not None else (simulator_core.get_state().agent_mode if simulator_core is not None else "without_c")
    stress_profile_id = payload.stress_profile_id if payload is not None else None
    stress_node_ids = payload.stress_node_ids if payload is not None else []

    stress_plan: list[tuple[int, str, int]] = []
    if stress_profile_id:
        profile = STRESS_PROFILES.get(stress_profile_id)
        if profile is None:
            raise HTTPException(status_code=400, detail=f"Unknown stress profile: {stress_profile_id}")

        unique_node_ids = []
        seen_ids: set[str] = set()
        world_data = simulator_core._world if simulator_core is not None else load_world()
        valid_node_ids = {node.id for node in world_data.nodes}
        for node_id in stress_node_ids:
            if node_id in seen_ids:
                continue
            if node_id not in valid_node_ids:
                raise HTTPException(status_code=400, detail=f"Unknown stress node id: {node_id}")
            seen_ids.add(node_id)
            unique_node_ids.append(node_id)

        if len(unique_node_ids) != profile.required_nodes:
            raise HTTPException(
                status_code=400,
                detail=f"Profile {profile.id} requires exactly {profile.required_nodes} selected nodes",
            )

        for event in profile.events:
            if event.slot_index >= len(unique_node_ids):
                raise HTTPException(status_code=400, detail="Stress profile slot index out of selected node range")
            stress_plan.append((event.tick, unique_node_ids[event.slot_index], event.shipment_count))

    if simulator_core is not None:
        simulator_core.set_agent_mode(agent_mode)
    if demand_generator is not None:
        demand_generator.configure(seed=seed, start_tick=0)
    if simulator_core is not None:
        simulator_core.set_stress_plan(stress_plan)
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


@app.post("/sim/agent")
def simulation_set_agent_mode(payload: SimulationAgentModeRequest) -> dict[str, str | bool]:
    mode = "with_c" if payload.agent_mode == "with_c" else "without_c"
    if simulator_core is not None:
        simulator_core.set_agent_mode(mode)
    return {"ok": True, "agent_mode": mode}


@app.post("/sim/clear")
def simulation_clear() -> SimulationClearResponse:
    next_seed = random.randint(1, 999_999_999)
    explanation_cache.clear()
    if demand_generator is not None:
        demand_generator.configure(seed=next_seed, start_tick=0)
    if simulator_core is not None:
        simulator_core.clear()
    return SimulationClearResponse(ok=True, seed=next_seed, tick=0)


@app.get("/sim/kpi-chart")
def simulation_kpi_chart() -> StreamingResponse:
    if simulator_core is None:
        raise HTTPException(status_code=503, detail="Simulator unavailable")

    points = simulator_core.get_kpi_history()
    if len(points) < 2:
        raise HTTPException(status_code=400, detail="Not enough KPI history")

    min_tick = int(points[0].get("tick", 0))
    max_tick = int(points[-1].get("tick", min_tick))
    ticks = [int(point.get("tick", 0)) for point in points]
    delivered = [float(point.get("delivered_pct", 0.0)) for point in points]
    backlog = [float(point.get("backlog_pct", 0.0)) for point in points]
    full_nodes = [float(point.get("full_node_pct", 0.0)) for point in points]
    avg_util = [float(point.get("avg_node_util_pct", 0.0)) for point in points]

    fig = Figure(figsize=(10.8, 4.9), dpi=180, facecolor="#0d0e0c")
    ax = fig.add_subplot(111)
    ax.set_facecolor("#0d0e0c")

    ax.plot(ticks, delivered, color="#22d3ee", linewidth=2.8, solid_capstyle="round", label="Delivered %")
    ax.plot(ticks, backlog, color="#f59e0b", linewidth=2.8, solid_capstyle="round", label="Backlog %")
    ax.plot(ticks, full_nodes, color="#f87171", linewidth=2.8, solid_capstyle="round", label="Full Nodes %")
    ax.plot(ticks, avg_util, color="#34d399", linewidth=2.8, solid_capstyle="round", label="Avg Node Util %")

    ax.set_xlim(min_tick, max_tick if max_tick > min_tick else min_tick + 1)
    ax.set_ylim(0, 100)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_yticklabels(["0%", "25%", "50%", "75%", "100%"], color="#e0e2df")
    ax.set_xticks([min_tick, max_tick])
    ax.set_xticklabels([f"T+{min_tick}", f"T+{max_tick}"], color="#e0e2df")

    ax.tick_params(colors="#d2d4d1", labelsize=10)
    ax.grid(True, color="#4a4c47", alpha=0.55, linewidth=0.85)
    for spine in ax.spines.values():
        spine.set_color("#9a9c99")
        spine.set_linewidth(1.0)

    ax.set_title("KPI vs Time", color="#e7e9e6", fontsize=13, pad=14)
    legend = ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.12),
        ncol=4,
        frameon=False,
        fontsize=10,
        handlelength=2.6,
    )
    for text in legend.get_texts():
        text.set_color("#d8dad7")

    fig.tight_layout(pad=1.0)
    image_bytes = io.BytesIO()
    FigureCanvas(fig).print_png(image_bytes)
    image_bytes.seek(0)
    return StreamingResponse(image_bytes, media_type="image/png")


@app.post("/sim/explain-log")
def simulation_explain_log(payload: ExplainLogRequest) -> dict[str, str | int | bool]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-lite").strip() or "gemini-2.0-flash-lite"
    if not api_key:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY is not configured")
    if simulator_core is None:
        raise HTTPException(status_code=503, detail="Simulator unavailable")

    if payload.log_id in explanation_cache:
        return {"ok": True, "log_id": payload.log_id, "explanation": explanation_cache[payload.log_id], "cached": True}

    log = next((item for item in simulator_core.get_state().recent_logs if item.log_id == payload.log_id), None)
    if log is None:
        raise HTTPException(status_code=404, detail="Log not found")
    if not log.counterfactual_diff or not log.counterfactual_context:
        raise HTTPException(status_code=400, detail="Log does not have counterfactual context")

    current_state = simulator_core.get_state()
    world = load_world()
    full_graph_state = {
        "world": world.model_dump(),
        "simulation_state": current_state.model_dump(),
    }

    prompt = (
        "You are explaining why the WITH C agent made the safer system-stability choice in a supply-chain simulation. "
        "The WITHOUT C agent is the actual baseline. WITH C is the preferred controller. "
        "Explain why WITH C's counterfactual decision is better for this exact state. "
        "Be concrete about pressure, backlog, downstream risk, and cascading failure. "
        "Use 3 short bullet points. Mention node names when useful. Avoid hype. Return plain text only. Do not return JSON. Do not include chain-of-thought.\n\n"
        f"Visible log message: {log.message}\n"
        f"Counterfactual summary: {log.counterfactual_summary}\n\n"
        "Structured decision context for this divergent log:\n"
        f"{json.dumps(log.counterfactual_context, separators=(',', ':'))}\n\n"
        "Full current graph state and simulator state:\n"
        f"{json.dumps(full_graph_state, separators=(',', ':'))}"
    )

    request_body = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt,
                    }
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "text/plain",
        },
        "thinkingConfig": {
            "thinkingBudget": 0,
        },
    }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model, safe='')}:generateContent?key={urllib.parse.quote(api_key, safe='')}"
    try:
        request = urllib.request.Request(
            url,
            data=json.dumps(request_body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")
        if err.code == 400 and "thinkingConfig" in detail:
            fallback_body = dict(request_body)
            fallback_body.pop("thinkingConfig", None)
            fallback_request = urllib.request.Request(
                url,
                data=json.dumps(fallback_body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(fallback_request, timeout=20) as response:
                    data = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as fallback_err:
                fallback_detail = fallback_err.read().decode("utf-8", errors="replace")
                raise HTTPException(status_code=502, detail=f"Gemini request failed: {fallback_detail}") from fallback_err
            except urllib.error.URLError as fallback_err:
                raise HTTPException(status_code=502, detail=f"Gemini request failed: {fallback_err.reason}") from fallback_err
        else:
            raise HTTPException(status_code=502, detail=f"Gemini request failed: {detail}") from err
    except urllib.error.URLError as err:
        raise HTTPException(status_code=502, detail=f"Gemini request failed: {err.reason}") from err

    explanation = ""
    for candidate in data.get("candidates", []):
        content = candidate.get("content", {})
        for part in content.get("parts", []):
            if part.get("thought") or part.get("thoughtSignature"):
                continue
            text = part.get("text", "")
            if text:
                explanation = text.strip()
                break
        if explanation:
            break

    if not explanation:
        for candidate in data.get("candidates", []):
            content = candidate.get("content", {})
            merged = "\n".join(part.get("text", "").strip() for part in content.get("parts", []) if part.get("text"))
            if merged:
                explanation = merged
                break

    if not explanation:
        raise HTTPException(status_code=502, detail="Gemini returned no explanation text")

    explanation_cache[payload.log_id] = explanation
    return {"ok": True, "log_id": payload.log_id, "explanation": explanation, "cached": False}


@app.post("/demand/inject/{node_id}")
def demand_inject(node_id: str) -> dict[str, int | bool | str]:
    if demand_generator is None:
        raise HTTPException(status_code=503, detail="Demand generator unavailable")

    injected = demand_generator.inject_destination_spike(node_id)
    if injected <= 0:
        raise HTTPException(status_code=404, detail=f"Unknown destination node: {node_id}")

    return {"ok": True, "node_id": node_id, "injected": injected}
