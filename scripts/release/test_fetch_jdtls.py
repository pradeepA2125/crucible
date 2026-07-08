import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fetch_jdtls import jdtls_download_url  # noqa: E402


def test_download_url_matches_real_eclipse_naming() -> None:
    # Verified live: this exact URL pattern (via curl -sL, following the
    # download.php mirror redirect) served a real 50MB jdt-language-server
    # tar.gz before this script was written.
    assert jdtls_download_url("1.61.0-202607070104") == (
        "https://www.eclipse.org/downloads/download.php?"
        "file=/jdtls/snapshots/jdt-language-server-1.61.0-202607070104.tar.gz")
