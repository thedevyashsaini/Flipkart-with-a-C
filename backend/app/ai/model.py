import torch
import torch.nn as nn


class PressurePredictor(nn.Module):
    def __init__(self, num_nodes: int, num_features: int = 3, hidden_dim: int = 128, num_layers: int = 2, dropout: float = 0.2, forecast_horizon: int = 12):
        super().__init__()
        self.num_nodes = num_nodes
        self.num_features = num_features
        self.forecast_horizon = forecast_horizon
        input_dim = num_nodes * num_features

        self.norm = nn.LayerNorm(input_dim)
        self.gru = nn.GRU(input_dim, hidden_dim, num_layers, batch_first=True, dropout=dropout if num_layers > 1 else 0)
        self.fc1 = nn.Linear(hidden_dim, hidden_dim * 2)
        self.relu = nn.ReLU()
        self.dropout = nn.Dropout(dropout)
        self.fc2 = nn.Linear(hidden_dim * 2, hidden_dim * 2)
        self.fc_out = nn.Linear(hidden_dim * 2, num_nodes * forecast_horizon)

    def forward(self, x):
        B, T, F = x.shape
        x = self.norm(x)
        out, _ = self.gru(x)
        last = out[:, -1, :]
        h = self.relu(self.fc1(last))
        h = self.dropout(h)
        h = self.relu(self.fc2(h))
        h = self.dropout(h)
        h = self.fc_out(h)
        return h.view(B, self.num_nodes, self.forecast_horizon)
