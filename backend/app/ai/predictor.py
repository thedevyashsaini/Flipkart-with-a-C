import json
import os
import time
from collections import deque
from pathlib import Path

import numpy as np
import torch

from app.ai.model import PressurePredictor

MODEL_DIR = Path(__file__).resolve().parent


class PressurePredictorInference:
    def __init__(self, model_path: str | None = None, norm_path: str | None = None):
        model_path = model_path or str(MODEL_DIR / "model.pt")
        norm_path = norm_path or str(MODEL_DIR / "norm_stats.json")

        if not os.path.exists(model_path):
            raise RuntimeError(f"Model not found at {model_path}. Run training first.")

        ckpt = torch.load(model_path, map_location="cpu")
        self.num_nodes = ckpt["num_nodes"]
        self.node_ids = ckpt["node_ids"]
        self.node_to_idx = {nid: i for i, nid in enumerate(self.node_ids)}
        self.seq_len = ckpt.get("seq_len", 20)
        self.forecast_horizon = ckpt.get("forecast_horizon", 12)
        self.hidden_dim = ckpt.get("hidden_dim", 128)
        self.num_layers = ckpt.get("num_layers", 2)

        self.feat_mean = torch.tensor(ckpt["feat_mean"])
        self.feat_std = torch.tensor(ckpt["feat_std"])
        self.target_mean = torch.tensor(ckpt["target_mean"]).view(1, 1, -1)
        self.target_std = torch.tensor(ckpt["target_std"]).view(1, 1, -1)

        self.model = PressurePredictor(
            num_nodes=self.num_nodes,
            hidden_dim=self.hidden_dim,
            num_layers=self.num_layers,
            forecast_horizon=self.forecast_horizon,
        )
        self.model.load_state_dict(ckpt["model_state_dict"])
        self.model.eval()

        # Circular buffer: chain_id -> deque of (pressure, projected, trend) dicts
        self._buffers: dict[str, deque] = {}

    def record(self, chain_id: str, pressure: dict[str, float], projected: dict[str, float], trend: dict[str, float]):
        if chain_id not in self._buffers:
            self._buffers[chain_id] = deque(maxlen=self.seq_len)
        entry = {nid: (pressure.get(nid, 0.0), projected.get(nid, 0.0), trend.get(nid, 0.0)) for nid in self.node_ids}
        self._buffers[chain_id].append(entry)

    def predict(self, chain_id: str) -> dict[str, dict[str, float]] | None:
        buf = self._buffers.get(chain_id)
        if buf is None or len(buf) < self.seq_len:
            return None

        last_entry = list(buf)[-1]

        # Build input tensor from buffer
        features = np.zeros((self.seq_len, self.num_nodes * 3), dtype=np.float32)
        for t, entry in enumerate(list(buf)):
            for i, nid in enumerate(self.node_ids):
                p, proj, tr = entry.get(nid, (0.0, 0.0, 0.0))
                features[t, i * 3] = p
                features[t, i * 3 + 1] = proj
                features[t, i * 3 + 2] = tr

        x = torch.tensor(features, dtype=torch.float32).unsqueeze(0)
        x_norm = (x - self.feat_mean) / self.feat_std

        with torch.no_grad():
            pred_norm = self.model(x_norm)

        # Denormalize: pred_norm shape (1, num_nodes, horizon)
        pred = pred_norm * self.target_std.squeeze() + self.target_mean.squeeze()
        pred = pred.clamp(min=0.0)

        result = {}
        for i, nid in enumerate(self.node_ids):
            result[nid] = {
                "current": round(float(last_entry.get(nid, (0.0, 0.0, 0.0))[0]), 4),
                "p3": round(float(pred[0, i, 2].item()), 4),
                "p6": round(float(pred[0, i, 5].item()), 4),
                "p12": round(float(pred[0, i, 11].item()), 4),
            }

        return result


# Global singleton
_predictor: PressurePredictorInference | None = None


def get_predictor() -> PressurePredictorInference | None:
    global _predictor
    if _predictor is not None:
        return _predictor
    try:
        _predictor = PressurePredictorInference()
        return _predictor
    except RuntimeError:
        return None
