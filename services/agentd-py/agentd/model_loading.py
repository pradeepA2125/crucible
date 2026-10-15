"""One process-wide lock for every local ML model load AND call.

The memory embedder, the reranker and the semantic index each load a sentence-transformers
model onto the torch device (MPS on Apple silicon). torch's MPS backend is not thread-safe
across models: concurrent loads crashed the backend (SIGSEGV in mps_copy_) or corrupted the
kernel cache into a 300% CPU spin that wedged turns, and concurrent encode/predict calls
crashed it on a Metal assertion (IOGPUMetalCommandBuffer setCurrentCommandEncoder) — live
2026-10-08, the semantic index embedding a large repo while the memory models warmed up;
reproduced outside agentd as two models encoding on two threads (exit 139). Per-model locks
do not help — the race is between DIFFERENT models — so every construction and every call
takes this one lock. Re-entrant: a call may trigger its model's lazy load under the same lock.
"""
import inspect
import threading
from importlib import import_module
from typing import Any

MODEL_LOCK = threading.RLock()
MODEL_LOAD_LOCK = MODEL_LOCK  # the original name, kept for existing imports

# One instance per (kind, model name) for the whole process. The memory harness is built
# twice when tasks are reachable (task loop + chat controller) and the semantic index uses
# the same bge-small; without this each kept and loaded its own copy, and the semantic
# index's copy loaded lazily inside the first chat's retrieval seed.
_SHARED: dict[tuple[str, str], Any] = {}


def shared_sentence_transformer(model_name: str) -> Any:
    with MODEL_LOAD_LOCK:
        key = ("sentence_transformer", model_name)
        if key not in _SHARED:
            SentenceTransformer = import_module("sentence_transformers").SentenceTransformer
            _SHARED[key] = SentenceTransformer(model_name)
        return _SHARED[key]


def shared_cross_encoder(model_name: str, *, bounded: bool = False) -> Any:
    with MODEL_LOAD_LOCK:
        key = ("cross_encoder_sigmoid_512" if bounded else "cross_encoder", model_name)
        if key not in _SHARED:
            CrossEncoder = import_module("sentence_transformers").CrossEncoder
            if bounded:
                nn = import_module("torch").nn

                # Sentence Transformers renamed this parameter; support installed 3.x+
                # while always specifying the activation rather than inheriting a model default.
                activation = (
                    "activation_fn" if "activation_fn" in inspect.signature(CrossEncoder).parameters
                    else "default_activation_function"
                )
                _SHARED[key] = CrossEncoder(
                    model_name, max_length=512, **{activation: nn.Sigmoid()},
                )
            else:
                _SHARED[key] = CrossEncoder(model_name)
        return _SHARED[key]


def clear_shared_models() -> None:
    """Tests swap in fake `sentence_transformers` modules; drop instances built from them."""
    with MODEL_LOAD_LOCK:
        _SHARED.clear()
