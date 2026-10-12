"""Ephemeral visual admission. Durable history contains references, never image bytes."""
from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


def visual_tokens(ref: dict[str, Any]) -> int:
    width = max(1, int(ref.get("width", 1)))
    height = max(1, int(ref.get("height", 1)))
    return max(int(ref.get("estimated_tokens", 0)),
               1024 + 512 * ((width + 511) // 512) * ((height + 511) // 512))


def image_tombstone(ref: dict[str, Any], reason: str) -> str:
    return (f"[Image {ref.get('asset_id', '?')} ({ref.get('name', 'image')}) unavailable: "
            f"{reason}. Do not claim to see it; reread using read_image with asset_id.]")


def history_visual_tokens(history: list[dict[str, Any]]) -> int:
    # Only newest origins count, including when called on unprojected history.
    refs = {str(r.get("asset_id")): r for m in history
            for r in m.get("image_refs", []) if isinstance(r, dict)}
    return sum(visual_tokens(r) for r in refs.values())


@dataclass
class ImageProjection:
    history: list[dict[str, Any]]
    image_assets: dict[str, dict[str, Any]]
    revision: str
    estimated_image_tokens: int
    image_bytes: int


def project_image_context(
    history: list[dict[str, Any]], resolve: Callable[[dict[str, Any]], dict[str, Any]], *,
    context_window: int | None = None, reserved_output_tokens: int = 0,
    text_and_fixed_tokens: int | None = None, max_images: int = 8,
    max_bytes: int = 24 * 1024 * 1024, max_visual_tokens: int = 16384,
) -> ImageProjection:
    projected = copy.deepcopy(history)
    budget = max_visual_tokens
    if context_window is not None and text_and_fixed_tokens is not None:
        headroom = context_window - reserved_output_tokens - text_and_fixed_tokens
        budget = min(budget, max(0, headroom))
    assets: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    excluded: dict[str, str] = {}
    dispositions: list[tuple[int, int, str, str]] = []
    used_tokens = used_bytes = 0
    for position in range(len(projected) - 1, -1, -1):
        message = projected[position]
        refs = message.pop("image_refs", [])
        active: list[tuple[int, dict[str, Any]]] = []
        notes: list[tuple[int, str]] = []
        for index in range(len(refs) - 1, -1, -1):
            ref = refs[index]
            if not isinstance(ref, dict):
                continue
            identity = str(ref.get("asset_id", ""))
            reason = "image context budget"
            if identity in seen:
                if identity in assets:
                    reason = "alias"
                    notes.append((index, f"[Image {identity} ({ref.get('name', 'image')}): "
                                  "alias of newer visual occurrence.]"))
                else:
                    reason = excluded[identity]
                    notes.append((index, image_tombstone(ref, reason)))
            else:
                seen.add(identity)
                try:
                    asset = resolve(ref)
                    if not asset:
                        raise ValueError("missing asset")
                except (OSError, ValueError, KeyError):
                    reason = "missing asset"
                    notes.append((index, image_tombstone(ref, reason)))
                else:
                    tokens = visual_tokens(asset)
                    size = int(asset.get("bytes") or ref.get("bytes") or 0)
                    if (len(assets) >= max_images or used_bytes + size > max_bytes
                            or used_tokens + tokens > budget):
                        notes.append((index, image_tombstone(ref, reason)))
                    else:
                        reason = "active"
                        safe_ref = {key: value for key, value in ref.items()
                                    if key in {"asset_id", "name", "media_type", "bytes",
                                               "width", "height", "estimated_tokens"}}
                        safe_ref.update(estimated_tokens=tokens, bytes=size)
                        for key in ("width", "height", "media_type", "original_width",
                                    "original_height", "original_bytes", "normalization_version",
                                    "transmitted_asset_id"):
                            if key in asset:
                                safe_ref[key] = asset[key]
                        active.append((index, safe_ref))
                        assets[identity] = asset
                        used_tokens += tokens
                        used_bytes += size
            if reason not in {"active", "alias"}:
                excluded[identity] = reason
            variant = str(assets.get(identity, {}).get("transmitted_asset_id", ""))
            dispositions.append((position, index, identity, reason + ":" + variant))
        if active:
            message["image_refs"] = [ref for _, ref in sorted(active)]
        if notes:
            message["content"] = (str(message.get("content", "")) + "\n"
                                  + "\n".join(note for _, note in sorted(notes)))
    revision = ""
    if dispositions:
        revision = hashlib.sha256(
            json.dumps(dispositions, separators=(",", ":")).encode()).hexdigest()
    return ImageProjection(projected, assets, revision, used_tokens, used_bytes)
