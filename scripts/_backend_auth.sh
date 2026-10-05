# shellcheck shell=bash
# Source me: crucible_auth_header <base_url>, crucible_backend_url <base_url> (spec §3.5).
# Thin wrappers so the token-verification logic lives once, in _backend_auth.py.
_CRUCIBLE_AUTH_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_backend_auth.py"

crucible_auth_header() {
  python3 "$_CRUCIBLE_AUTH_PY" header "$1"
}

crucible_backend_url() {
  python3 "$_CRUCIBLE_AUTH_PY" url "$1"
}
