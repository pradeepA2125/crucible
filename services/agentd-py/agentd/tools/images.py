"""Workspace image reading for native vision inputs (never binary tool text)."""
from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

from agentd.images import MAX_IMAGE_BYTES, ImageAssetStore, ImageValidationError
from agentd.tools.registry import ToolOutput


async def read_image(
    *, root: Path, path: str = "", asset_id: str = "", asset_root: Path | None = None,
) -> ToolOutput:
    return await asyncio.to_thread(
        _read_image_sync, path=path, asset_id=asset_id, root=root, asset_root=asset_root,
    )


def _read_workspace_image(root: Path, path: str) -> bytes:
    if not all(hasattr(os, flag) for flag in ("O_DIRECTORY", "O_NOFOLLOW")):
        raise ImageValidationError(
            "Secure image reading is unavailable on this platform; "
            "image support currently requires macOS or Linux",
        )
    root = root.resolve()
    resolved = (root / path).resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ImageValidationError("path traversal rejected: image is outside workspace") from exc
    # Open every resolved component with NOFOLLOW and pin descriptors: a symlink swap
    # after resolve cannot redirect the read outside the workspace.
    descriptors = []
    try:
        current = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(current)
        for component in relative.parts[:-1]:
            current = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=current)
            descriptors.append(current)
        if not relative.parts:
            raise ImageValidationError("Image path is a directory")
        descriptor = os.open(relative.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=current)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ImageValidationError("Image path is not a regular file")
            if info.st_size > MAX_IMAGE_BYTES:
                raise ImageValidationError("Image exceeds maximum supported size of 8 MiB", 413)
            raw = stream.read(MAX_IMAGE_BYTES + 1)
        if len(raw) > MAX_IMAGE_BYTES:
            raise ImageValidationError("Image exceeds maximum supported size of 8 MiB", 413)
        return raw
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _read_image_sync(
    *, root: Path, path: str = "", asset_id: str = "", asset_root: Path | None = None,
) -> ToolOutput:
    if bool(path) == bool(asset_id):
        return ToolOutput("Error: provide exactly one of path or asset_id", is_error=True)
    store = ImageAssetStore(asset_root if asset_root is not None else root)
    try:
        if asset_id:
            ref = store.ref_for_asset(asset_id)
        else:
            raw = _read_workspace_image(root, path)
            ref = store.import_bytes(raw, Path(path).name)
        return ToolOutput(
            output=(f"Image: {ref['name']}\nAsset ID: {ref['asset_id']}\n"
                    f"MIME type: {ref['media_type']}\nSize: {ref['bytes']} bytes\n"
                    f"Dimensions: {ref['width']} x {ref['height']}\n"
                    "Native image attached at this tool result."),
            image_refs=[ref],
        )
    except (ImageValidationError, OSError, ValueError) as exc:
        return ToolOutput(output=f"Error reading image: {exc}", is_error=True)