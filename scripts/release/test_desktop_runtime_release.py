import hashlib
import json
import zipfile
from pathlib import Path

import pytest
from importlib.util import spec_from_file_location, module_from_spec

spec = spec_from_file_location("desktop_runtime_release", Path(__file__).with_name("desktop-runtime-release.py"))
module = module_from_spec(spec)
spec.loader.exec_module(module)


def fixture(tmp_path):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    components = {}
    for name in module.VENDORS:
        target = "any" if name == "jdtls" else "darwin-arm64"
        filename = name + ".bin"
        (vendor / filename).write_bytes(name.encode())
        components[name] = {"version": "1.0.0", "urls": {target: module.REPO + "v0.8.1/" + filename}, "sha256": {target: module.digest(vendor / filename)}}
    components["lsps"] = {"version": "1", "npmPackages": ["pyright@1.1.400"]}
    manifest = tmp_path / "vendor.json"
    manifest.write_text(json.dumps({"manifestVersion": 1, "releaseTag": "v0.8.1", "components": components}))
    wheel = tmp_path / "crucible_agentd-0.2.1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("crucible_agentd-0.2.1.dist-info/METADATA", "Name: crucible-agentd\nVersion: 0.2.1\n")
    indexer = tmp_path / "indexer"
    indexer.write_bytes(bytes.fromhex("cffaedfe0c000001") + b"current source")
    return manifest, vendor, wheel, indexer


def test_runtime_uses_exact_vendor_pins_and_current_source_artifact_hashes(tmp_path):
    args = fixture(tmp_path)
    result = module.prepare(*args, "0.9.0", tmp_path / "out")
    assert result["releaseTag"] == "v0.9.0"
    assert result["components"]["agentd"]["sha256"]["any"] == module.digest(args[2])
    assert result["components"]["indexer"]["sha256"]["darwin-arm64"] == module.digest(args[3])
    assert "/v0.8.1/" in result["components"]["uv"]["urls"]["darwin-arm64"]
    assert "/v0.9.0/" in result["components"]["agentd"]["urls"]["any"]
    with pytest.raises(FileExistsError):
        module.prepare(*args, "0.9.0", tmp_path / "out")


def test_runtime_refuses_changed_vendor_or_wrong_architecture_without_output(tmp_path):
    args = fixture(tmp_path)
    (args[1] / "uv.bin").write_bytes(b"modified")
    with pytest.raises(ValueError, match="checksum"):
        module.prepare(*args, "0.9.0", tmp_path / "out")
    assert not (tmp_path / "out").exists()
    (args[1] / "uv.bin").write_bytes(b"uv")
    args[3].write_bytes(b"not an arm64 executable")
    with pytest.raises(ValueError, match="Apple Silicon"):
        module.prepare(*args, "0.9.0", tmp_path / "out")
