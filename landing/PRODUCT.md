# Crucible

<!-- impeccable:product-schema 1 -->

## Platform

web

This record covers Crucible's product landing website. The product itself is a VS Code coding assistant.

## Users

Developers evaluating Crucible for work in their repositories. Additional audience segments have not been established.

## Product Purpose

Present and celebrate Crucible's real product features. The owner confirmed on 2026-10-15: "it has to basically brag about product features. its a product landing page."

The website explains the assistant and provides installation and source links. It is separate from the extension's working interface.

## Positioning

Crucible combines repository exploration, checked edits with shadow patch staging, agents and teams, model/provider choice, memory and context management, and explicit approval controls inside VS Code. Describe concrete capabilities and their benefits; no comparative superiority, customer adoption or performance benchmark has been established.

## Capabilities and Constraints

The repository README is the current audited starting point for feature claims; verify important claims against implementation when making them prominent.

- Repository exploration includes text/semantic search and a Rust symbol graph; Axon offers a 3D dependency view.
- Editing includes structured patches, shadow staging and stale-write checks. Shadow staging is not a command sandbox.
- Agents and teams support delegated work, reports and shared team controls within configured limits.
- Provider choices include cloud and local paths, ChatGPT-plan sign-in and experimental Claude-subscription support. Availability depends on account/provider access.
- Memory includes compaction and an inspector. Scoped durable recall and the curator are experimental; candidate offers do not prove that memories were read or used.
- Skills, project instructions, prompt files and MCP extend the assistant.
- Approval controls let the user choose how much authority to grant. Neither local inference nor approval controls imply complete offline operation or full process isolation.
- Rewind restores captured editing checkpoints; it does not undo arbitrary external side effects.

Existing React/Vite code and dependencies establish the website stack. Preserve functional installation, source and documentation links. Published release contents may differ from the checkout; do not invent a new release or promote experimental defaults as universally enabled.

## Brand Commitments

The product name is Crucible. Existing logo assets are available in `public/crucible.svg` and `../docs/media/`. The approved direction uses the dependency-space hero, illustrated specialists and team room, rewind story, and actual Axon renderer demonstration. The owner requested its promotion to the public landing page on 2026-10-16. Preserve that composition and motion; the rejected procedural ring design is not an approved brand asset.

## Evidence on Hand

- `../README.md`: current feature and installation descriptions.
- `../docs/media/`: actual product captures, logo assets and Axon recording. The README identifies earlier captures as historical; do not present them as verified screenshots of the current interface.
- `../apps/vscode-extension/` and `../services/`: implementation evidence.
- `src/experience/`: canonical approved landing page and its locally served fonts, graph capture, poster and recording.
- `.impeccable/prototype/`: compatibility entry for the same production page, plus historical design notes. Do not maintain a separate design here.

No verified testimonials, customer logos, speed multipliers, user counts or comparative benchmark results have been supplied for this website.

## Product Principles

- Make product capabilities and their practical value the subject of the page.
- Show concrete evidence and keep marketing claims accurate.
- Give visitors a clear path to installation and further information.
- Use expressive media and motion in service of the product story.
