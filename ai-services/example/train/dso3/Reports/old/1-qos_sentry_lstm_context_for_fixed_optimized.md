# QoS Sentry — LSTM Forecasting: Complete Context & Methodology Brief

**Purpose:** Full context summary to be sent to Claude Opus for methodology guidance on maximising LSTM performance, then extended to TCN and Transformer models.

---

## 1. Project Context

**Project:** QoS Sentry — AI-powered SDN telemetry monitoring platform (academic project)  
**Framework:** PyTorch on Google Colab (free tier, T4 GPU)  
**Data source:** Mininet SDN simulation — telemetry collected every ~2.1 seconds  
**Core objective (DSO3):** Predict future QoE degradation (SLA breach) before it happens

**Forecasting task:**
- Input: 60-timestep window (~120s of telemetry history)
- Output: predicted QoE class at t+1 (~2s ahead), t+3 (~6s), t+5 (~10s)
- This is multi-class classification of future states, not raw metric regression

---

## 2. Dataset Facts (verified from outputs)

| Property | Value |
|---|---|
| Total rows | 93,276 |
| Segments | 4 (INTERNET, OUTDOOR_RAN, INDOOR_RAN, IMS_CDN) |
| Rows per segment | 23,319 |
| Sampling interval | ~2.06 seconds |
| Features (after engineering) | 70–73 |
| QoE classes | 6 |
| Train/Val/Test split | 70% / 15% / 15% (chronological, no shuffle) |

**Label distribution:**

| Class | Count | % |
|---|---|---|
| NORMAL | 29,028 | 31.1% |
| LOW_THROUGHPUT | 22,844 | 24.5% |
| POOR_VOICE_QUALITY | 15,804 | 16.9% |
| HIGH_LATENCY | 13,840 | 14.8% |
| CALL_DROP | 7,756 | 8.3% |
| CAPACITY_EXHAUSTED | 4,004 | 4.3% |

**Imbalance ratio:** 7.2× (NORMAL vs CAPACITY_EXHAUSTED)

---

## 3. Critical Data Characteristics

These are the properties that drive all design decisions.

### 3.1 State persistence (the dominant characteristic)

States persist for extremely long runs:

| Class | Mean run length | Min | Max |
|---|---|---|---|
| CALL_DROP | 84 steps | 57 | 109 |
| CAPACITY_EXHAUSTED | 91 steps | 65 | 112 |
| HIGH_LATENCY | 115 steps | 86 | 140 |
| LOW_THROUGHPUT | 114 steps | 65 | 143 |
| NORMAL | 49 steps | 2 | 326 |
| POOR_VOICE_QUALITY | 113 steps | 87 | 144 |

Average run = 78 steps. With window_size=60, most windows are PURE (single label throughout).

**Self-transition probabilities:**
- P(CALL_DROP → CALL_DROP) = 0.988
- P(CAPACITY_EXHAUSTED → CAPACITY_EXHAUSTED) = 0.989
- P(HIGH_LATENCY → HIGH_LATENCY) = 0.991
- P(LOW_THROUGHPUT → LOW_THROUGHPUT) = 0.991
- P(NORMAL → NORMAL) = 0.979
- P(POOR_VOICE_QUALITY → POOR_VOICE_QUALITY) = 0.991

### 3.2 Transition rarity

- Total state transitions: 297 out of 23,319 rows per segment = **1.27%**
- All transitions route through NORMAL (hub state):
  - NORMAL → X (all degradations start from NORMAL)
  - X → NORMAL (all recoveries return to NORMAL)
  - No direct transitions between degradation classes
- P(label_t+1 == label_t+3) = **0.9745**
- P(label_t+3 == label_t+5) = **0.9745**

### 3.3 Segment structure

Labels are **identical** across all 4 segments (same simulation run). Features differ significantly:
- INTERNET: e2e_delay_mean = 7.7ms
- OUTDOOR_RAN: e2e_delay_mean = 29.7ms
- INDOOR_RAN: e2e_delay_mean = 49.6ms
- IMS_CDN: e2e_delay_mean = 29.8ms

This means segments provide 4× training data with genuine feature diversity.

---

## 4. Why Val Metrics Start High at Epoch 1

**This is the single most confusing observation — it is NOT data leakage.**

### What happens

The feature set includes `label_encoded` (the current QoE class as a numeric feature), plus its lags (`label_lag1`, `label_lag3`, `label_lag5`) and rolling stats (`label_enc_rmean`, `label_enc_rstd`).

Because states persist for 78 steps on average, the last value in any 60-step window is almost always identical to the target:

- P(last_window_label == target at h=1) = **0.9872**
- P(last_window_label == target at h=3) = **0.9618**
- P(last_window_label == target at h=5) = **0.9383**

After just 1-2 gradient steps, the model discovers this shortcut: **"read the last label_encoded value and repeat it."** This instantly gives ~0.93 val accuracy at epoch 1.

### Why it is not leakage

Leakage = val data contaminating train data. That is not happening. The split is strictly chronological. What happens is the model learning a **valid but trivial shortcut** very fast because `label_encoded` is an extremely powerful feature.

### Why it is a problem anyway

The shortcut works for 98.7% of sequences but completely fails at the 1.3% that cross a state transition. Those transitions are exactly what the system needs to predict for SLA breach prevention. A model stuck at the shortcut gives:
- CALL_DROP F1 = 0.97 (because windows already in CALL_DROP stay in CALL_DROP)
- But misses all upcoming CALL_DROP events from NORMAL state
- Overall Macro F1 collapses (shown in the broken notebook: 0.358)

### Observed evidence (from training logs)

**Fixed notebook, t+1:**
- Epoch 1: Val F1 = 0.929 (shortcut learned instantly)
- Epoch 5: Val F1 = 0.949 (model learns some transitions on top)
- Epoch 10+: Val F1 drifts back to 0.908–0.914 as train loss pushes harder
- Best val loss at epoch ~2 (0.2317) — saved then

**Optimised notebook, t+1:**
- Epoch 1: Val F1 = 0.930 (same shortcut)
- Early stop monitors val F1 → saved at epoch 1 (best F1 = 0.9296)
- This is the **critical bug**: saving epoch-1 weights means saving the shortcut model, not a model that learned transitions

### The implication for early stopping strategy

**Monitoring val loss:** epoch 1 has artificially low val loss (model predicts the easy 98.7% correctly). Early stop fires at epoch 1–2, saving the shortcut model. The model that reaches test has never learned transitions.

**Monitoring val F1:** val F1 at epoch 1 is high (0.93) because 98.7% of samples are easy. But val F1 can still improve as the model learns the hard 1.3%. However, val F1 is also dominated by the easy majority, so the signal for transition learning is weak.

**The real solution:** neither metric alone is ideal. The correct approach is to evaluate only on **transition sequences** for early stopping — but that requires a custom evaluation loop.

---

## 5. Complete History of Experiments

### Version 1: Broken (original notebook)

**Architecture:** BiLSTM (bidirectional — conceptually wrong for forecasting)  
**Training:** 1 segment (INTERNET) only, window=60, standard CrossEntropy, patience=8  
**Results:**

| Horizon | Accuracy | Macro F1 |
|---|---|---|
| t+1 | 0.185 | 0.358 |
| t+3 | 0.258 | 0.417 |
| t+5 | 0.278 | 0.420 |

**Root cause of failure:**
1. Only 16,263 training sequences (1 segment)
2. 1.28% transition sequences → model never sees enough transitions to learn them
3. No `label_encoded` feature → model couldn't even find the easy shortcut
4. Early stop at epoch 8–17 → learned "predict LOW_THROUGHPUT or HIGH_LATENCY for everything"
5. Result: everything collapsed to majority class prediction

---

### Version 2: Fixed notebook (best proven result)

**Key changes from broken:**
1. All 4 segments → 65,124 training sequences (4× more)
2. `label_encoded` as input feature + lags + rolling stats
3. Transition oversampling 19× via WeightedRandomSampler
4. Balanced class weights + minority boost ×2.0 (CALL_DROP, CAPACITY_EXHAUSTED)
5. label_smoothing = 0
6. patience = 15, max_epochs = 100
7. Early stop on **val loss** (not val F1)

**Architecture:** Unidirectional LSTM, hidden=128, layers=2, dropout=0.3

**Training logs:**

t+1: Ep1 val_F1=0.929, best val_loss=0.2317 at ep~2, early stop ep=18  
t+3: Ep1 val_F1=0.854, best val_loss=0.5432 at ep~1, early stop ep=16  
t+5: Ep1 val_F1=0.636, best val_loss=1.0037 at ep~1, early stop ep=16  

**Results:**

| Horizon | Accuracy | Macro F1 | Delta from broken |
|---|---|---|---|
| t+1 | 0.9216 | **0.9182** | +0.560 |
| t+3 | 0.8476 | **0.8160** | +0.399 |
| t+5 | 0.4491 | **0.4577** | +0.037 |

**Per-class F1:**

| Class | t+1 | t+3 | t+5 |
|---|---|---|---|
| CALL_DROP | 0.908 | 0.770 | 0.734 |
| CAPACITY_EXHAUSTED | 0.908 | 0.723 | 0.313 |
| HIGH_LATENCY | 0.950 | 0.880 | 0.610 |
| LOW_THROUGHPUT | 0.930 | 0.830 | 0.520 |
| NORMAL | 0.880 | 0.780 | 0.220 |
| POOR_VOICE_QUALITY | 0.940 | 0.910 | 0.340 |

**Remaining issues:**
- t+5 severely underfitting: train acc=0.98, val acc=0.77, val loss=2.39 → model too large for 10s prediction horizon
- t+3 CALL_DROP recall=0.70 (missing 30% of upcoming call drops)
- CAPACITY_EXHAUSTED precision=0.60 at t+3 (40% false alarms)

---

### Version 3: Optimised notebook (mixed results)

**Additional changes from fixed:**
1. Attention pooling (replaces last-hidden-state)
2. Autoregressive chaining (t+3 model receives t+1 softmax probs as extra input; t+5 receives t+1+t+3)
3. Horizon-specific hyperparameters:
   - t+1: hidden=128, dropout=0.35, wd=2e-4, patience=15
   - t+3: hidden=128, dropout=0.40, wd=5e-4, patience=15
   - t+5: hidden=64, dropout=0.50, wd=1e-3, patience=12
4. Early stop on **val F1** (maximise) instead of val loss
5. Per-class threshold tuning on validation set (conservative range 0.30–0.80)
6. Minority boost ×2.0 (kept from fixed), CrossEntropy (not Focal Loss)

**Training logs:**

t+1: Ep1 val_F1=0.930, early stop ep=16, best val_F1=0.9296 (saved at ep1!)  
t+3: Ep1 val_F1=0.684, best val_F1=0.8061 at ep~5–8, early stop ep=22  
t+5: Ep1 val_F1=0.425, best val_F1=0.6964 at ep=50, early stop ep=62 (trained much longer!)  

**Results:**

| Horizon | Accuracy | Macro F1 | Delta vs fixed |
|---|---|---|---|
| t+1 | 0.8999 | 0.8813 | **-0.037** |
| t+3 | 0.7066 | 0.6901 | **-0.126** |
| t+5 | 0.5337 | **0.4918** | +0.034 |

**Per-class F1:**

| Class | t+1 | t+3 | t+5 |
|---|---|---|---|
| CALL_DROP | 0.811 | 0.768 | 0.536 |
| CAPACITY_EXHAUSTED | 0.858 | 0.565 | 0.248 |
| HIGH_LATENCY | 0.916 | 0.828 | 0.649 |
| LOW_THROUGHPUT | 0.901 | 0.611 | 0.626 |
| NORMAL | 0.865 | 0.660 | 0.554 |
| POOR_VOICE_QUALITY | 0.937 | 0.708 | 0.338 |

**Analysis of what worked and what did not:**

| Change | Effect | Verdict |
|---|---|---|
| Early stop on val F1 | t+5 trained 62 epochs → val F1=0.6964 | ✅ Worked for t+5 |
| Horizon-specific params (t+5: hidden=64) | Reduced overfitting at t+5 | ✅ Worked |
| AR chaining t+3 | val F1 reached 0.8061 but test only 0.6901 | ⚠️ Val/test gap |
| AR chaining t+5 | val F1=0.6964 but test only 0.4918 | ⚠️ Val/test gap 0.20 |
| Early stop on val F1 for t+1 | Saved epoch-1 weights (best F1=0.9296 at ep1) | ❌ Backfired |
| Attention pooling | Slightly disrupted the clean label shortcut | ❌ Minor harm |
| Threshold tuning | Negligible change (<0.001) | ⚠️ Neutral |

**Core remaining problem — val/test distribution shift:**

The val set = time period 70%–85% of the simulation. The test set = 85%–100%. Because this is a Mininet simulation with a fixed label sequence, the last 15% of the time series may have a different label distribution than the val set. The model optimised for val F1 does not generalise perfectly to test. This is the fundamental limitation of this dataset.

---

## 6. Key Design Decisions Proven Correct

These decisions are validated by experimental evidence and should be carried forward to TCN and Transformer:

### 6.1 Feature engineering (non-negotiable)

- `label_encoded`: current QoE class as numeric feature — the single most powerful feature
- `label_lag1`, `label_lag3`, `label_lag5`: past QoE states
- `label_enc_rmean`, `label_enc_rstd`: rolling mean/std of QoE state
- `label_diff`: rate of change of state (detects onset of transitions)
- Rolling stats on SLA features (window=10): rmean, rstd, rmax for e2e_delay, jitter, plr, throughput, mos_voice, ctrl_plane_rtt, availability
- Lag features on key SLA metrics (lags 1, 3, 5)
- Domain composites: voice_pressure, throughput_gap, stream_stress, flow_pressure
- Cyclical time encoding: hour_sin, hour_cos
- One-hot segment encoding (4 columns)

### 6.2 Data pipeline (non-negotiable)

- **All 4 segments**: processed independently (separate scaler per segment), sequences concatenated
- **Chronological split**: 70/15/15, strictly no shuffle at any stage
- **IQR clipping**: computed on train rows only, applied to all
- **RobustScaler**: fitted on train rows only per segment
- **Transition oversampling**: WeightedRandomSampler with weight=19 for transition sequences (those where target ≠ last window label)
- **Dead-zero columns dropped**: switch_id, rebuffering_count, total_stall_seconds, rx_dropped, tx_dropped
- **video_start_time_ms hard-capped** at 1e8 (raw max was 2.73e20)
- **flow_count forward-filled** (0 = missing, not truly zero)

### 6.3 Class weighting (validated)

- `compute_class_weight('balanced')` as base
- Minority boost ×2.0 on CALL_DROP and CAPACITY_EXHAUSTED (SLA-critical)
- label_smoothing = 0.0 (smoothing interferes with class weights)
- CrossEntropy (not Focal Loss — Focal combined with boost caused gradient imbalance)

### 6.4 Sequence creation

```
create_sequences(X, y, window=60, horizon=k):
    for i in range(len(X) - window - horizon + 1):
        X_seq[i] = X[i : i+window]
        y_seq[i] = y[i + window + horizon - 1]
        is_transition[i] = (y[i+window+horizon-1] != y[i+window-1])
```

---

## 7. Open Questions for Opus Methodology

The following are unresolved and need Opus guidance:

### 7.1 Early stopping strategy

**Problem:** Both val loss and val F1 fail as early stopping criteria for this data:
- Val loss → saves epoch-1 shortcut model (best loss occurs before model learns transitions)
- Val F1 → also dominated by the 98.7% easy majority, same issue at t+1; works better for t+5

**Options to explore:**
- Monitor val F1 only on **transition sequences** (custom eval loop)
- Use val **per-class minimum F1** (slowest-improving class drives patience)
- Separate patience per metric: stop only if both val loss AND val F1 stagnate
- Scheduled training (train for fixed N epochs, no early stop)
- Warmup epochs before early stopping activates

### 7.2 Val/test distribution gap

**Problem:** Val F1 is always higher than test F1, especially at t+5 (gap=0.20). This suggests the last 15% of the time series has a different label distribution than the val period.

**Options to explore:**
- Stratified time split that balances label distribution across val and test
- K-fold time series cross-validation
- Use a rolling window evaluation rather than a fixed held-out set
- Accept the gap as a data limitation and report both val and test metrics

### 7.3 Autoregressive chaining for t+3 and t+5

**Problem:** AR chaining (feeding t+1 predictions as input to t+3 model) improved val metrics but the test improvement was smaller than expected. When t+1 makes errors (F1=0.88 not 1.0), those errors propagate.

**Options to explore:**
- Teacher forcing during training (feed true t+1 label, not predicted)
- Scheduled sampling (mix true and predicted labels with decreasing true ratio)
- Train t+3 model end-to-end with t+1 model jointly (backprop through both)
- Use t+1 predicted probabilities (soft labels) rather than argmax (hard labels)

### 7.4 t+5 fundamental ceiling

**Problem:** t+5 (10s ahead) shows severe train/val gap and low test F1. P(label_t+5 == label_t) = 0.938 but many of the 6.2% transitions at t+5 have no detectable signal 10s in advance in this Mininet data.

**Question:** Is 0.49 test F1 at t+5 close to the information-theoretic ceiling for this dataset, or is there a principled way to push further?

---

## 8. Architecture Decisions for Each Model

### LSTM (proven)
- Unidirectional (causal — bidirectional leaks future information)
- hidden=128, layers=2, dropout=0.3 for t+1/t+3
- hidden=64, layers=2, dropout=0.5 for t+5
- Last-hidden-state → LayerNorm → FC → GELU → Dropout → FC (simpler than attention for this data)
- Attention pooling tried but showed marginal/negative impact

### TCN (to be built next)
- Causal convolutions (no future leakage by design)
- Exponentially increasing dilations: 1, 2, 4 → receptive field covers full window
- Channels: [64, 128, 128]
- Kernel size: 3
- Residual connections
- Fixed inference latency (production advantage over LSTM)

### Transformer (to be built after TCN)
- Causal mask (upper triangular) — mandatory for forecasting
- Pre-LN (more stable than post-LN)
- d_model=64, nhead=4, layers=2, dim_feedforward=128
- Sinusoidal positional encoding
- Last-timestep representation → classifier

---

## 9. Benchmark Results Summary

| Version | t+1 F1 | t+3 F1 | t+5 F1 | Status |
|---|---|---|---|---|
| Broken (original) | 0.358 | 0.417 | 0.420 | Discarded |
| Fixed | **0.918** | **0.816** | 0.458 | Best for t+1/t+3 |
| Optimised v3 | 0.881 | 0.690 | **0.492** | Best for t+5 |
| Theoretical ceiling | ~0.96 | ~0.92 | ~0.77 | Physical data limit |

**Current best to beat per horizon:**
- t+1: 0.918 (fixed notebook)
- t+3: 0.816 (fixed notebook)
- t+5: 0.492 (optimised v3)

---

## 10. Questions to Ask Opus

1. Given the data characteristics (98.7% non-transition, hub-state transitions, Mininet simulation), what is the theoretically correct early stopping strategy that avoids the epoch-1 shortcut problem?

2. Is autoregressive chaining (feeding predicted t+1 as input to t+3 model) the right approach given P(t+1==t+3)=0.9745? Or should we use a multi-output head predicting t+1, t+3, t+5 simultaneously from one model?

3. How should the val/test distribution gap (~0.20 at t+5) be addressed? Is stratified time-split appropriate for Mininet simulation data?

4. For TCN and Transformer applied to this same data and task: what architecture-specific adaptations are needed given the extreme state persistence and hub-state transition structure?

5. What is the recommended training protocol (learning rate schedule, batch size, gradient clipping) for TCN and Transformer on sequences of length 60 with this label structure?

6. Should all three models (LSTM, TCN, Transformer) share the same feature engineering pipeline, or should each model type receive different features suited to its inductive bias?
