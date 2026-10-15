# Memory recall precision — design

> **Superseded** by `2026-10-14-memory-recall-redesign-design.md` after the Claude ↔ Codex dialog
> (`docs/superpowers/discussions/2026-10-14-memory-redesign-dialog.md`). Kept as the dialog's
> starting point.

Date: 2026-10-14. Scope: `services/agentd-py/agentd/memory/` (recall + consolidation), the
controller's recall call site, and a one-off cleanup script. Compaction is untouched.

## Problem

Cross-session recall injects irrelevant memories into the controller's payload on almost every
turn, and the `recall()` tool returns the same irrelevant results. Evidence from this
workspace's 2,187 recall traces (`.crucible/state/artifacts/chat/*/*/memory-recall-*.json`) and
its `memory.sqlite3`:

1. **Nothing can decide that no memory is relevant.** Every recall injects up to `k=8`.
   - Each signal is min-max normalized within the candidate set, so the closest candidate
     always scores `semantic=1.0`, however poor the match. "UI smoke test: reply with exactly
     CLAUDE_UI_OK" received 8 memories.
   - Importance (weight 0.3) and recency (weight 0.2) do not depend on the query and add up to
     ~0.5, against a 0.15 floor. 616 injected memories had `semantic=0` and `lexical=0`.
   - The cross-encoder only reorders. Of the reranked memories that were injected, 84% scored
     below 0.01 and 90% below 0.05.
2. **The query is the raw latest user message.** Follow-ups such as "ok do it" or "you can use
   chatgpt plan" carry no topic.
3. **What gets stored is largely session status.** Consolidation writes notes that are true only
   briefly: "Focused verification passed with 66 backend tests…", "branch created",
   "remains uncommitted", "the contract is partially added". Status shows up in `semantic` as
   well as `episodic`. Old episodic notes are never superseded by design, so they never retire.
   Live consolidation memories: 49 from compaction slices and 20 from `submit_changes` summaries
   (`controller.py` passes only the summary text to consolidation).

Above a threshold the cross-encoder matches topic rather than need. For example, "you can use
chatgpt plan" scored 0.95 against a README-refresh memory. A threshold alone therefore makes
recall rare, not precise. The storage half of this design addresses the rest.

Score distribution over 2,552 unique (query, memory) pairs: <0.01: 2,358 · 0.01–0.1: 125 ·
0.1–0.3: 30 · ≥0.3: 39.

## Goals

- An empty result is the normal outcome of recall. A memory is injected only when the
  cross-encoder judges it relevant.
- The recall query carries what the previous turn did, so follow-up messages resolve.
- Automatic consolidation stores durable lessons, not session status or events.
- Existing status memories are retired once, reversibly.

## Non-goals

Compaction and the in-session anchor note; the memory inspector UI; query rewriting with an
extra LLM call; pull-only memory (the `recall()` tool did worse than automatic injection); a
larger reranker model.

## Part 1 — Recall

### Query (`memory/recall_query.py`, new, pure)

`build_recall_query(message: str, previous_closing: str | None) -> str` returns the message
first, then `"\n\nPrevious turn: " + previous_closing` trimmed to 600 chars. The message goes
first so that if the cross-encoder truncates at 512 tokens, the recap is what gets cut. With no
previous turn it returns the message alone.

`previous_closing` is the closing text of the thread's previous turn, read from the persisted
transcript:
- the `answer` text, or the `submit_changes` summary (both written by
  `ChatController._write_turn_message`);
- or, for a clarify, the combined `❓ question → answer` breadcrumb.

The text comes from the transcript rather than `controller_history`, because compaction can
evict the history copy. `ChatController` resolves it once per user message and passes the
composed query as `prepare_turn(query=…)`. Continuation loops (implement after `propose_mode`,
a clarify resume) reuse the same query. Sub-agents and team members have no previous turn and
use their activation input alone, which is today's behaviour.

The same query feeds candidate retrieval (embedding + FTS5) and the cross-encoder.

### Selection (`memory/recall.py`)

- **Candidates:** the union of the top 30 semantic and top 30 lexical matches in scope, as today.
- **Scoring:** the cross-encoder scores **every** candidate on every recall. The
  `rerank_min_candidates` count gate is removed.
- **Injection:** candidates with `rerank_score >= T`, ordered by that score, at most
  `MAX_INJECTED = 4`. The recall token budget still applies after that.
- **Removed from selection:** importance, recency, min-max normalization, the fused score and
  the 0.15 floor.
- **No reranker:** if the cross-encoder is unavailable (model or library missing, or a load
  failure), recall returns `[]`. The earlier ranking stage is not trusted to judge relevance.
- **The `recall()` tool** (`tool_source.py`) goes through the same path with the same cap. On an
  empty result it says so plainly ("No stored memory matches this query."). On an unavailable
  reranker it reports "memory search unavailable" as an error output.
- **Cost:** up to ~60 cross-encoder pairs per recall. Recall is cached per (run, query), so this
  runs once per user message. Measure it on MPS and CPU; do not assume it.

### Trace

The `RecallTrace` shape is unchanged, so the inspector and editor-client Zod do not change.
`floor` now holds `T` and `injected` reflects the new rule. `signals` and `fused_score` are
still computed, for display only. The trace's docstring says they no longer decide anything.
Every candidate is still recorded, including those not injected.

### Configuration

- Add `CRUCIBLE_MEMORY_RECALL_MIN_SCORE` (default `T`, chosen in step 0).
- Remove `CRUCIBLE_MEMORY_RERANK_MIN_CANDIDATES` and `CRUCIBLE_MEMORY_WEIGHTS`, which no longer
  affect anything.
- Update the CLAUDE.md memory sections and Key Configuration to match.

### Step 0 — labelled replay (decides `T` and confirms the query)

1. Collect the real user turns from the traces and their thread transcripts: the message plus
   the previous turn's closing text.
2. For each turn, score every live memory with the cross-encoder under two queries:
   message-only and `build_recall_query`.
3. Label **per turn, not per query**: for each memory scoring ≥0.05 under either query, record
   whether it would have helped that turn. Turn-level labels let both query variants be scored
   against the same truth.
4. Report precision and recall for both variants across candidate thresholds. Choose `T` for
   high precision. The fallback is message-only if the recap loses on precision at equal recall.
5. Save the labelled set as `tests/fixtures/memory_recall/labelled_turns.json`. A test runs the
   real cross-encoder over it (skipped without `sentence-transformers`, like
   `test_semantic_index.py`) and asserts precision and recall at `T` stay at or above the
   recorded values, so a later model or threshold change shows its effect.

The labels come from this workspace's database. The storage changes in Part 2 shrink that
database, so label against the database as it is now, before running the cleanup script.

## Part 2 — What gets stored (`memory/consolidator.py`)

### Kinds

Automatic consolidation emits only `semantic | procedural`:
- The schema enum is narrowed.
- `Consolidator` also drops any `episodic` candidate (logged at debug), so this does not depend
  on the model obeying the prompt.

`remember()` keeps all three kinds.

### Prompt

`_CONSOLIDATION_SYSTEM` gains:
- **The keep test:** "Would this still be true and useful in a fresh session a month from now,
  on a clean checkout?"
- **What fails it:** test or verification results, branch/commit/uncommitted state,
  work-in-progress or partial-completion status, and anything true only right now.
- **Lessons over events:** turn a worthwhile event into the durable lesson it implies, and keep
  failed approaches as "X fails because Y".
- **3–4 bad examples from this database,** each paired with what should have been stored.
  Example: "65 focused tests passed" → nothing; "HTTP client sent camelCase attachment keys the
  backend rejected" → "`HttpBackendClient.sendChatMessage` must send image attachment keys in
  snake_case".

The keep test and failure list are one shared constant (`DURABILITY_RULES`) so the cleanup
script judges by the same rules.

### Measurement before switching

`scripts/memory/replay_consolidation.py` runs consolidation with the old and new prompts over:
- real evicted slices from `compaction_segments`;
- real `submit_changes` summaries from the chat transcripts.

It prints the candidates side by side and writes nothing to the store. Ship criteria:
- status notes from the new prompt are close to zero;
- lessons the old prompt extracted from those inputs (labelled by hand) still appear.

If lessons disappear, the prompt is not ready.

## Part 3 — One-time cleanup (`scripts/memory/retire_stale_memories.py`)

`--db <memory.sqlite3> [--apply]`:
- Asks the configured model the `DURABILITY_RULES` question for each live memory (structured
  yes/no + reason), using the backend's own provider env.
- Prints the memories it would retire, with reasons.
- With `--apply`, retires them by setting `valid_to` (recall already filters on it). This can be
  undone, because nothing is deleted.

It runs per workspace database. Kept episodic memories stay as they are.

## Testing

- Unit (fake reranker):
  - the threshold, cap and ordering;
  - empty result when nothing passes;
  - empty result and tool error when the reranker is unavailable;
  - a reranker score of 0 is never injected regardless of importance or recency;
  - the tool's empty-result wording.
- Unit: `build_recall_query` (ordering, trimming, no previous turn).
- Unit: previous-closing resolution from a transcript with answer, submit_changes and clarify
  closings, and with a compacted history.
- Unit: consolidation drops `episodic` candidates; the schema enum is narrowed.
- Fixture test: the labelled replay at `T` (gated on `sentence-transformers`).
- Existing harness and loop tests whose fakes assume `k=8` or fused ordering are updated in
  lockstep.
- Live check after merge: one session in this repo. Confirm that greetings and unrelated
  questions get no `recalled_memories`, and that a turn about a stored topic gets the matching
  memory.

## Risks

- The cross-encoder matches topic, so on-topic but unhelpful memories can still pass `T`. This
  is mitigated by Part 2 and the cap of 4, not removed.
- Labels are from one workspace and one user. The fixture records the result; it does not prove
  the threshold generalises. The env override exists for this reason.
- A workspace whose reranker never loads loses automatic recall entirely. That is intended,
  and visible in the trace (`reranked=false`, nothing injected).
