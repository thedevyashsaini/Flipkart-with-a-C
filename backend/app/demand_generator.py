from __future__ import annotations

import random
import threading
import time
from collections import defaultdict
from itertools import count

from app.models import DemandLaneSummary, DemandState, ShipmentDemand, ShipmentPriority, SKUClass, World


class DemandGenerator:
    def __init__(self, world: World, seed: int = 7, tick_interval_sec: float = 1.0, window_size: int = 400) -> None:
        self._world = world
        self._tick_interval_sec = tick_interval_sec
        self._window_size = window_size
        self._seed = seed
        self._rng_core = random.Random(seed)
        self._rng_inject = random.Random(seed ^ 0x9E3779B9)
        self._shipment_seq = count(1)
        self._state_lock = threading.Lock()
        self._running = False

        self._shipments: list[ShipmentDemand] = []
        self._new_shipments_buffer: list[ShipmentDemand] = []
        self._tick = 0
        self._total_generated = 0
        self._latest_state = DemandState(
            seed=seed,
            tick=0,
            running=False,
            total_generated=0,
            in_window_shipments=0,
            within_region=[],
            cross_region=[],
        )

        self._nodes = list(world.nodes)
        self._node_by_id = {node.id: node for node in self._nodes}
        self._by_region: dict[str, list[str]] = defaultdict(list)
        self._node_region: dict[str, str] = {}

        for node in self._nodes:
            self._by_region[node.region].append(node.id)
            self._node_region[node.id] = node.region

        self._source_region_weights: dict[str, float] = {
            "IN-NORTH": 1.1,
            "IN-WEST": 1.25,
            "IN-SOUTH": 1.4,
            "IN-EAST": 0.95,
            "ME": 0.75,
            "EU": 0.55,
        }
        self._cross_region_bias: dict[str, float] = {
            "IN-NORTH": 0.5,
            "IN-WEST": 0.58,
            "IN-SOUTH": 0.56,
            "IN-EAST": 0.45,
            "ME": 0.62,
            "EU": 0.5,
        }
        self._lane_preferences: dict[str, dict[str, float]] = {
            "IN-NORTH": {"ME": 1.6, "EU": 1.3, "IN-WEST": 1.1, "IN-EAST": 0.9, "IN-SOUTH": 0.85},
            "IN-WEST": {"ME": 1.9, "EU": 1.15, "IN-NORTH": 0.95, "IN-SOUTH": 1.0, "IN-EAST": 0.75},
            "IN-SOUTH": {"ME": 1.7, "EU": 1.1, "IN-WEST": 1.05, "IN-EAST": 0.95, "IN-NORTH": 0.9},
            "IN-EAST": {"ME": 1.1, "EU": 0.9, "IN-NORTH": 1.0, "IN-WEST": 0.85, "IN-SOUTH": 1.05},
            "ME": {"EU": 2.0, "IN-WEST": 1.4, "IN-NORTH": 1.2, "IN-SOUTH": 1.3, "IN-EAST": 0.8},
            "EU": {"ME": 2.2, "IN-NORTH": 1.1, "IN-WEST": 1.0, "IN-SOUTH": 1.0, "IN-EAST": 0.75},
        }

    def start(self) -> None:
        thread = threading.Thread(target=self._run_forever, daemon=True)
        thread.start()

    def advance_one_tick(self) -> None:
        self._tick_once()

    def set_running(self, running: bool) -> None:
        with self._state_lock:
            self._running = running
            self._latest_state = self._build_state()

    def configure(self, seed: int, start_tick: int = 0) -> None:
        if start_tick < 0:
            start_tick = 0

        with self._state_lock:
            self._seed = seed
            self._rng_core = random.Random(seed)
            self._rng_inject = random.Random(seed ^ 0x9E3779B9)
            self._running = False
            self._shipments = []
            self._new_shipments_buffer = []
            self._tick = 0
            self._total_generated = 0
            self._shipment_seq = count(1)

            for _ in range(start_tick):
                self._tick += 1
                generated = self._generate_tick_shipments(self._tick)
                self._shipments.extend(generated)
                self._new_shipments_buffer.extend(generated)
                self._total_generated += len(generated)
                if len(self._shipments) > self._window_size:
                    self._shipments = self._shipments[-self._window_size :]

            self._latest_state = self._build_state()

    def clear(self) -> None:
        self.configure(seed=self._seed, start_tick=0)

    def get_state(self) -> DemandState:
        with self._state_lock:
            return self._latest_state

    def inject_destination_spike(self, destination: str, shipment_count: int = 28) -> int:
        with self._state_lock:
            destination_node = self._node_by_id.get(destination)
            if destination_node is None:
                return 0

            rng = self._rng_inject

            generated: list[ShipmentDemand] = []
            candidate_sources = [node.id for node in self._nodes if node.id != destination]
            if not candidate_sources:
                return 0

            for _ in range(shipment_count):
                source = rng.choice(candidate_sources)
                source_region = self._node_region[source]
                destination_region = destination_node.region
                load = round(rng.uniform(1.2, 3.8), 2)
                base_deadline = 7 if source_region == destination_region else 14
                jitter = rng.randint(0, 10)
                generated.append(
                    ShipmentDemand(
                        id=f"sh{next(self._shipment_seq):06d}",
                        source=source,
                        destination=destination,
                        source_region=source_region,
                        destination_region=destination_region,
                        load=load,
                        priority=ShipmentPriority.HIGH if rng.random() < 0.7 else ShipmentPriority.CRITICAL,
                        deadline_tick=self._tick + base_deadline + jitter,
                        sku_class=self._sample_sku(rng),
                        created_tick=self._tick,
                    )
                )

            self._shipments.extend(generated)
            self._new_shipments_buffer.extend(generated)
            self._total_generated += len(generated)

            if len(self._shipments) > self._window_size:
                self._shipments = self._shipments[-self._window_size :]

            self._latest_state = self._build_state()
            return len(generated)

    def drain_new_shipments(self) -> list[ShipmentDemand]:
        with self._state_lock:
            drained = self._new_shipments_buffer
            self._new_shipments_buffer = []
            return drained

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
            generated = self._generate_tick_shipments(self._tick)
            self._shipments.extend(generated)
            self._new_shipments_buffer.extend(generated)
            self._total_generated += len(generated)

            if len(self._shipments) > self._window_size:
                self._shipments = self._shipments[-self._window_size :]

            self._latest_state = self._build_state()

    def _generate_tick_shipments(self, tick: int) -> list[ShipmentDemand]:
        rng = self._rng_core
        low = 10
        high = 24
        if tick % 15 == 0:
            low = 26
            high = 46

        count_this_tick = rng.randint(low, high)
        generated: list[ShipmentDemand] = []

        regions = list(self._by_region.keys())
        hotspot_lane: tuple[str, str] | None = None
        if tick % 20 == 0:
            hotspots = [
                ("IN-WEST", "ME"),
                ("IN-SOUTH", "ME"),
                ("IN-NORTH", "EU"),
                ("ME", "EU"),
                ("IN-EAST", "IN-SOUTH"),
            ]
            hotspot_lane = rng.choice(hotspots)

        for _ in range(count_this_tick):
            source_region = self._weighted_choice(self._source_region_weights, rng)
            cross_region = rng.random() < self._cross_region_bias.get(source_region, 0.5)

            if hotspot_lane and rng.random() < 0.26:
                source_region, destination_region = hotspot_lane
            elif cross_region:
                destination_region = self._sample_cross_destination(source_region, regions, rng)
            else:
                destination_region = source_region

            if source_region not in self._by_region or destination_region not in self._by_region:
                continue

            source = rng.choice(self._by_region[source_region])
            destination_candidates = [n for n in self._by_region[destination_region] if n != source]
            if not destination_candidates:
                continue
            destination = rng.choice(destination_candidates)

            priority = self._sample_priority(rng)
            sku_class = self._sample_sku(rng)
            load = round(rng.uniform(0.4, 3.2), 2)
            base_deadline = 6 if source_region == destination_region else 12
            jitter = rng.randint(0, 12)

            shipment = ShipmentDemand(
                id=f"sh{next(self._shipment_seq):06d}",
                source=source,
                destination=destination,
                source_region=source_region,
                destination_region=destination_region,
                load=load,
                priority=priority,
                deadline_tick=tick + base_deadline + jitter,
                sku_class=sku_class,
                created_tick=tick,
            )
            generated.append(shipment)

        return generated

    def _weighted_choice(self, weights: dict[str, float], rng: random.Random) -> str:
        items = [(key, value) for key, value in weights.items() if value > 0 and key in self._by_region]
        total = sum(value for _, value in items)
        pick = rng.random() * total
        cursor = 0.0
        for key, value in items:
            cursor += value
            if pick <= cursor:
                return key
        return items[-1][0]

    def _sample_cross_destination(self, source_region: str, regions: list[str], rng: random.Random) -> str:
        candidates = [r for r in regions if r != source_region]
        preferred = self._lane_preferences.get(source_region, {})
        weighted = {region: preferred.get(region, 1.0) for region in candidates}
        return self._weighted_choice(weighted, rng)

    def _sample_priority(self, rng: random.Random) -> ShipmentPriority:
        p = rng.random()
        if p < 0.52:
            return ShipmentPriority.LOW
        if p < 0.82:
            return ShipmentPriority.MEDIUM
        if p < 0.95:
            return ShipmentPriority.HIGH
        return ShipmentPriority.CRITICAL

    def _sample_sku(self, rng: random.Random) -> SKUClass:
        p = rng.random()
        if p < 0.55:
            return SKUClass.STANDARD
        if p < 0.75:
            return SKUClass.ELECTRONICS
        if p < 0.9:
            return SKUClass.PERISHABLE
        return SKUClass.PHARMA

    def _build_state(self) -> DemandState:
        lane_counts: dict[tuple[str, str], int] = defaultdict(int)
        lane_loads: dict[tuple[str, str], float] = defaultdict(float)

        for shipment in self._shipments:
            key = (shipment.source_region, shipment.destination_region)
            lane_counts[key] += 1
            lane_loads[key] += shipment.load

        within: list[DemandLaneSummary] = []
        cross: list[DemandLaneSummary] = []

        for (source_region, destination_region), count_value in lane_counts.items():
            total_load = round(lane_loads[(source_region, destination_region)], 2)
            avg_load = round(total_load / max(count_value, 1), 2)
            summary = DemandLaneSummary(
                source_region=source_region,
                destination_region=destination_region,
                shipment_count=count_value,
                total_load=total_load,
                avg_load=avg_load,
            )

            if source_region == destination_region:
                within.append(summary)
            else:
                cross.append(summary)

        within.sort(key=lambda x: x.shipment_count, reverse=True)
        cross.sort(key=lambda x: x.shipment_count, reverse=True)

        return DemandState(
            seed=self._seed,
            tick=self._tick,
            running=self._running,
            total_generated=self._total_generated,
            in_window_shipments=len(self._shipments),
            within_region=within,
            cross_region=cross,
        )
