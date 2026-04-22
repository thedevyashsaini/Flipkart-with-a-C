from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from itertools import count

from app.agents.without_c import PlannedHop, WithoutCAgent
from app.demand_generator import DemandGenerator
from app.models import EdgeSimulationStat, NodeSimulationStat, ShipmentDemand, SimulationLog, SimulationState, World


@dataclass
class SimShipment:
    id: str
    source: str
    destination: str
    load: float
    created_tick: int
    current_node: str
    deadline_tick: int


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
    FAILURE_FULL_NODE_THRESHOLD = 2
    FULL_NODE_UTILIZATION_THRESHOLD = 0.98

    def __init__(self, world: World, demand_generator: DemandGenerator, tick_interval_sec: float = 1.0) -> None:
        self._world = world
        self._demand_generator = demand_generator
        self._tick_interval_sec = tick_interval_sec
        self._agent = WithoutCAgent(world)
        self._running = False
        self._failed = False
        self._failure_reason = ""

        self._node_by_id = {n.id: n for n in world.nodes}
        self._node_name = {n.id: n.name for n in world.nodes}
        self._edge_by_pair = {(e.source, e.target): e for e in world.edges}
        self._dispatch_limit = {n.id: max(3, int(n.capacity / 30)) for n in world.nodes}

        self._tick = 0
        self._shipment_seq = count(1)
        self._state_lock = threading.Lock()

        self._queues: dict[str, deque[SimShipment]] = defaultdict(deque)
        self._in_transit: list[InTransitShipment] = []
        self._holding: list[HoldingShipment] = []
        self._pending_admission: dict[str, deque[SimShipment]] = defaultdict(deque)
        self._consumed_shipments = 0
        self._consumed_shipments_tick_by_node: dict[str, int] = {}
        self._recent_logs: list[SimulationLog] = []
        self._last_edge_move_counts: dict[str, int] = {}
        self._last_edge_move_loads: dict[str, float] = {}
        self._latest_state = SimulationState(
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
            self._queues = defaultdict(deque)
            self._in_transit = []
            self._holding = []
            self._pending_admission = defaultdict(deque)
            self._consumed_shipments = 0
            self._consumed_shipments_tick_by_node = {}
            self._recent_logs = []
            self._last_edge_move_counts = {}
            self._last_edge_move_loads = {}
            self._latest_state = self._build_state(0, 0, 0, 0.0)

        for _ in range(start_tick):
            self._demand_generator.advance_one_tick()
            self._tick_once()

    def get_state(self) -> SimulationState:
        with self._state_lock:
            return self._latest_state

    def _run_forever(self) -> None:
        while True:
            with self._state_lock:
                should_run = self._running
            if should_run:
                self._tick_once()
            time.sleep(self._tick_interval_sec)

    def _tick_once(self) -> None:
        with self._state_lock:
            self._tick += 1
            blocked_admission_tick = self._retry_pending_admission()
            new_demands = self._demand_generator.drain_new_shipments()
            generated_shipments, blocked_new = self._ingest_demands(new_demands)
            blocked_admission_tick += blocked_new

            moved_shipments_tick, moved_load_tick = self._dispatch_moves()
            self._advance_transit()
            self._advance_holding()
            self._evaluate_failure()

            self._latest_state = self._build_state(generated_shipments, blocked_admission_tick, moved_shipments_tick, moved_load_tick)

    def _build_state(self, generated_shipments: int, blocked_admission_tick: int, moved_shipments_tick: int, moved_load_tick: float) -> SimulationState:
        return SimulationState(
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
            queue_load = sum(shipment.load for shipment in node_queue)
            active_load = queue_load + holding_load_by_node[node.id]
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
            )
            if self._node_can_accept(demand.source, shipment.load):
                self._queues[demand.source].append(shipment)
                generated += 1
            else:
                self._pending_admission[demand.source].append(shipment)
                blocked += 1
        if generated > 0:
            self._push_log(f"loaded {generated} shipments into simulation queues")
        if blocked > 0:
            self._push_log(f"blocked admission for {blocked} shipments at full source nodes")
        return generated, blocked

    def _retry_pending_admission(self) -> int:
        admitted = 0
        for node_id, pending in self._pending_admission.items():
            while pending:
                shipment = pending[0]
                if not self._node_can_accept(node_id, shipment.load):
                    break
                pending.popleft()
                self._queues[node_id].append(shipment)
                admitted += 1

        self._pending_admission = defaultdict(deque, {node_id: queue for node_id, queue in self._pending_admission.items() if queue})
        if admitted > 0:
            self._push_log(f"admitted {admitted} pending shipments into nodes with free capacity")
        return 0

    def _dispatch_moves(self) -> tuple[int, float]:
        moved_count = 0
        moved_load = 0.0
        lane_counters: dict[tuple[str, str], int] = defaultdict(int)
        edge_move_counts: dict[str, int] = defaultdict(int)
        edge_move_loads: dict[str, float] = defaultdict(float)

        for node_id, queue in self._queues.items():
            if not queue:
                continue

            dispatch_budget = self._dispatch_limit.get(node_id, 3)
            moved_from_node = 0

            while moved_from_node < dispatch_budget and queue:
                shipment = queue[0]
                hop = self._agent.next_hop(shipment.current_node, shipment.destination)
                if hop is None:
                    queue.popleft()
                    hold_ticks = self._hold_ticks_for_destination(shipment.destination, shipment.load)
                    self._holding.append(HoldingShipment(shipment=shipment, node_id=shipment.destination, remaining_hold_ticks=hold_ticks))
                    lane_counters[(shipment.current_node, shipment.destination)] += 1
                    moved_count += 1
                    moved_load += shipment.load
                    moved_from_node += 1
                    continue

                if not self._is_edge_valid(hop):
                    queue.popleft()
                    self._push_log(
                        f"dropped {shipment.id}: no valid edge from {self._node_name.get(shipment.current_node, shipment.current_node)}"
                    )
                    continue

                if not self._node_can_accept(hop.target, shipment.load):
                    if moved_from_node == 0:
                        self._push_log(
                            f"blocked at {self._node_name.get(node_id, node_id)}: next node {self._node_name.get(hop.target, hop.target)} is full"
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
                moved_count += 1
                moved_load += shipment.load
                moved_from_node += 1

        for (source, target), count_value in lane_counters.items():
            self._push_log(
                f"moved {count_value} shipments from {self._node_name.get(source, source)} to {self._node_name.get(target, target)}"
            )

        self._last_edge_move_counts = dict(edge_move_counts)
        self._last_edge_move_loads = {edge_id: round(value, 2) for edge_id, value in edge_move_loads.items()}

        return moved_count, moved_load

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
        return self._node_current_load(node_id) + incoming_load <= node.capacity

    def _count_full_nodes(self) -> int:
        return len(self._full_node_ids())

    def _full_node_ids(self) -> list[str]:
        full_node_ids: list[str] = []
        for node in self._world.nodes:
            if self._node_current_load(node.id) >= node.capacity * self.FULL_NODE_UTILIZATION_THRESHOLD:
                full_node_ids.append(node.id)
        return full_node_ids

    def _full_node_names(self) -> list[str]:
        return [self._node_name.get(node_id, node_id) for node_id in self._full_node_ids()]

    def _evaluate_failure(self) -> None:
        full_node_ids = self._full_node_ids()
        full_nodes = len(full_node_ids)
        if full_nodes > self.FAILURE_FULL_NODE_THRESHOLD:
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

    def _push_log(self, message: str) -> None:
        self._recent_logs.insert(0, SimulationLog(tick=self._tick, message=message))
        if len(self._recent_logs) > 250:
            self._recent_logs = self._recent_logs[:250]
