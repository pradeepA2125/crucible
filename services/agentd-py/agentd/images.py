"""Validated, workspace-scoped image assets; durable references never contain binary data."""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import os
import re
import stat
import uuid
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError
from typing_extensions import Buffer

MAX_TRANSMITTED_BYTES = 2 * 1024 * 1024
MAX_TRANSMITTED_EDGE = 2048
MAX_TRANSMITTED_PIXELS = 4_000_000
MIN_TRANSMITTED_EDGE = 512
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_UPLOAD_BYTES = 24 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MAX_UPLOAD_PIXELS = 40_000_000
MAX_UPLOAD_IMAGES = 8
MAX_ENCODED_BYTES = 4 * ((MAX_IMAGE_BYTES + 2) // 3)
_FORMAT_MIMES = {
    "PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp",
    "GIF": "image/gif", "BMP": "image/bmp", "TIFF": "image/tiff",
}
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class ImageValidationError(ValueError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def estimate_image_tokens(width: int, height: int) -> int:
    """Conservative application heuristic, not a provider billing measurement."""
    return 1024 + 512 * ((width + 511) // 512) * ((height + 511) // 512)


def _name(name: object) -> str:
    if not isinstance(name, str) or not name.strip() or len(name) > 255:
        raise ImageValidationError("Image name must be a nonempty string of at most 255 characters")
    # A display label, never a filesystem path or control-character-bearing prompt fragment.
    clean = name.replace("\\", "/").rsplit("/", 1)[-1]
    if not clean or any(ord(char) < 32 or ord(char) == 127 for char in clean):
        raise ImageValidationError("Image name contains invalid characters")
    return clean


class _BoundedBuffer(io.BytesIO):
    def write(self, data: Buffer, /) -> int:
        if self.tell() + memoryview(data).nbytes > MAX_IMAGE_BYTES:
            raise ImageValidationError("Normalized image exceeds maximum supported size", 413)
        return super().write(data)


def _normalize(
    raw: bytes, name: str, media_type: str | None,
) -> tuple[dict[str, Any], bytes]:
    name = _name(name)
    if not isinstance(raw, bytes) or not raw:
        raise ImageValidationError("Image bytes must be nonempty")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ImageValidationError("Image exceeds maximum supported size of 8 MiB", 413)
    if media_type is not None and media_type not in _FORMAT_MIMES.values():
        raise ImageValidationError("Unsupported image type; use PNG, JPEG, WebP, GIF, BMP or TIFF")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                actual_mime = _FORMAT_MIMES.get(image.format or "")
                if actual_mime is None:
                    raise ImageValidationError("Unsupported image type")
                if media_type is not None and actual_mime != media_type:
                    raise ImageValidationError(
                        "Declared image MIME type does not match image bytes",
                    )
                width, height = image.size
                if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                    raise ImageValidationError("Image exceeds maximum decoded pixel count", 413)
                if getattr(image, "n_frames", 1) != 1 or getattr(image, "is_animated", False):
                    raise ImageValidationError("Animated or multiframe images are not supported")
                image.verify()
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
                mode = "RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB"
                converted = image.convert(mode)
                # Rebuild from pixels: even implicit EXIF/ICC/text metadata is discarded.
                clean = Image.frombytes(mode, image.size, converted.tobytes())
                original_width, original_height = width, height
                scale = min(1.0, MAX_TRANSMITTED_EDGE / max(width, height),
                            (MAX_TRANSMITTED_PIXELS / (width * height)) ** 0.5)
                edge = max(1, int(max(width, height) * scale))
                floor = min(edge, MIN_TRANSMITTED_EDGE)
                while True:
                    ratio = edge / max(original_width, original_height)
                    width = max(1, int(original_width * ratio))
                    height = max(1, int(original_height * ratio))
                    variant = clean if clean.size == (width, height) else clean.resize(
                        (width, height), Image.Resampling.LANCZOS,
                    )
                    buffer = _BoundedBuffer()
                    try:
                        variant.save(buffer, format="PNG", optimize=True)
                        normalized = buffer.getvalue()
                    except ImageValidationError:
                        normalized = b""
                    if normalized and len(normalized) <= MAX_TRANSMITTED_BYTES:
                        break
                    if edge <= floor:
                        raise ImageValidationError(
                            "Image cannot meet 2 MiB transport limit at minimum dimensions; "
                            "attach a crop instead", 413,
                        )
                    edge = max(floor, int(edge * 0.85))
    except ImageValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageValidationError("Image decompression bomb / excessive pixel count", 413) from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, EOFError) as exc:
        raise ImageValidationError("Malformed or truncated image bytes") from exc
    ref = {
        "asset_id": hashlib.sha256(normalized).hexdigest(), "name": name,
        "media_type": "image/png", "bytes": len(normalized), "width": width, "height": height,
        "estimated_tokens": estimate_image_tokens(width, height),
        "original_width": original_width, "original_height": original_height,
        "original_bytes": len(raw), "normalization_version": 2,
    }
    return ref, normalized


class ImageAssetStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()

    @contextmanager
    def _directory(self, *, create: bool = False) -> Iterator[int]:
        """Pin directory descriptors, rejecting symlinks even during concurrent replacement."""
        if not all(hasattr(os, flag) for flag in ("O_DIRECTORY", "O_NOFOLLOW")):
            raise ImageValidationError(
                "Secure image asset storage is unavailable on this platform; "
                "image support currently requires macOS or Linux",
            )
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptors = []
        try:
            current = os.open(self.root, flags)
            descriptors.append(current)
            for component in (".crucible", "image-assets"):
                if create:
                    try:
                        os.mkdir(component, mode=0o700, dir_fd=current)
                    except FileExistsError:
                        pass
                current = os.open(component, flags, dir_fd=current)
                descriptors.append(current)
                if create:
                    os.fchmod(current, 0o700)
            yield current
        except OSError as exc:
            raise ImageValidationError("Image asset store is missing or unsafe") from exc
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def _store(self, ref: dict[str, Any], raw: bytes) -> None:
        filename = ref["asset_id"] + ".png"
        with self._directory(create=True) as directory:
            temporary = "." + uuid.uuid4().hex + ".tmp"
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600, dir_fd=directory,
            )
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    # Atomic no-overwrite publication: identical concurrent imports are harmless.
                    os.link(temporary, filename, src_dir_fd=directory, dst_dir_fd=directory,
                            follow_symlinks=False)
                except FileExistsError:
                    if self._read_from(directory, filename) != raw:
                        raise ImageValidationError(
                            "Existing image asset is corrupt or unsafe",
                        ) from None
                os.fsync(directory)
            finally:
                os.unlink(temporary, dir_fd=directory)

    @staticmethod
    def _read_from(directory: int, filename: str) -> bytes:
        descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ImageValidationError("Image asset is not a private regular file")
            if info.st_size > MAX_IMAGE_BYTES:
                raise ImageValidationError("Image asset exceeds maximum supported size", 413)
            raw = stream.read(MAX_IMAGE_BYTES + 1)
        if len(raw) > MAX_IMAGE_BYTES:
            raise ImageValidationError("Image asset exceeds maximum supported size", 413)
        return raw

    def import_uploads(self, attachments: object) -> list[dict[str, Any]]:
        if attachments is None:
            return []
        if not isinstance(attachments, list):
            raise ImageValidationError("Image attachments must be a list")
        if len(attachments) > MAX_UPLOAD_IMAGES:
            raise ImageValidationError("At most 8 images may be uploaded together", 413)
        prepared = []
        raw_total = normalized_total = pixels_total = 0
        for attachment in attachments:
            if not isinstance(attachment, dict):
                raise ImageValidationError("Each image attachment must be an object")
            name = _name(attachment.get("name"))
            mime = attachment.get("media_type")
            url = attachment.get("data_url")
            declared = attachment.get("bytes")
            if mime not in _FORMAT_MIMES.values() or not isinstance(url, str):
                raise ImageValidationError(
                    "Image requires a supported MIME type and base64 Data URL",
                )
            if type(declared) is not int or declared <= 0:
                raise ImageValidationError("Image byte count must be a positive integer")
            prefix = f"data:{mime};base64,"
            if declared > MAX_IMAGE_BYTES or len(url) > len(prefix) + MAX_ENCODED_BYTES:
                raise ImageValidationError("Encoded image exceeds maximum supported size", 413)
            if not url.startswith(prefix):
                raise ImageValidationError("Image Data URL must match its declared MIME type")
            raw_total += declared
            if raw_total > MAX_UPLOAD_BYTES:
                raise ImageValidationError("Aggregate uploaded image bytes exceed 24 MiB", 413)
            try:
                encoded = url[len(prefix):]
                raw = base64.b64decode(encoded, validate=True)
                if base64.b64encode(raw).decode("ascii") != encoded:
                    raise ImageValidationError("Image base64 encoding must be canonical")
            except (binascii.Error, ValueError) as exc:
                raise ImageValidationError("Invalid image base64 encoding") from exc
            if len(raw) != declared:
                raise ImageValidationError("Declared image byte count does not match decoded bytes")
            ref, normalized = _normalize(raw, name, mime)
            normalized_total += len(normalized)
            pixels_total += ref["original_width"] * ref["original_height"]
            if normalized_total > MAX_UPLOAD_BYTES or pixels_total > MAX_UPLOAD_PIXELS:
                raise ImageValidationError(
                    "Aggregate normalized image bytes or pixels exceed limit", 413,
                )
            prepared.append((ref, normalized))
        # No assets are written until the ENTIRE upload has passed validation.
        for ref, normalized in prepared:
            self._store(ref, normalized)
        return [ref for ref, _ in prepared]

    def import_bytes(
        self, raw: bytes, name: str, media_type: str | None = None,
    ) -> dict[str, Any]:
        ref, normalized = _normalize(raw, name, media_type)
        self._store(ref, normalized)
        return ref

    def read_asset(self, asset_id: str) -> bytes:
        if not isinstance(asset_id, str) or not _DIGEST.fullmatch(asset_id):
            raise ImageValidationError("Invalid image asset ID")
        with self._directory() as directory:
            raw = self._read_from(directory, asset_id + ".png")
        if hashlib.sha256(raw).hexdigest() != asset_id:
            raise ImageValidationError("Image asset is corrupt")
        return raw

    def ref_for_asset(self, asset_id: str, name: str | None = None) -> dict[str, Any]:
        raw = self.read_asset(asset_id)
        ref, normalized = _normalize(raw, name or f"{asset_id}.png", "image/png")
        if normalized != raw:
            self._store(ref, normalized)
        return ref

    def read_preview(self, asset_id: str) -> bytes:
        """Return a deterministic v2 raster without mutating the digest-owned asset."""
        raw = self.read_asset(asset_id)
        _, normalized = _normalize(raw, f"{asset_id}.png", "image/png")
        return normalized

    def resolve(self, ref: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(ref, dict):
            raise ImageValidationError("Image reference must be an object")
        asset_id = ref.get("asset_id")
        if not isinstance(asset_id, str):
            raise ImageValidationError("Invalid image asset ID")
        raw = self.read_asset(asset_id)
        trusted, normalized = _normalize(raw, ref.get("name") or f"{asset_id}.png", "image/png")
        # Provenance is advisory metadata, never used for validation or token costing.
        # Preserve it only for a self-consistent v2 ref describing these stored bytes.
        if (ref.get("normalization_version") == 2 and normalized == raw
                and all(ref.get(key) == trusted[key] for key in ("width", "height", "bytes"))):
            ow, oh, ob = (ref.get(key) for key in (
                "original_width", "original_height", "original_bytes",
            ))
            if (type(ow) is int and type(oh) is int and type(ob) is int
                    and trusted["width"] <= ow and trusted["height"] <= oh
                    and ow * oh <= MAX_IMAGE_PIXELS and 0 < ob <= MAX_IMAGE_BYTES):
                trusted.update(original_width=ow, original_height=oh, original_bytes=ob)
        encoded = base64.b64encode(normalized).decode("ascii")
        return {
            "asset_id": asset_id, "transmitted_asset_id": trusted["asset_id"],
            "data_url": f"data:image/png;base64,{encoded}", "base64_data": encoded,
            **{key: trusted[key] for key in (
                "media_type", "bytes", "width", "height", "estimated_tokens",
                "original_width", "original_height", "original_bytes", "normalization_version",
            )},
        }
