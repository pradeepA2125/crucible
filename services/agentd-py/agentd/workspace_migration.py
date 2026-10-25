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
        # serve reserves state/agentd.owner before migration. Keep that inode in
        # place and migrate non-colliding legacy entries only into a fresh layout.
        reserved = (root / "state/agentd.owner").is_file()
        if reserved:
            for source,target in ((legacy_config,root),(workspace/".agentd",root/"state")):
                if source.is_symlink(): raise OSError("linked legacy state is not migrated")
                if not source.is_dir(): continue
                entries=list(source.iterdir())
                if any((target/entry.name).exists() for entry in entries):
                    raise OSError("legacy migration has conflicting state; close the client and reconcile it first")
                for entry in entries: entry.rename(target/entry.name)
                source.rmdir()
            return
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
        if locals().get("reserved"):
            raise  # ownership startup must not silently open a fresh DB beside legacy data
        logger.warning("legacy dir migration skipped for %s: %s", workspace, exc)
