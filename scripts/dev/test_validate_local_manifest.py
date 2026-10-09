import copy
import unittest

from validate_local_manifest import validate


class ManifestValidationTest(unittest.TestCase):
    def setUp(self):
        platforms = ("darwin-arm64", "darwin-x64", "linux-x64", "win32-x64")
        components = {}
        for name in ("uv", "indexer", "ripgrep", "rust-analyzer", "gopls", "jre", "agentd", "jdtls"):
            keys = ("any",) if name in ("agentd", "jdtls") else platforms
            components[name] = {
                "version": "1", "urls": {key: "https://example.com/artifact" for key in keys},
                "sha256": {key: "a" * 64 for key in keys},
            }
        components["lsps"] = {"npmPackages": ["pyright@1.1.400"]}
        self.manifest = {"manifestVersion": 1, "releaseTag": "v0.6.1", "components": components}

    def test_complete_manifest(self):
        validate(self.manifest)

    def test_version_only_pinning_is_rejected(self):
        self.manifest["components"]["uv"]["urls"] = {}
        with self.assertRaisesRegex(ValueError, "uv/darwin-arm64"):
            validate(self.manifest)

    def test_missing_or_invalid_checksums(self):
        for checksum in ("", "not-a-checksum"):
            manifest = copy.deepcopy(self.manifest)
            manifest["components"]["jdtls"]["sha256"]["any"] = checksum
            with self.assertRaisesRegex(ValueError, "jdtls/any"):
                validate(manifest)


if __name__ == "__main__":
    unittest.main()
