# Theming Phase 3: composer and authoritative approval modes

Date: 14 October 2026. Implements the approved revision-6 theming design, §3 and Phase 3 testing requirements.

Work in `codex/theming-phase3` / `.superpowers/wt-theme3`. Save local commits at coherent boundaries; do not push. Review the entire Phase 3 diff before fast-forwarding onto `feat/codex-style-chat-theming`.

- [x] Backend policy/version/ownership transition; eligible-edit resolution extracted from the legacy setter; legacy message writers respect ownership.
- [x] Config/live capability and effective shell readback; conditional process admission on all four work-starting routes; API conflict and validation coverage.
- [x] Editor-client approval schemas, mapper, method and structured admission errors; effort descriptor accepts unsupported entries.
- [x] Host coordinator: serialized selections and connect decisions, migration, confirmed state, persistence after acknowledgement, three capability tiers and readiness/restart recovery; four operations wait for readiness and retain drafts/cards on admission failure.
- [x] Webview mode dropdown, full-access warning with focus management and remembered confirmation; same confirmed state broadcast to all panels.
- [x] One model menu with the notched native effort slider, Default/null, supported/unknown filtering, clamp notes, pending/error handling and usage links.
- [x] Composer + menu, plan chip, compact context ring with context and thread/team totals, round Send/Stop, header gear; preserve availability, images, mentions, slash expansion and existing stop behavior. Quiet WorkBar.
- [x] Settings overlay focus entry/trap/Escape/return; Phase 3 type/radius/color/decoration lint and narrow real-browser checks.
- [ ] Scoped and full relevant suites, build/typecheck, separate whole-phase review and fix pass, local installed-app verification where available; record limits truthfully before integration.

The three approval policies are Ask for approval (review edits, ask commands), Accept edits (accept edits, ask commands), and Full access (accept edits, allow all commands). Auto is reserved. Plan remains a separate toggle. Existing protected/untrusted edit gates, MCP approval and sub-agent definitions retain their exceptions. A policy write never resolves a command gate.
