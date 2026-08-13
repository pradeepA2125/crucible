# Capture spec for README media

**Status:** every asset below is captured and live in the README.

Two deviations from the original spec, both deliberate:

- `chat.png` stands in for `chat.gif` — a still of the loop (tool pills → applied
  diff) rather than a recording. A GIF can replace it without touching the README
  markup beyond the filename.
- `axon.gif` opens on an already-focused file rather than on the wide overview the
  spec below asks for, and the search field visibly carries a stale query. It is a
  real 10s capture of the panel orbiting this repo's own 76k-node graph, but the
  opening beat is worth re-shooting when the window is free. Capture rig:
  `Page.startScreencast` over CDP → per-frame PNG → `gifenc`; **the window must be
  frontmost or the compositor throttles and the screencast delivers one frame for
  the whole take.**

The README references these files by exact name. Drop them in and the sections
render; nothing else needs editing.

**The failure mode to design against is an empty panel.** A screenshot of the
Memory inspector with no memories in it, or an MCP list with no servers
connected, is worse than no screenshot — it makes a working feature look
unfinished. Each entry below therefore specifies the *state to set up first*,
not just the thing to point the camera at.

Target width **1280px** (2560 @2x on a Retina display). GIFs under ~8 MB so
GitHub renders them inline rather than making people click through. Use a dark
theme — the whole UI is designed around the dark token palette and reads flat in
light.

---

## `axon.gif` — the headline asset

**What it is:** the 3D dependency-space visualizer, which is the most visually
distinctive thing in the project and belongs at the top of the README.

**Set up first:** index a repository with enough real structure to look like a
system rather than a toy — this repo itself works well (~4k+ nodes). A graph
with twelve nodes looks like a diagram, not a codebase.

**Record:** 6–10 seconds. Open on the full graph, orbit slowly, then focus a
single symbol so its edges highlight against the dimmed rest. End on the focused
state rather than mid-rotation — the last frame is what people see while the GIF
loops back.

**Avoid:** fast spinning (reads as a screensaver), and starting zoomed-in
(nobody knows what they're looking at yet).

---

## `chat.gif` — the core loop

**What it is:** the thing the tool actually does.

**Set up first:** a real repo, a real request that touches 2–3 files. Not
"write hello world."

**Record:** 10–15 seconds showing the loop that makes this different from a
completion box — the agent calling a tool, the tool pills appearing, a diff card
arriving, and *you accepting it*. The accept is the moment worth capturing,
because the shadow-workspace model is the product claim.

---

## `gates.png` — approval, the trust story

**Set up first:** trigger a `run_command` gate on something with obvious
consequence (a test run, not `ls`).

**Capture:** the command card with `Accept once` / `Accept and remember` /
`Reject` visible. This is the screenshot that answers "will it wreck my repo."

---

## `memory.png` — and why it's two panels, not one

**Set up first:** run several turns across more than one session so there is
real recall to show. Then open the Memory inspector.

**Capture two shots:**
- `memory-browser.png` — the browser tab, showing memories of more than one
  kind (semantic / procedural / episodic) so the type distinction is visible,
  ideally including one superseded entry so the supersede chain has a reason to
  exist.
- `memory-recall.png` — the recall-trace tab, showing the per-signal score bars
  (semantic / lexical / structural / importance / recency) and which entries
  were actually injected. This is the one that proves recall is scored rather
  than vibes.

---

## `settings.png` — provider config

**Set up first:** a configured provider with a real model, and the context-window
field populated.

**Capture:** the Provider section. If the reasoning-effort control is in frame
with a rung selected, better — it shows the capability model.

---

## `skills.png` and `instructions.png`

**Set up first:** at least two skills in `.crucible/skills/` with meaningful
names, and a real `AGENTS.md` with actual project rules in it.

**Capture:** for skills, the `/`-autocomplete dropdown showing both prompt files
and skills with their badges — it demonstrates discovery, not just existence.
For instructions, the AGENTS.md editor with real content.

---

## `mcp.png`

**Set up first:** at least one MCP server actually *connected*, with a non-zero
tool count. A disconnected server proves nothing.

**Capture:** the MCP section of Settings with the connected state dot and tool
count, or an `mcp_tool` approval card mid-turn — the latter is stronger, since
it shows the gate rather than just configuration.

---

## Notes

- Redact anything identifying: API keys, absolute home paths, private repo names,
  window titles with client names.
- If a feature genuinely doesn't look good yet, leave it out. A README with four
  strong assets beats one with eight mediocre ones, and every screenshot is a
  promise about polish.
