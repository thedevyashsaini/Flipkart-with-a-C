import argparse
import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.demand_generator import DemandGenerator
from app.models.world import World
from app.simulator_core import SimulatorCore


def load_world(path: str | None = None) -> World:
    p = Path(path) if path else Path(__file__).resolve().parent.parent / "data" / "world.json"
    raw = json.loads(p.read_text(encoding="utf-8"))
    return World.model_validate(raw)


def collect(seeds: range, ticks_per_run: int, output: str, stress_profile: str | None = None):
    world = load_world()
    node_order = [n.id for n in world.nodes]
    all_records: list[dict] = []

    for seed in seeds:
        demand_gen = DemandGenerator(world, seed=seed, tick_interval_sec=999, window_size=600)
        sim = SimulatorCore(world, demand_gen, tick_interval_sec=999)

        demand_gen.configure(seed=seed, start_tick=0)
        sim.configure(start_tick=0)

        stress_seeds: list[int] = []
        if stress_profile == "random":
            import random
            stress_seeds = [seed ^ 0xA55]

        stress_nodes = [n.id for n in world.nodes if n.type.value == "hub"]
        import random as rnd

        for tick in range(ticks_per_run):
            if stress_profile == "cascade" and tick % 40 == 0 and tick > 0:
                stress_node = rnd.choice(stress_nodes)
                stress_count = rnd.randint(20, 40)
                demand_gen.inject_destination_spike(stress_node, shipment_count=stress_count)

            sim._tick_once(advance_demand=True)
            pressure, projected, trend = sim._pressure_maps()

            for node_id in node_order:
                all_records.append({
                    "seed": seed,
                    "tick": tick,
                    "node_id": node_id,
                    "pressure": pressure.get(node_id, 0.0),
                    "projected": projected.get(node_id, 0.0),
                    "flow_trend": trend.get(node_id, 0.0),
                })

        sys.stdout.write(f"seed {seed}: {ticks_per_run} ticks, {len(all_records)} records so far\n")
        sys.stdout.flush()

    table = pa.table({
        "seed": pa.array([r["seed"] for r in all_records], type=pa.int32()),
        "tick": pa.array([r["tick"] for r in all_records], type=pa.int32()),
        "node_id": pa.array([r["node_id"] for r in all_records], type=pa.string()),
        "pressure": pa.array([r["pressure"] for r in all_records], type=pa.float32()),
        "projected": pa.array([r["projected"] for r in all_records], type=pa.float32()),
        "flow_trend": pa.array([r["flow_trend"] for r in all_records], type=pa.float32()),
    })
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out_path)
    print(f"\nSaved {len(all_records)} records to {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=80)
    parser.add_argument("--ticks", type=int, default=200)
    parser.add_argument("--output", default="app/ai/data/pressure_history.parquet")
    parser.add_argument("--stress", choices=["none", "cascade"], default="cascade")
    args = parser.parse_args()
    collect(seeds=range(1, args.seeds + 1), ticks_per_run=args.ticks, output=args.output, stress_profile=args.stress)
