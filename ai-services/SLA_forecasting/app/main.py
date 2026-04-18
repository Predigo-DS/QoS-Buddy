from __future__ import annotations

import os
import json
import re
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


class CausalConvBlock(nn.Module):
    def __init__(self, in_ch: int, out_ch: int, kernel_size: int = 3, dilation: int = 1, dropout: float = 0.2):
        super().__init__()
        pad = (kernel_size - 1) * dilation
        self.conv = nn.Conv1d(in_ch, out_ch, kernel_size, padding=pad, dilation=dilation)
        self.chomp = lambda x: x[:, :, :-pad] if pad > 0 else x
        self.norm = nn.LayerNorm(out_ch)
        self.act = nn.GELU()
        self.drop = nn.Dropout(dropout)
        self.res_proj = nn.Conv1d(in_ch, out_ch, 1) if in_ch != out_ch else nn.Identity()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = self.conv(x)
        out = self.chomp(out)
        out = self.norm(out.transpose(1, 2)).transpose(1, 2)
        out = self.act(out)
        out = self.drop(out)
        return out + self.res_proj(x)


class TCNForecaster(nn.Module):
    def __init__(self, input_size: int, channels: list[int], num_classes: int, dropout: float = 0.2):
        super().__init__()
        layers: list[nn.Module] = []
        in_ch = input_size
        for i, out_ch in enumerate(channels):
            dilation = 2 ** i
            layers.append(
                CausalConvBlock(
                    in_ch,
                    out_ch,
                    kernel_size=3,
                    dilation=dilation,
                    dropout=dropout,
                )
            )
            in_ch = out_ch
        self.tcn = nn.Sequential(*layers)
        self.classifier = nn.Sequential(
            nn.Linear(in_ch, in_ch // 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(in_ch // 2, num_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = self.tcn(x.transpose(1, 2))
        out = out[:, :, -1]
        return self.classifier(out)


class BiLSTMForecaster(nn.Module):
    def __init__(
        self,
        input_size: int,
        hidden_size: int,
        num_layers: int,
        num_classes: int,
        dropout: float = 0.2,
        input_dropout: float = 0.0,
        lstm_out_dropout: float = 0.0,
        classifier_hidden_mult: float = 1.0,
    ):
        super().__init__()
        self.input_drop = nn.Dropout(input_dropout)
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
            bidirectional=True,
        )
        self.out_drop = nn.Dropout(lstm_out_dropout)
        self.norm = nn.LayerNorm(hidden_size * 2)

        cls_hidden = max(16, int(hidden_size * classifier_hidden_mult))
        self.classifier = nn.Sequential(
            nn.Linear(hidden_size * 2, cls_hidden),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(cls_hidden, num_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.input_drop(x)
        out, _ = self.lstm(x)
        last = self.out_drop(out[:, -1, :])
        return self.classifier(self.norm(last))


class ForecastRequest(BaseModel):
    run_id: str = Field(..., description="Run identifier used during preprocessing")
    segment: str = Field(..., description="Segment name used during preprocessing")
    rows: list[dict[str, Any]] = Field(..., description="Chronological raw telemetry rows")
    use_all_windows: bool = Field(
        default=False,
        description="If true, predicts on all sliding windows; otherwise predicts only latest window",
    )
    stride: int = Field(default=1, ge=1, description="Stride for sliding windows when use_all_windows=true")
    sla_alert_threshold: float = Field(default=0.30, ge=0.0, le=1.0)


class WindowForecast(BaseModel):
    window_index: int
    start_row: int
    end_row: int
    predicted_class: str
    predicted_class_index: int
    probabilities: dict[str, float]
    sla_risk_score: float
    sla_alert: bool


class ForecastResponse(BaseModel):
    run_id: str
    segment: str
    window_size: int
    horizon: int
    predictions: list[WindowForecast]


APP_DIR = Path(__file__).resolve().parent
if (APP_DIR / "train").exists():
    TRAIN_DIR = APP_DIR / "train"
else:
    TRAIN_DIR = APP_DIR.parent / "train"

ARTIFACT_DIR = TRAIN_DIR / "artifacts"
PREPROCESS_ARTIFACTS_PATH = ARTIFACT_DIR / "preprocess_artifacts.joblib"
LABEL_ENCODER_PATH = ARTIFACT_DIR / "label_encoder.pkl"
SEG_ENCODER_PATH = ARTIFACT_DIR / "seg_encoder.pkl"
CFG_PATH = ARTIFACT_DIR / "cfg.json"
BEST_BILSTM_PARAMS_PATH = ARTIFACT_DIR / "best_bilstm_params.json"
TCN_MODEL_PATH = ARTIFACT_DIR / "tcn_final.pt"
BILSTM_MODEL_PATH = ARTIFACT_DIR / "bilstm_final.pt"

_requested_device = os.getenv("MODEL_DEVICE", "cpu").strip().lower()
if _requested_device == "cuda" and torch.cuda.is_available():
    DEVICE = torch.device("cuda")
else:
    DEVICE = torch.device("cpu")

_state: dict[str, Any] = {}


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _safe_float_series(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce").astype(np.float32)


def _infer_tcn_from_state_dict(state_dict: dict[str, torch.Tensor]) -> dict[str, Any]:
    channels: list[int] = []
    i = 0
    while f"tcn.{i}.conv.weight" in state_dict:
        channels.append(int(state_dict[f"tcn.{i}.conv.weight"].shape[0]))
        i += 1

    if not channels:
        raise RuntimeError("Could not infer TCN channels from checkpoint")

    input_size = int(state_dict["tcn.0.conv.weight"].shape[1])
    num_classes = int(state_dict["classifier.3.weight"].shape[0])
    return {
        "input_size": input_size,
        "channels": channels,
        "num_classes": num_classes,
    }


def _infer_bilstm_from_state_dict(state_dict: dict[str, torch.Tensor]) -> dict[str, Any]:
    ih_l0 = state_dict["lstm.weight_ih_l0"]
    hidden_size = int(ih_l0.shape[0] // 4)
    input_size = int(ih_l0.shape[1])

    layer_indices: list[int] = []
    for key in state_dict:
        m = re.match(r"lstm\.weight_ih_l(\d+)$", key)
        if m:
            layer_indices.append(int(m.group(1)))
    num_layers = max(layer_indices) + 1 if layer_indices else 1

    classifier_hidden = int(state_dict["classifier.0.weight"].shape[0])
    num_classes = int(state_dict["classifier.3.weight"].shape[0])
    classifier_hidden_mult = float(classifier_hidden) / float(hidden_size)

    return {
        "input_size": input_size,
        "hidden_size": hidden_size,
        "num_layers": num_layers,
        "num_classes": num_classes,
        "classifier_hidden_mult": classifier_hidden_mult,
    }


def _build_models(input_size: int, num_classes: int, cfg: dict[str, Any]) -> tuple[nn.Module, nn.Module]:
    tcn_state = torch.load(TCN_MODEL_PATH, map_location="cpu")
    bilstm_state = torch.load(BILSTM_MODEL_PATH, map_location="cpu")

    tcn_meta = _infer_tcn_from_state_dict(tcn_state)
    bilstm_meta = _infer_bilstm_from_state_dict(bilstm_state)

    if tcn_meta["input_size"] != input_size or bilstm_meta["input_size"] != input_size:
        raise RuntimeError(
            "Feature count mismatch between preprocessing and checkpoints: "
            f"preprocess={input_size}, tcn_ckpt={tcn_meta['input_size']}, bilstm_ckpt={bilstm_meta['input_size']}"
        )

    if tcn_meta["num_classes"] != num_classes or bilstm_meta["num_classes"] != num_classes:
        raise RuntimeError(
            "Class count mismatch between label encoder and checkpoints: "
            f"encoder={num_classes}, tcn_ckpt={tcn_meta['num_classes']}, bilstm_ckpt={bilstm_meta['num_classes']}"
        )

    tcn = TCNForecaster(
        input_size=input_size,
        channels=tcn_meta["channels"],
        num_classes=num_classes,
        dropout=float(cfg["dropout"]),
    ).to(DEVICE)

    bilstm = BiLSTMForecaster(
        input_size=input_size,
        hidden_size=bilstm_meta["hidden_size"],
        num_layers=bilstm_meta["num_layers"],
        num_classes=num_classes,
        dropout=float(cfg.get("dropout", 0.2)),
        input_dropout=float(cfg.get("input_dropout", 0.0)),
        lstm_out_dropout=float(cfg.get("lstm_out_dropout", 0.0)),
        classifier_hidden_mult=float(bilstm_meta["classifier_hidden_mult"]),
    ).to(DEVICE)

    tcn.load_state_dict(tcn_state)
    bilstm.load_state_dict(bilstm_state)
    tcn.eval()
    bilstm.eval()
    return tcn, bilstm


def _load_state() -> None:
    required = [
        PREPROCESS_ARTIFACTS_PATH,
        LABEL_ENCODER_PATH,
        SEG_ENCODER_PATH,
        CFG_PATH,
        TCN_MODEL_PATH,
        BILSTM_MODEL_PATH,
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise FileNotFoundError(f"Missing required SLA artifacts: {missing}")

    preprocess = joblib.load(PREPROCESS_ARTIFACTS_PATH)
    label_encoder = joblib.load(LABEL_ENCODER_PATH)
    seg_encoder = joblib.load(SEG_ENCODER_PATH)
    cfg = _load_json(CFG_PATH)

    selected_features: list[str] = preprocess["selected_feature_columns"]
    all_engineered_features: list[str] = preprocess["all_engineered_feature_columns"]
    models = _build_models(
        input_size=len(selected_features),
        num_classes=len(label_encoder.classes_),
        cfg=cfg,
    )

    _state.update(
        {
            "preprocess": preprocess,
            "label_encoder": label_encoder,
            "seg_encoder": seg_encoder,
            "cfg": cfg,
            "selected_features": selected_features,
            "all_engineered_features": all_engineered_features,
            "tcn": models[0],
            "bilstm": models[1],
        }
    )


def _require_ready() -> None:
    keys = ["preprocess", "label_encoder", "seg_encoder", "tcn", "bilstm"]
    if any(k not in _state for k in keys):
        raise HTTPException(status_code=503, detail="SLA models are not ready")


def _preprocess_rows(df_raw: pd.DataFrame, run_id: str, segment: str) -> np.ndarray:
    preprocess = _state["preprocess"]
    seg_encoder = _state["seg_encoder"]

    group_key = f"{run_id}::{segment}"
    group_preprocessors: dict[str, Any] = preprocess["group_preprocessors"]
    if group_key not in group_preprocessors:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown run/segment '{group_key}'. Available keys: {list(group_preprocessors.keys())[:10]}",
        )

    scaler = group_preprocessors[group_key]["scaler"]
    cfg = _state["cfg"]
    all_engineered_features: list[str] = _state["all_engineered_features"]
    selected_features: list[str] = _state["selected_features"]

    df = df_raw.copy()
    if "timestamp" not in df.columns:
        raise HTTPException(status_code=400, detail="Each row must include 'timestamp'")

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    if df["timestamp"].isna().any():
        raise HTTPException(status_code=400, detail="Invalid timestamp format found in rows")

    df = df.sort_values("timestamp").reset_index(drop=True)

    for col in preprocess.get("drop_columns", []):
        if col in df.columns:
            df.drop(columns=[col], inplace=True)

    df["segment"] = segment
    df["seg_enc"] = seg_encoder.transform(df[["segment"]]).astype(np.float32)
    df.drop(columns=["segment"], inplace=True)

    hour = df["timestamp"].dt.hour
    df["hour_sin"] = np.sin(2 * np.pi * hour / 24).astype(np.float32)
    df["hour_cos"] = np.cos(2 * np.pi * hour / 24).astype(np.float32)
    df.drop(columns=["timestamp"], inplace=True)

    if "dataplane_latency_ms" in df.columns:
        df["dataplane_missing"] = df["dataplane_latency_ms"].isnull().astype(np.float32)

    if "video_start_time_ms" in df.columns:
        df["video_start_time_ms"] = _safe_float_series(df, "video_start_time_ms").clip(0, 1e5)

    if "flow_count" in df.columns:
        flow_count = _safe_float_series(df, "flow_count")
        df["flow_count"] = flow_count.replace(0, np.nan).ffill().bfill().fillna(0)

    df.drop_duplicates(inplace=True)
    df.reset_index(drop=True, inplace=True)

    for c in preprocess.get("log_columns", []):
        if c in df.columns:
            s = _safe_float_series(df, c)
            df[c] = np.log1p(s.clip(lower=0).fillna(0))

    for w in preprocess.get("rolling_windows", cfg["roll_windows"]):
        for c in ["e2e_delay_ms", "throughput_mbps", "mos_voice", "plr", "jitter_ms"]:
            if c in df.columns:
                col = _safe_float_series(df, c)
                df[c] = col
                df[f"{c}_rmean{w}"] = col.rolling(w, min_periods=1).mean()
                df[f"{c}_rstd{w}"] = col.rolling(w, min_periods=1).std().fillna(0)
                df[f"{c}_rmax{w}"] = col.rolling(w, min_periods=1).max()

    df.fillna(0, inplace=True)

    for col in all_engineered_features:
        if col not in df.columns:
            df[col] = 0.0

    X_all = df[all_engineered_features].astype(np.float32).values
    X_all = scaler.transform(X_all).astype(np.float32)

    selected_idx = [all_engineered_features.index(c) for c in selected_features]
    X_selected = X_all[:, selected_idx]
    return X_selected


def _build_windows(X: np.ndarray, window_size: int, use_all_windows: bool, stride: int) -> tuple[np.ndarray, list[tuple[int, int]]]:
    if len(X) < window_size:
        raise HTTPException(
            status_code=400,
            detail=f"Not enough rows ({len(X)}) for window_size={window_size}",
        )

    if not use_all_windows:
        start = len(X) - window_size
        return X[start: start + window_size][None, :, :], [(start, len(X) - 1)]

    windows = []
    spans: list[tuple[int, int]] = []
    for start in range(0, len(X) - window_size + 1, stride):
        end = start + window_size
        windows.append(X[start:end])
        spans.append((start, end - 1))

    return np.asarray(windows, dtype=np.float32), spans


def _ensemble_probs(windows: np.ndarray) -> np.ndarray:
    tcn: nn.Module = _state["tcn"]
    bilstm: nn.Module = _state["bilstm"]

    with torch.no_grad():
        xb = torch.as_tensor(windows, dtype=torch.float32, device=DEVICE)
        p_tcn = F.softmax(tcn(xb), dim=1)
        p_bilstm = F.softmax(bilstm(xb), dim=1)
        probs = (p_tcn + p_bilstm) / 2.0
    return probs.cpu().numpy()


app = FastAPI(title="QoS-Buddy SLA Forecasting Service", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup_event() -> None:
    _load_state()


@app.get("/health")
def health() -> dict[str, Any]:
    ready = all(k in _state for k in ["tcn", "bilstm", "preprocess", "label_encoder"])
    return {
        "status": "ok" if ready else "starting",
        "service": "sla_forecasting",
        "ready": ready,
    }


@app.get("/metadata")
def metadata() -> dict[str, Any]:
    _require_ready()
    preprocess = _state["preprocess"]
    classes = [str(c) for c in _state["label_encoder"].classes_]
    return {
        "window_size": int(preprocess.get("window_size", _state["cfg"]["window_size"])),
        "horizon": int(preprocess.get("horizon", _state["cfg"]["horizon"])),
        "selected_feature_columns": _state["selected_features"],
        "classes": classes,
        "run_segment_keys": preprocess.get("run_segment_keys", []),
    }


@app.post("/predict", response_model=ForecastResponse)
def predict(req: ForecastRequest) -> ForecastResponse:
    _require_ready()

    if not req.rows:
        raise HTTPException(status_code=400, detail="rows cannot be empty")

    preprocess = _state["preprocess"]
    label_encoder = _state["label_encoder"]

    X = _preprocess_rows(pd.DataFrame(req.rows), req.run_id, req.segment)
    window_size = int(preprocess.get("window_size", _state["cfg"]["window_size"]))
    horizon = int(preprocess.get("horizon", _state["cfg"]["horizon"]))

    windows, spans = _build_windows(
        X,
        window_size=window_size,
        use_all_windows=req.use_all_windows,
        stride=req.stride,
    )

    probs = _ensemble_probs(windows)
    pred_idx = probs.argmax(axis=1)
    pred_labels = label_encoder.inverse_transform(pred_idx)

    classes = [str(c) for c in label_encoder.classes_]
    risk_indices = [i for i, c in enumerate(classes) if c in {"CALL_DROP", "CAPACITY_EXHAUSTED"}]

    predictions: list[WindowForecast] = []
    for i, (start, end) in enumerate(spans):
        p = probs[i]
        prob_map = {classes[j]: float(p[j]) for j in range(len(classes))}
        risk_score = float(np.sum(p[risk_indices])) if risk_indices else 0.0
        predictions.append(
            WindowForecast(
                window_index=i,
                start_row=start,
                end_row=end,
                predicted_class=str(pred_labels[i]),
                predicted_class_index=int(pred_idx[i]),
                probabilities=prob_map,
                sla_risk_score=risk_score,
                sla_alert=bool(risk_score > req.sla_alert_threshold),
            )
        )

    return ForecastResponse(
        run_id=req.run_id,
        segment=req.segment,
        window_size=window_size,
        horizon=horizon,
        predictions=predictions,
    )
