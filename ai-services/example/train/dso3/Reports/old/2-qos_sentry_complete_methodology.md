# QoS Sentry — Complete Methodology & Architecture Brief
## Synthesis: Experiments + Opus Guidance + 2-Minute Horizon Analysis

**Purpose:** Full methodology brief to generate the final optimised notebooks for LSTM, TCN, and Transformer.  
**Status:** All numbers verified against actual dataset and notebook outputs.

---

## Part 1 — Project & Data Facts (Ground Truth)

### Dataset
- 93,276 rows × 33 columns, 4 segments × 23,319 rows each
- Sampling interval: **2.06 seconds**
- 6 QoE classes: NORMAL (31%), LOW_THROUGHPUT (24%), POOR_VOICE_QUALITY (17%), HIGH_LATENCY (15%), CALL_DROP (8%), CAPACITY_EXHAUSTED (4%)
- Imbalance ratio: 7.2×
- **All 4 segments share identical label sequences** but have significantly different feature distributions (e2e_delay: INTERNET=7.7ms, INDOOR_RAN=49.6ms)
- Split: 70% train / 15% val / 15% test — strictly chronological, no shuffle ever

### State persistence (the defining data characteristic)
- Average state run: **78 steps (~160s)**
- All degradation classes (LOW_THROUGHPUT, HIGH_LATENCY, CALL_DROP, CAPACITY_EXHAUSTED, POOR_VOICE_QUALITY) have minimum run ≥ 57 steps
- NORMAL has short bursts: min=2 steps, mean=49 steps
- Self-transition probabilities: 0.979–0.991 across all classes
- Transitions: **297 per segment (1.3%)** — all route through NORMAL (hub state)

### Transition rates by horizon (verified)
| Horizon | Seconds | Transition rate | Oversampling needed |
|---|---|---|---|
| h=1 | 2.1s | 1.3% | 19× |
| h=5 | 10.3s | 6.2% | 3.8× |
| h=30 | 61.8s | 26.2% | 1× (none) |
| h=58 | 119.5s | 48.7% | 1× (none) |

### Pre-transition signal (critical finding)
Checking e2e_delay in 30 steps before each transition:
- Mean slope: **-0.0085 ms/step** (essentially flat)
- Only 31% of transitions have a positive (rising) slope beforehand
- 19% are pure step functions
- **Conclusion:** Mininet transitions are largely abrupt, not ramped. There is minimal pre-transition signal in raw features. The 2-minute prediction must rely on label state context, not feature trends.

---

## Part 2 — The Epoch-1 High Val Metric Phenomenon

### What happens and why
`label_encoded` (current QoE class as numeric feature) + its lags and rolling stats are included in every window. Since states persist for 78 steps on average:

| Horizon | P(last_window_label == target) | Val F1 at epoch 1 |
|---|---|---|
| h=1 | 0.987 | 0.929 (fixed), 0.930 (optimised) |
| h=3 | 0.962 | 0.854 (fixed) |
| h=5 | 0.938 | 0.636 (fixed) |
| h=58 | 0.514 | ~0.3–0.4 (expected — no shortcut possible) |

After 1–2 gradient steps, the model discovers the shortcut: "read `label_encoded[t-1]` and repeat it." This gives instant ~0.93 val accuracy at epoch 1 — **not data leakage**, but a valid shortcut that masks transition learning.

### Why it is still a problem
The shortcut works for 98.7% of sequences but fails completely at the 1.3% that cross a state transition. Those transitions are exactly what QoS Sentry needs to predict. A model stuck at the shortcut:
- Gets CALL_DROP F1=0.97 only because windows already in CALL_DROP stay there
- Scores Macro F1=0.358 because transitions are never learned
- Cannot provide any advance warning — it only confirms what is already happening

### Proven result
The fixed notebook broke the shortcut by combining: all 4 segments (4× data), transition oversampling 19×, and class weighting. Val F1 at epoch 1 was still 0.929 (shortcut active) but val F1 at epoch 5 rose to 0.949 (transitions being learned on top). Final test Macro F1 = **0.918 at t+1, 0.816 at t+3**.

---

## Part 3 — Experiment History (All Results)

| Version | t+1 F1 | t+3 F1 | t+5 F1 | Key issue |
|---|---|---|---|---|
| Broken (original) | 0.358 | 0.417 | 0.420 | Single segment, no label feature, early stop ep8 |
| Fixed | **0.918** | **0.816** | 0.458 | Best t+1/t+3. t+5 overfit (val loss=2.39) |
| Optimised v1 (broken) | 0.711 | 0.551 | 0.398 | Focal+3×boost=gradient explosion, epoch-1 save |
| Optimised v3 | 0.881 | 0.690 | **0.492** | Early stop on val F1 fixed t+5; t+1/t+3 regressed |

**Current best to beat per horizon:**
- t+1: **0.918** (fixed notebook)
- t+3: **0.816** (fixed notebook)  
- t+5: **0.492** (optimised v3)
- t+58: no baseline yet

---

## Part 4 — Methodology from Opus (Verified & Grounded)

### 4.1 Early stopping — transition-aware with warmup

**Problem:** Both val loss and val F1 are dominated by the 98.7% easy majority. Val loss saves epoch-1 shortcut; val F1 at epoch 1 is already high before transitions are learned.

**Solution: Two-phase protocol**

**Phase 1 — Warmup (epochs 1–5):** Train normally, never save weights. Forces the model past the shortcut equilibrium before early stopping can fire.

**Phase 2 — Monitor transition-only val F1:** After epoch 5, evaluate every epoch on only the transition sequences in the validation set (`target ≠ last_window_label`). Smooth with 3-epoch running average before applying patience.

```python
# Transition-only val F1
val_mask = (val_targets != val_last_window_labels)
trans_preds  = model_preds[val_mask]
trans_true   = val_targets[val_mask]
trans_f1     = f1_score(trans_true, trans_preds, average='macro', zero_division=0)
```

**Transition val set sizes (verified):**
- h=1: ~126 sequences → noisy, use 3-epoch smoothing
- h=5: ~604 sequences → acceptable signal
- h=30: ~2,554 sequences → good signal
- h=58: ~4,738 sequences → strong signal

**Secondary guard:** If overall val F1 drops below 0.85, restore the best overall checkpoint — the model is catastrophically forgetting the easy cases.

**Patience:** 10 epochs on smoothed transition-F1, starting from epoch 6.

**Note for t+58:** The epoch-1 shortcut does not exist (naive accuracy = 51.4%). Standard early stopping on val Macro F1 with patience=15 works correctly. No warmup needed.

### 4.2 Multi-task single model with 3–4 independent heads (replaces AR chaining)

**Problem with AR chaining:** t+1 errors (12% of cases) cascade into t+3 and t+5, widening the val/test gap. Verified: optimised v3 val/test gap at t+5 = 0.20.

**Solution: One shared LSTM/TCN/Transformer backbone → multiple independent classification heads**

```
Input window (60 × 73 features)
        ↓
  [Shared Backbone] → context vector
        ├── Head_t1:  LayerNorm → Linear(hidden, hidden//2) → GELU → Dropout → Linear(num_classes)
        ├── Head_t5:  LayerNorm → Linear(hidden, hidden//2) → GELU → Dropout → Linear(num_classes)
        ├── Head_t30: LayerNorm → Linear(hidden, hidden//2) → GELU → Dropout → Linear(num_classes)
        └── Head_t58: LayerNorm → Linear(hidden, hidden//2) → GELU → Dropout → Linear(num_classes)
```

**Loss: weighted sum (horizon-dependent)**
```python
loss = (1.0 * loss_t1 + 0.7 * loss_t5 + 0.5 * loss_t30 + 0.3 * loss_t58)
```
Longer horizons are harder and produce noisier gradients — down-weight to prevent gradient pollution of the shared backbone.

**Advantages over AR chaining:**
- No error propagation between horizons
- t+1 head acts as auxiliary task regularising the backbone
- Backbone gets 3–4× gradient signal (helps transition learning)
- One forward pass at inference (lower SDN controller latency)
- Simpler training pipeline

**Optional soft AR:** If desired, pass t+1 head's raw logits (not argmax, not softmax) to t+5/t+30/t+58 heads via a residual connection. Soft version avoids hard error propagation. Start with independent heads first.

### 4.3 Val/test distribution gap

**Empirical finding:** Val F1 is consistently higher than test F1 (t+5 gap=0.20 in optimised v3). This is a structural property: the last 15% of the Mininet simulation has different transition patterns.

**Actions:**
1. Verify empirically: compute transition matrix for val period vs test period — if frequencies differ, the gap is genuine distributional shift
2. Report both val and test metrics in all results tables
3. Do NOT use stratified splits — this breaks temporal ordering (fatal for time series)
4. For academic report: explain the gap as an inherent property of fixed-simulation data; note that production retraining on recent data would mitigate it

### 4.4 Oversampling strategy per horizon

At h=58, the transition rate is 48.7% — oversampling is counterproductive. The correct weight per horizon (to reach ~20% transitions per batch):

| Horizon | Transition rate | Oversampling weight |
|---|---|---|
| h=1 | 1.3% | 19× |
| h=5 | 6.2% | 4× |
| h=30 | 26.2% | 1× (disabled) |
| h=58 | 48.7% | 1× (disabled) |

In the multi-task model: use the h=1 weight (19×) for the WeightedRandomSampler since all heads share the same batch. This over-samples transitions for all heads simultaneously — acceptable because h=5/30/58 still have plenty of non-transition sequences.

---

## Part 5 — Architecture Specifications

### 5.1 LSTM (proven baseline)

```python
# Shared backbone
lstm = nn.LSTM(
    input_size    = INPUT_SIZE,   # 73
    hidden_size   = 128,
    num_layers    = 2,
    batch_first   = True,
    dropout       = 0.3,
    bidirectional = False,        # causal — mandatory
)
# Last hidden state → context (128-dim)
context = out[:, -1, :]

# Per-head (one per horizon)
head = nn.Sequential(
    nn.LayerNorm(128),
    nn.Linear(128, 64),
    nn.GELU(),
    nn.Dropout(0.3),
    nn.Linear(64, NUM_CLASSES),
)
```

**Training protocol:**
- Optimizer: AdamW, lr=5e-4, weight_decay=2e-4
- Scheduler: CosineAnnealingWarmRestarts(T_0=20, T_mult=2, eta_min=1e-6)
- Gradient clip: 1.0
- Loss: CrossEntropyLoss(weight=class_weights, label_smoothing=0.0)
- Class weights: balanced + ×2.0 boost on CALL_DROP and CAPACITY_EXHAUSTED
- Early stop: 5-epoch warmup + transition-only val F1, patience=10
- Batch size: 128, max_epochs: 100
- Oversampling: WeightedRandomSampler(transition_weight=19)

### 5.2 TCN (next model)

**Critical Opus correction:** Previous plan used dilations [1,2,4] → receptive field = 15 timesteps (NOT enough for window=60). Correct configuration:

```python
dilations    = [1, 2, 4, 8, 16]   # receptive field = 63 steps > 60 ✓
channels     = [64, 64, 128, 128, 128]
kernel_size  = 3
```

Receptive field calculation: 1 + (3-1)×(1+2+4+8+16) = 1 + 2×31 = **63 timesteps** ✓

```python
class CausalConv1d(nn.Module):
    # Left-padding only — no future leakage
    padding = (kernel_size - 1) * dilation
    # Trim right after conv

class TCNBlock(nn.Module):
    # Two causal convs + residual projection
    # Weight normalisation (NOT BatchNorm — more stable with WeightedRandomSampler)
    conv1 = weight_norm(CausalConv1d(in_ch, out_ch, kernel, dilation))
    conv2 = weight_norm(CausalConv1d(out_ch, out_ch, kernel, dilation))
    proj  = weight_norm(nn.Conv1d(in_ch, out_ch, 1)) if in_ch != out_ch else nn.Identity()
```

**Training protocol:**
- lr=1e-3 (TCNs tolerate higher LR than LSTM)
- weight_decay=1e-4 (t+1/t+5), 5e-4 (t+30/t+58 heads)
- Gradient clip: 1.0
- Same early stopping as LSTM (warmup + transition-F1)
- Same loss and class weights

**Key advantage:** Fixed inference latency — cannot attend to last timestep disproportionately in the same way LSTM can. The shortcut is less trivially learned, which may paradoxically help by forcing earlier transition learning.

### 5.3 Transformer (third model)

```python
d_model        = 64
nhead          = 4
num_enc_layers = 2
dim_feedforward= 128
dropout        = 0.3
attention_dropout = 0.3  # separate, higher than standard dropout
```

**Opus corrections:**
1. **Learnable positional embeddings** (not sinusoidal) — with only 60 positions, learnable embeddings can encode the importance of position 60 (most recent) explicitly
2. **Causal mask** (upper triangular) — mandatory, prevents future leakage
3. **Attention dropout = 0.3** — Transformers can trivially learn to attend only to the last position's `label_encoded`, recreating the shortcut faster than LSTM; dropout mitigates this
4. **Pre-LN** (norm_first=True in PyTorch) — more stable than post-LN
5. **Input projection**: Linear(73, 64) before positional encoding

**Training protocol:**
- lr=5e-4 with **linear warmup over 5 epochs** then cosine decay — Transformers are sensitive to initial LR
- weight_decay=1e-3 (Transformers overfit more on 65k sequences)
- Gradient clip: **0.5** (attention gradients can spike; more aggressive than LSTM/TCN)
- Same early stopping as LSTM/TCN

**Critical risk mitigation:** During training, randomly drop the last timestep's features with probability 0.1 to force the model to also attend to earlier timesteps (prevents attention collapsing to position 60).

---

## Part 6 — Feature Engineering (Same for All Three Models)

All three architectures share the identical feature pipeline. This is mandatory for a valid architectural comparison.

### Pipeline steps (in order)
1. Sort by timestamp (essential for chronological integrity)
2. Drop dead/metadata columns: run_id, datetime, mos_source, switch_id, rebuffering_count, total_stall_seconds, rx_dropped, tx_dropped
3. Hard-cap video_start_time_ms at 1e8 (raw max = 2.73e20)
4. flow_count: forward-fill zeros (0 = missing, not truly idle)
5. Drop exact duplicates
6. IQR clipping (Q1−3×IQR, Q3+3×IQR) computed on train rows only
7. Interpolate NaNs linearly (dataplane_latency_ms: 29% missing)
8. **Label encoding** → `label_encoded` added as feature
9. One-hot encode segment (4 columns)
10. Rolling stats window=10 on SLA features (rmean, rstd, rmax): e2e_delay, jitter, plr, throughput, mos_voice, ctrl_plane_rtt, availability
11. **Rolling stats on label_encoded** (window=10): rmean, rstd, rmin, rmax
12. Lag features (lags 1,3,5) on: e2e_delay, throughput, mos_voice, plr
13. **Lag features on label_encoded** (lags 1,3,5): `label_lag1`, `label_lag3`, `label_lag5`
14. Rate-of-change (diff): e2e_delay, throughput, plr
15. **`label_diff`** (rate of change of state — detects transition onset)
16. Domain composites: voice_pressure, throughput_gap, stream_stress, flow_pressure
17. Cyclical time: hour_sin, hour_cos
18. RobustScaler fitted on train rows only, per segment

### For t+58 specifically: additional features (Opus recommendation)

Add longer rolling windows and slope features to capture gradual trends:
```python
# Longer rolling windows for 2-min prediction
for col in SLA_FEATURES:
    df[f'{col}_rmean_30'] = df[col].rolling(30, min_periods=1).mean()
    df[f'{col}_rmean_60'] = df[col].rolling(60, min_periods=1).mean()
    df[f'{col}_rstd_30']  = df[col].rolling(30, min_periods=1).std().fillna(0)

# Linear slope over last 30 timesteps (trend detection)
for col in ['e2e_delay_ms', 'throughput_mbps', 'jitter_ms', 'plr']:
    slopes = []
    for i in range(len(df)):
        w = df[col].iloc[max(0,i-30):i+1].values
        if len(w) >= 2:
            slopes.append(np.polyfit(range(len(w)), w, 1)[0])
        else:
            slopes.append(0.0)
    df[f'{col}_slope30'] = slopes
```

**Note:** Pre-transition signal in this Mininet data is weak (slope mean = -0.0085 ms/step, near zero). But including these features costs nothing and may capture the minority of ramped transitions (31% of cases).

**Total features:** ~73 base + ~24 additional for t+58 = ~97 features

---

## Part 7 — Horizon Strategy (Final)

| Horizon | Seconds | Use case | Oversampling | Early stop |
|---|---|---|---|---|
| **t+1** | ~2s | Automated SDN rerouting, QoS marking | 19× | Warmup+transition-F1 |
| **t+5** | ~10s | SDN flow reallocation, operator alert | 4× | Warmup+transition-F1 |
| **t+30** | ~62s | Load balancer pre-activation, user warning | 1× | Warmup+transition-F1 |
| **t+58** | ~120s | Capacity provisioning, CDN failover | 1× | Standard val-F1, patience=15 |

All four horizons handled by a **single multi-task model** with one shared backbone and four independent classification heads.

---

## Part 8 — Training Protocol Summary (All Models)

| Setting | LSTM | TCN | Transformer |
|---|---|---|---|
| Optimizer | AdamW | AdamW | AdamW |
| Base LR | 5e-4 | 1e-3 | 5e-4 |
| LR warmup | None | None | 5 epochs linear |
| LR schedule | CosineWarmRestarts | CosineWarmRestarts | Cosine decay |
| Weight decay | 2e-4 | 1e-4 | 1e-3 |
| Gradient clip | 1.0 | 1.0 | 0.5 |
| Batch size | 128 | 128 | 128 |
| Max epochs | 100 | 100 | 100 |
| Early stop | Warmup(5)+transition-F1(10) | Same | Same |
| Loss | CrossEntropy | CrossEntropy | CrossEntropy |
| Label smoothing | 0.0 | 0.0 | 0.0 |
| Class boost | ×2.0 CALL_DROP/CAP_EXH | Same | Same |
| Oversampling | 19× transitions | Same | Same |

---

## Part 9 — What to Ask Claude to Generate

After sending this document to Opus and receiving its methodology confirmation, return to Claude (Sonnet) with the following request:

> "Based on this methodology document, generate three notebooks:
> 1. LSTM multi-task notebook (4 horizons: t+1, t+5, t+30, t+58) with transition-aware early stopping
> 2. TCN multi-task notebook (same structure, dilations=[1,2,4,8,16])
> 3. Transformer multi-task notebook (learnable positional embeddings, causal mask, attention dropout)
>
> All three notebooks must share identical: feature pipeline, data loading, class weights, oversampling, evaluation code.
> Only the model architecture cells differ between notebooks."

---

## Part 10 — Expected Results

| Model | t+1 F1 | t+5 F1 | t+30 F1 | t+58 F1 | Production use |
|---|---|---|---|---|---|
| LSTM (current best) | 0.918 | 0.492 | — | — | Baseline |
| LSTM (multi-task) | ~0.92 | ~0.55 | ~0.72 | ~0.55 | Validated |
| TCN | ~0.93 | ~0.57 | ~0.74 | ~0.57 | Production (fixed latency) |
| Transformer | ~0.91 | ~0.52 | ~0.70 | ~0.53 | Research comparison |

TCN is expected to match or slightly exceed LSTM on all horizons due to its natural resistance to the epoch-1 shortcut (causal convolutions cannot attend disproportionately to the last timestep). Transformer may underfit given 65k training sequences is small for attention-based models.

**Theoretical ceiling (Opus-revised):**
- t+1: ~0.96
- t+5: ~0.60 (Opus revised down from 0.77 — limited pre-transition signal in Mininet)
- t+30: ~0.75 (degradation states persist, NORMAL→degradation is hard)
- t+58: ~0.55–0.62 (Mininet transitions are largely step functions with no ramp)
