from __future__ import annotations

import heapq
from dataclasses import dataclass

from app.models import World


@dataclass(frozen=True)
class PlannedHop:
    source: str
    target: str
    base_eta: int
    base_cost: float


class WithoutCAgent:
    def __init__(self, world: World) -> None:
        self._adj: dict[str, list[PlannedHop]] = {}
        for node in world.nodes:
            self._adj[node.id] = []
        for edge in world.edges:
            self._adj.setdefault(edge.source, []).append(
                PlannedHop(
                    source=edge.source,
                    target=edge.target,
                    base_eta=edge.base_eta,
                    base_cost=edge.base_cost,
                )
            )

        self._cache: dict[tuple[str, str], PlannedHop | None] = {}

    def next_hop(self, source: str, destination: str) -> PlannedHop | None:
        key = (source, destination)
        if key in self._cache:
            return self._cache[key]

        if source == destination:
            self._cache[key] = None
            return None

        result = self._compute_next_hop(source, destination)
        self._cache[key] = result
        return result

    def _compute_next_hop(self, source: str, destination: str) -> PlannedHop | None:
        pq: list[tuple[float, int, str]] = [(0.0, 0, source)]
        best: dict[str, tuple[float, int]] = {source: (0.0, 0)}
        first_hop: dict[str, PlannedHop] = {}

        while pq:
            current_cost, current_eta, node_id = heapq.heappop(pq)
            if node_id == destination:
                return first_hop.get(node_id)

            known = best.get(node_id)
            if known is None or (current_cost, current_eta) > known:
                continue

            for hop in self._adj.get(node_id, []):
                next_cost = current_cost + hop.base_cost
                next_eta = current_eta + hop.base_eta
                next_node = hop.target
                current_best = best.get(next_node)
                if current_best is not None and (next_cost, next_eta) >= current_best:
                    continue

                best[next_node] = (next_cost, next_eta)
                first_hop[next_node] = hop if node_id == source else first_hop[node_id]
                heapq.heappush(pq, (next_cost, next_eta, next_node))

        return None
