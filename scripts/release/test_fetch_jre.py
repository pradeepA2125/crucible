import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fetch_jre import jre_asset_name, jre_download_url  # noqa: E402


def test_asset_names_match_real_adoptium_naming() -> None:
    # Exact filenames verified live against the Adoptium API
    # (api.adoptium.net/v3/assets/latest/21/hotspot) before writing this.
    assert jre_asset_name("21", "21.0.11_10", "darwin-arm64") == (
        "OpenJDK21U-jre_aarch64_mac_hotspot_21.0.11_10.tar.gz")
    assert jre_asset_name("21", "21.0.11_10", "darwin-x64") == (
        "OpenJDK21U-jre_x64_mac_hotspot_21.0.11_10.tar.gz")
    assert jre_asset_name("21", "21.0.11_10", "linux-x64") == (
        "OpenJDK21U-jre_x64_linux_hotspot_21.0.11_10.tar.gz")
    assert jre_asset_name("21", "21.0.11_10", "win32-x64") == (
        "OpenJDK21U-jre_x64_windows_hotspot_21.0.11_10.zip")


def test_download_url_construction_and_plus_encoding() -> None:
    url = jre_download_url("21", "jdk-21.0.11+10", "21.0.11_10", "darwin-arm64")
    assert url == (
        "https://github.com/adoptium/temurin21-binaries/releases/download/"
        "jdk-21.0.11%2B10/OpenJDK21U-jre_aarch64_mac_hotspot_21.0.11_10.tar.gz")


def test_windows_gets_zip_posix_gets_tar_gz() -> None:
    win = jre_download_url("21", "jdk-21.0.11+10", "21.0.11_10", "win32-x64")
    linux = jre_download_url("21", "jdk-21.0.11+10", "21.0.11_10", "linux-x64")
    assert win.endswith(".zip")
    assert linux.endswith(".tar.gz")
