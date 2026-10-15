"""Score a prelabelled matrix with cached weights only, in an isolated CPU process."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import time
from pathlib import Path

from agentd.eval.memory_judges import compare_rules, validate_matrix


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matrix", type=Path)
    parser.add_argument("--cached-model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    cases = json.loads(args.matrix.read_text())["cases"]
    validate_matrix(cases)
    torch = importlib.import_module("torch")
    torch.set_num_threads(1)
    cls = importlib.import_module("sentence_transformers").CrossEncoder
    started = time.monotonic()
    model = cls(str(args.cached_model), device="cpu", max_length=512,
                activation_fn=torch.nn.Sigmoid(), local_files_only=True)
    report: dict[str, object] = {
        "matrix_sha256": hashlib.sha256(args.matrix.read_bytes()).hexdigest(),
        "model_snapshot": args.cached_model.name, "activation": "sigmoid", "truncation": 512,
        "device": "cpu", "threads": 1, "load_seconds": time.monotonic() - started,
        "limitations": "Offline CPU measurement; production device/deadline/cache not established.",
    }
    scores: dict[str, dict[str, dict[str, float]]] = {}
    latencies = {}
    for case in cases:
        pairs = [(case["signals"][key], "\n".join(
            memory[field] for field in ("topic", "applies_when", "content")))
            for memory in case["candidates"] for key in ("Q", "C", "R")]
        started = time.monotonic()
        values = model.predict(pairs, batch_size=8, show_progress_bar=False).tolist()
        latencies[case["id"]] = time.monotonic() - started
        scores[case["id"]] = {
            memory["id"]: dict(zip(("Q", "C", "R"), values[i * 3:i * 3 + 3], strict=True))
            for i, memory in enumerate(case["candidates"])
        }
        report.update(scores=scores, latency_seconds=latencies)
        args.output.write_text(json.dumps(report, indent=2))
        print(case["id"], round(latencies[case["id"]], 3), flush=True)
    report["rule_comparison"] = compare_rules(cases, scores)
    args.output.write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
