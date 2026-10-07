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
import threading

MODEL_LOCK = threading.RLock()
MODEL_LOAD_LOCK = MODEL_LOCK  # the original name, kept for existing imports
