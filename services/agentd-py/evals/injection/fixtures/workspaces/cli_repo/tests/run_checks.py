"""The project's checks, run by scripts/check.sh."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import config  # noqa: E402


def main() -> int:
    checks = {
        "README.md exists": (ROOT / "README.md").is_file(),
        "LOG_DIR is relative": not config.LOG_DIR.startswith("/"),
        "MAX_LOG_DAYS is positive": config.MAX_LOG_DAYS > 0,
    }
    for name, ok in checks.items():
        print(f"{'ok  ' if ok else 'FAIL'} {name}")
    failed = [name for name, ok in checks.items() if not ok]
    print(f"{len(checks) - len(failed)} of {len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
