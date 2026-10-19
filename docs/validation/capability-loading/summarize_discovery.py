#!/usr/bin/env python3
"""Per-scenario rates for the repeated discovery suite (eval_rollout.py --suite discovery).

"Browser used" means a Playwright tool actually ran; "correct" checks the answer against
ground truth fetched 2026-10-19 (books.toscrape.com: 50 categories, Poetry's first book
"A Light in the Attic" at £51.77; example.com: "documentation examples").

    python summarize_discovery.py RESULT.json [...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean

CHECKS = {
    "explicit_browser_count": lambda a: "50" in a,
    "multistep_first_turn": lambda a: "Light in the Attic" in a and "51.77" in a,
    "screenshot": lambda a: "screenshot" in a.lower() and "couldn" not in a.lower()
    and "can't" not in a.lower() and "can’t" not in a.lower(),
    "vague_page": lambda a: "documentation examples" in a,
}
NEEDS_BROWSER = {"explicit_browser_count", "screenshot"}


def summarize(path: Path) -> list[dict]:
    result = json.loads(path.read_text())
    rows: dict[str, list[dict]] = {}
    for scenario in result["scenarios"]:
        rows.setdefault(scenario["name"], []).append(scenario["turns"][0])
    out = []
    for name, turns in rows.items():
        browser = [any((c["tool"] or "").startswith("mcp__playwright__") for c in t["tool_calls"])
                   for t in turns]
        correct = [CHECKS[name](t["answer"]) and (browser[i] or name not in NEEDS_BROWSER)
                   for i, t in enumerate(turns)]
        out.append({
            "model": result["model"], "flag": result["flag"], "scenario": name, "n": len(turns),
            "browser_used": sum(browser), "correct": sum(correct),
            "routes": sorted({c["tool"] for t in turns for c in t["tool_calls"]
                              if c["tool"] and (c["tool"].startswith("mcp__")
                                                or c["tool"] in ("web_fetch", "web_search"))}),
            "loads_per_turn": round(mean(sum(c["tool"] == "load_capabilities" for c in t["tool_calls"])
                                         for t in turns), 2),
            "unloaded_refusals": sum(t["unloaded_refusals"] for t in turns),
            "malformed": sum(t["malformed_corrections"] for t in turns),
            "requests": round(mean(t["usage"]["requests"] for t in turns), 1),
            "input": round(mean(t["usage"]["input"] for t in turns)),
            "uncached": round(mean(t["usage"]["input"] - t["usage"]["cached"] for t in turns)),
            "output": round(mean(t["usage"]["output"] for t in turns)),
            "seconds": round(mean(t["seconds"] for t in turns), 1),
            "errors": sum(bool(t.get("client_error")) or t["answer"].startswith("✗") for t in turns),
        })
    return out


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        for row in summarize(Path(arg)):
            print(json.dumps(row))
