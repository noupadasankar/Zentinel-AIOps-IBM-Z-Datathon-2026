import torch
from torch import nn

from zentinel.config import PAD_TOKEN, UNKNOWN_TOKEN
from zentinel.models.inference import InferenceEngine


class ConstantModel(nn.Module):
    """Outputs constant logits for deterministic test verification."""

    def __init__(self, vocab_size=10):
        super().__init__()
        self.vocab_size = vocab_size

    def forward(self, tokens):
        # tokens: (1, seq_len) -> return (1, seq_len, vocab_size)
        seq_len = tokens.size(1)
        # Uniform logits
        return torch.zeros((1, seq_len, self.vocab_size), dtype=torch.float32)


def test_consecutive_known_tokens_score_properly():
    engine = InferenceEngine()
    engine._is_loaded = True
    engine.model = ConstantModel(vocab_size=10)

    # 4 known tokens -> 1 run of length 4 -> 3 transitions
    tokens = [2, 3, 4, 5]
    score, transitions = engine.score_known_runs(tokens)

    assert score is not None
    assert transitions == 3
    # Cross-entropy with uniform logits over 10 classes is ln(10) ≈ 2.302585
    assert abs(score - 2.302585) < 1e-4


def test_unk_token_splits_runs():
    engine = InferenceEngine()
    engine._is_loaded = True
    engine.model = ConstantModel(vocab_size=10)

    # [2, 3] (1 transition), UNK, [4, 5, 6] (2 transitions) -> total 3 transitions
    tokens = [2, 3, UNKNOWN_TOKEN, 4, 5, 6]
    score, transitions = engine.score_known_runs(tokens)

    assert score is not None
    assert transitions == 3
    assert abs(score - 2.302585) < 1e-4


def test_pad_token_splits_runs():
    engine = InferenceEngine()
    engine._is_loaded = True
    engine.model = ConstantModel(vocab_size=10)

    # [2, 3], PAD, [4, 5] -> total 2 transitions
    tokens = [2, 3, PAD_TOKEN, 4, 5]
    score, transitions = engine.score_known_runs(tokens)

    assert score is not None
    assert transitions == 2


def test_isolated_tokens_produce_zero_transitions():
    engine = InferenceEngine()
    engine._is_loaded = True
    engine.model = ConstantModel(vocab_size=10)

    # Single known tokens separated by UNK have length 1 -> 0 transitions
    tokens = [2, UNKNOWN_TOKEN, 3, UNKNOWN_TOKEN, 4]
    score, transitions = engine.score_known_runs(tokens)

    assert score is None
    assert transitions == 0


def test_all_unknown_tokens_returns_unscorable():
    engine = InferenceEngine()
    engine._is_loaded = True
    engine.model = ConstantModel(vocab_size=10)

    tokens = [UNKNOWN_TOKEN] * 10
    score, transitions = engine.score_known_runs(tokens)

    assert score is None
    assert transitions == 0


def test_empty_tokens_returns_unscorable():
    engine = InferenceEngine()
    engine._is_loaded = True
    engine.model = ConstantModel(vocab_size=10)

    score, transitions = engine.score_known_runs([])
    assert score is None
    assert transitions == 0
