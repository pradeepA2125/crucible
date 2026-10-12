import base64
import hashlib
import io
import json
import random
import stat

import pytest
from PIL import Image, PngImagePlugin

import agentd.images as images
from agentd.images import ImageAssetStore, ImageValidationError, estimate_image_tokens


def image_bytes(fmt="PNG", size=(12, 10), color="red", **kwargs):
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, format=fmt, **kwargs)
    return stream.getvalue()


def upload(raw=None, mime="image/png", name="sample.png"):
    raw = raw if raw is not None else image_bytes()
    return {"name": name, "media_type": mime, "bytes": len(raw),
            "data_url": f"data:{mime};base64,{base64.b64encode(raw).decode()}"}


def test_durable_normalization_and_transport_only_binary(tmp_path):
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("secret", "not retained")
    raw = image_bytes(pnginfo=metadata)
    store = ImageAssetStore(tmp_path)
    ref = store.import_uploads([upload(raw)])[0]
    assert set(ref) == {"asset_id", "name", "media_type", "bytes", "width", "height",
                        "estimated_tokens", "original_width", "original_height",
                        "original_bytes", "normalization_version"}
    assert "data:" not in json.dumps(ref)
    normalized = store.read_asset(ref["asset_id"])
    with Image.open(io.BytesIO(normalized)) as image:
        assert image.info == {}
    reread = ImageAssetStore(tmp_path).ref_for_asset(ref["asset_id"], "again.png")
    assert reread["name"] == "again.png"
    transport = store.resolve({**ref, "width": 999999})
    assert transport["width"] == 12
    assert base64.b64decode(transport["base64_data"]) == normalized
    assert transport["data_url"].startswith("data:image/png;base64,")
    asset = tmp_path / ".crucible" / "image-assets" / (ref["asset_id"] + ".png")
    assert stat.S_IMODE(asset.stat().st_mode) == 0o600
    assert stat.S_IMODE(asset.parent.stat().st_mode) == 0o700
    assert store.import_bytes(raw, "other.png")["asset_id"] == ref["asset_id"]
    different = store.import_bytes(image_bytes(color="blue"), "sample.png")
    assert different["asset_id"] != ref["asset_id"]
    assert estimate_image_tokens(513, 512) == 2048


@pytest.mark.parametrize("fmt,mime", list(images._FORMAT_MIMES.items()))
def test_actual_supported_formats_normalize(tmp_path, fmt, mime):
    ref = ImageAssetStore(tmp_path).import_bytes(image_bytes(fmt), "sample", mime)
    assert ref["media_type"] == "image/png"


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(media_type="image/jpeg"),
    lambda value: value.update(bytes=True),
    lambda value: value.update(bytes=value["bytes"] + 1),
    lambda value: value.update(data_url="data:image/png;base64,%%%"),
    lambda value: value.update(name="bad\nname"),
])
def test_invalid_upload_is_atomic(tmp_path, mutate):
    bad = upload()
    mutate(bad)
    with pytest.raises(ImageValidationError):
        ImageAssetStore(tmp_path).import_uploads([upload(), bad])
    assert not (tmp_path / ".crucible").exists()


@pytest.mark.parametrize("raw,mime", [
    (b"not image", "image/png"),
    (image_bytes("JPEG"), "image/png"),
    (image_bytes()[:40], "image/png"),
])
def test_spoofed_truncated_actual_bytes_rejected(tmp_path, raw, mime):
    with pytest.raises(ImageValidationError):
        ImageAssetStore(tmp_path).import_bytes(raw, "spoof.png", mime)


def test_animated_and_multiframe_rejected(tmp_path):
    for fmt in ("GIF", "TIFF", "PNG", "WEBP"):
        stream = io.BytesIO()
        Image.new("RGB", (4, 4), "red").save(
            stream, format=fmt, save_all=True,
            append_images=[Image.new("RGB", (4, 4), "blue")],
        )
        with pytest.raises(ImageValidationError, match="multiframe"):
            ImageAssetStore(tmp_path).import_bytes(stream.getvalue(), "frames")


def test_encoded_limit_before_decode(tmp_path, monkeypatch):
    monkeypatch.setattr(images, "MAX_ENCODED_BYTES", 8)
    def forbidden(*args, **kwargs):
        pytest.fail("decode called before encoded size guard")
    monkeypatch.setattr(images.base64, "b64decode", forbidden)
    with pytest.raises(ImageValidationError) as error:
        ImageAssetStore(tmp_path).import_uploads([
            {"name": "x", "media_type": "image/png", "bytes": 1,
             "data_url": "data:image/png;base64," + "A" * 12},
        ])
    assert error.value.status_code == 413


def test_pixel_and_bomb_guard_before_load(tmp_path, monkeypatch):
    raw = image_bytes(size=(20, 20))
    monkeypatch.setattr(images, "MAX_IMAGE_PIXELS", 399)
    with pytest.raises(ImageValidationError) as error:
        ImageAssetStore(tmp_path).import_bytes(raw, "pixels")
    assert error.value.status_code == 413
    monkeypatch.setattr(images, "MAX_IMAGE_PIXELS", 16_000_000)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(ImageValidationError, match="bomb"):
        ImageAssetStore(tmp_path).import_bytes(raw, "bomb")


@pytest.mark.parametrize("limit,value", [("MAX_UPLOAD_BYTES", 100), ("MAX_UPLOAD_PIXELS", 200)])
def test_aggregate_bounds_atomic(tmp_path, monkeypatch, limit, value):
    monkeypatch.setattr(images, limit, value)
    with pytest.raises(ImageValidationError) as error:
        ImageAssetStore(tmp_path).import_uploads([upload(), upload()])
    assert error.value.status_code == 413
    assert not (tmp_path / ".crucible").exists()


def test_normalized_size_and_aggregate_are_bounded(tmp_path, monkeypatch):
    raw = image_bytes("JPEG")
    real_normalize = images._normalize
    def large(*args):
        ref, normalized = real_normalize(*args)
        return ref, normalized + b"x" * 2000
    monkeypatch.setattr(images, "_normalize", large)
    monkeypatch.setattr(images, "MAX_UPLOAD_BYTES", 1500)
    with pytest.raises(ImageValidationError, match="normalized"):
        ImageAssetStore(tmp_path).import_uploads([upload(raw, "image/jpeg")])
    monkeypatch.setattr(images, "_normalize", real_normalize)
    buffer = images._BoundedBuffer()
    monkeypatch.setattr(images, "MAX_IMAGE_BYTES", 100)
    with pytest.raises(ImageValidationError, match="Normalized"):
        buffer.write(b"x" * 101)


@pytest.mark.parametrize("size", [(12, 10), (3000, 1500), (2200, 2200), (1, 3000)])
def test_v2_geometry_and_idempotency(tmp_path, size):
    store = ImageAssetStore(tmp_path)
    raw = image_bytes(size=size)
    ref = store.import_bytes(raw, "geometry")
    assert max(ref["width"], ref["height"]) <= 2048
    assert ref["width"] * ref["height"] <= 4_000_000
    assert ref["width"] <= size[0] and ref["height"] <= size[1]
    assert abs(ref["width"] * size[1] - ref["height"] * size[0]) <= max(size)
    assert (ref["original_width"], ref["original_height"]) == size
    assert ref["original_bytes"] == len(raw)
    normalized = store.read_asset(ref["asset_id"])
    assert store.import_bytes(normalized, "again")["asset_id"] == ref["asset_id"]
    assert store.read_preview(ref["asset_id"]) == normalized
    assert store.resolve(ref)["original_width"] == size[0]


def test_legacy_derivative_is_immutable(tmp_path):
    store = ImageAssetStore(tmp_path)
    raw = image_bytes(size=(3000, 1500))
    digest = hashlib.sha256(raw).hexdigest()
    store._store({"asset_id": digest}, raw)
    ref = {"asset_id": digest, "name": "legacy", "width": 3000, "height": 1500}
    resolved = store.resolve(ref)
    preview = store.read_preview(digest)
    assert preview == base64.b64decode(resolved["base64_data"])
    assert resolved == store.resolve(ref)
    assert resolved["asset_id"] == digest
    assert resolved["transmitted_asset_id"] == hashlib.sha256(preview).hexdigest()
    assert resolved["width"] == 2048 and resolved["height"] == 1024
    assert resolved["original_bytes"] == len(raw)
    assert store.read_asset(digest) == raw
    reread = store.ref_for_asset(digest)
    assert reread["asset_id"] == resolved["transmitted_asset_id"] != digest
    assert store.read_asset(reread["asset_id"]) == preview
    assert store.read_asset(digest) == raw


def test_original_limits_survive_resize(tmp_path, monkeypatch):
    raw = image_bytes(size=(3000, 1500))
    monkeypatch.setattr(images, "MAX_UPLOAD_PIXELS", 8_000_000)
    with pytest.raises(ImageValidationError):
        ImageAssetStore(tmp_path).import_uploads([upload(raw), upload(raw)])
    assert not (tmp_path / ".crucible").exists()
    monkeypatch.setattr(images, "MAX_IMAGE_PIXELS", 4_000_000)
    with pytest.raises(ImageValidationError):
        ImageAssetStore(tmp_path).import_bytes(raw, "oversize")


def test_progressive_floor_rejection_and_no_upscale(tmp_path, monkeypatch):
    monkeypatch.setattr(images, "MAX_TRANSMITTED_BYTES", 1)
    with pytest.raises(ImageValidationError, match="attach a crop") as error:
        ImageAssetStore(tmp_path).import_bytes(image_bytes(size=(700, 350)), "floor")
    assert error.value.status_code == 413
    assert not (tmp_path / ".crucible").exists()


def test_progressive_resamples_original_and_is_idempotent(tmp_path, monkeypatch):
    image = Image.frombytes("RGB", (1200, 800), random.Random(2).randbytes(1200 * 800 * 3))
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    monkeypatch.setattr(images, "MAX_TRANSMITTED_BYTES", 600_000)
    store = ImageAssetStore(tmp_path)
    ref = store.import_bytes(stream.getvalue(), "progressive")
    normalized = store.read_asset(ref["asset_id"])
    assert 512 <= ref["width"] < 1020  # At least two progressive variants.
    assert len(normalized) <= 600_000
    expected = image.resize((ref["width"], ref["height"]), Image.Resampling.LANCZOS)
    with Image.open(io.BytesIO(normalized)) as decoded:
        assert decoded.tobytes() == expected.tobytes()
    assert store.import_bytes(normalized, "again")["asset_id"] == ref["asset_id"]


def test_scaled_high_contrast_edge(tmp_path):
    from PIL import ImageDraw
    image = Image.new("RGB", (3000, 1500), "white")
    ImageDraw.Draw(image).rectangle((300, 300, 900, 900), fill="black")
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    store = ImageAssetStore(tmp_path)
    ref = store.import_bytes(stream.getvalue(), "edge")
    with Image.open(io.BytesIO(store.read_preview(ref["asset_id"]))) as decoded:
        assert decoded.getpixel((300, 300)) == (0, 0, 0)
        assert decoded.getpixel((100, 300)) == (255, 255, 255)
        row = [decoded.getpixel((x, 300))[0] for x in range(200, 211)]
        assert min(row) == 0 and max(row) == 255


def test_high_contrast_edges_are_lossless_without_resize(tmp_path):
    from PIL import ImageDraw
    image = Image.new("RGB", (600, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.text((10, 10), "Readable screenshot text 0123456789", fill="black")
    draw.line((10, 40, 580, 40), fill="black", width=1)
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    store = ImageAssetStore(tmp_path)
    ref = store.import_bytes(stream.getvalue(), "text")
    with Image.open(io.BytesIO(store.read_preview(ref["asset_id"]))) as decoded:
        assert decoded.tobytes() == image.tobytes()


@pytest.mark.parametrize("size", [(2880, 1800), (3200, 2000)])
def test_synthetic_retina_measurements(tmp_path, size):
    from PIL import ImageDraw
    # Reproducible screenshot: flat UI/text above a noisy photograph panel.
    image = Image.new("RGB", size, "white")
    panel = Image.frombytes("RGB", (size[0], 300), random.Random(69).randbytes(size[0] * 900))
    image.paste(panel, (0, size[1] - 300))
    draw = ImageDraw.Draw(image)
    for y in range(20, size[1] - 320, 32):
        draw.text((20, y), "Retina editor: high contrast text 0123456789", fill="black")
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    raw = stream.getvalue()
    assert 2 * 1024 * 1024 <= len(raw) <= 5 * 1024 * 1024
    store = ImageAssetStore(tmp_path)
    ref = store.import_bytes(raw, "retina")
    normalized = store.read_asset(ref["asset_id"])
    with Image.open(io.BytesIO(normalized)) as decoded:
        decoded.load()
        assert decoded.format == "PNG" and decoded.size == (ref["width"], ref["height"])
    assert len(normalized) <= 2 * 1024 * 1024
    assert max(ref["width"], ref["height"]) <= 2048
    assert store.import_bytes(normalized, "idempotent")["asset_id"] == ref["asset_id"]
    print(f"RETINA {size} {len(raw)} bytes -> "
          f"{ref['width']}x{ref['height']} {len(normalized)} bytes; "
          f"tokens {estimate_image_tokens(*size)} -> {ref['estimated_tokens']}")


def test_shape_count_and_empty(tmp_path):
    store = ImageAssetStore(tmp_path)
    assert store.import_uploads(None) == store.import_uploads([]) == []
    for invalid in ({}, [None], [upload()] * 9):
        with pytest.raises(ImageValidationError):
            store.import_uploads(invalid)
    assert not (tmp_path / ".crucible").exists()


def test_unsupported_secure_filesystem_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delattr(images.os, "O_NOFOLLOW")
    with pytest.raises(ImageValidationError, match="requires macOS or Linux"):
        ImageAssetStore(tmp_path).import_bytes(image_bytes(), "x")
    assert not (tmp_path / ".crucible").exists()


def test_store_escape_corrupt_and_cross_workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / ".crucible").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ImageValidationError):
        ImageAssetStore(root).import_bytes(image_bytes(), "x")
    assert list(outside.iterdir()) == []
    (root / ".crucible").unlink()
    store = ImageAssetStore(root)
    ref = store.import_bytes(image_bytes(), "x")
    with pytest.raises(ImageValidationError):
        ImageAssetStore(outside).read_asset(ref["asset_id"])
    for invalid in ("../x", "A" * 64, "x"):
        with pytest.raises(ImageValidationError):
            store.read_asset(invalid)
    asset = root / ".crucible" / "image-assets" / (ref["asset_id"] + ".png")
    asset.write_bytes(b"corrupt")
    with pytest.raises(ImageValidationError, match="corrupt"):
        store.resolve(ref)
    asset.unlink()
    target = outside / "target"
    target.write_bytes(image_bytes())
    asset.symlink_to(target)
    with pytest.raises(ImageValidationError):
        store.read_asset(ref["asset_id"])
