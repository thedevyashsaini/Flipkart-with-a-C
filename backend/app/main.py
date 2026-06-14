from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import json
import os
import random
import secrets
from openai import OpenAI
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from google.cloud import firestore
from google.oauth2 import service_account
from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas
from matplotlib.figure import Figure
from pydantic import BaseModel, Field

from app.agents.with_c import WithCAgent
from app.demand_generator import DemandGenerator
from app.models import DemandState, ShipmentPriority, SimulationState, World
from app.simulator_core import SimulatorCore

try:
    from app.ai.predictor import get_predictor as _get_ai_predictor
    _ai_predictor = _get_ai_predictor()
except Exception:
    _ai_predictor = None

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://flipkart-with-a-c.vercel.app",
        "https://flipkart-with-a-c.thedevyash.tech",
        "https://flipkart-with-a-c.onrender.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WORLD_PATH = Path(__file__).parent / "data" / "world.json"
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
demand_generator: DemandGenerator | None = None
simulator_core: SimulatorCore | None = None
explanation_cache: dict[int, str] = {}
live_explanation_cache: dict[str, str] = {}
firestore_client: firestore.Client | None = None
with_c_agent_cache: dict[str, WithCAgent] = {}


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


class LiveNodeCreate(BaseModel):
    id: str
    name: str
    type: str
    region: str
    capacity: float = Field(gt=0)
    avg_hold_ticks_per_ton: float = Field(ge=0)
    x: float
    y: float


class LiveEdgeCreate(BaseModel):
    id: str
    source: str
    target: str
    base_eta: int = Field(ge=1)
    base_cost: float = Field(ge=0)


class LiveChainCreateRequest(BaseModel):
    chain_name: str
    nodes: list[LiveNodeCreate]
    edges: list[LiveEdgeCreate]


class LiveNodeTokenResult(BaseModel):
    node_id: str
    api_token: str


class LiveChainCreateResponse(BaseModel):
    ok: bool
    chain_id: str
    node_tokens: list[LiveNodeTokenResult]


class LiveNodeEventRequest(BaseModel):
    event_type: Literal["shipment_created", "shipment_arrived", "shipment_dispatched", "shipment_delivered", "shipment_held", "shipment_rerouted", "node_status"]
    shipment_id: str
    source_node_id: str | None = None
    destination_node_id: str | None = None
    current_node_id: str
    load: float | None = Field(default=None, gt=0)
    priority: ShipmentPriority | None = ShipmentPriority.MEDIUM
    deadline_tick: int | None = Field(default=None, ge=0)
    timestamp_iso: str | None = None


class LiveExplainDecisionRequest(BaseModel):
    chain_id: str
    decision_id: str


class LiveTimelineItem(BaseModel):
    id: str
    kind: Literal["suggestion", "event"]
    created_at: str
    message: str
    decision_id: str | None = None
    event_type: str | None = None


def load_world() -> World:
    raw = json.loads(WORLD_PATH.read_text(encoding="utf-8"))
    return World.model_validate(raw)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def require_firestore() -> firestore.Client:
    if firestore_client is None:
        raise HTTPException(status_code=503, detail="Firestore is not configured")
    return firestore_client


def generate_node_token() -> str:
    return secrets.token_urlsafe(36)


def token_hash(token: str) -> str:
    signing_secret = os.getenv("TOKEN_SIGNING_SECRET", "").strip()
    if not signing_secret:
        raise HTTPException(status_code=503, detail="TOKEN_SIGNING_SECRET is not configured")
    return hmac.new(signing_secret.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def require_admin_key(x_admin_api_key: str | None) -> None:
    expected = os.getenv("SUPPLY_ADMIN_API_KEY", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="SUPPLY_ADMIN_API_KEY is not configured")
    provided = (x_admin_api_key or "").strip()
    if provided != expected:
        raise HTTPException(status_code=401, detail="Invalid supply admin API key")


def is_valid_admin_key(x_admin_api_key: str | None) -> bool:
    expected = os.getenv("SUPPLY_ADMIN_API_KEY", "").strip()
    if not expected:
        return False
    return (x_admin_api_key or "").strip() == expected


def parse_bearer_token(auth_header: str | None) -> str:
    value = (auth_header or "").strip()
    if not value.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = value[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    return token


def load_live_chain(client: firestore.Client, chain_id: str) -> dict[str, object]:
    chain_ref = client.collection("supply_chains").document(chain_id)
    chain_doc = chain_ref.get()
    if not chain_doc.exists:
        raise HTTPException(status_code=404, detail="Chain not found")

    nodes = [doc.to_dict() for doc in chain_ref.collection("nodes").stream()]
    edges = [doc.to_dict() for doc in chain_ref.collection("edges").stream()]
    if not nodes:
        raise HTTPException(status_code=400, detail="Chain has no nodes")
    if not edges:
        raise HTTPException(status_code=400, detail="Chain has no edges")

    return {
        "chain": chain_doc.to_dict(),
        "nodes": nodes,
        "edges": edges,
    }


def live_agent(chain_id: str, nodes: list[dict[str, object]], edges: list[dict[str, object]]) -> WithCAgent:
    cached = with_c_agent_cache.get(chain_id)
    if cached is not None:
        cached.update_world(nodes, edges)
        return cached
    world = World.model_validate({"nodes": nodes, "edges": edges})
    agent = WithCAgent(world)
    with_c_agent_cache[chain_id] = agent
    return agent


def load_recent_shipments(client: firestore.Client, chain_id: str) -> dict[str, dict[str, object]]:
    shipments: dict[str, dict[str, object]] = {}
    docs = (
        client.collection("supply_chains")
        .document(chain_id)
        .collection("shipments")
        .order_by("updated_at", direction=firestore.Query.DESCENDING)
        .limit(3000)
        .stream()
    )
    for doc in docs:
        shipments[doc.id] = doc.to_dict() or {}
    return shipments


def compute_pressure_maps_live(
    *,
    nodes: list[dict[str, object]],
    edges: list[dict[str, object]],
    shipments: dict[str, dict[str, object]],
) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    active_nodes = [n for n in nodes if n.get("status") != "flushed"]
    inbound: dict[str, float] = {str(node["id"]): 0.0 for node in active_nodes}
    outbound: dict[str, float] = {str(node["id"]): 0.0 for node in active_nodes}
    queued: dict[str, float] = {str(node["id"]): 0.0 for node in active_nodes}
    holding: dict[str, float] = {str(node["id"]): 0.0 for node in active_nodes}
    in_transit_to: dict[str, float] = {str(node["id"]): 0.0 for node in active_nodes}

    for shipment in shipments.values():
        state = str(shipment.get("state", "queued"))
        node_id = str(shipment.get("current_node_id", ""))
        load = float(shipment.get("load", 0.0) or 0.0)
        if state == "holding":
            if node_id in holding:
                holding[node_id] += load
            continue
        if state == "in_transit":
            target_id = str(shipment.get("target_node_id", ""))
            source_id = str(shipment.get("source_node_id", ""))
            if target_id in inbound:
                inbound[target_id] += load
                in_transit_to[target_id] += load
            if source_id in outbound:
                outbound[source_id] += load
            continue
        if state in {"queued", "arrived"} and node_id in queued:
            queued[node_id] += load

    pressure: dict[str, float] = {}
    projected: dict[str, float] = {}
    flow_trend: dict[str, float] = {}
    for node in active_nodes:
        node_id = str(node["id"])
        cap = max(1.0, float(node.get("capacity", 1.0) or 1.0))
        current = queued.get(node_id, 0.0) + 0.22 * holding.get(node_id, 0.0)
        in_load = inbound.get(node_id, 0.0)
        out_load = outbound.get(node_id, 0.0)
        pressure[node_id] = (current + 0.35 * in_load) / cap
        projected[node_id] = max(0.0, current + in_load - 0.35 * out_load) / cap
        flow_trend[node_id] = (in_load - out_load) / cap

    return pressure, projected, flow_trend


def resolve_next_hop_live(
    *,
    chain_id: str,
    nodes: list[dict[str, object]],
    edges: list[dict[str, object]],
    shipment: dict[str, object],
    pressure: dict[str, float],
    projected: dict[str, float],
    trend: dict[str, float],
) -> dict[str, object]:
    agent = live_agent(chain_id, nodes, edges)
    source = str(shipment.get("current_node_id", ""))
    destination = str(shipment.get("destination_node_id", ""))
    priority_raw = str(shipment.get("priority", "medium")).upper()
    priority = ShipmentPriority[priority_raw] if priority_raw in ShipmentPriority.__members__ else ShipmentPriority.MEDIUM
    decision = agent.decide(
        source=source,
        destination=destination,
        priority=priority,
        node_pressure=pressure,
        node_projected_pressure=projected,
        node_flow_trend=trend,
    )
    if decision.action == "hold" or decision.hop is None:
        return {
            "action": "hold",
            "target_node_id": None,
            "reason": decision.reason,
            "expected_extra_cost": decision.expected_extra_cost,
        }
    return {
        "action": "move",
        "target_node_id": decision.hop.target,
        "reason": decision.reason,
        "expected_extra_cost": decision.expected_extra_cost,
    }


@app.on_event("startup")
def startup() -> None:
    global demand_generator
    global simulator_core
    global firestore_client
    world_data = load_world()
    demand_generator = DemandGenerator(world=world_data, seed=42, tick_interval_sec=1.0, window_size=600)
    simulator_core = SimulatorCore(world=world_data, demand_generator=demand_generator, tick_interval_sec=1.0)
    simulator_core.start()

    project_id = os.getenv("FIREBASE_PROJECT_ID", "").strip()
    svc_raw = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    if project_id and svc_raw:
        try:
            svc_info = json.loads(svc_raw)
            credentials = service_account.Credentials.from_service_account_info(svc_info)
            firestore_client = firestore.Client(project=project_id, credentials=credentials)
        except Exception:
            firestore_client = None

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


@app.get("/sim/ai-predictions")
def sim_ai_predictions() -> dict[str, object]:
    if _ai_predictor is None:
        return {"ok": False, "predictions": None, "reason": "AI predictor not available (no model found)"}

    pred = _ai_predictor.predict("simulator")
    if pred is None:
        return {"ok": False, "predictions": None, "reason": "Not enough data collected yet (need 20 ticks)"}

    return {"ok": True, "predictions": pred}


@app.get("/live/chains/{chain_id}/ai-predictions")
def live_ai_predictions(chain_id: str, x_admin_api_key: str | None = Header(default=None)) -> dict[str, object]:
    require_admin_key(x_admin_api_key)

    if _ai_predictor is None:
        return {"ok": False, "predictions": None, "reason": "AI predictor not available"}

    client = require_firestore()
    live = load_live_chain(client, chain_id)
    shipments = load_recent_shipments(client, chain_id)
    pressure, projected, trend = compute_pressure_maps_live(nodes=live["nodes"], edges=live["edges"], shipments=shipments)

    _ai_predictor.record(chain_id, pressure, projected, trend)
    pred = _ai_predictor.predict(chain_id)
    if pred is None:
        return {"ok": False, "predictions": None, "reason": "Not enough data (need 20 ticks)"}

    return {"ok": True, "predictions": pred}


CLOUDFLARE_AI_URL = "https://api.cloudflare.com/client/v4/accounts/d87048eb82bfdd948b5f8fd837c8cbd2/ai/v1"


def _cloudflare_ai(prompt: str, temperature: float = 0.2) -> str:
    token = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
    model = os.getenv("GEMINI_MODEL", "google/gemini-2.0-flash-lite").strip() or "google/gemini-2.0-flash-lite"
    if not token:
        raise HTTPException(status_code=503, detail="CLOUDFLARE_API_TOKEN is not configured")
    client = OpenAI(base_url=CLOUDFLARE_AI_URL, api_key=token)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
        )
    except Exception as err:
        raise HTTPException(status_code=502, detail=f"AI request failed: {err}") from err
    content = response.choices[0].message.content
    if not content or not content.strip():
        raise HTTPException(status_code=502, detail="AI returned empty explanation")
    return content.strip()


@app.post("/sim/explain-log")
def simulation_explain_log(payload: ExplainLogRequest) -> dict[str, str | int | bool]:
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

    try:
        explanation = _cloudflare_ai(prompt)
    except HTTPException:
        if log and log.message:
            return {"ok": True, "log_id": payload.log_id, "explanation": f"Counterfactual WITH C: {log.message}", "cached": False}
        raise

    explanation_cache[payload.log_id] = explanation
    return {"ok": True, "log_id": payload.log_id, "explanation": explanation, "cached": False}


@app.post("/live/chains", response_model=LiveChainCreateResponse)
def live_create_chain(payload: LiveChainCreateRequest, x_admin_api_key: str | None = Header(default=None)) -> LiveChainCreateResponse:
    require_admin_key(x_admin_api_key)
    client = require_firestore()

    node_ids = {node.id for node in payload.nodes}
    if len(node_ids) != len(payload.nodes):
        raise HTTPException(status_code=400, detail="Duplicate node id in request")
    for edge in payload.edges:
        if edge.source not in node_ids or edge.target not in node_ids:
            raise HTTPException(status_code=400, detail=f"Edge {edge.id} references unknown nodes")

    chain_id = f"ch_{uuid4().hex[:10]}"
    chain_ref = client.collection("supply_chains").document(chain_id)
    chain_ref.set(
        {
            "chain_id": chain_id,
            "chain_name": payload.chain_name,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "status": "active",
        }
    )

    node_tokens: list[LiveNodeTokenResult] = []
    for node in payload.nodes:
        plain_token = generate_node_token()
        node_tokens.append(LiveNodeTokenResult(node_id=node.id, api_token=plain_token))
        node_doc = node.model_dump()
        node_doc.update(
            {
                "token_hash": token_hash(plain_token),
                "token_last4": plain_token[-4:],
                "enabled": True,
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }
        )
        chain_ref.collection("nodes").document(node.id).set(node_doc)

    for edge in payload.edges:
        edge_doc = edge.model_dump()
        edge_doc.update({"created_at": now_iso(), "updated_at": now_iso()})
        chain_ref.collection("edges").document(edge.id).set(edge_doc)

    with_c_agent_cache.pop(chain_id, None)
    return LiveChainCreateResponse(ok=True, chain_id=chain_id, node_tokens=node_tokens)


@app.post("/live/chains/{chain_id}/nodes/{node_id}/token")
def live_rotate_node_token(chain_id: str, node_id: str, x_admin_api_key: str | None = Header(default=None)) -> dict[str, str | bool]:
    require_admin_key(x_admin_api_key)
    client = require_firestore()
    node_ref = client.collection("supply_chains").document(chain_id).collection("nodes").document(node_id)
    node_doc = node_ref.get()
    if not node_doc.exists:
        raise HTTPException(status_code=404, detail="Node not found")

    plain_token = generate_node_token()
    node_ref.update(
        {
            "token_hash": token_hash(plain_token),
            "token_last4": plain_token[-4:],
            "updated_at": now_iso(),
        }
    )
    return {"ok": True, "node_id": node_id, "api_token": plain_token}


@app.delete("/live/chains/{chain_id}")
def live_delete_chain(chain_id: str, x_admin_api_key: str | None = Header(default=None)) -> dict[str, str | bool]:
    require_admin_key(x_admin_api_key)
    client = require_firestore()
    chain_ref = client.collection("supply_chains").document(chain_id)
    chain_doc = chain_ref.get()
    if not chain_doc.exists:
        raise HTTPException(status_code=404, detail="Chain not found")

    client.recursive_delete(chain_ref)
    with_c_agent_cache.pop(chain_id, None)
    return {"ok": True, "chain_id": chain_id}


@app.get("/live/chains/{chain_id}/snapshot")
def live_chain_snapshot(chain_id: str, x_admin_api_key: str | None = Header(default=None)) -> dict[str, object]:
    require_admin_key(x_admin_api_key)
    client = require_firestore()
    live = load_live_chain(client, chain_id)
    shipments = load_recent_shipments(client, chain_id)
    recent_logs = [doc.to_dict() for doc in client.collection("supply_chains").document(chain_id).collection("decisions").order_by("tick", direction=firestore.Query.DESCENDING).limit(120).stream()]
    return {
        "ok": True,
        "chain_id": chain_id,
        "chain": live["chain"],
        "nodes": live["nodes"],
        "edges": live["edges"],
        "shipments": list(shipments.values()),
        "recent_logs": recent_logs,
    }


@app.get("/live/chains/{chain_id}/logs")
def live_chain_logs(chain_id: str, x_admin_api_key: str | None = Header(default=None)) -> dict[str, object]:
    require_admin_key(x_admin_api_key)
    client = require_firestore()
    logs = [
        doc.to_dict()
        for doc in client.collection("supply_chains")
        .document(chain_id)
        .collection("decisions")
        .order_by("tick", direction=firestore.Query.DESCENDING)
        .limit(150)
        .stream()
    ]
    return {"ok": True, "chain_id": chain_id, "logs": logs}


@app.get("/live/chains/{chain_id}/events")
def live_chain_events(chain_id: str, x_admin_api_key: str | None = Header(default=None)) -> dict[str, object]:
    require_admin_key(x_admin_api_key)
    client = require_firestore()
    events = [
        doc.to_dict()
        for doc in client.collection("supply_chains")
        .document(chain_id)
        .collection("events")
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(200)
        .stream()
    ]
    return {"ok": True, "chain_id": chain_id, "events": events}


@app.get("/live/chains/{chain_id}/suggestions")
def live_chain_suggestions(chain_id: str, x_admin_api_key: str | None = Header(default=None)) -> dict[str, object]:
    require_admin_key(x_admin_api_key)
    client = require_firestore()
    suggestions = [
        doc.to_dict()
        for doc in client.collection("supply_chains")
        .document(chain_id)
        .collection("decisions")
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(120)
        .stream()
    ]
    return {"ok": True, "chain_id": chain_id, "suggestions": suggestions}


@app.get("/live/chains/{chain_id}/shipments/search")
def live_search_shipments(chain_id: str, q: str = "", x_admin_api_key: str | None = Header(default=None)) -> dict[str, object]:
    require_admin_key(x_admin_api_key)
    client = require_firestore()
    needle = q.strip().lower()
    docs = (
        client.collection("supply_chains")
        .document(chain_id)
        .collection("shipments")
        .order_by("updated_at", direction=firestore.Query.DESCENDING)
        .limit(500)
        .stream()
    )
    ids: list[str] = []
    for doc in docs:
        shipment_id = doc.id
        if not needle or needle in shipment_id.lower():
            ids.append(shipment_id)
        if len(ids) >= 40:
            break
    return {"ok": True, "chain_id": chain_id, "shipment_ids": ids}


@app.get("/live/chains/{chain_id}/nodes/{node_id}/tasks")
def live_node_tasks(
    chain_id: str,
    node_id: str,
    authorization: str | None = Header(default=None),
    x_admin_api_key: str | None = Header(default=None),
) -> dict[str, object]:
    client = require_firestore()
    admin_mode = is_valid_admin_key(x_admin_api_key)
    token = ""
    if not admin_mode:
        token = parse_bearer_token(authorization)
    node_ref = client.collection("supply_chains").document(chain_id).collection("nodes").document(node_id)
    node_doc = node_ref.get()
    if not node_doc.exists:
        raise HTTPException(status_code=404, detail="Node not found")
    node_data = node_doc.to_dict() or {}
    if not admin_mode and token_hash(token) != str(node_data.get("token_hash", "")):
        raise HTTPException(status_code=401, detail="Invalid node token")

    tasks = [
        doc.to_dict()
        for doc in client.collection("supply_chains")
        .document(chain_id)
        .collection("node_tasks")
        .document(node_id)
        .collection("tasks")
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(100)
        .stream()
    ]
    return {"ok": True, "chain_id": chain_id, "node_id": node_id, "tasks": tasks}


@app.get("/live/chains/{chain_id}/nodes/{node_id}/timeline")
def live_node_timeline(
    chain_id: str,
    node_id: str,
    authorization: str | None = Header(default=None),
    x_admin_api_key: str | None = Header(default=None),
) -> dict[str, object]:
    client = require_firestore()
    admin_mode = is_valid_admin_key(x_admin_api_key)
    token = ""
    if not admin_mode:
        token = parse_bearer_token(authorization)
    node_ref = client.collection("supply_chains").document(chain_id).collection("nodes").document(node_id)
    node_doc = node_ref.get()
    if not node_doc.exists:
        raise HTTPException(status_code=404, detail="Node not found")
    node_data = node_doc.to_dict() or {}
    if not admin_mode and token_hash(token) != str(node_data.get("token_hash", "")):
        raise HTTPException(status_code=401, detail="Invalid node token")

    decisions = [
        doc.to_dict()
        for doc in client.collection("supply_chains")
        .document(chain_id)
        .collection("decisions")
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(300)
        .stream()
        if str((doc.to_dict() or {}).get("node_id", "")) == node_id
    ][:120]
    events = [
        doc.to_dict()
        for doc in client.collection("supply_chains")
        .document(chain_id)
        .collection("events")
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(300)
        .stream()
        if str((doc.to_dict() or {}).get("node_id", "")) == node_id
    ][:120]

    items: list[LiveTimelineItem] = []
    for decision in decisions:
        items.append(
            LiveTimelineItem(
                id=str(decision.get("decision_id", "")),
                kind="suggestion",
                created_at=str(decision.get("created_at", "")),
                message=str(decision.get("message", "")),
                decision_id=str(decision.get("decision_id", "")),
                event_type=None,
            )
        )
    for event in events:
        shipment_id = str(event.get("shipment_id", ""))
        ev_type = str(event.get("event_type", ""))
        items.append(
            LiveTimelineItem(
                id=str(event.get("event_id", "")),
                kind="event",
                created_at=str(event.get("created_at", "")),
                message=f"{ev_type}: {shipment_id}" if shipment_id else ev_type,
                decision_id=None,
                event_type=ev_type,
            )
        )

    items.sort(key=lambda item: item.created_at, reverse=True)
    return {"ok": True, "chain_id": chain_id, "node_id": node_id, "timeline": [item.model_dump() for item in items[:200]]}


@app.post("/live/events")
def live_ingest_event(payload: LiveNodeEventRequest, authorization: str | None = Header(default=None)) -> dict[str, object]:
    client = require_firestore()
    token = parse_bearer_token(authorization)

    # resolve node by token hash
    token_digest = token_hash(token)
    chain_doc_match = None
    node_doc_match = None
    for chain_doc in client.collection("supply_chains").stream():
        matches = list(chain_doc.reference.collection("nodes").where("token_hash", "==", token_digest).limit(1).stream())
        if matches:
            chain_doc_match = chain_doc
            node_doc_match = matches[0]
            break
    if chain_doc_match is None or node_doc_match is None:
        raise HTTPException(status_code=401, detail="Invalid node token")

    chain_id = chain_doc_match.id
    node_id = node_doc_match.id
    if payload.current_node_id != node_id:
        raise HTTPException(status_code=400, detail="Token node and payload node mismatch")

    chain_ref = client.collection("supply_chains").document(chain_id)
    event_id = f"evt_{uuid4().hex[:12]}"
    event_doc = payload.model_dump()
    event_doc.update(
        {
            "event_id": event_id,
            "chain_id": chain_id,
            "node_id": node_id,
            "created_at": now_iso(),
        }
    )
    chain_ref.collection("events").document(event_id).set(event_doc)

    shipment_ref = chain_ref.collection("shipments").document(payload.shipment_id)
    shipment_existing = shipment_ref.get()
    shipment_existing_data = shipment_existing.to_dict() or {}

    if payload.event_type != "shipment_created" and not shipment_existing.exists:
        raise HTTPException(status_code=404, detail="Shipment not found. Create it first.")

    shipment_state = "queued"
    if payload.event_type == "shipment_arrived":
        shipment_state = "arrived"
    elif payload.event_type in {"shipment_dispatched", "shipment_rerouted"}:
        shipment_state = "in_transit"
    elif payload.event_type == "shipment_held":
        shipment_state = "holding"
    elif payload.event_type == "shipment_delivered":
        shipment_state = "delivered"
    elif payload.event_type == "node_status":
        shipment_state = str(shipment_existing_data.get("state", "queued") or "queued")

    resolved_load = payload.load if payload.load is not None else shipment_existing_data.get("load")
    if resolved_load is None:
        raise HTTPException(status_code=400, detail="Load is required for shipment creation")

    resolved_priority = payload.priority.value if payload.priority is not None else str(shipment_existing_data.get("priority", ShipmentPriority.MEDIUM.value))
    if payload.deadline_tick is not None:
        resolved_deadline = payload.deadline_tick
    elif shipment_existing_data.get("deadline_tick") is not None:
        resolved_deadline = shipment_existing_data.get("deadline_tick")
    else:
        resolved_deadline = int(datetime.now(timezone.utc).timestamp()) + 86400

    resolved_source = payload.source_node_id or str(shipment_existing_data.get("source_node_id", payload.current_node_id))
    resolved_destination = payload.destination_node_id or str(shipment_existing_data.get("destination_node_id", payload.current_node_id))
    resolved_target = payload.destination_node_id or str(shipment_existing_data.get("target_node_id", resolved_destination))

    shipment_doc = {
        "shipment_id": payload.shipment_id,
        "source_node_id": resolved_source,
        "destination_node_id": resolved_destination,
        "current_node_id": payload.current_node_id,
        "target_node_id": resolved_target,
        "load": resolved_load,
        "priority": resolved_priority,
        "deadline_tick": resolved_deadline,
        "state": shipment_state,
        "updated_at": now_iso(),
        "last_event_type": payload.event_type,
    }
    if not shipment_existing.exists:
        shipment_doc["created_at"] = now_iso()
    shipment_ref.set(shipment_doc, merge=True)

    decision_id: str | None = None
    task_id: str | None = None
    next_action: dict[str, object] | None = None
    should_plan_next_action = payload.event_type in {"shipment_created", "shipment_arrived", "node_status"}

    if should_plan_next_action:
        live = load_live_chain(client, chain_id)
        shipments = load_recent_shipments(client, chain_id)
        pressure, projected, trend = compute_pressure_maps_live(nodes=live["nodes"], edges=live["edges"], shipments=shipments)

        current_shipment = shipments.get(payload.shipment_id, shipment_doc)
        next_action = resolve_next_hop_live(
            chain_id=chain_id,
            nodes=live["nodes"],
            edges=live["edges"],
            shipment=current_shipment,
            pressure=pressure,
            projected=projected,
            trend=trend,
        )

        decision_id = f"dec_{uuid4().hex[:12]}"
        task_id = f"tsk_{uuid4().hex[:12]}"
        task = {
            "task_id": task_id,
            "decision_id": decision_id,
            "shipment_id": payload.shipment_id,
            "action": next_action["action"],
            "target_node_id": next_action["target_node_id"],
            "reason": next_action["reason"],
            "status": "pending",
            "created_at": now_iso(),
        }
        chain_ref.collection("node_tasks").document(node_id).collection("tasks").document(task_id).set(task)

        decision = {
            "decision_id": decision_id,
            "tick": int(datetime.now(timezone.utc).timestamp()),
            "chain_id": chain_id,
            "node_id": node_id,
            "shipment_id": payload.shipment_id,
            "action": next_action["action"],
            "target_node_id": next_action["target_node_id"],
            "reason": next_action["reason"],
            "message": (
                f"{node_id}: hold shipment {payload.shipment_id}"
                if next_action["action"] == "hold"
                else f"{node_id}: move shipment {payload.shipment_id} to {next_action['target_node_id']}"
            ),
            "counterfactual_context": {
                "event": event_doc,
                "shipment": current_shipment,
                "next_action": next_action,
                "pressure": pressure,
                "projected_pressure": projected,
                "flow_trend": trend,
                "nodes": live["nodes"],
                "edges": live["edges"],
            },
            "created_at": now_iso(),
        }
        chain_ref.collection("decisions").document(decision_id).set(decision)

    return {
        "ok": True,
        "chain_id": chain_id,
        "node_id": node_id,
        "event_id": event_id,
        "decision_id": decision_id,
        "task_id": task_id,
        "next_action": next_action,
    }


@app.post("/live/decisions/{decision_id}/explain")
def live_explain_decision(decision_id: str, payload: LiveExplainDecisionRequest, x_admin_api_key: str | None = Header(default=None)) -> dict[str, object]:
    require_admin_key(x_admin_api_key)
    client = require_firestore()
    cache_key = f"{payload.chain_id}:{decision_id}"
    if cache_key in live_explanation_cache:
        return {"ok": True, "decision_id": decision_id, "explanation": live_explanation_cache[cache_key], "cached": True}

    decision_doc = (
        client.collection("supply_chains")
        .document(payload.chain_id)
        .collection("decisions")
        .document(decision_id)
        .get()
    )
    if not decision_doc.exists:
        raise HTTPException(status_code=404, detail="Decision not found")
    decision = decision_doc.to_dict() or {}
    context = decision.get("counterfactual_context")
    if not context:
        raise HTTPException(status_code=400, detail="Decision has no explainable context")

    prompt = (
        "Explain why the recommended WITH C action is appropriate for this live supply-chain state. "
        "Focus on pressure, congestion risk, and stability under uncertainty. "
        "Return 3 short bullet points, plain text only.\n\n"
        f"Decision message: {decision.get('message', '')}\n"
        f"Decision reason: {decision.get('reason', '')}\n"
        f"Structured context: {json.dumps(context, separators=(',', ':'))}"
    )

    try:
        explanation = _cloudflare_ai(prompt)
    except HTTPException:
        if decision and decision.get("reason"):
            return {"ok": True, "decision_id": decision_id, "explanation": f"Recommendation: {decision.get('reason', 'See suggestion')}", "cached": False}
        raise

    live_explanation_cache[cache_key] = explanation
    return {"ok": True, "decision_id": decision_id, "explanation": explanation, "cached": False}


@app.post("/demand/inject/{node_id}")
def demand_inject(node_id: str) -> dict[str, int | bool | str]:
    if demand_generator is None:
        raise HTTPException(status_code=503, detail="Demand generator unavailable")

    injected = demand_generator.inject_destination_spike(node_id)
    if injected <= 0:
        raise HTTPException(status_code=404, detail=f"Unknown destination node: {node_id}")

    return {"ok": True, "node_id": node_id, "injected": injected}


def _find_warehouses_for_hub(nodes: list[dict], edges: list[dict], hub_id: str) -> list[str]:
    warehouse_ids = set()
    hub_node = next((n for n in nodes if str(n.get("id")) == hub_id), None)
    if hub_node is None or hub_node.get("type") != "hub":
        return []

    for edge in edges:
        source = str(edge.get("source", ""))
        target = str(edge.get("target", ""))
        if source == hub_id or target == hub_id:
            other_id = target if source == hub_id else source
            other_node = next((n for n in nodes if str(n.get("id")) == other_id), None)
            if other_node and other_node.get("type") == "warehouse":
                warehouse_ids.add(other_id)

    return list(warehouse_ids)


@app.post("/live/chains/{chain_id}/nodes/{node_id}/flush")
def live_flush_node(chain_id: str, node_id: str, x_admin_api_key: str | None = Header(default=None)) -> dict[str, object]:
    require_admin_key(x_admin_api_key)
    client = require_firestore()
    chain_ref = client.collection("supply_chains").document(chain_id)

    live = load_live_chain(client, chain_id)
    nodes = live["nodes"]
    edges = live["edges"]

    node_doc_ref = chain_ref.collection("nodes").document(node_id)
    node_doc = node_doc_ref.get()
    if not node_doc.exists:
        raise HTTPException(status_code=404, detail="Node not found")

    node_data = node_doc.to_dict() or {}
    if node_data.get("status") == "flushed":
        raise HTTPException(status_code=400, detail="Node is already flushed")

    nodes_to_flush: list[str] = [node_id]
    node_type = str(node_data.get("type", ""))

    if node_type == "hub":
        warehouse_ids = _find_warehouses_for_hub(nodes, edges, node_id)
        nodes_to_flush.extend(warehouse_ids)

    now = now_iso()
    flushed_node_ids: list[str] = []

    for nid in nodes_to_flush:
        ref = chain_ref.collection("nodes").document(nid)
        doc = ref.get()
        if doc.exists and doc.to_dict().get("status") != "flushed":
            ref.set({"status": "flushed", "flushed_at": now, "draining": True}, merge=True)
            flushed_node_ids.append(nid)

            shipment_docs = chain_ref.collection("shipments").where("current_node_id", "==", nid).stream()
            for shipment in shipment_docs:
                sdata = shipment.to_dict() or {}
                sstate = str(sdata.get("state", "queued"))
                target = str(sdata.get("target_node_id", ""))
                if sstate in {"queued", "arrived", "holding"} and target:
                    chain_ref.collection("shipments").document(shipment.id).set({
                        "state": "in_transit",
                        "updated_at": now,
                    }, merge=True)

    return {"ok": True, "chain_id": chain_id, "flushed_nodes": flushed_node_ids, "draining": True}


@app.post("/live/chains/{chain_id}/nodes/{node_id}/reopen")
def live_reopen_node(chain_id: str, node_id: str, x_admin_api_key: str | None = Header(default=None)) -> dict[str, object]:
    require_admin_key(x_admin_api_key)
    client = require_firestore()
    chain_ref = client.collection("supply_chains").document(chain_id)

    live = load_live_chain(client, chain_id)
    nodes = live["nodes"]
    edges = live["edges"]

    node_doc_ref = chain_ref.collection("nodes").document(node_id)
    node_doc = node_doc_ref.get()
    if not node_doc.exists:
        raise HTTPException(status_code=404, detail="Node not found")

    node_data = node_doc.to_dict() or {}
    node_type = str(node_data.get("type", ""))

    nodes_to_reopen: list[str] = [node_id]

    if node_type == "hub":
        warehouse_ids = _find_warehouses_for_hub(nodes, edges, node_id)
        nodes_to_reopen.extend(warehouse_ids)

    reopened_node_ids: list[str] = []

    for nid in nodes_to_reopen:
        ref = chain_ref.collection("nodes").document(nid)
        doc = ref.get()
        if doc.exists and doc.to_dict().get("status") == "flushed":
            ref.set({"status": "active", "draining": False}, merge=True)
            reopened_node_ids.append(nid)

    return {"ok": True, "chain_id": chain_id, "reopened_nodes": reopened_node_ids}
