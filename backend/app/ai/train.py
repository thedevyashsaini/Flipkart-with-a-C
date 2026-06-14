import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset

from app.ai.model import PressurePredictor

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

DATA_DIR = Path(__file__).resolve().parent / "data"
MODEL_DIR = Path(__file__).resolve().parent


class PressureDataset(Dataset):
    def __init__(self, data_path: str, seq_len: int = 20, forecast_horizon: int = 12):
        table = pq.read_table(data_path)
        self.seq_len = seq_len
        self.forecast_horizon = forecast_horizon

        df = table.to_pandas()
        self.node_ids = sorted(df["node_id"].unique())
        self.num_nodes = len(self.node_ids)
        self.node_to_idx = {nid: i for i, nid in enumerate(self.node_ids)}

        self.X: list[np.ndarray] = []
        self.Y: list[np.ndarray] = []

        for seed, group in df.groupby("seed"):
            group = group.sort_values(["tick", "node_id"]).reset_index(drop=True)
            ticks = group["tick"].unique()
            if len(ticks) < seq_len + forecast_horizon:
                continue

            n_ticks = len(ticks)
            features = np.zeros((n_ticks, self.num_nodes, 3), dtype=np.float32)
            for i, tick in enumerate(sorted(ticks)):
                tick_data = group[group["tick"] == tick]
                for _, row in tick_data.iterrows():
                    idx = self.node_to_idx[row["node_id"]]
                    features[i, idx, 0] = row["pressure"]
                    features[i, idx, 1] = row["projected"]
                    features[i, idx, 2] = row["flow_trend"]

            for start in range(n_ticks - seq_len - forecast_horizon + 1):
                x_seq = features[start:start + seq_len]
                y_seq = features[start + seq_len:start + seq_len + forecast_horizon, :, 0]
                y_seq = y_seq.transpose(1, 0)
                self.X.append(x_seq.reshape(seq_len, -1))
                self.Y.append(y_seq)

        self.X = np.array(self.X, dtype=np.float32)
        self.Y = np.array(self.Y, dtype=np.float32)

        # Normalize features (per-feature across all timesteps)
        flat_x = self.X.reshape(-1, self.X.shape[-1])
        self.feat_mean = flat_x.mean(axis=0)
        self.feat_std = flat_x.std(axis=0) + 1e-8

        # Normalize targets (per-node across all windows)
        flat_y = self.Y.reshape(-1, self.Y.shape[-1])
        self.target_mean = flat_y.mean(axis=0)
        self.target_std = flat_y.std(axis=0) + 1e-8

        print(f"Windows: {len(self.X)}, Target mean: {flat_y.mean():.6f}, Target std: {flat_y.std():.6f}")

    def normalize_x(self, x):
        return (x - self.feat_mean) / self.feat_std

    def normalize_y(self, y):
        return (y - self.target_mean) / self.target_std

    def denormalize_y(self, y):
        return y * self.target_std + self.target_mean

    def __len__(self):
        return len(self.X)

    def __getitem__(self, idx):
        x = self.normalize_x(self.X[idx])
        y = self.normalize_y(self.Y[idx])
        return torch.tensor(x), torch.tensor(y)


def evaluate(model, loader, criterion, device):
    model.eval()
    total_loss = 0.0
    count = 0
    with torch.no_grad():
        for x, y in loader:
            x, y = x.to(device), y.to(device)
            pred = model(x)
            loss = criterion(pred, y)
            total_loss += loss.item() * x.size(0)
            count += x.size(0)
    return total_loss / max(count, 1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=str(DATA_DIR / "pressure_history.parquet"))
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--seq-len", type=int, default=20)
    parser.add_argument("--horizon", type=int, default=12)
    parser.add_argument("--hidden", type=int, default=128)
    parser.add_argument("--layers", type=int, default=2)
    parser.add_argument("--output", default=str(MODEL_DIR / "model.pt"))
    parser.add_argument("--graph-output", default=str(DATA_DIR / "training_curve.png"))
    args = parser.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    dataset = PressureDataset(args.data, seq_len=args.seq_len, forecast_horizon=args.horizon)
    num_nodes = dataset.num_nodes
    print(f"Nodes: {num_nodes}, Total windows: {len(dataset)}")

    split = int(len(dataset) * 0.85)
    train_ds, val_ds = torch.utils.data.random_split(dataset, [split, len(dataset) - split])
    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_ds, batch_size=args.batch_size * 2, num_workers=0)

    model = PressurePredictor(
        num_nodes=num_nodes,
        num_features=3,
        hidden_dim=args.hidden,
        num_layers=args.layers,
        forecast_horizon=args.horizon,
    ).to(device)

    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=5, factor=0.5)
    criterion = nn.MSELoss()

    best_val_loss = float("inf")
    patience_counter = 0
    train_losses = []
    val_losses = []

    for epoch in range(1, args.epochs + 1):
        model.train()
        total_loss = 0.0
        count = 0
        for x, y in train_loader:
            x, y = x.to(device), y.to(device)
            optimizer.zero_grad()
            pred = model(x)
            loss = criterion(pred, y)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total_loss += loss.item() * x.size(0)
            count += x.size(0)

        train_loss = total_loss / max(count, 1)
        val_loss = evaluate(model, val_loader, criterion, device)
        scheduler.step(val_loss)
        train_losses.append(train_loss)
        val_losses.append(val_loss)

        print(f"Epoch {epoch:3d}/{args.epochs}: train_loss={train_loss:.6f} val_loss={val_loss:.6f} lr={optimizer.param_groups[0]['lr']:.6f}")

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            torch.save({
                "model_state_dict": model.state_dict(),
                "num_nodes": num_nodes,
                "node_ids": dataset.node_ids,
                "feat_mean": dataset.feat_mean.tolist(),
                "feat_std": dataset.feat_std.tolist(),
                "target_mean": dataset.target_mean.tolist(),
                "target_std": dataset.target_std.tolist(),
                "seq_len": args.seq_len,
                "forecast_horizon": args.horizon,
                "hidden_dim": args.hidden,
                "num_layers": args.layers,
            }, args.output)
            patience_counter = 0
        else:
            patience_counter += 1
            if patience_counter >= 10:
                print(f"Early stopping at epoch {epoch}")
                break

    print(f"\nBest val_loss: {best_val_loss:.6f}")
    print(f"Model saved to {args.output}")

    # Training curve graph
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(2, 2, figsize=(16, 10), facecolor="#0d0e0c")

        for ax in axes.flat:
            ax.set_facecolor("#0d0e0c")
            ax.tick_params(colors="#d2d4d1", labelsize=10)
            ax.grid(True, color="#4a4c47", alpha=0.55, linewidth=0.85)
            for spine in ax.spines.values():
                spine.set_color("#9a9c99")
                spine.set_linewidth(1.0)

        # Loss curve
        ax = axes[0, 0]
        ax.plot(train_losses, color="#22d3ee", linewidth=2.0, label="Train")
        ax.plot(val_losses, color="#f59e0b", linewidth=2.0, label="Validation")
        ax.set_title("Training Loss", color="#e7e9e6", fontsize=13, pad=14)
        ax.set_xlabel("Epoch", color="#d2d4d1", fontsize=11)
        ax.set_ylabel("MSE Loss (normalized)", color="#d2d4d1", fontsize=11)
        legend = ax.legend(frameon=False, fontsize=10)
        for text in legend.get_texts():
            text.set_color("#d8dad7")

        # Predicted vs actual sample
        model.eval()
        val_x, val_y = next(iter(val_loader))
        with torch.no_grad():
            val_pred = model(val_x.to(device)).cpu()

        sample_node = 5
        y_actual = dataset.denormalize_y(val_y[0])[sample_node]
        y_pred = dataset.denormalize_y(val_pred[0])[sample_node]

        ax = axes[0, 1]
        ticks_fwd = list(range(1, args.horizon + 1))
        ax.plot(ticks_fwd, y_actual.numpy(), color="#34d399", linewidth=2.5, marker="o", markersize=5, label="Actual")
        ax.plot(ticks_fwd, y_pred.numpy(), color="#f87171", linewidth=2.5, marker="x", markersize=5, label="Predicted")
        ax.set_title("Predicted vs Actual Pressure (sample node)", color="#e7e9e6", fontsize=13, pad=14)
        ax.set_xlabel("Ticks Ahead", color="#d2d4d1", fontsize=11)
        ax.set_ylabel("Pressure", color="#d2d4d1", fontsize=11)
        legend = ax.legend(frameon=False, fontsize=10)
        for text in legend.get_texts():
            text.set_color("#d8dad7")

        # Scatter: all val preds vs actual
        all_actual = []
        all_pred = []
        for bx, by in val_loader:
            with torch.no_grad():
                bp = model(bx.to(device)).cpu()
            b_actual = dataset.denormalize_y(by)
            b_pred = dataset.denormalize_y(bp)
            for i in range(min(bx.size(0), 8)):
                all_actual.append(b_actual[i].reshape(-1).numpy())
                all_pred.append(b_pred[i].reshape(-1).numpy())
        all_actual = np.concatenate(all_actual)
        all_pred = np.concatenate(all_pred)

        ax = axes[1, 0]
        ax.scatter(all_actual, all_pred, alpha=0.3, s=2, color="#22d3ee")
        lims = [min(all_actual.min(), all_pred.min()), max(all_actual.max(), all_pred.max())]
        ax.plot(lims, lims, color="#f59e0b", linewidth=1.5, linestyle="--", alpha=0.7)
        ax.set_xlim(lims)
        ax.set_ylim(lims)
        ax.set_title("All Predictions: Actual vs Predicted", color="#e7e9e6", fontsize=13, pad=14)
        ax.set_xlabel("Actual Pressure", color="#d2d4d1", fontsize=11)
        ax.set_ylabel("Predicted Pressure", color="#d2d4d1", fontsize=11)

        # Error histogram
        errors = all_pred - all_actual
        ax = axes[1, 1]
        ax.hist(errors, bins=50, color="#f59e0b", alpha=0.7, edgecolor="none")
        ax.axvline(0, color="#34d399", linewidth=1.5, linestyle="--")
        ax.set_title(f"Error Distribution (MAE: {np.abs(errors).mean():.6f})", color="#e7e9e6", fontsize=13, pad=14)
        ax.set_xlabel("Prediction Error", color="#d2d4d1", fontsize=11)
        ax.set_ylabel("Count", color="#d2d4d1", fontsize=11)

        fig.tight_layout(pad=2.5)
        fig.savefig(args.graph_output, dpi=180, facecolor="#0d0e0c")
        print(f"Training graph saved to {args.graph_output}")
        plt.close(fig)
    except Exception as e:
        print(f"Graph generation skipped: {e}")

    # Save norm stats separately for inference
    norm_stats = {
        "feat_mean": dataset.feat_mean.tolist(),
        "feat_std": dataset.feat_std.tolist(),
        "target_mean": dataset.target_mean.tolist(),
        "target_std": dataset.target_std.tolist(),
        "node_ids": dataset.node_ids,
        "num_nodes": num_nodes,
    }
    with open(str(MODEL_DIR / "norm_stats.json"), "w") as f:
        json.dump(norm_stats, f)
    print(f"Norm stats saved to {MODEL_DIR / 'norm_stats.json'}")


if __name__ == "__main__":
    main()
