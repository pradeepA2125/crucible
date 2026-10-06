"""One process-wide lock for constructing local ML models.

The memory embedder, the reranker and the semantic index each load a sentence-transformers
model, which moves its weights onto the torch device (MPS on Apple silicon) in the
constructor. torch's MPS kernel cache is not thread-safe: concurrent loads either crashed
the backend (SIGSEGV in mps_copy_) or corrupted the cache so the loading threads spun at
300% CPU forever, wedging every turn that awaited recall. Per-model locks did not help —
the race is between DIFFERENT models — so every construction takes this one lock.
"""
import threading

MODEL_LOAD_LOCK = threading.Lock()
