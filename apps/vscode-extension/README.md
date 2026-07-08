# crucible-vscode-extension

VS Code MVP extension for Crucible task review loop.

## Commands
- `Crucible: Start Task`
- `Crucible: Open Review Panel`
- `Crucible: Accept Patch`
- `Crucible: Reject Patch`
- `Crucible: Refresh Task`

## Settings
- `crucible.backendBaseUrl` (default `http://127.0.0.1:8000`)
- `crucible.defaultMode` (default `project_edit`)
- `crucible.pollIntervalMs` (default `1000`)

## Notes
- The extension attaches to an already-running `agentd-py` service.
- It does not start or manage backend processes.
