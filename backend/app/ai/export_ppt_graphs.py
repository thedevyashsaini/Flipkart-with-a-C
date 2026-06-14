"""Export individual high-quality PPT graphs from trained model."""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pyarrow.parquet as pq
import torch
from torch.utils.data import DataLoader

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from app.ai.train import PressureDataset
from app.ai.model import PressurePredictor

DATA_DIR = Path(__file__).resolve().parent / "data"
MODEL_DIR = Path(__file__).resolve().parent
OUT_DIR = DATA_DIR / "ppt_graphs"
OUT_DIR.mkdir(exist_ok=True)

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# Load dataset and model
dataset = PressureDataset(str(DATA_DIR / "pressure_history.parquet"))
split = int(len(dataset) * 0.85)
_, val_ds = torch.utils.data.random_split(dataset, [split, len(dataset) - split])
val_loader = DataLoader(val_ds, batch_size=64, num_workers=0)

ckpt = torch.load(str(MODEL_DIR / "model.pt"), map_location="cpu")
model = PressurePredictor(
    num_nodes=ckpt["num_nodes"],
    hidden_dim=ckpt.get("hidden_dim", 128),
    num_layers=ckpt.get("num_layers", 2),
    forecast_horizon=ckpt.get("forecast_horizon", 12),
).to(device)
model.load_state_dict(ckpt["model_state_dict"])
model.eval()

# Collect all predictions
all_actual = []
all_pred = []
with torch.no_grad():
    for bx, by in val_loader:
        bp = model(bx.to(device)).cpu()
        b_actual = dataset.denormalize_y(by)
        b_pred = dataset.denormalize_y(bp)
        for i in range(bx.size(0)):
            all_actual.append(b_actual[i].reshape(-1).numpy())
            all_pred.append(b_pred[i].reshape(-1).numpy())
all_actual = np.concatenate(all_actual)
all_pred = np.concatenate(all_pred)

errors = all_pred - all_actual
mae = np.abs(errors).mean()
rmse = np.sqrt((errors**2).mean())
r = np.corrcoef(all_actual, all_pred)[0, 1]

print(f"MAE: {mae:.6f}, RMSE: {rmse:.6f}, Pearson r: {r:.4f}")
print(f"Samples: {len(all_actual)}")
print(f"Actual range: [{all_actual.min():.6f}, {all_actual.max():.6f}]")
print(f"Pred range: [{all_pred.min():.6f}, {all_pred.max():.6f}]")

DARK_BG = "#0d0e0c"
TEXT_COLOR = "#e7e9e6"
AXIS_COLOR = "#d2d4d1"
GRID_COLOR = "#4a4c47"
SPINE_COLOR = "#9a9c99"


def style_ax(ax):
    ax.set_facecolor(DARK_BG)
    ax.tick_params(colors=AXIS_COLOR, labelsize=12)
    ax.grid(True, color=GRID_COLOR, alpha=0.55, linewidth=0.85)
    for spine in ax.spines.values():
        spine.set_color(SPINE_COLOR)
        spine.set_linewidth(1.0)


# 1. Scatter plot
fig, ax = plt.subplots(figsize=(8, 7), facecolor=DARK_BG)
style_ax(ax)
ax.scatter(all_actual, all_pred, alpha=0.25, s=3, color="#22d3ee", rasterized=True)
lims = [min(all_actual.min(), all_pred.min()), max(all_actual.max(), all_pred.max())]
ax.plot(lims, lims, color="#f59e0b", linewidth=2, linestyle="--", alpha=0.8)
ax.set_xlim(lims)
ax.set_ylim(lims)
ax.set_xlabel("Actual Pressure", color=TEXT_COLOR, fontsize=13)
ax.set_ylabel("Predicted Pressure", color=TEXT_COLOR, fontsize=13)
ax.set_title(f"GRU Predictions: Actual vs Predicted\nMAE={mae:.5f}, RMSE={rmse:.5f}, r={r:.3f}",
             color=TEXT_COLOR, fontsize=14, pad=16)
fig.tight_layout(pad=1.5)
fig.savefig(str(OUT_DIR / "scatter_actual_vs_predicted.png"), dpi=200, facecolor=DARK_BG)
print(f"Saved scatter to {OUT_DIR / 'scatter_actual_vs_predicted.png'}")
plt.close(fig)

# 2. Error histogram
fig, ax = plt.subplots(figsize=(8, 5.5), facecolor=DARK_BG)
style_ax(ax)
n, bins, patches = ax.hist(errors, bins=60, color="#f59e0b", alpha=0.75, edgecolor="none")
ax.axvline(0, color="#34d399", linewidth=2.5, linestyle="--", alpha=0.9)
ax.set_xlabel("Prediction Error (Actual - Predicted)", color=TEXT_COLOR, fontsize=13)
ax.set_ylabel("Count", color=TEXT_COLOR, fontsize=13)
ax.set_title(f"Error Distribution (MAE={mae:.5f})", color=TEXT_COLOR, fontsize=14, pad=16)
fig.tight_layout(pad=1.5)
fig.savefig(str(OUT_DIR / "error_histogram.png"), dpi=200, facecolor=DARK_BG)
print(f"Saved histogram to {OUT_DIR / 'error_histogram.png'}")
plt.close(fig)

# 3. Sample node forecast (time series)
fig, ax = plt.subplots(figsize=(9, 5), facecolor=DARK_BG)
style_ax(ax)
val_x, val_y = next(iter(val_loader))
with torch.no_grad():
    val_pred = model(val_x.to(device)).cpu()
sample_node = 5
horizon = ckpt.get("forecast_horizon", 12)
ticks_fwd = list(range(1, horizon + 1))
y_actual = dataset.denormalize_y(val_y[0])[sample_node]
y_pred = dataset.denormalize_y(val_pred[0])[sample_node]
ax.plot(ticks_fwd, y_actual.numpy(), color="#34d399", linewidth=2.8, marker="o", markersize=6, label="Actual")
ax.plot(ticks_fwd, y_pred.numpy(), color="#f87171", linewidth=2.8, marker="x", markersize=6, label="Predicted")
ax.set_xlabel("Ticks Ahead", color=TEXT_COLOR, fontsize=13)
ax.set_ylabel("Pressure", color=TEXT_COLOR, fontsize=13)
ax.set_title(f"Node {dataset.node_ids[sample_node]}: Forecast Over {horizon} Ticks",
             color=TEXT_COLOR, fontsize=14, pad=16)
legend = ax.legend(frameon=False, fontsize=12)
for text in legend.get_texts():
    text.set_color("#d8dad7")
fig.tight_layout(pad=1.5)
fig.savefig(str(OUT_DIR / "sample_forecast.png"), dpi=200, facecolor=DARK_BG)
print(f"Saved forecast to {OUT_DIR / 'sample_forecast.png'}")
plt.close(fig)

# 4. Accuracy metrics summary as a table-figure
fig, ax = plt.subplots(figsize=(7, 3.5), facecolor=DARK_BG)
ax.axis("off")
ax.set_facecolor(DARK_BG)

metrics = [
    ("Metric", "Value"),
    ("Samples", f"{len(all_actual):,}"),
    ("Mean Absolute Error (MAE)", f"{mae:.6f}"),
    ("Root Mean Squared Error (RMSE)", f"{rmse:.6f}"),
    ("Pearson Correlation (r)", f"{r:.4f}"),
    ("R-squared (r\u00b2)", f"{r*r:.4f}"),
    ("Actual Pressure Range", f"[{all_actual.min():.4f}, {all_actual.max():.4f}]"),
    ("Predicted Pressure Range", f"[{all_pred.min():.4f}, {all_pred.max():.4f}]"),
    ("Mean Error (bias)", f"{errors.mean():.6f}"),
]
table = ax.table(cellText=metrics[1:], colLabels=metrics[0],
                 cellLoc="left", loc="center",
                 colWidths=[0.55, 0.35])
table.auto_set_font_size(False)
table.set_fontsize(12)
table.scale(1, 1.6)

for (row, col), cell in table.get_celld().items():
    cell.set_edgecolor(SPINE_COLOR)
    cell.set_linewidth(0.8)
    if row == 0:
        cell.set_text_props(color=TEXT_COLOR, fontweight="bold")
        cell.set_facecolor("#1a1b19")
    else:
        cell.set_text_props(color="#d8dad7")
        cell.set_facecolor(DARK_BG)

ax.set_title("GRU Pressure Predictor - Accuracy Metrics",
             color=TEXT_COLOR, fontsize=14, pad=20)
fig.tight_layout(pad=1.5)
fig.savefig(str(OUT_DIR / "accuracy_metrics.png"), dpi=200, facecolor=DARK_BG)
print(f"Saved metrics to {OUT_DIR / 'accuracy_metrics.png'}")
plt.close(fig)

print(f"\nAll PPT graphs exported to {OUT_DIR}")
print(f"  - scatter_actual_vs_predicted.png")
print(f"  - error_histogram.png")
print(f"  - sample_forecast.png")
print(f"  - accuracy_metrics.png")
