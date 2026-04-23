from __future__ import annotations

import heapq
from dataclasses import dataclass

from app.agents.without_c import PlannedHop
from app.models import ShipmentPriority, World


@dataclass(frozen=True)
class FlowDecision:
    action: str
    hop: PlannedHop | None = None
    reason: str = ""
    expected_extra_cost: float = 0.0


class WithCAgent:
    SAFE_TARGET_PRESSURE = 1.1
    HARD_RISK_PRESSURE = 1.28
    FORECAST_HORIZON_MIN = 3
    FORECAST_HORIZON_MAX = 7
    FORECAST_HORIZON_PAD = 2
    TREND_WEIGHT = 0.08

    def __init__(self, world: World) -> None:
        self._node_by_id = {node.id: node for node in world.nodes}
        self._adj: dict[str, list[PlannedHop]] = {node.id: [] for node in world.nodes}
        self._rev_adj: dict[str, list[tuple[str, float]]] = {node.id: [] for node in world.nodes}
        for edge in world.edges:
            hop = PlannedHop(source=edge.source, target=edge.target, base_eta=edge.base_eta, base_cost=edge.base_cost)
            self._adj.setdefault(edge.source, []).append(hop)
            self._rev_adj.setdefault(edge.target, []).append((edge.source, edge.base_cost))

        self._distance_cache: dict[str, dict[str, float]] = {}
        self._baseline_hop_cache: dict[tuple[str, str], PlannedHop | None] = {}

    def decide(
        self,
        *,
        source: str,
        destination: str,
        priority: ShipmentPriority,
        node_pressure: dict[str, float],
        node_projected_pressure: dict[str, float],
        node_flow_trend: dict[str, float],
    ) -> FlowDecision:
        if source == destination:
            return FlowDecision(action="arrive", hop=None, reason="at-destination")

        baseline_hop = self._baseline_hop(source, destination)
        distances = self._distances_to(destination)
        baseline_remaining = distances.get(source, float("inf"))
        if baseline_hop is None or baseline_remaining == float("inf"):
            return FlowDecision(action="hold", hop=None, reason="no-reachable-path")

        candidates = self.ranked_hops(
            source=source,
            destination=destination,
            priority=priority,
            node_pressure=node_pressure,
            node_projected_pressure=node_projected_pressure,
            node_flow_trend=node_flow_trend,
        )
        baseline_candidate: tuple[float, float, float, float, PlannedHop] | None = None
        for item in candidates:
            if item[4].target == baseline_hop.target:
                baseline_candidate = item
                break

        if not candidates:
            return FlowDecision(action="hold", hop=None, reason="no-feasible-hop")

        if baseline_candidate is not None and baseline_candidate[3] < self.SAFE_TARGET_PRESSURE:
            return FlowDecision(
                action="move",
                hop=baseline_candidate[4],
                reason="baseline-safe-forecast",
                expected_extra_cost=round(max(0.0, baseline_candidate[1]), 3),
            )

        safe_candidates = [item for item in candidates if item[3] < self.SAFE_TARGET_PRESSURE]
        if safe_candidates:
            chosen = min(safe_candidates, key=lambda item: (item[2], item[1], item[0], item[4].target))
            return FlowDecision(action="move", hop=chosen[4], reason="forecast-safe-reroute", expected_extra_cost=round(max(0.0, chosen[1]), 3))

        baseline_risk = baseline_candidate[3] if baseline_candidate is not None else float("inf")
        mitigations = [item for item in candidates if item[3] <= self.HARD_RISK_PRESSURE and item[3] + 0.08 < baseline_risk]
        if mitigations:
            chosen = min(mitigations, key=lambda item: (item[3], item[1], item[2], item[4].target))
            return FlowDecision(action="move", hop=chosen[4], reason="reduce-near-term-risk", expected_extra_cost=round(max(0.0, chosen[1]), 3))

        if baseline_candidate is not None and baseline_candidate[3] <= self.HARD_RISK_PRESSURE:
            return FlowDecision(
                action="move",
                hop=baseline_candidate[4],
                reason="baseline-under-hard-risk",
                expected_extra_cost=round(max(0.0, baseline_candidate[1]), 3),
            )

        # Keep flow moving unless there is literally no feasible path.
        # Holding is reserved for unreachable/blocked path states, not as default risk response.
        chosen = min(candidates, key=lambda item: (item[3], item[2], item[1], item[4].target))
        return FlowDecision(action="move", hop=chosen[4], reason="least-risk-forward", expected_extra_cost=round(max(0.0, chosen[1]), 3))

    def ranked_hops(
        self,
        *,
        source: str,
        destination: str,
        priority: ShipmentPriority,
        node_pressure: dict[str, float],
        node_projected_pressure: dict[str, float],
        node_flow_trend: dict[str, float],
    ) -> list[tuple[float, float, float, float, PlannedHop]]:
        distances = self._distances_to(destination)
        baseline_remaining = distances.get(source, float("inf"))
        if baseline_remaining == float("inf"):
            return []

        rel_limit, abs_limit = self._extra_cost_limits(priority)
        extra_cost_limit = max(abs_limit, baseline_remaining * rel_limit)

        ranked: list[tuple[float, float, float, float, PlannedHop]] = []
        for hop in self._adj.get(source, []):
            remaining = distances.get(hop.target, float("inf"))
            if remaining == float("inf"):
                continue

            route_cost = hop.base_cost + remaining
            extra_cost = route_cost - baseline_remaining
            if extra_cost > extra_cost_limit:
                continue

            pressure = node_pressure.get(hop.target, 0.0)
            projected = node_projected_pressure.get(hop.target, pressure)
            trend = max(0.0, node_flow_trend.get(hop.target, 0.0))
            horizon = max(self.FORECAST_HORIZON_MIN, min(self.FORECAST_HORIZON_MAX, hop.base_eta + self.FORECAST_HORIZON_PAD))
            forecast_risk = projected + (trend * horizon * self.TREND_WEIGHT)
            score = route_cost + self._risk_penalty(hop.target, pressure, projected, forecast_risk)
            ranked.append((score, extra_cost, route_cost, forecast_risk, hop))

        ranked.sort(key=lambda item: (item[0], item[1], item[2], item[4].target))
        return ranked

    def _distances_to(self, destination: str) -> dict[str, float]:
        cached = self._distance_cache.get(destination)
        if cached is not None:
            return cached

        dist = {node_id: float("inf") for node_id in self._node_by_id}
        if destination not in dist:
            return dist

        dist[destination] = 0.0
        pq: list[tuple[float, str]] = [(0.0, destination)]
        while pq:
            current_cost, node_id = heapq.heappop(pq)
            if current_cost > dist[node_id]:
                continue
            for prev_node, edge_cost in self._rev_adj.get(node_id, []):
                next_cost = current_cost + edge_cost
                if next_cost >= dist[prev_node]:
                    continue
                dist[prev_node] = next_cost
                heapq.heappush(pq, (next_cost, prev_node))

        self._distance_cache[destination] = dist
        return dist

    def _baseline_hop(self, source: str, destination: str) -> PlannedHop | None:
        key = (source, destination)
        if key in self._baseline_hop_cache:
            return self._baseline_hop_cache[key]

        distances = self._distances_to(destination)
        best: tuple[float, PlannedHop] | None = None
        for hop in self._adj.get(source, []):
            remaining = distances.get(hop.target, float("inf"))
            if remaining == float("inf"):
                continue
            total = hop.base_cost + remaining
            if best is None or total < best[0]:
                best = (total, hop)

        self._baseline_hop_cache[key] = None if best is None else best[1]
        return self._baseline_hop_cache[key]

    def _extra_cost_limits(self, priority: ShipmentPriority) -> tuple[float, float]:
        if priority == ShipmentPriority.CRITICAL:
            return (0.7, 5.0)
        if priority == ShipmentPriority.HIGH:
            return (1.0, 6.0)
        if priority == ShipmentPriority.MEDIUM:
            return (1.2, 7.0)
        return (1.4, 8.0)

    def _risk_penalty(self, node_id: str, pressure: float, projected_pressure: float, forecast_risk: float) -> float:
        node = self._node_by_id.get(node_id)
        warehouse_relief = -0.55 if node is not None and node.type.value == "warehouse" else 0.0
        overload_penalty = max(0.0, forecast_risk - 1.0) * 5.0
        return 1.7 * forecast_risk + 0.9 * projected_pressure + 0.5 * pressure + overload_penalty + warehouse_relief
