"""Local JSON-lines scorer for the quantized Namo v1 English model."""

import json
import math
import resource
import sys
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer


model = Path(sys.argv[1])
tokenizer = Tokenizer.from_file(str(model / "tokenizer.json"))
tokenizer.enable_truncation(max_length=512)
options = ort.SessionOptions()
options.intra_op_num_threads = 1
options.inter_op_num_threads = 1
session = ort.InferenceSession(str(model / "model_quant.onnx"), options, providers=["CPUExecutionProvider"])
print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    start = time.perf_counter()
    text = json.loads(line)
    encoded = tokenizer.encode(text)
    logits = session.run(None, {
        "input_ids": np.array([encoded.ids], dtype=np.int64),
        "attention_mask": np.array([encoded.attention_mask], dtype=np.int64),
    })[0][0]
    probability = 1 / (1 + math.exp(float(logits[0] - logits[1])))
    print(json.dumps({
        "probability": probability,
        "ms": (time.perf_counter() - start) * 1000,
        "peakRssBytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }), flush=True)
