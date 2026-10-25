"""Restage a current-source Apple Silicon runtime using exact existing vendor pins.

Offline: the caller downloads the vendor release manifest/assets, builds the wheel
and indexer from the reviewed source, and supplies those paths explicitly.
"""
import argparse
import hashlib
import json
import re
import shutil
import zipfile
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import urlparse

REPO = "https://github.com/pradeepA2125/crucible/releases/download/"
VENDORS = ("uv", "ripgrep", "rust-analyzer", "gopls", "jre", "jdtls")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def prepare(vendor_manifest, vendor_assets, wheel, indexer, version, output):
    if not re.fullmatch(r"(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})", version):
        raise ValueError("An exact three-part desktop version is required")
    manifest = json.loads(vendor_manifest.read_text())
    if manifest.get("manifestVersion") != 1 or not re.fullmatch(r"v\d+\.\d+\.\d+", manifest.get("releaseTag", "")):
        raise ValueError("Expected an actual published vendor release manifest")
    copies = []
    components = {}
    for name in VENDORS:
        component = manifest["components"][name]
        target = "any" if name == "jdtls" else "darwin-arm64"
        url = component["urls"][target]
        parsed = urlparse(url)
        if not url.startswith(REPO + manifest["releaseTag"] + "/") or parsed.query or parsed.fragment:
            raise ValueError("Vendor URL must belong to the selected repository release")
        filename = Path(parsed.path).name
        if not re.fullmatch(r"[A-Za-z0-9_.+-]+", filename):
            raise ValueError("Unsafe vendor filename")
        source = vendor_assets / filename
        if source.is_symlink() or not source.is_file() or digest(source) != component["sha256"][target]:
            raise ValueError(f"Vendor checksum/type mismatch: {name}")
        components[name] = {"version": component["version"], "urls": {target: url}, "sha256": {target: digest(source)}}
        copies.append((source, filename))
    for source in (wheel, indexer):
        if source.is_symlink() or not source.is_file():
            raise ValueError("Current source artifacts must be regular files")
    with zipfile.ZipFile(wheel) as archive:
        entries = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
        if len(entries) != 1:
            raise ValueError("Expected a single wheel distribution")
        metadata = BytesParser().parsebytes(archive.read(entries[0]))
        if metadata["Name"].replace("_", "-") != "crucible-agentd" or not wheel.name.endswith("-py3-none-any.whl"):
            raise ValueError("Expected the current pure-Python Crucible backend wheel")
        wheel_version = metadata["Version"]
    header = indexer.read_bytes()[:8]
    if header != bytes.fromhex("cffaedfe0c000001"):
        raise ValueError("Expected an Apple Silicon Mach-O indexer")
    for name, source, filename, target, component_version in (
        ("agentd", wheel, wheel.name, "any", wheel_version),
        ("indexer", indexer, "crucible-indexer-darwin-arm64", "darwin-arm64", version),
    ):
        components[name] = {"version": component_version, "urls": {target: f"{REPO}v{version}/{filename}"}, "sha256": {target: digest(source)}}
        copies.append((source, filename))
    lsps = manifest["components"]["lsps"]
    if not lsps.get("npmPackages") or any(not re.fullmatch(r"(?:@[a-z0-9-]+/)?[a-z0-9-]+@\d+\.\d+\.\d+(?:[+-][a-z0-9.-]+)?", p, re.I) for p in lsps["npmPackages"]):
        raise ValueError("Language servers require exact package pins")
    components["lsps"] = lsps
    result = {"manifestVersion": 1, "releaseTag": f"v{version}", "components": components}
    output.mkdir(mode=0o700)  # refuse existing output, never overwrite staged assets
    try:
        for source, filename in copies:
            shutil.copyfile(source, output / filename)
        (output / "runtime-manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    except BaseException:
        shutil.rmtree(output)
        raise
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("vendor-manifest", "vendor-assets", "wheel", "indexer", "out"):
        parser.add_argument("--" + arg, type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    prepare(args.vendor_manifest, args.vendor_assets, args.wheel, args.indexer, args.version, args.out)
