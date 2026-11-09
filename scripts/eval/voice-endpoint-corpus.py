#!/usr/bin/env python3
"""Prepare a deterministic English replay sample from LiveKit EoT Bench (CC BY 4.0)."""

import argparse
import io
import json
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen


DATASET = "livekit/eot-bench-data"
REVISION = "ca9d98a9686b920a2d8c9eb984224ba9be74e4dd"
API = "https://datasets-server.huggingface.co/rows?"
RATE = 16000


def fetch(url):
    with urlopen(url, timeout=35) as response:
        return response.read()


def rows(offset):
    query = urlencode(dict(dataset=DATASET, config="en", split="validation", offset=offset, length=100))
    return json.loads(fetch(API + query))["rows"]


def select(rows):
    seen = set()
    for split, count, group in (
        ("development", 4, "short"),
        ("development", 4, "long"),
        ("evaluation", 8, "long"),
        ("evaluation", 8, "pause"),
        ("evaluation", 8, "short"),
    ):
        selected = 0
        for item in rows:
            row = item["row"]
            conversation = row["id"].rsplit("__user_turn_", 1)[0]
            spans = row["silence_spans"]
            words = row["words"]
            if conversation in seen or not spans or len(words) < 5:
                continue
            duration = spans[-1]["start"] - words[0]["start"]
            pause = next((span for span in spans[:-1] if
                          0.65 <= span["end"] - span["start"] < 1.9
                          and span["start"] > words[0]["start"] + 1
                          and span["end"] < spans[-1]["start"] - 1), None)
            match = {"short": 2 <= duration < 8, "long": 15 <= duration <= 26,
                     "pause": pause is not None and 5 <= duration <= 14}[group]
            if match:
                seen.add(conversation)
                selected += 1
                yield row, split, group, pause
                if selected == count:
                    break
        if selected != count:
            raise RuntimeError(f"Found only {selected} of {count} requested {split} {group} turns.")


def prepare(directory, item):
    row, split, group, pause = item
    url = row["audio"][0]["src"]
    parsed = urlparse(url)
    if parsed.hostname != "datasets-server.huggingface.co" or REVISION not in parsed.path.split("/"):
        raise ValueError("Unexpected audio host or dataset revision")
    with wave.open(io.BytesIO(fetch(url)), "rb") as wav:
        if (wav.getframerate(), wav.getsampwidth(), wav.getnchannels()) != (RATE, 2, 1):
            raise ValueError("Expected 16 kHz mono PCM16")
        pcm = wav.readframes(wav.getnframes())
    end = row["silence_spans"][-1]["start"]
    pcm = pcm[:round(end * RATE) * 2]
    reference = " ".join(word["word"] for word in row["words"])

    def save(suffix, samples, true_end, category, hold):
        name = row["id"] + suffix + ".wav"
        with wave.open(str(directory / name), "wb") as wav:
            wav.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
            wav.writeframes(samples)
        return dict(clip=name, voice=row["id"].rsplit("__user_turn_", 1)[0], seconds=true_end,
                    reference=reference, split=split, category=category, hold=hold)

    entries = [save("", pcm, end, group, None)]
    if group == "pause":
        extra = 2.0 - (pause["end"] - pause["start"])
        index = round((pause["start"] + pause["end"]) * RATE / 2) * 2
        extended = pcm[:index] + bytes(round(extra * RATE) * 2) + pcm[index:]
        hold = dict(start=pause["start"], end=pause["end"] + extra)
        entries.append(save("-2s", extended, end + extra, "two-second-pause", hold))
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    output = parser.parse_args().output
    output.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as executor:
        records = [item for page in executor.map(rows, (0, 100, 200, 300)) for item in page]
        sample = list(select(records))
        manifest = [entry for batch in executor.map(lambda item: prepare(output, item), sample) for entry in batch]
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Prepared {len(sample)} original turns and {len(manifest) - len(sample)} pause variants.")


if __name__ == "__main__":
    main()
