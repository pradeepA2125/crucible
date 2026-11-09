#!/usr/bin/env python3
"""Summarize the held-out replay and the optional raw Parakeet run."""

import argparse
import json
import math
from pathlib import Path
from statistics import median


def latency(values):
    if not values:
        return "—"
    return f"{median(values):.0f}/{sorted(values)[math.ceil(0.95 * len(values)) - 1]:.0f}"


def replay_table(results):
    held = [entry for entry in results if entry["split"] == "evaluation"]
    print("| Lane | Cases | Sent once after end | Early sends | No send | Repeat prompts | Handoff p50/p95 ms |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for lane in ("smart", "namo", "control"):
        for category in ("all", "long", "two-second-pause"):
            n = clean = early = missed = repeats = 0
            handoff = []
            for entry in held:
                if category != "all" and entry["category"] != category:
                    continue
                events = entry["lanes"][lane]
                n += 1
                offsets = [send["at"] - 1000 * entry["seconds"] for send in events["sends"]]
                early += any(offset < -150 for offset in offsets)
                missed += not offsets
                repeats += bool(events["clarifications"])
                if len(offsets) == 1 and offsets[0] >= -150:
                    clean += 1
                    handoff.append(offsets[0])
            print(f"| {lane} ({category}) | {n} | {clean} | {early} | {missed} | {repeats} | {latency(handoff)} |")
        scores = [score for entry in held for score in entry["lanes"][lane]["scores"]]
        if scores:
            print(f"{lane}: {len(scores)} pause scores, p50/p95 {latency([s['ms'] for s in scores])} ms")
            if "peakRssBytes" in scores[0]:
                rss = max(score["peakRssBytes"] for score in scores) / 1024**2
                print(f"{lane}: scorer process peak RSS {rss:.1f} MiB")


def parakeet_table(report, manifest):
    held = {entry["clip"]: entry for entry in manifest if entry["split"] == "evaluation"}
    clips = [row for row in report if row["clip"] in held]
    for category in ("original", "long", "two-second-pause"):
        rows = [row for row in clips if held[row["clip"]]["category"] == category or
                (category == "original" and held[row["clip"]]["category"] in ("short", "long", "pause"))]
        offsets = [row["endpointOffsetMs"] for row in rows if "endpointOffsetMs" in row]
        early = sum(offset < -150 for offset in offsets)
        delay = [offset for offset in offsets if offset >= -150]
        print(f"Parakeet {category}: {early}/{len(rows)} early endpoints, {len(rows) - len(offsets)} misses, "
              f"p50/p95 audio fed after end {latency(delay)} ms for {len(delay)} on-time endpoints")
        if category == "two-second-pause":
            before_resume = sum("firstEndpointMs" in row and
                                row["firstEndpointMs"] < held[row["clip"]]["hold"]["end"] * 1000
                                for row in rows)
            print(f"Parakeet {category}: {before_resume}/{len(rows)} endpoints before the speaker resumed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("replay", type=Path)
    parser.add_argument("--parakeet", type=Path)
    parser.add_argument("--parakeet-manifest", type=Path)
    args = parser.parse_args()
    replay_table(json.loads(args.replay.read_text())["results"])
    if args.parakeet:
        if not args.parakeet_manifest:
            parser.error("--parakeet requires --parakeet-manifest")
        print()
        parakeet_table(json.loads(args.parakeet.read_text()), json.loads(args.parakeet_manifest.read_text()))


if __name__ == "__main__":
    main()
