# Zentinel AIOps: Model Card

## 1. Model Details
- **Model Name**: Zentinel Next-Event Log Anomaly Predictor (`LSTMNextEvent`)
- **Version**: 1.0.0 (Frozen Baseline)
- **Model Type**: Autoregressive Recurrent Neural Network (Embedding + LSTM + Linear)
- **Framework**: PyTorch 2.4+ / ONNX Runtime 1.19+ (Opset 17)
- **License**: Apache 2.0
- **Artifacts**:
  - `artifacts/drain3_train_state_0_7.bin` (Frozen Drain3 parser state, SHA-256: `c3442ca...`)
  - `artifacts/event_token_vocabulary_v2.json` (Token vocabulary mapping, 616 tokens, SHA-256: `72052e8...`)
  - `artifacts/lstm_next_event.pt` (PyTorch model checkpoint, SHA-256: `a63fee0...`)
  - `artifacts/bgl_lstm_next_event.onnx` (ONNX export opset 17, dynamic seq/batch)
  - `artifacts/lstm_known_run_threshold.json` (Scoring threshold config, SHA-256: `85ecbf8...`)

---

## 2. Architecture Specifications

```
Input Tokens: [t_1, t_2, ..., t_L] (LongTensor, shape [batch_size, seq_len])
       |
       v
Embedding Layer: nn.Embedding(num_embeddings=616, embedding_dim=32, padding_idx=0)
       |
       v
Recurrent Layer: nn.LSTM(input_size=32, hidden_size=64, num_layers=1, batch_first=True)
       |
       v
Output Layer:    nn.Linear(in_features=64, out_features=616)
       |
       v
Output Logits:   shape [batch_size, seq_len, 616]
```

- **Total Trainable Parameters**:
  - Embedding: $616 \times 32 = 19,712$
  - LSTM: $4 \times ((32 \times 64) + (64 \times 64) + 64 + 64) = 25,088$
  - Linear Output: $(64 \times 616) + 616 = 40,040$
  - **Total Parameters**: **84,840**
- **Token Vocabulary**:
  - `0`: `PAD_TOKEN`
  - `1`: `UNKNOWN_TOKEN` (unmatched log templates)
  - `[2..615]`: 614 distinct log templates learned from the training split.

---

## 3. Training & Validation Setup

### 3.1 Training Data & Hyperparameters
- **Training Set**: Chronological Split $T_1$ (lines 1 to 3,323,600).
- **Optimization**: Adam optimizer, learning rate $1.0 \times 10^{-3}$, batch size 64.
- **Loss Function**: Masked Cross-Entropy Loss with padding ignored.
- **Drain3 Configuration**:
  - Depth: 4
  - Similarity Threshold: 0.70
  - Max Children per Node: 100
  - Parameter Delimiter: Whitespace and standard delimiters.

### 3.2 Non-Negotiable Training Invariants
- **Rule 2**: Frozen artifacts are never modified in place. Retraining scripts write new, versioned files.
- **Rule 3**: `TemplateMiner.add_log_message()` is strictly forbidden at inference. Only `TemplateMiner.match()` is called.
- **Rule 4**: Labels are strictly targets, never model features.

---

## 4. Decision Thresholds & Calibration

Thresholds were calibrated exclusively on scorable windows from the **Validation Period ($T_2$)**:

| Parameter | Calibrated Value | Calibration Objective / Behavior |
| :--- | :--- | :--- |
| **LSTM Anomaly Threshold ($\tau$)** | **3.47912** | Maximizes validation F1 (0.8482) while constraining validation FPR to 0.33%. |
| **Hysteresis Closing Threshold** | **2.43538** ($0.70 \times \tau$) | Open incident resolves only after 3 consecutive windows stay below this value. |
| **Novelty Unknown Rate Threshold** | **0.0200** ($2.0\%$) | Windows exceeding 2% unknown tokens trigger `NOVELTY_DRIFT_REVIEW`. |
| **Minimum Run Length** | **2 tokens** | Contiguous runs must have at least 1 transition ($L \ge 2$) to compute perplexity. |

---

## 5. ONNX Export and Numerical Parity

- **Export Configuration**:
  - ONNX Opset: **17**
  - Dynamic Axes: `{"tokens": {0: "batch_size", 1: "seq_len"}, "logits": {0: "batch_size", 1: "seq_len"}}`
  - Constant folding enabled.
- **Measured Parity Report (`reports/onnx_parity_report.json`)**:
  - Runs Evaluated: 1,000 real runs extracted from test data.
  - **Max Logit Difference**: **$4.77 \times 10^{-6}$** (well within floating point epsilon).
  - **Mean Logit Difference**: **$2.61 \times 10^{-6}$**.
  - **Identical Window Decisions**: **100.00%** agreement with PyTorch across all test windows.
  - **PyTorch CPU Latency**: 0.501 ms mean per run.
  - **ONNX Runtime CPU Latency**: 0.645 ms mean per run.

---

## 6. Hardware Portability Statement (Rule 5)

> [!NOTE]
> **Enterprise Hardware Statement**:
> Zentinel AIOps is **portable by design** across standard x86-64, ARM64, and Linux platforms via Python 3.11 and ONNX Runtime.
> **Not yet validated on s390x / IBM Z accelerator.** No hardware acceleration claims are made without an empirical run executed on that specific architecture.
