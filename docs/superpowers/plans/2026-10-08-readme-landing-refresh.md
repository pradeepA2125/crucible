# README and Landing Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refresh Crucible's README and landing page to reflect approved recent capabilities, while preserving the shadow-workspace story and deferring new screenshots to a detailed capture checklist.

**Architecture:** Keep the README as the detailed product and trust reference, the existing React landing page as concise static marketing content, and `docs/media/README.md` as the capture playbook. Make no backend or application behavior changes; add no references to screenshot assets until real captures exist.

**Tech Stack:** Markdown; React 19, TypeScript, Vite, Tailwind CSS; landing npm scripts `lint` (`oxlint`) and `build` (`tsc -b && vite build`).

## Global Constraints

- “No backend behavior changes, new dependencies, screenshot capture, fabricated product screenshots, missing-image references, or visible coming-soon placeholders are part of this work.”
- “Preserve the existing visual style, shadow-workspace explanation, and existing media.”
- “Do not describe the reverted voting experiment as current behavior.”
- “Do not fabricate current passing-test counts; date or remove stale current-tense totals as appropriate.”
- “Keep the page complete without new screenshots. Existing assets remain; new screenshot markup is added only in the future capture pass once the assets exist.”
- “Use real populated product states, readable dark-theme captures, and the existing capture guide's sizing guidance.”

---

## File map

- `README.md` — navigation, core workflow/trust story, providers, agents/teams, rewind, security, release/test claims.
- `landing/src/App.tsx` — compose one new static agents/teams section in the existing page flow.
- `landing/src/components/Nav.tsx` — add a reachable section anchor to the existing desktop navigation links.
- `landing/src/components/Features.tsx` — concise cards for rewind, ChatGPT-plan access, usage and custom agents, keeping current design conventions.
- `landing/src/components/RecentlyForged.tsx` — replace dated entries that no longer represent the most significant recent additions; preserve the existing component and style.
- `landing/src/components/AgentsTeams.tsx` — new focused text-first landing section, only if following existing component-per-section pattern confirmed during implementation.
- `docs/media/README.md` — add future capture checklist, with every requested state, framing, redactions, caption and both-page placement; do not turn checklist filenames into README image references.

No backend or new landing dependencies are in scope. Add no tests for static copy; verify links and media, lint/build, and rendered desktop/mobile behavior.

## Task 1: Refresh README product story and accuracy

**Files:** `README.md`

**Interfaces:** No code interfaces. Maintain current section anchors and existing media paths.

- [ ] Read the current implementation for behavioral claims before writing them: `services/agentd-py/agentd/chat/rewind.py`, `services/agentd-py/agentd/subagents/tool_source.py`, `services/agentd-py/agentd/teams/tools.py`, `services/agentd-py/agentd/chatgpt_auth/service.py`, and `services/agentd-py/agentd/auth.py`. Use only behavior confirmed there and keep limitations explicit.
- [ ] Update release badge and install details to point to the latest-release page without hard-coding stale `v0.5.4` claims. Remove or date the stale test badge and the unsupported “2,430 tests pass” present-tense figure; retain explanatory testing strategy.
- [ ] Add an Agents and teams entry to top navigation and a section after “How it works.” Explain independent background delegation separately from lead-led coordinated teams; cover retained-context follow-ups, visible status/transcript, stop controls, custom agent definitions/settings, assignments, member feedback, optional user approval, budgets, pause/resume, review, and finished-team follow-up. Do not say the team votes.
- [ ] Extend workflow copy with rewind limitations (captured file and conversation/controller state, not arbitrary external side effects) and usage visibility. Correct absolute guarantees about accept/reject only after reading current promotion/review behavior.
- [ ] Add ChatGPT-plan authentication as a distinct provider route from API keys. Explain availability caveats and account model catalog/sign-out only as confirmed by source.
- [ ] Add conservative local backend authentication and concurrent-edit safeguard copy, with no assertion that all operations or conflicts are eliminated.
- [ ] Preserve existing screenshots and memory/code intelligence/extension sections. Do not cite assets not present in the repository.
- [ ] Check all README anchors and referenced local media paths with a script or shell check; expected result: no broken anchor or local-file references.

## Task 2: Add the focused landing agents/teams section

**Files:** `landing/src/App.tsx`, `landing/src/components/Nav.tsx`, and (if consistent with current section components) create `landing/src/components/AgentsTeams.tsx`.

**Interfaces:** The section is a static default-export React component with a stable `id` (use `agents`) that `App.tsx` composes; nav links to `#agents`. No props, state, network calls, or new package dependencies.

- [ ] Follow `ShadowSection.tsx` / `Lifecycle.tsx` / `Features.tsx` conventions read during implementation: use shared `SectionHeading`, `Reveal`, `Em`, existing color/tailwind tokens and a responsive text-first layout.
- [ ] Add a new section that differentiates background agents (independent delegated tasks, retained follow-up context/status/stop) from teams (lead proposer, member feedback and assignments, optional user approval, pause/resume/review), mentioning usage visibility without inventing values or voting.
- [ ] Import and compose the section after `Features` and before `RecentlyForged` in `landing/src/App.tsx`; add the `#agents` link in `Nav.tsx`.
- [ ] Run `npm run lint` and `npm run build` from `landing/`. Expected: oxlint reports no issues and TypeScript/Vite build exits successfully.

## Task 3: Refresh landing feature cards and recent items

**Files:** `landing/src/components/Features.tsx`, `landing/src/components/RecentlyForged.tsx`.

**Interfaces:** Retain the existing `Feature` and `Entry` data shapes and presentational component exports.

- [ ] Update feature-card copy to surface rewind, ChatGPT-plan access, usage visibility and custom-agent configuration. Correct any absolute “nothing ever leaves your machine” or unconditional side-effect approval language if contradicted by verified behavior. Keep claims short and link the deeper story to existing sections where possible.
- [ ] Replace the July/August 2026 entries with significant August–October releases: rewind, background agents/custom agents, lead-led teams, ChatGPT-plan sign-in, usage/security/concurrent edit safeguards. Keep other still-current entries only where they remain useful; no commit-by-commit list.
- [ ] Preserve the existing layout, animation, typography, responsive classes, and working Axon feature block unless revised copy makes its “just merged” wording stale; align that label with reality without adding fake dates.
- [ ] Run `npm run lint` and `npm run build` from `landing/`; expected exit 0.

## Task 4: Extend the deferred capture playbook

**Files:** `docs/media/README.md`.

**Interfaces:** Documentation only. Capture filenames are planning targets, not files or image references in this change.

- [ ] Add a clearly labeled “Planned captures (not yet captured)” section without changing the existing status of current assets.
- [ ] For each target `team-board.png`, `background-agents.png`, `rewind.png`, `chatgpt-plan.png`, and `agents-settings.png`, specify populated state, framing, redactions, proposed caption and exact intended README and landing section placement.
- [ ] Include usage in one agent or team shot where it is legible rather than adding a separate image by default. Require dark theme and existing 1280 px target; redact credentials, email/account identifiers, home paths, private repositories and sensitive transcripts. A rewind capture must show real counts/warnings without confirming a destructive action for the screenshot.
- [ ] State that weak captures should be omitted and no page should link to these planned files before capture. Review existing asset status and guidance for contradictions.

## Task 5: Integrated verification and visual review

**Files:** All files changed in Tasks 1–4; no further file changes unless a specific acceptance issue is discovered.

- [ ] Run `npm run lint` from `landing/`; expected: no lint findings and exit 0.
- [ ] Run `npm run build` from `landing/`; expected: `tsc -b` and Vite production build exit 0.
- [ ] Verify README heading anchors and local media links, and verify planned screenshot names occur only in the capture guide (no broken references from either page).
- [ ] Run the landing app using its documented npm `dev` script and inspect desktop and mobile layouts: agents navigation reaches the section, no horizontal overflow, readable copy, coherent hierarchy and existing theme behavior. Stop any dev server started for the check.
- [ ] Compare README and landing terminology against the approved spec; report exactly which checks ran and do not claim full-stack tests or current test counts.

## Spec coverage self-check

- README product story, providers, trust and stale release/test claims: Task 1.
- Landing static agents/teams section, nav, features and recent additions: Tasks 2–3.
- Deferred captures and no missing image references: Task 4 and Task 5.
- Build/lint, links/media, desktop/mobile and truthful verification: Task 5.
- No runtime changes, new dependencies, screenshots, fabricated states or voting claims: global constraints and all tasks.
