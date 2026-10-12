"""Main-agent byte identity (spec §4.7.4). Only Plan 1A Task 11 may re-capture."""
import json

from tests.prompt_goldens import GOLDEN_PATH, collect


# Explicit image-branch addition to the legacy fixture, not derived from the registry.
_IMAGE_TOOL = {
    "name": "read_image",
    "description": (
        "Read an image file or durable image asset from this workspace. "
        "Returns metadata and a native image attachment, never binary text. "
        "Provide exactly one of path or asset_id. Static PNG, JPEG, GIF, "
        "WebP, BMP and TIFF are supported."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative image path within the workspace"},
            "asset_id": {"type": "string", "description": "Durable image asset ID in this workspace"},
        },
        "oneOf": [{"required": ["path"]}, {"required": ["asset_id"]}],
    },
}


def _with_image_tool(catalog: list[dict]) -> list[dict]:
    if any(tool.get("name") == "read_image" for tool in catalog):
        return catalog
    index = next(i for i, tool in enumerate(catalog) if tool["name"] == "read_file")
    return catalog[:index + 1] + [_IMAGE_TOOL] + catalog[index + 1:]


def _upgrade_legacy_image_fixture(golden: dict[str, str]) -> dict[str, str]:
    """Only insert the frozen image definition; retain all unrelated fixture bytes."""
    upgraded = dict(golden)
    for key, value in golden.items():
        if key.startswith("system/"):
            start = value.index("Available tools:\n") + len("Available tools:\n")
            catalog, length = json.JSONDecoder().raw_decode(value[start:])
            # Reject unexpected legacy formatting instead of silently normalizing it.
            assert json.dumps(catalog, indent=2, sort_keys=True) == value[start:start + length]
            replacement = json.dumps(_with_image_tool(catalog), indent=2, sort_keys=True)
            upgraded[key] = value[:start] + replacement + value[start + length:]
        elif key in {"tools/builtin/explore", "tools/builtin/verify"}:
            catalog = json.loads(value)
            assert json.dumps(catalog, indent=1, sort_keys=True) == value
            upgraded[key] = json.dumps(_with_image_tool(catalog), indent=1, sort_keys=True)
    return upgraded


def test_main_agent_text_matches_goldens() -> None:
    golden = _upgrade_legacy_image_fixture(json.loads(GOLDEN_PATH.read_text(encoding="utf-8")))
    live = collect()
    assert sorted(live) == sorted(golden), "golden keys changed — re-capture is Task 11 only"
    mismatched = [key for key in golden if live[key] != golden[key]]
    assert not mismatched, f"main-agent text changed for: {mismatched}"
