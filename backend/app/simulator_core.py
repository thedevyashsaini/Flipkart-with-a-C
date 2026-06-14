from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from itertools import count

from app.agents.with_c import WithCAgent
from app.agents.without_c import PlannedHop, WithoutCAgent
from app.demand_generator import DemandGenerator
from app.models import EdgeSimulationStat, NodeSimulationStat, ShipmentDemand, ShipmentPriority, SimulationLog, SimulationState, World

try:
    from app.ai.predictor import get_predictor as _get_ai_predictor
    _ai_predictor = _get_ai_predictor()
except Exception:
    _ai_predictor = None


@dataclass
class SimShipment:
    id: str
    source: str
    destination: str
    load: float
    created_tick: int
    current_node: str
    deadline_tick: int
    priority: ShipmentPriority


@dataclass
class InTransitShipment:
    shipment: SimShipment
    source: str
    target: str
    edge_id: str
    remaining_ticks: int
    just_started: bool = True
    stalled: bool = False
    stall_logged: bool = False


@dataclass
class HoldingShipment:
    shipment: SimShipment
    node_id: str
    remaining_hold_ticks: int


class SimulatorCore:
    FAILURE_FULL_NODE_THRESHOLD = 3
    FULL_NODE_UTILIZATION_THRESHOLD = 0.98
    FAILURE_SUSTAINED_TICKS = 1
    WITH_C_ADDITIONAL_COST_THRESHOLD = 220.0
    WITH_C_MAX_HOLD_SHARE = 0.08
    WITH_C_FORCE_FORWARD_SOURCE_PRESSURE = 0.72
    HOLDING_ADMISSION_WEIGHT = 0.22
    HOLDING_FAILURE_WEIGHT = 0.28
    PENDING_FAILURE_WEIGHT = 0.15
    QUEUE_DISPATCH_SOFT_THRESHOLD = 0.45
    MAX_QUEUE_DISPATCH_BOOST = 3.5
    SOURCE_THROTTLE_UTILIZATION = 0.9
    SOURCE_THROTTLE_ADMIT_FRACTION = 0.35

    def __init__(self, world: World, demand_generator: DemandGenerator, tick_interval_sec: float = 1.0) -> None:
        self._world = world
        self._demand_generator = demand_generator
        self._tick_interval_sec = tick_interval_sec
        self._without_c_agent = WithoutCAgent(world)
        self._with_c_agent = WithCAgent(world)
        self._agent_mode = "without_c"
        self._running = False
        self._failed = False
        self._failure_reason = ""

        self._node_by_id = {n.id: n for n in world.nodes}
        self._node_name = {n.id: n.name for n in world.nodes}
        self._edge_by_pair = {(e.source, e.target): e for e in world.edges}
        self._dispatch_limit = {n.id: max(3, int(n.capacity / 30)) for n in world.nodes}

        self._tick = 0
        self._shipment_seq = count(1)
        self._log_seq = count(1)
        self._state_lock = threading.Lock()

        self._queues: dict[str, deque[SimShipment]] = defaultdict(deque)
        self._in_transit: list[InTransitShipment] = []
        self._holding: list[HoldingShipment] = []
        self._pending_admission: dict[str, deque[SimShipment]] = defaultdict(deque)
        self._stress_plan_by_tick: dict[int, list[tuple[str, int]]] = defaultdict(list)
        self._consumed_shipments = 0
        self._consumed_shipments_tick_by_node: dict[str, int] = {}
        self._pending_admitted_tick_by_node: dict[str, int] = {}
        self._pending_admitted_total: int = 0
        self._ingested_shipments_total: int = 0
        self._dropped_shipments_total: int = 0
        self._recent_logs: list[SimulationLog] = []
        self._last_edge_move_counts: dict[str, int] = {}
        self._last_edge_move_loads: dict[str, float] = {}
        self._additional_cost = 0.0
        self._overload_streak = 0
        self._kpi_history: list[dict[str, float | int]] = []
        self._latest_state = SimulationState(
            agent_mode=self._agent_mode,
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
            additional_cost_threshold=self.WITH_C_ADDITIONAL_COST_THRESHOLD,
            node_stats={},
            edge_stats={},
            recent_logs=[],
        )

    def start(self) -> None:
        thread = threading.Thread(target=self._run_forever, daemon=True)
        thread.start()

    def set_running(self, running: bool) -> None:
        with self._state_lock:
            if running:
                self._failed = False
                self._failure_reason = ""
            self._running = running
            self._latest_state = self._build_state(0, 0, 0, 0.0)

    def set_agent_mode(self, mode: str) -> None:
        with self._state_lock:
            self._agent_mode = "with_c" if mode == "with_c" else "without_c"
            self._latest_state = self._build_state(0, 0, 0, 0.0)

    def clear(self) -> None:
        self.configure(start_tick=0)

    def configure(self, start_tick: int = 0) -> None:
        if start_tick < 0:
            start_tick = 0

        with self._state_lock:
            self._running = False
            self._failed = False
            self._failure_reason = ""
            self._tick = 0
            self._shipment_seq = count(1)
            self._log_seq = count(1)
            self._queues = defaultdict(deque)
            self._in_transit = []
            self._holding = []
            self._pending_admission = defaultdict(deque)
            self._consumed_shipments = 0
            self._consumed_shipments_tick_by_node = {}
            self._pending_admitted_tick_by_node = {}
            self._pending_admitted_total = 0
            self._ingested_shipments_total = 0
            self._dropped_shipments_total = 0
            self._recent_logs = []
            self._last_edge_move_counts = {}
            self._last_edge_move_loads = {}
            self._additional_cost = 0.0
            self._overload_streak = 0
            self._kpi_history = []
            self._latest_state = self._build_state(0, 0, 0, 0.0)

        for _ in range(start_tick):
            self._tick_once(advance_demand=True)

    def get_state(self) -> SimulationState:
        with self._state_lock:
            return self._latest_state

    def get_kpi_history(self) -> list[dict[str, float | int]]:
        with self._state_lock:
            return [dict(item) for item in self._kpi_history]

    def set_stress_plan(self, plan: list[tuple[int, str, int]]) -> None:
        mapped: dict[int, list[tuple[str, int]]] = defaultdict(list)
        for tick, node_id, shipment_count in plan:
            if tick <= 0 or shipment_count <= 0:
                continue
            mapped[tick].append((node_id, shipment_count))
        with self._state_lock:
            self._stress_plan_by_tick = defaultdict(list, mapped)

    def _run_forever(self) -> None:
        while True:
            with self._state_lock:
                should_run = self._running
            if should_run:
                self._tick_once()
            time.sleep(self._tick_interval_sec)

    def _tick_once(self, advance_demand: bool = True) -> None:
        with self._state_lock:
            if advance_demand:
                self._demand_generator.advance_one_tick()
            self._tick += 1
            self._apply_stress_events_for_tick(self._tick)
            blocked_admission_tick = self._retry_pending_admission()
            new_demands = self._demand_generator.drain_new_shipments()
            generated_shipments, blocked_new = self._ingest_demands(new_demands)
            blocked_admission_tick += blocked_new

            moved_shipments_tick, moved_load_tick = self._dispatch_moves()

            if _ai_predictor is not None:
                pressure, projected, trend = self._pressure_maps()
                _ai_predictor.record("simulator", pressure, projected, trend)

            self._advance_transit()
            self._advance_holding()
            self._validate_mass_balance()
            self._evaluate_failure()

            self._latest_state = self._build_state(generated_shipments, blocked_admission_tick, moved_shipments_tick, moved_load_tick)
            self._record_kpi_point(self._latest_state)

    def _record_kpi_point(self, state: SimulationState) -> None:
        total_generated = self._demand_generator.get_state().total_generated
        backlog = state.queued_shipments + state.in_transit_shipments + state.holding_shipments
        node_count = max(1, len(self._world.nodes))

        sum_util = 0.0
        for stat in state.node_stats.values():
            if stat.capacity <= 0:
                continue
            sum_util += stat.active_load_tons / stat.capacity

        point = {
            "tick": state.tick,
            "delivered_pct": (state.consumed_shipments / total_generated) * 100 if total_generated > 0 else 0.0,
            "backlog_pct": (backlog / total_generated) * 100 if total_generated > 0 else 0.0,
            "full_node_pct": (state.full_nodes / node_count) * 100,
            "avg_node_util_pct": (sum_util / node_count) * 100,
        }

        if self._kpi_history and int(self._kpi_history[-1].get("tick", -1)) == state.tick:
            self._kpi_history[-1] = point
        else:
            self._kpi_history.append(point)

        if len(self._kpi_history) > 1200:
            self._kpi_history = self._kpi_history[-1200:]

    def _build_state(self, generated_shipments: int, blocked_admission_tick: int, moved_shipments_tick: int, moved_load_tick: float) -> SimulationState:
        return SimulationState(
            agent_mode=self._agent_mode,
            tick=self._tick,
            running=self._running,
            failed=self._failed,
            failure_reason=self._failure_reason,
            full_nodes=self._count_full_nodes(),
            failed_node_names=self._full_node_names(),
            generated_shipments_tick=generated_shipments,
            blocked_admission_tick=blocked_admission_tick,
            moved_shipments_tick=moved_shipments_tick,
            moved_load_tick=round(moved_load_tick, 2),
            queued_shipments=sum(len(q) for q in self._queues.values()),
            in_transit_shipments=len(self._in_transit),
            holding_shipments=len(self._holding),
            consumed_shipments=self._consumed_shipments,
            additional_cost=round(self._additional_cost, 3),
            additional_cost_threshold=self.WITH_C_ADDITIONAL_COST_THRESHOLD,
            node_stats=self._build_node_stats(),
            edge_stats=self._build_edge_stats(),
            recent_logs=list(self._recent_logs[:50]),
        )

    def _build_node_stats(self) -> dict[str, NodeSimulationStat]:
        queued_by_node = {node_id: len(queue) for node_id, queue in self._queues.items()}
        holding_by_node: dict[str, int] = defaultdict(int)
        holding_load_by_node: dict[str, float] = defaultdict(float)
        inbound_by_node: dict[str, int] = defaultdict(int)
        outbound_by_node: dict[str, int] = defaultdict(int)

        for item in self._holding:
            holding_by_node[item.node_id] += 1
            holding_load_by_node[item.node_id] += item.shipment.load

        for transit in self._in_transit:
            outbound_by_node[transit.source] += 1
            inbound_by_node[transit.target] += 1

        stats: dict[str, NodeSimulationStat] = {}
        for node in self._world.nodes:
            node_queue = self._queues.get(node.id, deque())
            active_load = self._node_operational_load(node.id)
            stats[node.id] = NodeSimulationStat(
                queued_shipments=queued_by_node.get(node.id, 0),
                inbound_shipments=inbound_by_node.get(node.id, 0),
                outbound_shipments=outbound_by_node.get(node.id, 0),
                holding_shipments=holding_by_node.get(node.id, 0),
                consumed_shipments_tick=self._consumed_shipments_tick_by_node.get(node.id, 0),
                active_load_tons=round(active_load, 2),
                capacity=node.capacity,
            )
        return stats

    def _build_edge_stats(self) -> dict[str, EdgeSimulationStat]:
        in_transit_count_by_edge: dict[str, int] = defaultdict(int)
        in_transit_load_by_edge: dict[str, float] = defaultdict(float)
        remaining_ticks_by_edge: dict[str, list[int]] = defaultdict(list)

        for transit in self._in_transit:
            in_transit_count_by_edge[transit.edge_id] += 1
            in_transit_load_by_edge[transit.edge_id] += transit.shipment.load
            remaining_ticks_by_edge[transit.edge_id].append(transit.remaining_ticks)

        edge_stats: dict[str, EdgeSimulationStat] = {}
        for edge in self._world.edges:
            remains = remaining_ticks_by_edge.get(edge.id, [])
            avg_remaining = 0.0
            min_remaining = 0
            max_remaining = 0
            if remains:
                avg_remaining = sum(remains) / len(remains)
                min_remaining = min(remains)
                max_remaining = max(remains)

            edge_stats[edge.id] = EdgeSimulationStat(
                dispatched_shipments_tick=self._last_edge_move_counts.get(edge.id, 0),
                dispatched_load_tick=round(self._last_edge_move_loads.get(edge.id, 0.0), 2),
                in_transit_shipments=in_transit_count_by_edge.get(edge.id, 0),
                in_transit_load=round(in_transit_load_by_edge.get(edge.id, 0.0), 2),
                avg_remaining_ticks=round(avg_remaining, 2),
                min_remaining_ticks=min_remaining,
                max_remaining_ticks=max_remaining,
            )
        return edge_stats

    def _ingest_demands(self, demands: list[ShipmentDemand]) -> tuple[int, int]:
        generated = 0
        blocked = 0
        for demand in demands:
            shipment = SimShipment(
                id=f"sim{next(self._shipment_seq):06d}",
                source=demand.source,
                destination=demand.destination,
                load=demand.load,
                created_tick=self._tick,
                current_node=demand.source,
                deadline_tick=demand.deadline_tick,
                priority=demand.priority,
            )
            if self._node_can_accept(demand.source, shipment.load):
                self._queues[demand.source].append(shipment)
                generated += 1
            else:
                self._pending_admission[demand.source].append(shipment)
                blocked += 1
            self._ingested_shipments_total += 1
        if generated > 0:
            self._push_log(f"loaded {generated} shipments into simulation queues")
        if blocked > 0:
            self._push_log(f"blocked admission for {blocked} shipments at full source nodes")
        return generated, blocked

    def _retry_pending_admission(self) -> int:
        admitted = 0
        admitted_by_node: dict[str, int] = defaultdict(int)
        for node_id, pending in self._pending_admission.items():
            node = self._node_by_id.get(node_id)
            cap = node.capacity if node is not None else 0.0
            overload_ratio = 0.0 if cap <= 0 else self._node_operational_load(node_id) / cap
            throttle_active = overload_ratio >= self.SOURCE_THROTTLE_UTILIZATION
            if throttle_active:
                max_admits = max(1, int(math.ceil(len(pending) * self.SOURCE_THROTTLE_ADMIT_FRACTION)))
            else:
                max_admits = len(pending)

            admits_here = 0
            while pending:
                if admits_here >= max_admits:
                    break
                shipment = pending[0]
                if not self._node_can_accept(node_id, shipment.load):
                    break
                pending.popleft()
                self._queues[node_id].append(shipment)
                admitted += 1
                admits_here += 1
                admitted_by_node[node_id] += 1

        self._pending_admission = defaultdict(deque, {node_id: queue for node_id, queue in self._pending_admission.items() if queue})
        self._pending_admitted_tick_by_node = dict(admitted_by_node)
        self._pending_admitted_total += admitted
        if admitted > 0:
            self._push_log(f"admitted {admitted} pending shipments into nodes with free capacity")
        return 0

    def _dispatch_moves(self) -> tuple[int, float]:
        moved_count = 0
        moved_load = 0.0
        lane_counters: dict[tuple[str, str], int] = defaultdict(int)
        lane_counterfactuals: dict[tuple[str, str], dict[str, object]] = {}
        edge_move_counts: dict[str, int] = defaultdict(int)
        edge_move_loads: dict[str, float] = defaultdict(float)

        for node_id, queue in self._queues.items():
            if not queue:
                continue

            if self._agent_mode == "with_c":
                self._prioritize_queue(node_id)
                queue = self._queues.get(node_id, queue)

            dispatch_budget = self._dispatch_limit.get(node_id, 3)
            dispatch_budget = self._dynamic_dispatch_budget(node_id, dispatch_budget)
            if self._agent_mode == "with_c":
                dispatch_budget = max(dispatch_budget + 2, int(math.ceil(dispatch_budget * 1.6)))
            moved_from_node = 0
            held_from_node = 0
            inspected = 0
            max_inspected = max(len(queue), dispatch_budget * 5)
            hold_cap = max(1, int(math.ceil(max_inspected * self.WITH_C_MAX_HOLD_SHARE)))

            while moved_from_node < dispatch_budget and queue and inspected < max_inspected:
                inspected += 1
                shipment = queue[0]
                extra_cost = 0.0
                counterfactual_decision: dict[str, object] | None = None

                if self._agent_mode == "with_c":
                    node_pressure, node_projected_pressure, node_flow_trend = self._pressure_maps()
                    resolved = self._resolve_with_c_decision(
                        shipment=shipment,
                        node_id=node_id,
                        held_from_node=held_from_node,
                        hold_cap=hold_cap,
                        node_pressure=node_pressure,
                        node_projected_pressure=node_projected_pressure,
                        node_flow_trend=node_flow_trend,
                    )
                    if resolved["action"] == "hold":
                        queue.rotate(-1)
                        held_from_node += 1
                        continue
                    hop = resolved["hop"]
                    extra_cost = float(resolved["expected_extra_cost"])
                else:
                    node_pressure, node_projected_pressure, node_flow_trend = self._pressure_maps()
                    counterfactual_decision = self._resolve_with_c_decision(
                        shipment=shipment,
                        node_id=node_id,
                        held_from_node=held_from_node,
                        hold_cap=hold_cap,
                        node_pressure=node_pressure,
                        node_projected_pressure=node_projected_pressure,
                        node_flow_trend=node_flow_trend,
                    )
                    hop = self._without_c_agent.next_hop(shipment.current_node, shipment.destination)
                    if hop is not None and not self._node_can_accept(hop.target, shipment.load):
                        hop = self._find_feasible_without_c_hop(shipment.current_node, shipment.destination, shipment.load)

                if hop is None:
                    if shipment.current_node == shipment.destination:
                        queue.popleft()
                        hold_ticks = self._hold_ticks_for_destination(shipment.destination, shipment.load)
                        self._holding.append(HoldingShipment(shipment=shipment, node_id=shipment.destination, remaining_hold_ticks=hold_ticks))
                        lane_counters[(shipment.current_node, shipment.destination)] += 1
                        moved_count += 1
                        moved_load += shipment.load
                        moved_from_node += 1
                    else:
                        queue.rotate(-1)
                        held_from_node += 1
                        log_meta = self._build_counterfactual_log_metadata(
                            shipment=shipment,
                            actual_action="queue",
                            actual_hop=None,
                            counterfactual_decision=counterfactual_decision,
                            node_pressure=node_pressure,
                            node_projected_pressure=node_projected_pressure,
                            node_flow_trend=node_flow_trend,
                            aggregate_count=1,
                        ) if self._agent_mode == "without_c" else None
                        self._push_log(
                            f"route unavailable for {shipment.id} at {self._node_name.get(node_id, node_id)}; keeping shipment queued",
                            **(log_meta or {}),
                        )
                    continue

                if not self._is_edge_valid(hop):
                    queue.popleft()
                    self._dropped_shipments_total += 1
                    self._push_log(
                        f"dropped {shipment.id}: no valid edge from {self._node_name.get(shipment.current_node, shipment.current_node)}"
                    )
                    continue

                if not self._node_can_accept(hop.target, shipment.load):
                    if self._agent_mode == "with_c":
                        queue.rotate(-1)
                        held_from_node += 1
                        continue
                    if moved_from_node == 0:
                        log_meta = self._build_counterfactual_log_metadata(
                            shipment=shipment,
                            actual_action="queue",
                            actual_hop=hop,
                            counterfactual_decision=counterfactual_decision,
                            node_pressure=node_pressure,
                            node_projected_pressure=node_projected_pressure,
                            node_flow_trend=node_flow_trend,
                            aggregate_count=1,
                        )
                        self._push_log(
                            f"blocked at {self._node_name.get(node_id, node_id)}: next node {self._node_name.get(hop.target, hop.target)} is full",
                            **(log_meta or {}),
                        )
                    break

                queue.popleft()
                eta_ticks = max(1, int(hop.base_eta))
                edge = self._edge_by_pair.get((shipment.current_node, hop.target))
                if edge is not None:
                    edge_move_counts[edge.id] += 1
                    edge_move_loads[edge.id] += shipment.load
                self._in_transit.append(
                    InTransitShipment(
                        shipment=shipment,
                        source=shipment.current_node,
                        target=hop.target,
                        edge_id=edge.id if edge is not None else "",
                        remaining_ticks=eta_ticks,
                    )
                )
                lane_counters[(shipment.current_node, hop.target)] += 1
                if self._agent_mode == "without_c":
                    self._record_lane_counterfactual(
                        lane_counterfactuals=lane_counterfactuals,
                        shipment=shipment,
                        actual_hop=hop,
                        counterfactual_decision=counterfactual_decision,
                        node_pressure=node_pressure,
                        node_projected_pressure=node_projected_pressure,
                        node_flow_trend=node_flow_trend,
                    )
                moved_count += 1
                moved_load += shipment.load
                if self._agent_mode == "with_c":
                    self._additional_cost += extra_cost
                moved_from_node += 1

            if held_from_node > 0:
                self._push_log(f"held {held_from_node} shipments at {self._node_name.get(node_id, node_id)} to avoid downstream pressure")

        for (source, target), count_value in lane_counters.items():
            log_meta = None
            if self._agent_mode == "without_c":
                log_meta = self._lane_counterfactual_log_metadata(
                    lane_counterfactuals.get((source, target)),
                    aggregate_count=count_value,
                )
            self._push_log(
                f"moved {count_value} shipments from {self._node_name.get(source, source)} to {self._node_name.get(target, target)}",
                **(log_meta or {}),
            )

        self._last_edge_move_counts = dict(edge_move_counts)
        self._last_edge_move_loads = {edge_id: round(value, 2) for edge_id, value in edge_move_loads.items()}

        return moved_count, moved_load

    def _prioritize_queue(self, node_id: str) -> None:
        queue = self._queues.get(node_id)
        if queue is None or len(queue) < 2:
            return
        ordered = sorted(queue, key=lambda shipment: (self._priority_rank(shipment.priority), shipment.deadline_tick, shipment.id))
        queue.clear()
        queue.extend(ordered)

    def _priority_rank(self, priority: ShipmentPriority) -> int:
        if priority == ShipmentPriority.CRITICAL:
            return 0
        if priority == ShipmentPriority.HIGH:
            return 1
        if priority == ShipmentPriority.MEDIUM:
            return 2
        return 3

    def _pressure_maps(self) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
        inbound_load: dict[str, float] = defaultdict(float)
        outbound_load: dict[str, float] = defaultdict(float)
        pending_load: dict[str, float] = defaultdict(float)

        for transit in self._in_transit:
            inbound_load[transit.target] += transit.shipment.load
            outbound_load[transit.source] += transit.shipment.load

        for node_id, pending in self._pending_admission.items():
            pending_load[node_id] = sum(item.load for item in pending)

        pressure: dict[str, float] = {}
        projected: dict[str, float] = {}
        flow_trend: dict[str, float] = {}
        for node in self._world.nodes:
            current = self._node_operational_load(node.id)
            cap = max(1.0, node.capacity)
            inbound = inbound_load.get(node.id, 0.0)
            outbound = outbound_load.get(node.id, 0.0)
            pending = pending_load.get(node.id, 0.0)

            pressure[node.id] = (current + 0.35 * inbound + 0.15 * pending) / cap
            projected[node.id] = max(0.0, current + inbound + pending - 0.35 * outbound) / cap
            flow_trend[node.id] = (inbound - outbound + pending) / cap

        return pressure, projected, flow_trend

    def _advance_transit(self) -> None:
        if not self._in_transit:
            return

        arrived: list[InTransitShipment] = []
        still_moving: list[InTransitShipment] = []

        for transit in self._in_transit:
            if transit.just_started:
                transit.just_started = False
                still_moving.append(transit)
                continue

            if transit.stalled:
                if self._node_can_accept(transit.target, transit.shipment.load):
                    transit.stalled = False
                    arrived.append(transit)
                else:
                    still_moving.append(transit)
                continue

            transit.remaining_ticks -= 1
            if transit.remaining_ticks <= 0:
                arrived.append(transit)
            else:
                still_moving.append(transit)

        self._in_transit = still_moving

        for transit in arrived:
            shipment = transit.shipment
            if not self._node_can_accept(transit.target, shipment.load):
                transit.remaining_ticks = 0
                transit.stalled = True
                if not transit.stall_logged:
                    self._push_log(
                        f"failure at {self._node_name.get(transit.target, transit.target)}: inbound shipment from {self._node_name.get(transit.source, transit.source)} cannot enter full node"
                    )
                    transit.stall_logged = True
                self._in_transit.append(transit)
                continue

            shipment.current_node = transit.target
            if transit.target == shipment.destination:
                hold_ticks = self._hold_ticks_for_destination(transit.target, shipment.load)
                self._holding.append(HoldingShipment(shipment=shipment, node_id=transit.target, remaining_hold_ticks=hold_ticks))
            else:
                self._queues[transit.target].append(shipment)

    def _advance_holding(self) -> None:
        if not self._holding:
            self._consumed_shipments_tick_by_node = {}
            return

        remaining: list[HoldingShipment] = []
        consumed_tick = 0
        consumed_by_node: dict[str, int] = defaultdict(int)
        for item in self._holding:
            item.remaining_hold_ticks -= 1
            if item.remaining_hold_ticks <= 0:
                consumed_tick += 1
                consumed_by_node[item.node_id] += 1
            else:
                remaining.append(item)

        self._holding = remaining
        self._consumed_shipments += consumed_tick
        self._consumed_shipments_tick_by_node = dict(consumed_by_node)
        if consumed_tick > 0:
            self._push_log(f"consumed {consumed_tick} delivered shipments at destination nodes")

    def _hold_ticks_for_destination(self, node_id: str, load: float) -> int:
        node = self._node_by_id.get(node_id)
        if node is None:
            return 1
        return max(1, int(math.ceil(load * node.avg_hold_ticks_per_ton)))

    def _is_edge_valid(self, hop: PlannedHop) -> bool:
        return (hop.source, hop.target) in self._edge_by_pair

    def _node_can_accept(self, node_id: str, incoming_load: float) -> bool:
        node = self._node_by_id.get(node_id)
        if node is None:
            return False
        return self._node_operational_load(node_id) + incoming_load <= node.capacity

    def _count_full_nodes(self) -> int:
        return len(self._full_node_ids())

    def _full_node_ids(self) -> list[str]:
        full_node_ids: list[str] = []
        for node in self._world.nodes:
            if self._node_failure_load(node.id) >= node.capacity * self.FULL_NODE_UTILIZATION_THRESHOLD:
                full_node_ids.append(node.id)
        return full_node_ids

    def _full_node_names(self) -> list[str]:
        return [self._node_name.get(node_id, node_id) for node_id in self._full_node_ids()]

    def _evaluate_failure(self) -> None:
        full_node_ids = self._full_node_ids()
        full_nodes = len(full_node_ids)
        if full_nodes >= self.FAILURE_FULL_NODE_THRESHOLD:
            self._overload_streak += 1
        else:
            self._overload_streak = 0

        if self._overload_streak >= self.FAILURE_SUSTAINED_TICKS:
            failed_names = [self._node_name.get(node_id, node_id) for node_id in full_node_ids]
            reason = f"system failed: {full_nodes} nodes reached capacity"
            if self._failure_reason == reason:
                return

            self._failed = True
            self._running = False
            self._failure_reason = reason
            self._demand_generator.set_running(False)
            self._push_log(f"{reason} ({', '.join(failed_names)})")

    def _node_current_load(self, node_id: str) -> float:
        queue_load = sum(shipment.load for shipment in self._queues.get(node_id, deque()))
        holding_load = sum(item.shipment.load for item in self._holding if item.node_id == node_id)
        return queue_load + holding_load

    def _dynamic_dispatch_budget(self, node_id: str, base_budget: int) -> int:
        node = self._node_by_id.get(node_id)
        if node is None:
            return base_budget
        queue_load = sum(shipment.load for shipment in self._queues.get(node_id, deque()))
        util = queue_load / max(1.0, node.capacity)
        if util <= self.QUEUE_DISPATCH_SOFT_THRESHOLD:
            return base_budget
        over = util - self.QUEUE_DISPATCH_SOFT_THRESHOLD
        boost = 1.0 + min(self.MAX_QUEUE_DISPATCH_BOOST - 1.0, (over / 0.55) * 2.0)
        return max(base_budget, int(math.ceil(base_budget * boost)))

    def _find_feasible_without_c_hop(self, source: str, destination: str, load: float) -> PlannedHop | None:
        ranked: list[tuple[float, int, PlannedHop]] = []
        for edge in self._world.edges:
            if edge.source != source:
                continue
            hop = PlannedHop(source=edge.source, target=edge.target, base_eta=edge.base_eta, base_cost=edge.base_cost)
            next_hop = self._without_c_agent.next_hop(hop.target, destination)
            if hop.target != destination and next_hop is None:
                continue
            score_cost = hop.base_cost + (next_hop.base_cost if next_hop is not None else 0.0)
            score_eta = hop.base_eta + (next_hop.base_eta if next_hop is not None else 0)
            ranked.append((score_cost, score_eta, hop))

        ranked.sort(key=lambda item: (item[0], item[1], item[2].target))
        for _, _, hop in ranked:
            if self._node_can_accept(hop.target, load):
                return hop
        return None

    def _node_physical_load(self, node_id: str) -> float:
        return self._node_current_load(node_id)

    def _node_operational_load(self, node_id: str) -> float:
        queue_load = sum(shipment.load for shipment in self._queues.get(node_id, deque()))
        holding_load = sum(item.shipment.load for item in self._holding if item.node_id == node_id)
        return queue_load + (self.HOLDING_ADMISSION_WEIGHT * holding_load)

    def _node_failure_load(self, node_id: str) -> float:
        queue_load = sum(shipment.load for shipment in self._queues.get(node_id, deque()))
        holding_load = sum(item.shipment.load for item in self._holding if item.node_id == node_id)
        current_load = queue_load + (self.HOLDING_FAILURE_WEIGHT * holding_load)
        pending_load = sum(item.load for item in self._pending_admission.get(node_id, deque()))
        stalled_gate_load = sum(
            transit.shipment.load
            for transit in self._in_transit
            if transit.target == node_id and transit.stalled
        )
        return current_load + (self.PENDING_FAILURE_WEIGHT * pending_load) + stalled_gate_load

    def _resolve_with_c_decision(
        self,
        *,
        shipment: SimShipment,
        node_id: str,
        held_from_node: int,
        hold_cap: int,
        node_pressure: dict[str, float],
        node_projected_pressure: dict[str, float],
        node_flow_trend: dict[str, float],
    ) -> dict[str, object]:
        decision = self._with_c_agent.decide(
            source=shipment.current_node,
            destination=shipment.destination,
            priority=shipment.priority,
            node_pressure=node_pressure,
            node_projected_pressure=node_projected_pressure,
            node_flow_trend=node_flow_trend,
        )
        chosen_hop: PlannedHop | None = decision.hop
        expected_extra_cost = decision.expected_extra_cost
        if chosen_hop is not None and not self._node_can_accept(chosen_hop.target, shipment.load):
            ranked = self._with_c_agent.ranked_hops(
                source=shipment.current_node,
                destination=shipment.destination,
                priority=shipment.priority,
                node_pressure=node_pressure,
                node_projected_pressure=node_projected_pressure,
                node_flow_trend=node_flow_trend,
            )
            chosen_hop = None
            expected_extra_cost = 0.0
            for item in ranked:
                candidate_hop = item[4]
                if self._node_can_accept(candidate_hop.target, shipment.load):
                    chosen_hop = candidate_hop
                    expected_extra_cost = item[1]
                    break

        action = str(decision.action)
        reason = str(decision.reason)
        if decision.action == "hold" or chosen_hop is None:
            source_projected_pressure = node_projected_pressure.get(node_id, 0.0)
            should_force_forward = (
                source_projected_pressure >= self.WITH_C_FORCE_FORWARD_SOURCE_PRESSURE
                or held_from_node >= hold_cap
            )
            if should_force_forward:
                forced_hop = self._without_c_agent.next_hop(shipment.current_node, shipment.destination)
                if forced_hop is not None:
                    action = "move"
                    chosen_hop = forced_hop
                    reason = "force-forward-source-pressure"
                else:
                    action = "hold"
                    chosen_hop = None
            else:
                action = "hold"
                chosen_hop = None

        return {
            "action": action,
            "hop": chosen_hop,
            "reason": reason,
            "expected_extra_cost": round(max(0.0, float(expected_extra_cost)), 3),
        }

    def _record_lane_counterfactual(
        self,
        *,
        lane_counterfactuals: dict[tuple[str, str], dict[str, object]],
        shipment: SimShipment,
        actual_hop: PlannedHop,
        counterfactual_decision: dict[str, object] | None,
        node_pressure: dict[str, float],
        node_projected_pressure: dict[str, float],
        node_flow_trend: dict[str, float],
    ) -> None:
        if counterfactual_decision is None:
            return
        cf_action = str(counterfactual_decision.get("action", "hold"))
        cf_hop = counterfactual_decision.get("hop")
        cf_target = cf_hop.target if isinstance(cf_hop, PlannedHop) else None
        if cf_action == "move" and cf_target == actual_hop.target:
            return

        key = (shipment.current_node, actual_hop.target)
        bucket = lane_counterfactuals.get(key)
        if bucket is None:
            bucket = {
                "hold_count": 0,
                "reroute_counts": defaultdict(int),
                "sample_context": self._build_counterfactual_context(
                    shipment=shipment,
                    actual_action="move",
                    actual_hop=actual_hop,
                    counterfactual_decision=counterfactual_decision,
                    node_pressure=node_pressure,
                    node_projected_pressure=node_projected_pressure,
                    node_flow_trend=node_flow_trend,
                    aggregate_count=1,
                ),
            }
            lane_counterfactuals[key] = bucket

        if cf_action == "hold" or cf_target is None:
            bucket["hold_count"] = int(bucket["hold_count"]) + 1
        else:
            reroute_counts = bucket["reroute_counts"]
            assert isinstance(reroute_counts, defaultdict)
            reroute_counts[cf_target] += 1

    def _lane_counterfactual_log_metadata(self, bucket: dict[str, object] | None, *, aggregate_count: int) -> dict[str, object] | None:
        if not bucket:
            return None

        hold_count = int(bucket.get("hold_count", 0))
        reroute_counts = bucket.get("reroute_counts")
        reroute_parts: list[str] = []
        if isinstance(reroute_counts, defaultdict):
            for target, count_value in sorted(reroute_counts.items(), key=lambda item: (-item[1], self._node_name.get(item[0], item[0]))):
                reroute_parts.append(f"reroute {count_value} to {self._node_name.get(target, target)}")

        parts: list[str] = []
        if hold_count > 0:
            parts.append(f"hold {hold_count}")
        parts.extend(reroute_parts)
        if not parts:
            return None

        sample_context = bucket.get("sample_context")
        context = dict(sample_context) if isinstance(sample_context, dict) else None
        if context is not None:
            context["aggregate_count"] = aggregate_count
            context["counterfactual_aggregate"] = {
                "hold_count": hold_count,
                "reroutes": [
                    {
                        "target_node": self._node_name.get(target, target),
                        "count": count_value,
                    }
                    for target, count_value in (sorted(reroute_counts.items(), key=lambda item: (-item[1], self._node_name.get(item[0], item[0]))) if isinstance(reroute_counts, defaultdict) else [])
                ],
            }

        return {
            "counterfactual_diff": True,
            "counterfactual_agent": "with_c",
            "counterfactual_summary": f"WITH C would {' and '.join(parts)}",
            "counterfactual_context": context,
        }

    def _build_counterfactual_log_metadata(
        self,
        *,
        shipment: SimShipment,
        actual_action: str,
        actual_hop: PlannedHop | None,
        counterfactual_decision: dict[str, object] | None,
        node_pressure: dict[str, float],
        node_projected_pressure: dict[str, float],
        node_flow_trend: dict[str, float],
        aggregate_count: int,
    ) -> dict[str, object] | None:
        if counterfactual_decision is None:
            return None

        cf_action = str(counterfactual_decision.get("action", "hold"))
        cf_hop = counterfactual_decision.get("hop")
        cf_target = cf_hop.target if isinstance(cf_hop, PlannedHop) else None
        actual_target = actual_hop.target if actual_hop is not None else None
        if actual_action == cf_action and actual_target == cf_target:
            return None

        summary = "WITH C would hold"
        if cf_action == "move" and cf_target is not None:
            summary = f"WITH C would reroute to {self._node_name.get(cf_target, cf_target)}"

        return {
            "counterfactual_diff": True,
            "counterfactual_agent": "with_c",
            "counterfactual_summary": summary,
            "counterfactual_context": self._build_counterfactual_context(
                shipment=shipment,
                actual_action=actual_action,
                actual_hop=actual_hop,
                counterfactual_decision=counterfactual_decision,
                node_pressure=node_pressure,
                node_projected_pressure=node_projected_pressure,
                node_flow_trend=node_flow_trend,
                aggregate_count=aggregate_count,
            ),
        }

    def _build_counterfactual_context(
        self,
        *,
        shipment: SimShipment,
        actual_action: str,
        actual_hop: PlannedHop | None,
        counterfactual_decision: dict[str, object],
        node_pressure: dict[str, float],
        node_projected_pressure: dict[str, float],
        node_flow_trend: dict[str, float],
        aggregate_count: int,
    ) -> dict[str, object]:
        cf_hop = counterfactual_decision.get("hop")
        cf_target = cf_hop.target if isinstance(cf_hop, PlannedHop) else None
        relevant_nodes = {shipment.current_node, shipment.destination}
        if actual_hop is not None:
            relevant_nodes.add(actual_hop.target)
        if cf_target is not None:
            relevant_nodes.add(cf_target)

        node_stats = self._build_node_stats()
        relevant_snapshot = {}
        for node_id in relevant_nodes:
            if node_id not in node_stats:
                continue
            relevant_snapshot[node_id] = {
                "name": self._node_name.get(node_id, node_id),
                "node_stats": node_stats[node_id].model_dump(),
                "pressure": round(node_pressure.get(node_id, 0.0), 3),
                "projected_pressure": round(node_projected_pressure.get(node_id, 0.0), 3),
                "flow_trend": round(node_flow_trend.get(node_id, 0.0), 3),
            }

        return {
            "tick": self._tick,
            "active_agent": self._agent_mode,
            "shipment": {
                "id": shipment.id,
                "source_node": self._node_name.get(shipment.current_node, shipment.current_node),
                "destination_node": self._node_name.get(shipment.destination, shipment.destination),
                "load": shipment.load,
                "priority": shipment.priority.value,
                "deadline_tick": shipment.deadline_tick,
            },
            "aggregate_count": aggregate_count,
            "actual_decision": {
                "action": actual_action,
                "target_node": self._node_name.get(actual_hop.target, actual_hop.target) if actual_hop is not None else None,
            },
            "with_c_decision": {
                "action": str(counterfactual_decision.get("action", "hold")),
                "target_node": self._node_name.get(cf_target, cf_target) if cf_target is not None else None,
                "reason": str(counterfactual_decision.get("reason", "")),
                "expected_extra_cost": float(counterfactual_decision.get("expected_extra_cost", 0.0)),
            },
            "simulation_summary": {
                "queued_shipments": sum(len(q) for q in self._queues.values()),
                "in_transit_shipments": len(self._in_transit),
                "holding_shipments": len(self._holding),
                "consumed_shipments": self._consumed_shipments,
                "full_nodes": self._count_full_nodes(),
                "failed": self._failed,
                "failure_reason": self._failure_reason,
            },
            "relevant_nodes": relevant_snapshot,
        }

    def _push_log(
        self,
        message: str,
        *,
        counterfactual_diff: bool = False,
        counterfactual_agent: str | None = None,
        counterfactual_summary: str | None = None,
        counterfactual_context: dict[str, object] | None = None,
    ) -> None:
        self._recent_logs.insert(
            0,
            SimulationLog(
                log_id=next(self._log_seq),
                tick=self._tick,
                message=message,
                counterfactual_diff=counterfactual_diff,
                counterfactual_agent=counterfactual_agent,
                counterfactual_summary=counterfactual_summary,
                counterfactual_context=counterfactual_context,
            ),
        )
        if len(self._recent_logs) > 250:
            self._recent_logs = self._recent_logs[:250]

    def _apply_stress_events_for_tick(self, tick: int) -> None:
        events = self._stress_plan_by_tick.pop(tick, [])
        if not events:
            return

        total_injected = 0
        targets: list[str] = []
        for node_id, shipment_count in events:
            injected = self._demand_generator.inject_destination_spike(node_id, shipment_count=shipment_count)
            if injected <= 0:
                continue
            total_injected += injected
            targets.append(self._node_name.get(node_id, node_id))

        if total_injected > 0:
            self._push_log(
                f"stress profile injected {total_injected} shipments at tick {tick} "
                f"into {', '.join(targets)}"
            )

    def _validate_mass_balance(self) -> None:
        queue_count = 0
        in_transit_count = len(self._in_transit)
        holding_count = len(self._holding)
        pending_count = 0

        seen: dict[str, str] = {}
        duplicates: list[str] = []

        for node_id, queue in self._queues.items():
            for shipment in queue:
                queue_count += 1
                previous = seen.get(shipment.id)
                location = f"queue:{node_id}"
                if previous is not None:
                    duplicates.append(f"{shipment.id}({previous},{location})")
                else:
                    seen[shipment.id] = location

        for transit in self._in_transit:
            previous = seen.get(transit.shipment.id)
            location = f"in_transit:{transit.source}->{transit.target}"
            if previous is not None:
                duplicates.append(f"{transit.shipment.id}({previous},{location})")
            else:
                seen[transit.shipment.id] = location

        for item in self._holding:
            previous = seen.get(item.shipment.id)
            location = f"holding:{item.node_id}"
            if previous is not None:
                duplicates.append(f"{item.shipment.id}({previous},{location})")
            else:
                seen[item.shipment.id] = location

        for node_id, pending in self._pending_admission.items():
            for shipment in pending:
                pending_count += 1
                previous = seen.get(shipment.id)
                location = f"pending:{node_id}"
                if previous is not None:
                    duplicates.append(f"{shipment.id}({previous},{location})")
                else:
                    seen[shipment.id] = location

        if duplicates:
            preview = ", ".join(duplicates[:5])
            reason = f"data integrity error: duplicate shipment ids across states ({preview})"
            self._failed = True
            self._running = False
            self._failure_reason = reason
            self._demand_generator.set_running(False)
            self._push_log(reason)
            return

        live_count = queue_count + in_transit_count + holding_count + pending_count
        expected_total = self._ingested_shipments_total
        accounted_total = live_count + self._consumed_shipments + self._dropped_shipments_total

        if accounted_total != expected_total:
            reason = (
                "data integrity error: shipment mass mismatch "
                f"(expected={expected_total}, accounted={accounted_total}, live={live_count}, "
                f"consumed={self._consumed_shipments}, dropped={self._dropped_shipments_total})"
            )
            self._failed = True
            self._running = False
            self._failure_reason = reason
            self._demand_generator.set_running(False)
            self._push_log(reason)
