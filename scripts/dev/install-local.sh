#!/usr/bin/env bash
# Build this checkout and install it as the REAL extension, wired to run from source.
#
# Why: the dev-host flow (--extensionDevelopmentPath + start-backend.sh) exercises the
# code but NOT the path a user actually gets — managed runtime, packaged webview,
# installed VSIX. This installs the real thing while still pointing the backend at
# your working tree, so the shipped path can be dogfooded against local changes.
#
#   frontend  built + packaged + `code --install-extension`  (real VSIX, not dev host)
#   backend   crucible.devSourcePath -> `uv pip install -e services/agentd-py`
#             in ~/.crucible/runtime/venv (restart the backend to pick up edits)
#   indexer   built from services/indexer-rs and installed to ~/.crucible/runtime/bin
#
# Usage:  scripts/dev/install-local.sh [--no-backend] [--code-cmd code]
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EXT_DIR="$REPO/apps/vscode-extension"
CODE_CMD="code"
DO_BACKEND=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-backend) DO_BACKEND=0; shift ;;
    --code-cmd)   CODE_CMD="${2:?missing value for --code-cmd}"; shift 2 ;;
    -h|--help)    sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

command -v "$CODE_CMD" >/dev/null 2>&1 || {
  echo "'$CODE_CMD' not on PATH. In VS Code: Cmd+Shift+P -> 'Shell Command: Install \"code\" command in PATH'." >&2
  exit 1
}

echo "==> building (editor-client -> extension -> webview)"
# Build order matters: the extension types off editor-client's compiled dist,
# not its source, so a stale dist yields phantom type errors.
( cd "$REPO" && npm run build )

echo "==> pinning the bundled manifest to the INSTALLED runtime"
# resources/runtime-manifest.json is checked in as a placeholder: releaseTag
# "dev-unpinned", agentd/indexer at 0.0.0, and NO urls or sha256 for any component —
# the release workflow generates the real one. Packaging it as-is makes the extension
# compare "dev-unpinned" against the installed runtime.json tag, mismatch, and prompt
# "run setup" on every launch. Running that setup would then try to download from a
# manifest with no artifact URLs and could damage a working runtime.
#
# So: stamp the placeholder with whatever runtime is already installed, purely to keep
# the version check quiet. This never installs anything — a local build has no
# artifacts to offer — it just stops the extension nagging about an upgrade it cannot
# perform. Restored after packaging so the checkout stays clean.
MANIFEST="$EXT_DIR/resources/runtime-manifest.json"
INSTALLED="$HOME/.crucible/runtime/runtime.json"
RESTORE=0
if [[ -f "$INSTALLED" ]]; then
  cp "$MANIFEST" "$MANIFEST.bak"; RESTORE=1
  python3 - "$MANIFEST" "$INSTALLED" <<'PY'
import json, sys
mpath, ipath = sys.argv[1], sys.argv[2]
m = json.load(open(mpath)); inst = json.load(open(ipath))
m["releaseTag"] = inst.get("releaseTag", m.get("releaseTag"))
for cid, ver in (inst.get("components") or {}).items():
    if cid in m.get("components", {}):
        m["components"][cid]["version"] = ver
json.dump(m, open(mpath, "w"), indent=2)
print(f"    pinned to {m['releaseTag']}")
PY
  trap '[[ "$RESTORE" == "1" ]] && mv -f "$MANIFEST.bak" "$MANIFEST"' EXIT
else
  echo "    no installed runtime — leaving the placeholder (setup will be offered)"
fi

echo "==> packaging VSIX"
cd "$EXT_DIR"
rm -f ./*.vsix
# --no-dependencies: this is an npm workspace, and vsce otherwise tries to resolve
# the workspace protocol and fails. .vscodeignore already excludes src/ and tests.
npx --yes @vscode/vsce package --no-dependencies --out crucible-local.vsix >/dev/null
VSIX="$EXT_DIR/crucible-local.vsix"
[[ -f "$VSIX" ]] || { echo "vsce produced no VSIX" >&2; exit 1; }
echo "    $(du -h "$VSIX" | cut -f1)  $VSIX"

echo "==> installing extension"
"$CODE_CMD" --install-extension "$VSIX" --force

if [[ "$DO_BACKEND" == "1" ]]; then
  echo
  echo "==> backend: point the managed runtime at this checkout"
  echo "    Set this in VS Code settings (Cmd+,) so agentd installs EDITABLE:"
  echo
  echo "        \"crucible.devSourcePath\": \"$REPO\""
  echo
  # Deliberately NOT written for you: it lives in user settings, and silently editing
  # a user's settings.json is the kind of surprise this repo avoids elsewhere.
  VENV="$HOME/.crucible/runtime/venv"
  if [[ -d "$VENV" ]]; then
    echo "    An existing runtime venv is present:"
    echo "        $VENV"
    echo "    The installer skips agentd when it is already present, so after setting"
    echo "    devSourcePath, force a reinstall:"
    echo
    echo "        rm -rf \"$VENV\" && <reload window, then Crucible: Run Setup>"
  fi
fi

echo "==> building the indexer"
# shellcheck source=../stress/_indexer.sh
source "$REPO/scripts/stress/_indexer.sh"
if _indexer_bin="$(ensure_indexer_binary "$REPO/services/indexer-rs")"; then
  _dest="$HOME/.crucible/runtime/bin/crucible-indexer"
  mkdir -p "$(dirname "$_dest")"
  # Temp file + mv: overwriting a running binary in place gets it killed on macOS arm64
  # and fails with ETXTBSY on Linux.
  cp "$_indexer_bin" "$_dest.tmp.$$"
  chmod 755 "$_dest.tmp.$$"
  mv -f "$_dest.tmp.$$" "$_dest"
  echo "    installed $_dest; restart the backend (Crucible: Restart Backend) so the watcher picks it up"
else
  echo "    indexer build failed (see above); the managed runtime keeps its current indexer" >&2
fi

echo
echo "==> done. Reload VS Code (Cmd+Shift+P -> Developer: Reload Window)."
echo "    Verify: the extension is 'Crucible' in the Extensions list (not a dev host),"
echo "    and ~/.crucible/runtime/venv has an editable agentd:"
echo "        \"\$HOME/.crucible/runtime/venv/bin/python\" -c \\"
echo "          'import agentd,pathlib;print(pathlib.Path(agentd.__file__).parent)'"
echo "    That should print this checkout's services/agentd-py/agentd, not site-packages."
