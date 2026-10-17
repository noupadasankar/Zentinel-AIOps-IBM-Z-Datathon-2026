import json
from pathlib import Path
from typing import Any

import torch
import torch.nn.functional as F
from drain3 import TemplateMiner
from drain3.file_persistence import FilePersistence
from drain3.template_miner_config import TemplateMinerConfig
from torch import nn

from zentinel.config import (
    DECISION_REPORT_PATH,
    DRAIN_STATE_PATH,
    MODEL_PATH,
    MODEL_THRESHOLD_PATH,
    PAD_TOKEN,
    UNKNOWN_TOKEN,
    VOCAB_PATH,
)


class LSTMNextEvent(nn.Module):
    """Exact architecture used by the trained next-event model."""

    def __init__(
        self,
        vocab_size: int,
        embedding_dim: int,
        hidden_dim: int,
    ):
        super().__init__()

        self.embedding = nn.Embedding(
            vocab_size,
            embedding_dim,
            padding_idx=PAD_TOKEN,
        )
        self.lstm = nn.LSTM(
            embedding_dim,
            hidden_dim,
            batch_first=True,
        )
        self.output = nn.Linear(hidden_dim, vocab_size)

    def forward(self, tokens: torch.Tensor) -> torch.Tensor:
        embedded = self.embedding(tokens)
        hidden, _ = self.lstm(embedded)
        return self.output(hidden)


class InferenceEngine:
    """Encapsulates Drain3 matching, token mapping, and LSTM window scoring."""

    def __init__(
        self,
        drain_state_path: Path = DRAIN_STATE_PATH,
        vocab_path: Path = VOCAB_PATH,
        model_path: Path = MODEL_PATH,
        model_threshold_path: Path = MODEL_THRESHOLD_PATH,
        decision_report_path: Path = DECISION_REPORT_PATH,
    ):
        self.drain_state_path = Path(drain_state_path)
        self.vocab_path = Path(vocab_path)
        self.model_path = Path(model_path)
        self.model_threshold_path = Path(model_threshold_path)
        self.decision_report_path = Path(decision_report_path)

        self.vocab: dict[str, Any] = {}
        self.original_id_to_token: dict[str, int] = {}
        self.lstm_threshold: float = 0.0
        self.novelty_threshold: float = 0.0
        self.model: LSTMNextEvent | None = None
        self.miner: TemplateMiner | None = None
        self._is_loaded = False

    def load(self) -> None:
        """Load vocabulary, thresholds, PyTorch model, and Drain3 state."""
        if self._is_loaded:
            return

        for path in [
            self.drain_state_path,
            self.vocab_path,
            self.model_path,
            self.model_threshold_path,
            self.decision_report_path,
        ]:
            if not path.is_file():
                raise FileNotFoundError(f"Required inference asset missing: {path}")

        # Vocabulary & Thresholds
        self.vocab = json.loads(self.vocab_path.read_text(encoding="utf-8"))
        self.original_id_to_token = self.vocab["original_event_id_to_token"]

        threshold_config = json.loads(self.model_threshold_path.read_text(encoding="utf-8"))
        self.lstm_threshold = float(threshold_config["threshold"])

        decision_report = json.loads(self.decision_report_path.read_text(encoding="utf-8"))
        self.novelty_threshold = float(decision_report["novelty_unknown_rate_threshold"])

        if int(self.vocab["padding_token"]) != PAD_TOKEN:
            raise ValueError("Unexpected padding-token ID.")
        if int(self.vocab["unknown_token"]) != UNKNOWN_TOKEN:
            raise ValueError("Unexpected unknown-token ID.")
        if int(self.vocab["vocabulary_size"]) != 616:
            raise ValueError("Vocabulary size does not match the saved LSTM.")

        # PyTorch Model Checkpoint
        checkpoint = torch.load(
            self.model_path,
            map_location="cpu",
            weights_only=True,
        )
        model_config = checkpoint["config"]

        if model_config["model_type"] != "LSTMNextEvent":
            raise ValueError("Unexpected model type.")
        if int(model_config["vocab_size"]) != int(self.vocab["vocabulary_size"]):
            raise ValueError("Model and vocabulary sizes do not match.")

        self.model = LSTMNextEvent(
            vocab_size=int(model_config["vocab_size"]),
            embedding_dim=int(model_config["embedding_dim"]),
            hidden_dim=int(model_config["hidden_dim"]),
        )
        self.model.load_state_dict(checkpoint["model_state_dict"], strict=True)
        self.model.eval()

        # Frozen Drain3 miner. Rule 3: Never call add_log_message() at inference!
        drain_config = TemplateMinerConfig()
        drain_config.drain_sim_th = 0.70
        drain_config.drain_depth = 4

        self.miner = TemplateMiner(
            persistence_handler=FilePersistence(str(self.drain_state_path)),
            config=drain_config,
        )

        self._is_loaded = True

    def make_template_input(self, record: Any) -> str:
        """Match the label-free input representation used offline."""
        values = [
            record.event_type,
            record.component,
            record.severity,
            record.message,
        ]
        template_input = " ".join(str(v) for v in values if v)
        return template_input or "[EMPTY_LOG_MESSAGE]"

    def map_event_to_token(self, record: Any) -> dict[str, Any]:
        """
        Match a parsed record against frozen Drain3 templates.
        Missing templates and template IDs absent from vocabulary are mapped to UNK.
        """
        if not self._is_loaded or self.miner is None:
            raise RuntimeError("InferenceEngine must be loaded before mapping events.")

        template_input = self.make_template_input(record)

        # match() only looks up existing templates. Never call add_log_message().
        cluster = self.miner.match(
            template_input,
            full_search_strategy="never",
        )

        if cluster is None:
            return {
                "token": UNKNOWN_TOKEN,
                "event_status": "unknown_template",
                "matched_event_id": None,
                "template": None,
            }

        event_id = int(cluster.cluster_id)
        token = self.original_id_to_token.get(str(event_id))

        if token is None:
            return {
                "token": UNKNOWN_TOKEN,
                "event_status": "template_missing_from_vocabulary",
                "matched_event_id": event_id,
                "template": cluster.get_template(),
            }

        return {
            "token": int(token),
            "event_status": "matched",
            "matched_event_id": event_id,
            "template": cluster.get_template(),
        }

    def score_known_runs(self, tokens: list[int]) -> tuple[float | None, int]:
        """
        Return mean next-event cross-entropy over known-to-known transitions.
        UNK and PAD tokens break a run. Runs < 2 events contribute no transitions.
        """
        if not self._is_loaded or self.model is None:
            raise RuntimeError("InferenceEngine must be loaded before scoring.")

        total_loss = 0.0
        total_transitions = 0
        current_run: list[int] = []

        def _score_single_run(run: list[int]) -> tuple[float, int]:
            if len(run) < 2:
                return 0.0, 0

            inputs = torch.tensor(
                run[:-1],
                dtype=torch.long,
            ).unsqueeze(0)

            targets = torch.tensor(
                run[1:],
                dtype=torch.long,
            )

            with torch.inference_mode():
                logits = self.model(inputs).squeeze(0)  # type: ignore[misc]
                loss_sum = F.cross_entropy(
                    logits,
                    targets,
                    reduction="sum",
                )

            return float(loss_sum.item()), len(run) - 1

        for token in [*tokens, UNKNOWN_TOKEN]:
            if token >= 2:
                current_run.append(token)
            else:
                run_loss, transitions = _score_single_run(current_run)
                total_loss += run_loss
                total_transitions += transitions
                current_run = []

        if total_transitions == 0:
            return None, 0

        return total_loss / total_transitions, total_transitions

    def decide_window(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        """Evaluate a window of events and return decision and diagnostic counters."""
        tokens = [int(event["token"]) for event in events]

        unknown_count = sum(token == UNKNOWN_TOKEN for token in tokens)
        known_count = sum(token >= 2 for token in tokens)
        observed_count = len(tokens)
        unknown_rate = unknown_count / observed_count if observed_count else 0.0

        lstm_score, transition_count = self.score_known_runs(tokens)
        scorable = lstm_score is not None

        lstm_would_alert = bool(lstm_score >= self.lstm_threshold) if scorable else None

        high_unknown_rate = unknown_rate > self.novelty_threshold

        if not scorable:
            decision = "NOVELTY_DRIFT_REVIEW"
            reason = "INSUFFICIENT_KNOWN_TRANSITIONS"
        elif high_unknown_rate:
            decision = "NOVELTY_DRIFT_REVIEW"
            reason = "UNKNOWN_RATE_ABOVE_VALIDATION_THRESHOLD"
        elif lstm_would_alert:
            decision = "ANOMALY_ALERT"
            reason = "LSTM_SCORE_ABOVE_VALIDATION_THRESHOLD"
        else:
            decision = "NO_ANOMALY_DETECTED"
            reason = "BELOW_ANOMALY_AND_NOVELTY_THRESHOLDS"

        return {
            "unknown_event_count": int(unknown_count),
            "known_event_count": int(known_count),
            "unknown_event_rate": float(unknown_rate),
            "known_transitions_scored": int(transition_count),
            "model_scorable": bool(scorable),
            "lstm_anomaly_score": (float(lstm_score) if scorable else None),
            "lstm_would_alert": lstm_would_alert,
            "high_unknown_rate": bool(high_unknown_rate),
            "decision": decision,
            "decision_reason": reason,
        }
