from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import joblib
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


class Attention(nn.Module):
    def __init__(self, hidden_dim: int):
        super().__init__()
        self.attn = nn.Linear(hidden_dim * 2, hidden_dim * 2)
        self.v = nn.Linear(hidden_dim * 2, 1, bias=False)

    def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
        weights = torch.softmax(
            self.v(torch.tanh(self.attn(hidden_states))).squeeze(2), dim=1
        )
        return torch.bmm(weights.unsqueeze(1), hidden_states).squeeze(1)


class AttnBiLSTMAutoencoder(nn.Module):
    def __init__(self, n_feat: int, hidden: int = 256, latent: int = 64, dropout: float = 0.2):
        super().__init__()
        self.encoder = nn.LSTM(n_feat, hidden, batch_first=True, bidirectional=True)
        self.attention = Attention(hidden)
        self.enc_fc = nn.Linear(hidden * 2, latent)
        self.enc_h_fc = nn.Linear(hidden * 2, hidden)
        self.enc_c_fc = nn.Linear(hidden * 2, hidden)
        self.decoder = nn.LSTM(latent, hidden, batch_first=True)
        self.dec_fc = nn.Linear(hidden, n_feat)
        self.drop = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        outputs, (h, c) = self.encoder(x)
        h_cat = torch.cat([h[0], h[1]], dim=-1)
        c_cat = torch.cat([c[0], c[1]], dim=-1)
        z = torch.relu(self.enc_fc(self.drop(self.attention(outputs))))
        h0 = torch.tanh(self.enc_h_fc(h_cat)).unsqueeze(0)
        c0 = torch.tanh(self.enc_c_fc(c_cat)).unsqueeze(0)
        out, _ = self.decoder(self.drop(z.unsqueeze(1).repeat(1, x.size(1), 1)), (h0, c0))
        return self.dec_fc(out)


class TransformerAE(nn.Module):
    def __init__(
        self,
        n_feat: int,
        d_model: int = 128,
        nhead: int = 4,
        num_layers: int = 3,
        max_len: int = 512,
        dropout: float = 0.2,
    ):
        super().__init__()
        self.embedding = nn.Linear(n_feat, d_model)
        self.pos_encoder = nn.Parameter(torch.randn(1, max_len, d_model))
        self.transformer = nn.TransformerEncoder(
            nn.TransformerEncoderLayer(
                d_model=d_model,
                nhead=nhead,
                batch_first=True,
                dropout=dropout,
            ),
            num_layers=num_layers,
        )
        self.decoder = nn.Linear(d_model, n_feat)
        self.drop = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        seq = x.size(1)
        x = self.embedding(x) + self.pos_encoder[:, :seq, :]
        return self.decoder(self.transformer(self.drop(x)))


class TCNAutoencoder(nn.Module):
    def __init__(self, n_feat: int, hidden: int = 128, latent: int = 64, dropout: float = 0.2):
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Conv1d(n_feat, hidden, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Conv1d(hidden, latent, kernel_size=3, padding=1),
            nn.ReLU(),
        )
        self.decoder = nn.Sequential(
            nn.ConvTranspose1d(latent, hidden, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.ConvTranspose1d(hidden, n_feat, kernel_size=3, padding=1),
            nn.Tanh(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.decoder(self.encoder(x.permute(0, 2, 1))).permute(0, 2, 1)


class PredictRequest(BaseModel):
    rows: list[dict[str, float | int | None]] = Field(
        ..., description="Ordered telemetry rows containing all expected feature columns"
    )
    stride: int | None = Field(
        default=None,
        ge=1,
        description="Window stride; defaults to window_size for train-like non-overlap windows",
    )
    threshold_name: Literal["best", "youden", "fpr_10", "fpr_5", "blind"] = "best"


class WindowPrediction(BaseModel):
    window_index: int
    start_row: int
    end_row: int
    reconstruction_score: float
    threshold_used: float
    is_anomaly: bool


class PredictResponse(BaseModel):
    model_type: str
    window_size: int
    stride: int
    threshold_name: str
    threshold_value: float
    total_windows: int
    anomaly_windows: int
    windows: list[WindowPrediction]


APP_DIR = Path(__file__).resolve().parent
if (APP_DIR / "train").exists():
    TRAIN_DIR = APP_DIR / "train"
else:
    TRAIN_DIR = APP_DIR.parent / "train"

ARTIFACTS_PATH = TRAIN_DIR / "outputs" / "inference_artifacts.joblib"
MODEL_PATH = TRAIN_DIR / "outputs" / "best_ae_model.pth"
_requested_device = os.getenv("MODEL_DEVICE", "cpu").strip().lower()
if _requested_device == "cuda" and torch.cuda.is_available():
    DEVICE = torch.device("cuda")
else:
    DEVICE = torch.device("cpu")

_state: dict[str, Any] = {}


def _build_model(model_type: str, model_params: dict[str, Any], n_feat: int) -> nn.Module:
    if model_type == "BiLSTM":
        return AttnBiLSTMAutoencoder(
            n_feat,
            hidden=int(model_params.get("hidden", 256)),
            latent=int(model_params.get("latent", 64)),
        )
    if model_type == "TCN":
        return TCNAutoencoder(
            n_feat,
            hidden=int(model_params.get("hidden", 128)),
            latent=int(model_params.get("latent", 64)),
        )

    nhead = int(model_params.get("nhead", 4))
    d_model_mult = int(model_params.get("d_model_mult", 32))
    return TransformerAE(n_feat, d_model=d_model_mult * nhead, nhead=nhead)


def _load_inference_state() -> None:
    if not ARTIFACTS_PATH.exists():
        raise FileNotFoundError(f"Missing artifact: {ARTIFACTS_PATH}")
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Missing model checkpoint: {MODEL_PATH}")

    artifacts = joblib.load(ARTIFACTS_PATH)
    features: list[str] = artifacts["features"]
    model_type: str = artifacts["model_type"]
    model_params: dict[str, Any] = artifacts.get("model_params", {})

    model = _build_model(model_type, model_params, n_feat=len(features)).to(DEVICE)
    state_dict = torch.load(MODEL_PATH, map_location=DEVICE)
    model.load_state_dict(state_dict)
    model.eval()

    _state["artifacts"] = artifacts
    _state["model"] = model


def _require_ready() -> None:
    if "model" not in _state or "artifacts" not in _state:
        raise HTTPException(status_code=503, detail="Model artifacts are not loaded")


def _apply_clips(X: np.ndarray, features: list[str], clip_bounds: dict[str, tuple[float, float]]) -> np.ndarray:
    X = X.copy()
    for i, feat in enumerate(features):
        lo, hi = clip_bounds[feat]
        X[:, i] = np.clip(X[:, i], lo, hi)
    return X


def _build_windows(X: np.ndarray, window_size: int, stride: int) -> tuple[np.ndarray, list[tuple[int, int]]]:
    windows: list[np.ndarray] = []
    ranges: list[tuple[int, int]] = []
    for start in range(0, len(X) - window_size + 1, stride):
        end = start + window_size
        windows.append(X[start:end])
        ranges.append((start, end - 1))

    if not windows:
        raise HTTPException(
            status_code=400,
            detail=f"Not enough rows ({len(X)}) for one window of size {window_size}",
        )

    return np.asarray(windows, dtype=np.float32), ranges


def _reconstruction_scores(model: nn.Module, windows: np.ndarray, batch_size: int = 512) -> np.ndarray:
    model.eval()
    scores: list[float] = []
    with torch.no_grad():
        for i in range(0, len(windows), batch_size):
            batch_np = windows[i : i + batch_size]
            batch = torch.as_tensor(batch_np, dtype=torch.float32, device=DEVICE)
            recon = model(batch).cpu().numpy()
            scores.extend(np.mean((batch_np - recon) ** 2, axis=(1, 2)).tolist())
    return np.asarray(scores, dtype=np.float64)


app = FastAPI(title="QoS-Buddy Anomaly Inference Service", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup_event() -> None:
    _load_inference_state()


@app.get("/health")
def health() -> dict[str, Any]:
    ready = "model" in _state and "artifacts" in _state
    return {
        "status": "ok" if ready else "starting",
        "service": "anomaly_detection",
        "ready": ready,
        "model_type": _state.get("artifacts", {}).get("model_type") if ready else None,
    }


@app.get("/metadata")
def metadata() -> dict[str, Any]:
    _require_ready()
    artifacts = _state["artifacts"]
    return {
        "model_type": artifacts["model_type"],
        "features": artifacts["features"],
        "window_size": int(artifacts["window_size"]),
        "thresholds": artifacts["thresholds"],
    }


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest) -> PredictResponse:
    _require_ready()
    artifacts = _state["artifacts"]
    model: nn.Module = _state["model"]

    features: list[str] = artifacts["features"]
    missing = [f for f in features if any(f not in row for row in req.rows)]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing feature(s): {missing}")

    window_size = int(artifacts["window_size"])
    stride = int(req.stride or window_size)
    threshold_name = req.threshold_name
    threshold = float(artifacts["thresholds"][threshold_name])

    df = pd.DataFrame(req.rows)
    X_raw = df[features].astype(np.float32).values
    X_clip = _apply_clips(X_raw, features, artifacts["clip_bounds"])
    X_scaled = artifacts["scaler"].transform(X_clip).astype(np.float32)

    windows, row_ranges = _build_windows(X_scaled, window_size, stride)
    scores = _reconstruction_scores(model, windows)
    preds = scores >= threshold

    output_windows = [
        WindowPrediction(
            window_index=i,
            start_row=s,
            end_row=e,
            reconstruction_score=float(scores[i]),
            threshold_used=threshold,
            is_anomaly=bool(preds[i]),
        )
        for i, (s, e) in enumerate(row_ranges)
    ]

    return PredictResponse(
        model_type=artifacts["model_type"],
        window_size=window_size,
        stride=stride,
        threshold_name=threshold_name,
        threshold_value=threshold,
        total_windows=len(output_windows),
        anomaly_windows=int(np.sum(preds)),
        windows=output_windows,
    )
