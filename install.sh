#!/usr/bin/env bash
# One-shot installer for the Crucible VS Code extension, straight from GitHub
# Releases — no Marketplace/Open VSX publish required.
#
#   curl -fsSL https://raw.githubusercontent.com/pradeepA2125/crucible/main/install.sh | bash
#
# Downloads the .vsix attached to the latest GitHub Release and installs it
# via `code --install-extension`. Override CRUCIBLE_INSTALL_REPO or
# CRUCIBLE_INSTALL_CODE_BIN for a fork or a non-default editor binary
# (code-insiders, cursor, ...).
set -euo pipefail

REPO="${CRUCIBLE_INSTALL_REPO:-pradeepA2125/crucible}"
API_URL="https://api.github.com/repos/${REPO}/releases/latest"

log() { printf '==> %s\n' "$1"; }
die() { printf 'error: %s\n' "$1" >&2; exit 1; }

find_code_bin() {
  if [ -n "${CRUCIBLE_INSTALL_CODE_BIN:-}" ]; then
    command -v "$CRUCIBLE_INSTALL_CODE_BIN" 2>/dev/null && return
    die "CRUCIBLE_INSTALL_CODE_BIN='$CRUCIBLE_INSTALL_CODE_BIN' not found on PATH"
  fi
  for bin in code code-insiders cursor; do
    if command -v "$bin" >/dev/null 2>&1; then
      command -v "$bin"
      return
    fi
  done
  die "no VS Code CLI found on PATH (looked for: code, code-insiders, cursor). Install the 'code' shell command first (VS Code: Cmd/Ctrl+Shift+P -> 'Shell Command: Install code command in PATH'), then re-run this script."
}

CODE_BIN="$(find_code_bin)"
log "using editor CLI: $CODE_BIN"

command -v curl >/dev/null 2>&1 || die "curl is required"

log "looking up latest release of $REPO"
RELEASE_JSON="$(curl -fsSL "$API_URL")" || die "failed to query $API_URL (repo may have no releases yet)"

VSIX_URL="$(printf '%s' "$RELEASE_JSON" | grep -o '"browser_download_url": *"[^"]*\.vsix"' | head -n1 | sed -E 's/.*"(https[^"]+)"/\1/')"
[ -n "$VSIX_URL" ] || die "latest release has no .vsix asset attached"

TAG="$(printf '%s' "$RELEASE_JSON" | grep -o '"tag_name": *"[^"]*"' | head -n1 | sed -E 's/.*"([^"]+)"$/\1/')"
log "found $TAG: $(basename "$VSIX_URL")"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
VSIX_PATH="$WORKDIR/$(basename "$VSIX_URL")"

log "downloading vsix"
curl -fsSL "$VSIX_URL" -o "$VSIX_PATH" || die "download failed"

log "installing into $CODE_BIN"
"$CODE_BIN" --install-extension "$VSIX_PATH" --force

log "done. Open (or reload) VS Code, open a folder, and the Crucible setup wizard will guide you through the rest."
