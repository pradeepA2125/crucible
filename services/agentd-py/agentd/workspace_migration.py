"""One-time migration of legacy per-workspace dirs to the .crucible layout.

.ai-editor/ (user config: skills, prompts, mcp.json)  -> .crucible/
.agentd/    (runtime state: shadows, DBs, artifacts)  -> .crucible/state/

Best-effort by design: a failed migration must never prevent startup — the
backend then simply starts with fresh dirs and the legacy ones stay on disk.
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def migrate_legacy_dirs(workspace: Path) -> None:
    try:
        root = workspace / ".crucible"
        legacy_config = workspace / ".ai-editor"
        if legacy_config.is_dir() and not root.exists():
            legacy_config.rename(root)
            logger.info("migrated legacy config dir %s -> %s", legacy_config, root)

        state = root / "state"
        legacy_state = workspace / ".agentd"
        if legacy_state.is_dir() and not state.exists():
            root.mkdir(parents=True, exist_ok=True)
            legacy_state.rename(state)
            logger.info("migrated legacy state dir %s -> %s", legacy_state, state)
    except OSError as exc:
        logger.warning("legacy dir migration skipped for %s: %s", workspace, exc)
