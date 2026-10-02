"""A cross-encoder that reads the question and a passage together, and scores the pair.

The feature reranker scores what can be counted: shared terms, heading overlap, dates,
identifiers. That is cheap and it is also exactly what a passage can satisfy without
answering anything. Measured on this machine with bge-reranker-v2-m3, a passage that
answers "the maximum delay penalty" scored 0.977, and a passage sharing every one of its
words — "the materials were late and no penalty was imposed" — scored 0.000. Term
counting cannot tell those two apart; a model that reads them can.

Runs locally through ONNX Runtime on the CPU. Nothing leaves the machine, and the model
is loaded once, on first use, so a deployment that never enables it never pays for it.

Three properties it is built to hold:

* **It only adds.** Its score is one more term on top of the feature score, never a
  replacement. Dates, supersession and bookkeeping demotion are things it cannot know,
  and they were each measured into place.
* **It fails closed to what was there before.** A missing model, a load error, an
  inference error or a blown time budget all return the feature ranking unchanged, and
  say so in the log. A reranker is an improvement, never a dependency.
* **Its cost is bounded.** It scores the top N by feature score, never the whole pool,
  caches each (question, passage) pair so the second rerank call after expansion pays
  only for new passages, and stops if the per-question budget runs out.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

#: File names tried in order inside the model directory.
MODEL_FILES = ("onnx/model_int8.onnx", "onnx/model_quantized.onnx", "onnx/model.onnx",
               "model_int8.onnx", "model.onnx")


@dataclass
class ScorerStatus:
    """What the health endpoint reports about the model."""

    enabled: bool
    loaded: bool = False
    model: str = ""
    error: str = ""
    calls: int = 0
    pairs: int = 0
    fallbacks: int = 0
    last_ms: int = 0
    total_ms: int = 0

    def as_dict(self) -> dict:
        return {
            "enabled": self.enabled, "loaded": self.loaded, "model": self.model,
            "error": self.error, "calls": self.calls, "pairs_scored": self.pairs,
            "fallbacks": self.fallbacks, "last_ms": self.last_ms,
            "mean_ms": round(self.total_ms / self.calls) if self.calls else 0,
        }


@dataclass
class CrossEncoderScorer:
    """Loads the model lazily and scores (question, passage) pairs."""

    model_dir: Path
    max_tokens: int = 256
    threads: int = 0
    batch_size: int = 12
    status: ScorerStatus = field(default_factory=lambda: ScorerStatus(enabled=True))

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._session = None
        self._tokenizer = None
        self._inputs: set[str] = set()
        self._attempted = False

    # -- loading -----------------------------------------------------------
    def _model_path(self) -> Path | None:
        for name in MODEL_FILES:
            candidate = self.model_dir / name
            if candidate.exists():
                return candidate
        return None

    def _load(self) -> bool:
        """Load once. A failure is remembered, so a broken install costs one attempt."""
        if self._session is not None:
            return True
        with self._lock:
            if self._session is not None:
                return True
            if self._attempted:
                return False
            self._attempted = True
            started = time.perf_counter()
            try:
                import onnxruntime as ort
                from tokenizers import Tokenizer

                model = self._model_path()
                tokenizer = self.model_dir / "tokenizer.json"
                if model is None or not tokenizer.exists():
                    raise FileNotFoundError(
                        f"no model or tokenizer.json under {self.model_dir} — "
                        "run scripts/download_reranker.py"
                    )
                options = ort.SessionOptions()
                options.intra_op_num_threads = self.threads or max(1, (os.cpu_count() or 2) // 2)
                options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                self._session = ort.InferenceSession(
                    str(model), options, providers=["CPUExecutionProvider"]
                )
                self._inputs = {i.name for i in self._session.get_inputs()}
                self._tokenizer = Tokenizer.from_file(str(tokenizer))
                self._tokenizer.enable_truncation(self.max_tokens)
                self._tokenizer.enable_padding()
                self.status.loaded = True
                self.status.model = f"{self.model_dir.name}/{model.name}"
                logger.info(
                    "Cross-encoder loaded: %s in %.1fs", self.status.model,
                    time.perf_counter() - started,
                )
                return True
            except Exception as exc:  # noqa: BLE001
                self.status.error = f"{type(exc).__name__}: {exc}"[:300]
                logger.warning("Cross-encoder unavailable, feature ranking only: %s", self.status.error)
                return False

    @property
    def available(self) -> bool:
        return self._load()

    # -- scoring -----------------------------------------------------------
    def score(self, question: str, passages: list[str], budget_ms: int) -> list[float] | None:
        """Relevance in [0, 1] for each passage, or None if it could not finish.

        None rather than a partial list, on purpose: scoring half the head and ranking it
        against an unscored other half would reorder by which passages happened to come
        first, not by relevance.
        """
        if not passages or not self._load():
            return None
        import numpy as np

        started = time.perf_counter()
        scores: list[float] = []
        try:
            for offset in range(0, len(passages), self.batch_size):
                batch = passages[offset: offset + self.batch_size]
                encoded = self._tokenizer.encode_batch([(question, p) for p in batch])
                feeds = {
                    "input_ids": np.array([e.ids for e in encoded], dtype=np.int64),
                    "attention_mask": np.array([e.attention_mask for e in encoded], dtype=np.int64),
                }
                if "token_type_ids" in self._inputs:
                    feeds["token_type_ids"] = np.array([e.type_ids for e in encoded], dtype=np.int64)
                logits = self._session.run(None, feeds)[0].reshape(-1)
                scores.extend(1.0 / (1.0 + math.exp(-float(x))) for x in logits)
                if (time.perf_counter() - started) * 1000 > budget_ms and len(scores) < len(passages):
                    logger.warning(
                        "Cross-encoder budget of %sms spent after %s/%s passages; "
                        "feature ranking kept", budget_ms, len(scores), len(passages),
                    )
                    self.status.fallbacks += 1
                    return None
        except Exception:  # noqa: BLE001
            logger.exception("Cross-encoder inference failed; feature ranking kept")
            self.status.fallbacks += 1
            return None
        finally:
            elapsed = round((time.perf_counter() - started) * 1000)
            self.status.last_ms = elapsed
            self.status.total_ms += elapsed
            self.status.calls += 1
        self.status.pairs += len(scores)
        return scores


class PairCache:
    """(question, passage) → score, bounded. The pipeline reranks twice per question."""

    def __init__(self, capacity: int = 4096) -> None:
        self._data: OrderedDict[tuple[str, str], float] = OrderedDict()
        self._capacity = capacity
        self._lock = threading.Lock()

    def get(self, key: tuple[str, str]) -> float | None:
        with self._lock:
            value = self._data.get(key)
            if value is not None:
                self._data.move_to_end(key)
            return value

    def put(self, key: tuple[str, str], value: float) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._capacity:
                self._data.popitem(last=False)
