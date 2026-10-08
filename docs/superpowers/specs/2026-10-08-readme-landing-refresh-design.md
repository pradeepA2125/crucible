# README and landing content refresh

Date: 2026-10-08
Status: Scope approved in chat; written specification awaiting review.

## Goal

Bring the README and landing page up to date with the capabilities added since their substantial 2026-08-13 refresh. Integrate those capabilities into the product story rather than append a commit log. Publish content now; capture new screenshots in a separate pass.

## Approach and boundaries

Use an integrated refresh: preserve the existing visual style, shadow-workspace explanation, and existing media, while giving agents and teams a dedicated section. A changelog-only update would leave the main story stale; a full redesign is unnecessary.

No backend behavior changes, new dependencies, screenshot capture, fabricated product screenshots, missing-image references, or visible coming-soon placeholders are part of this work. Low-level fixes support concise reliability copy, not a large marketing changelog.

## README

- Add an Agents and teams section and a navigation link. Distinguish background delegation for independent work from coordinated teams for shared planning, implementation, and review.
- Cover retained-context agent follow-ups, live transcripts/status, stopping work, custom agent definitions, and Settings > Agents.
- Describe teams with a single lead proposer, member feedback, optional user plan approval, assignments/file ownership, budgets, pause/resume, review, and reopening finished teams for follow-up work. Do not describe the reverted voting experiment as current behavior.
- Add rewind and usage visibility to the workflow explanation. Explain that rewind restores captured files and conversation/controller state, with limitations and failures surfaced; it is not a universal undo for arbitrary shell or external-system side effects.
- Add ChatGPT-plan sign-in to Providers, distinguishing it from API-key access. Mention account model selection and session/access handling without promising universal model or plan availability.
- Add concise local-backend authentication and concurrent-edit safeguards. Avoid blanket claims that authentication makes all operations safe or that stale-write guards eliminate every conflict.
- Reconcile existing shadow/edit-approval language with the actual review preference rather than repeat unconditional acceptance guarantees. Preserve the shadow-workspace story with accurate boundaries.
- Update stale release wording to a latest-release link/badge rather than claiming a release build has completed. Do not fabricate current passing-test counts; date or remove stale current-tense totals as appropriate.
- Preserve existing valid images and sections for code intelligence, memory, local models, skills, and MCP.

## Landing page

Retain the existing typography, colors, responsive conventions, and page sections. Add one focused, text-first agents/teams section near the workflow/features area. Keep independent background agents and lead-led teams distinct, with concise copy describing their different purposes and user controls.

Refresh feature copy to surface rewind, ChatGPT-plan access, usage visibility, and custom-agent configuration. Mention authentication/concurrent-edit safeguards briefly in the trust story. Update Recently Forged with the significant August–October additions, without listing every implementation commit. Update navigation only where needed to make the new section reachable. Avoid new application state, network requests, or backend dependencies: the new content remains static React presentation composed by the existing landing app.

Keep the page complete without new screenshots. Existing assets remain; new screenshot markup is added only in the future capture pass once the assets exist.

## Deferred capture checklist

Extend docs/media/README.md, clearly separating existing captured assets from planned captures. Each planned entry must specify filename, populated UI state, framing, sensitive data to redact, proposed caption, and intended placement in both the README and landing page.

| Planned asset | Required subject | Intended placement |
| --- | --- | --- |
| team-board.png | A real team with its lead, proposal/member feedback, phase and meaningful activity; include usage if legible | README teams subsection; landing agents/teams section |
| background-agents.png | Multiple agents with useful labels and differing live/completed states; show transcript access and usage where legible | README background delegation subsection; landing agents/teams section |
| rewind.png | Rewind confirmation with real affected message/file counts and warnings; do not confirm a destructive action just for a capture | README workflow/rewind; landing rewind feature |
| chatgpt-plan.png | Connected ChatGPT-plan state and usable model selection, with account details redacted | README Providers; landing provider feature |
| agents-settings.png | Settings > Agents populated with meaningful custom agents and configuration/trust controls | README custom agents; landing custom-agent feature |

Use real populated product states, readable dark-theme captures, and the existing capture guide's sizing guidance. Redact credentials, email/account identifiers, home paths, private repositories, and sensitive transcript content. Usage should be included in an agent/team image rather than require another image by default. These filenames are checklist targets, not image links to add now.

## Verification and acceptance

1. Read current source behind new or corrected behavioral claims during implementation; retain caveats where support or verification is limited.
2. README navigation anchors resolve and all local linked media/files exist. No new references to uncaptured assets are rendered.
3. Landing lint and production build pass using its actual package scripts and workspace environment.
4. Inspect desktop and mobile rendering for readable new copy, no horizontal overflow, coherent section hierarchy, and usable anchors. Preserve the existing theme behavior.
5. Check consistency between README and landing terminology: background agents, lead-led teams, rewind, ChatGPT plan, usage, and security controls.
6. Report the checks actually run; do not turn historical test totals or source review into claims of fresh full-stack verification.

## Implementation boundaries

Expected work is confined to README.md, landing presentation/content and associated navigation where required, docs/media/README.md, and this design/implementation documentation. No runtime/provider/team behavior changes are needed. Existing components must be read before editing; avoid unrelated refactoring.
