# Memory recall redesign — design

Date: 2026-10-14. Branch: `discuss/memory-redesign`.

Supersedes `2026-10-14-memory-recall-precision-design.md` (the first draft). Background and
evidence: `docs/superpowers/discussions/2026-10-14-memory-redesign-dialog.md` (Claude ↔ Codex,
three rounds each). A catalog-in-the-system-prompt design came out of that dialog and was then
dropped in favour of this one, because any change to the system prompt invalidates the whole
conversation's prompt cache.

## Problem

Cross-session recall injects irrelevant memories on nearly every turn. Measured on this
workspace (dialog, Claude round 1 and Codex round 1):

1. **Nothing filters the final set.** Every recall injected up to 8 memories.
   - Selection used min-max-normalized signals plus query-independent importance and recency, so
     the 0.15 floor rarely filtered anything.
   - The cross-encoder only reordered: 7,976 of 9,520 reranked injection flags scored below 0.01.
2. **What gets stored is poor.** The corpus is largely session status ("66 tests passed", "branch
   created", "remains uncommitted"). The `submit_changes` path hands consolidation only the
   closing summary (`chat/controller.py:1286`).
3. **The query is weak.** It is the raw latest user message, and follow-ups like "ok do it" carry
   no topic.
4. **Bugs:**
   - `remember(scope="thread")` stores the workspace path as the thread id
     (`memory/tool_source.py:93`).
   - `MemoryStore.get_memory` reads by ID with no scope or liveness check (`store.py:188`).
   - `_dispose` supersedes any existing ID without a same-scope check (`consolidator.py:209`).
   - A failed reranker call scores 0.0, the same as "irrelevant" (`reranker.py:49`).
   - The trace marks `injected` before the render budget applies (`recall.py:137`,
     `harness.py:216`).
   - The recall cache is keyed by query text, not turn (`harness.py:189`).
   - The cross-encoder runs synchronously on the event loop (`recall.py:132`).
5. **Memory is keyed by folder path,** so a worktree or a moved checkout starts with no memory.

The candidate search itself (semantic plus keyword) is sound and stays. What is missing is a
filter on the final set, a better query, and better input to consolidation.

## Decisions (Pradeep, 2026-10-14)

1. **Target capable hosted models** (NIM, ChatGPT plan, Gemini, Claude). Small local models get
   a deferred conditional, not a weaker design.
2. **Recall pipeline:** candidates from the existing semantic + keyword search → a judge →
   at most 4 injected, and an empty result is normal.
3. **Three query signals** for candidate retrieval. The October 15 approved iteration combines
   them for reranking in R → C → Q order:
   - Q, the current request;
   - C, the last substantive completed closing (`submit_changes` summary or `answer`),
     excluding operational notices, progress, breadcrumbs and unfinished answers;
   - R, a thread recap.
4. **The judge:**
   - The cross-encoder when local models are available: one combined-context score against
     each memory’s claim plus detail, accepted at 0.65. This provisional iteration is supported
     by replay evidence; fresh-chat recall remains an open quality gate.
   - Otherwise an LLM judge on the configured provider, which chooses the final memories directly
     with no scores.
5. **Local models (embedder + reranker) are an install-time option** chosen by the user. Without
   them:
   - the `search_semantic` tool is not offered;
   - the retrieval seed and memory candidates use keyword search only;
   - memory falls back to the LLM judge.
6. **The recap is written by the agent** as a field of `answer` and `submit_changes`. It costs no
   extra call.
7. **Scopes:**
   - `user` holds preferences the user states about themselves, applied in every project.
   - `repo` belongs to the repository, shared by its worktrees and moved checkouts; a fork with
     another remote is separate.
   - `thread` is the conversation.

   There is no migration: the new store starts empty.
8. **Writes:** explicit saves and forgetting take effect immediately, and every change is audited.
9. **Status stays out of memory.** Temporary project status stays in the thread. Shared rules
   reach `AGENTS.md` only through an edit the user approves.
10. **Children** (sub-agents, team members) get recall and the read/search tools, but cannot write
    memory.
11. **Evaluation is done by Claude;** Codex independently labels an overlapping subset.
12. **No hosted decision models** (Jev, OpenAI Decisions) in v1.
13. **Curation runs when a thread goes idle,** not at compaction. Recall runs per turn. The curator
    reads the raw evicted turns from `compaction_segments` plus the hot turns still in history,
    since its watermark, and skips the compaction summary. It therefore covers every thread,
    including its last hot turns and the 74% of threads here that never compact.

## Non-goals

Compaction's trigger and hot-turn selection (unchanged; only what its summarizer is sent
changes, see "Evidence rendering and per-call budget"); the task path (`tools/loop.py`); a memory
catalog in the system prompt; symbol-level
relevance hints beyond the mid-turn triggers below; file-hash revalidation; a temporary
project-state store; automatic `AGENTS.md` edits; a memory inspector redesign.

## Architecture

```
trigger (user message | working-set change mid-turn)
  → signals  Q (request / agent thought + new files)   C (previous closing)   R (thread recap)
  → candidates  semantic (embedder) ∪ keyword (FTS5) ∪ entity, scope-filtered
  → judge       reranker: combined R/C/Q context + memory body → score ≥ 0.65
                or LLM judge: chooses IDs directly
  → inject ≤4 short lines, appended once to history (cache-safe), not yet shown in this thread
  → read_memory(id) for detail; search_memory for anything else
```

### Local models capability

- **Setup.** The setup wizard gets a step: "Local models: better code and memory search. About
  1 GB download, runs on this machine." It is recommended on, and the choice is stored in
  `globalState` (`crucible.localModels`). Settings can change it later: install or remove, then
  restart the backend.
- **Install.**
  - On: the installer installs `crucible-agentd[local-models]`, which merges today's `[semantic]`
    and `[memory]` extras into one.
  - Off: plain `crucible-agentd`, without PyTorch.
- **Backend flag.** `CRUCIBLE_LOCAL_MODELS` (`1`/`0`) replaces `CRUCIBLE_SEMANTIC_RETRIEVAL` and
  `CRUCIBLE_MEMORY_RERANKER`.
- **Capability.** `agentd/local_models.py` reports a state per model: `ready`, `unavailable`
  (opted in but import or load failed, with the error) or `disabled` (opted out).
  - It is fed by the existing startup warmup; until warmup finishes, the state is `loading`.
  - A model that fails at runtime moves to `unavailable` for the rest of the process.
  - Exposed in `/v1/config` as `local_models: {embedder, reranker}` and shown in Settings.
- **Consumers.** Each asks the capability for the current state, not the flag:

  | Consumer | Embedder ready | Embedder not ready |
  |---|---|---|
  | `search_semantic` tool (`tools/search.py`, planning and execution registries) | offered | not registered |
  | Retrieval seed (`retrieval/`: semantic + keyword merge for planner and controller context) | semantic + keyword | keyword only |
  | Memory candidates | semantic + keyword + entity | keyword + entity |
  | Memory judge | reranker if ready | LLM judge if the reranker is not ready |

### Store and scopes (`memory/store.py`, `memory/scope.py` new)

- **Location.** `~/.crucible/memory/memory.sqlite3` (override `CRUCIBLE_MEMORY_STORE_PATH`), in WAL
  mode with a busy timeout, because one backend per workspace may write concurrently. Compaction
  segments and anchors stay in the workspace database.
- **Scope kinds:**

  | Kind | `scope_id` |
  |---|---|
  | `user` | `"user"` |
  | `repo` | repository identity |
  | `thread` | chat thread id |

  Every read path filters to the current three. Nothing reads by bare ID.
- **Repository identity** (`resolve_repo_identity`, pure):
  1. the normalized `origin` URL (`git:<host>/<path>`);
  2. else the root commit (`root:<sha>`);
  3. else the absolute path (`path:<abs>`).

  It is resolved once at backend start.
- **Entry fields:**

  | Field | Content |
  |---|---|
  | `content` | the claim, ≤140 chars, with any material condition |
  | `topic` | ≤60 chars; existing topic names are reused |
  | `applies_when` | ≤160 chars |
  | `detail` | rationale, procedure, evidence; ≤2,000 chars; returned only by `read_memory` |
  | `provenance` | `user_said` \| `tool_verified` \| `agent_inferred` |
  | `source_thread_id`, `source_message_id` | where the evidence was |
  | `revision` | bumped on correction |

  `kind` stays (`episodic` only from an explicit save). `importance` is no longer read.
- **Vector table** (created fresh; no old vectors are converted). Partition keys and metadata
  filters were verified on the installed `sqlite-vec` 0.1.9.

  ```sql
  CREATE VIRTUAL TABLE memory_vectors USING vec0(
    memory_id  TEXT PRIMARY KEY,
    scope_key  TEXT PARTITION KEY,   -- 'user:user' | 'repo:<identity>' | 'thread:<id>'
    live       INTEGER,              -- metadata column; set to 0 on retire
    embedding  float[384]
  );
  ```

  - **New columns on `memories`:** `embedding_state` (`pending` | `done`), `embedding_model`, and
    `embedded_hash` (a hash of the embedded text).
  - **Embedded text:** `topic + applies_when + content`. `detail` is not embedded: it is long,
    would dilute the vector, and can exceed the model's 512-token limit.
  - **Search:** Q, C and R are embedded separately. Each runs a nearest-neighbour search with
    k = 30 inside each eligible scope's partition, filtered on `live = 1`, and the results are
    unioned. Scope and liveness apply inside the search, so out-of-scope and retired rows never
    take a slot. Today's table filters scope only after an over-fetch (`store.py:220`), which a
    machine-wide store would break.
  - **Re-embedding:**
    - a revised entry is re-embedded when its text hash changes;
    - an entry saved while the embedder is not ready is `pending` and gets embedded once it is;
    - a change to `CRUCIBLE_EMBEDDING_MODEL` marks every vector for re-embedding, and a change of
      vector size rebuilds the table.
- **FTS5** switches to the `porter unicode61` tokenizer and also indexes `topic` and
  `applies_when`.
- **`memory_audit`** records every retire, rewrite, forget and supersede, with the old row and a
  reason.

### Recap (controller)

- **Field.** V2 main-agent `answer` and `submit_changes` gain a required `recap`: at most 5 lines and 400
  chars, enforced in Python. It says what the thread is doing, written by the agent.
- **Input.** The previous recap is in the per-turn payload, so the agent rewrites it rather than
  starting over.
- **Storage.** Persisted in a new `chat_threads.recap` column and included in rewind checkpoints
  with the other `controller_*` blobs (`chat/rewind.py:109`).
- **Schemas.** Teaching and the goldens are updated. The OpenAI strict-schema codec test
  (`test_every_schema_we_send_encodes_to_openai_strict`) covers the changed action schemas.
- **Children** do not write a recap; their `report` plays that role.

### Recall pipeline (`memory/recall.py`, rewritten)

**Triggers.**
1. **Turn start**, from the user message, including a continuation after `propose_mode`
   implement or a clarify answer.
2. **Working-set change mid-turn.** A path or `path:Symbol` appears in a tool call's arguments or
   results, or a new error signature appears (the first line of a failing command's error
   output), that has not triggered recall in this turn.
   - At most 6 triggers per turn.
   - Mid-turn triggers run once the iteration's tool results are in, so they never block a
     model call.
   - **Mid-turn triggers run only on the reranker path.** Without a ready reranker, recall runs at
     turn start only, and mid-turn discovery is left to the agent's own `search_memory`. This
     keeps the LLM judge to one call per user message.

**Signals per trigger.**
- **Q.** At turn start, the user message plus mentioned file paths. Mid-turn, the agent's latest
  `thought` plus the new working-set items.
- **C.** The last substantive completed agent answer or submission summary (first 600 chars).
  Read newest-first from the transcript, since compaction can evict the history copy. Skip
  generated system notices, progress, breadcrumbs and unfinished answers. Known historical
  usage/error/stop notices are filtered by their generated wording; new error closings carry
  explicit notice metadata. If no substantive answer exists, omit C.
- **R.** The thread recap.

On the first turn C and R are empty. Children use their assignment as Q and have no C or R.

**Candidates.** The union of:
- the top 30 semantic matches on Q, C and R combined (when the embedder is ready);
- the top 30 keyword (FTS5) matches;
- every entry whose `entities` exactly match a working-set path or symbol.

All are restricted to the current scopes, minus memories already shown in this thread. Each
entry carries its catalog line `[m:<id>@<rev>] <topic> — when: <applies_when>: <content>
(<provenance>)`.

**Reranker judge** (when the reranker is ready):
- The cross-encoder scores one pair per candidate: combined **R → C → Q** context against
  the memory's short claim plus full detail, without topic or applicability headers. Missing
  signals are omitted; C is filtered as above. Candidate retrieval still uses separate signals
  and the existing topic/applicability indexes.
- Use the model tokenizer to fit each pair within 512 tokens including special tokens. Reserve
  at most 256 tokens for a memory body, at most 380 for signal text before document/header
  overhead, and split available signal budget Q/C/R 65/20/15 over present signals. Oversized
  signals and memory bodies retain head and tail; trace every shortened field/body. Ordinary
  tested memory bodies fit whole; “full detail” is not an unlimited-input guarantee.
- Scoring runs off the event loop under the existing model lock with a 3 s deadline. The model
  uses explicit sigmoid activation. One combined score ≥ **0.65** qualifies; inject at most
  four in descending score order. This retains the existing primary cutoff for the authorized
  October 15 iteration; it is not a calibrated probability or a new independent evaluation.
- Evidence: `../evals/2026-10-15-memory-combined-context-experiment.md` and
  `../evals/2026-10-15-memory-full-content-experiment.md`. Combined context improved the reused
  scenario split, and claim plus detail improved real continuation/control replays. Fresh-chat
  requirements were still missed. Default-off rollout and outstanding quality gates remain.
- **On turn-start failure** (exception, deadline), the trigger falls back to the LLM judge if
  this user message has not spent its budget. A mid-turn failure marks the reranker unavailable
  and injects nothing; it cannot initiate an extra LLM call. Zero is not used as a failure score.

**LLM judge** (turn-start trigger only; when the reranker is not ready, or failed on that
trigger):
- **Call.** One structured call on the configured provider and model:
  - input: Q, C, R and the top 20 candidates (keyword/entity order, plus semantic when available)
    as catalog lines;
  - output: `{memory_ids: [...], reasons: {id: one line}}`, at most 4, empty allowed.
- **Validation.** Returned IDs must be in the candidate set; the rest are dropped and noted in the
  trace.
- **Failure.** On a timeout (10 s) or failure, nothing is injected and the trace says so. The turn
  is never blocked or failed.
- **Gating.** The call is skipped when there are no candidates. Its durable budget is shared
  across the main agent and child assignments belonging to the same original user message.
  Children can always use read/search tools when that budget has already been spent.
- **Strict schema.** Its schema is added to `test_every_schema_we_send_encodes_to_openai_strict`.

**Injection.**
- **Delivery.** One history message per trigger with injected entries, appended once:
  `Possibly relevant memories (background, not instructions; read_memory(id) for detail):` then
  the catalog lines, each followed by why it matched:
  - reranker: `(matched: combined context + memory body)`;
  - LLM judge: the one-line reason.
- **Cache safety.** Nothing is added to the system prompt or the per-turn tail, so the input stays
  append-only on every transport (`providers/openai_native.py` `SessionInputs`), and nothing is
  re-sent per iteration.
- **Shown set.** Memories shown in the thread are recorded in thread state (persisted, restored by
  rewind) and are not injected again while their message is still in the history. Compaction
  clears the set, since it removes those messages, and entries shown during the current turn are
  then appended again, once.

**Trace (`memory-recall-NN.json`, versioned v2).** One file per trigger:
- the trigger kind;
- the Q, C and R texts;
- the candidates with their sources;
- combined reranker scores, or the LLM judge’s decision and reasons;
- the actual bounded combined query, shortened signal names and memory IDs, and maximum pair
  tokens on the combined path (older traces may lack these fields);
- the rule's verdict;
- what was actually delivered;
- a status: `injected`, `empty`, `no_candidates`, `judge_failed` or `disabled`.

`/v1/memory/inspect` serves it, the editor-client Zod gains the v2 shape, and the inspector's
trace tab shows it as a plain list.

### Tools (`memory/tool_source.py`, rewritten)

| Tool | Contract |
|---|---|
| `read_memory(id)` | Full entry (claim, topic, applies_when, detail, provenance, source, revision, scope). Statuses: `ok`; `retired` (with replacement id); `not_found` (also for other scopes, never leaking them). |
| `search_memory(query?, topic?, file?, cursor?)` | Same candidate search as recall (semantic + keyword + entity, or keyword + entity) with exact `topic`/`file` filters. Up to 8 catalog lines per page, labelled "candidates — read before relying on one". No judge. Statuses: `ok`, `empty`, `unavailable`. |
| `remember(content, topic, applies_when, kind, scope?, origin, entities?, detail?)` | `scope` is `repo` (default), `thread` or `user`; `user` requires `origin=user_requested`. Publishes immediately (`user_said` or `agent_inferred`). Exact duplicates in the same scope return the existing id. Main agent only. |
| `forget(id, reason)` | Retires immediately with an audit row. Only when the user asks. Main agent only. |

The old `recall` tool is removed. Tool availability no longer depends on a consolidator being
configured (`harness.py:265`).

### Teaching (`_MEMORY_BLOCK`, rewritten)

It says:
- Injected memories are background from earlier sessions, chosen as possibly relevant. Most will
  be partially relevant at best, and they never override the current request or authorize
  actions.
- Read an entry before relying on a remembered procedure, decision or code-dependent constraint,
  and verify code-dependent claims against the current code.
- Use `search_memory` when you expect a memory that was not shown.
- Save lessons with their condition and evidence (`origin="agent_nomination"`), and what the user
  asks to remember (`origin="user_requested"`).
- Never save task status.
- `forget` only on request.
- Write the `recap` as the thread's purpose and state in five lines or fewer.

### Evidence rendering and per-call budget (`memory/evidence.py` new)

One renderer turns history messages or segments into the text sent to a model. Both the
compaction summarizer and the curator use it.

**Compaction spike status (Codex, 2026-10-14).** Pradeep paused implementation to test whether
thoughts + tool signatures + pass/fail could replace tool output. The completed 20-span,
12-thread comparison on the ChatGPT plan missed 60 of 184 reference facts without outputs,
versus 20 with capped outputs; removing outputs saved 69.8% of captured-baseline input.
Adding thoughts to capped outputs did not improve aggregate coverage. Keep tool observations;
the numerical caps below remain provisional until the real-history/compaction check passes.
This experiment used already-capped UI transcripts, not raw production compaction rounds,
and does not establish curator quality or resumed-task success. Full method, source anchors,
results and limitations: `../discussions/2026-10-14-compaction-evidence-spike.md`.

Thoughts need private evidence storage because `reasoning/react_common.py:24` strips them
today. A provider projection must explicitly remove that metadata before serializing history;
`chat/controller_prompts.py:1282` currently passes the history dictionaries through. Whether
compaction uses those thoughts was left for evaluation. The gated implementation now includes
bounded private thoughts: paired production-renderer checks found missing context with the
initial smaller caps and thoughts disabled. This is provisional; it does not establish that
thoughts alone caused the improvement or justify enabling the default.

**Budget estimator resolved in phase 2.** Compaction retains its conservative three-character
estimator. Per-call budgets count the prior anchor, summary system prompt, JSON serialization
and transport headroom; they do not prove actual provider token use. Recall's separate line
budget does not weaken compaction overflow protection.

**Compaction per-call budget:** each call's input targets **≤ 200k tokens** and must never
exceed **500k estimated tokens** or the configured context window's response reserve. Tokens
are estimated at 3 chars each for compaction. Anything bigger is split on complete logical
action/result turns; an indivisible oversized turn is rejected before a call, with its raw
source retained. A single turn may exceed the 200k target only if it fits the hard/context limit.

**Per-type caps.** Command and test output keep head *and* tail, because errors and summaries
come last:

| Content | Kept |
|---|---|
| File reads (`read_file`) | path, line range, line count, first 2,000 + last 1,000 chars |
| Successful command output (`run_command`, exec sessions) | first 1,600 + last 3,200 chars, with `[… N chars omitted …]` between |
| Failed command output | first 800 + last 1,200 chars |
| Search, web and other tool output | first 1,000 + last 1,000 chars |
| Edits (patch ops and results) | the file and op, first 4,000 + last 1,000 chars of the change |
| User messages, agent actions and texts, reports | up to 8,000 chars each; long messages may be cut |
| Compaction summaries, injected or recalled memories, skill and `AGENTS.md` bodies | excluded |

**Earlier cap analysis:** the discussion reported 9.7M chars of evicted segments reduced to
2.9M (−70%). The implemented phase 2 renderer instead measured about 70.1% reduction on 69 typed
controller histories, and 16.6% on the frozen legacy segments, whose role/tool metadata is
unavailable. It does not guess old types. Call signatures are retained separately; each
argument excerpt is bounded at 4,000 chars, with an omission marker. The original 300-character
file cap lost many observed declaration names. After the increases above and enabling bounded
thoughts, a read-only rerun measured **56.64% reduction** on 69 retained typed histories
(3,997,215 approximate raw characters to 1,733,152 rendered), and 16.57% on legacy segments.
The earlier 70% result describes the old cap profile, not the current implementation.
These are size diagnostics, not a summary-quality pass. Method and original limits:
`../discussions/2026-10-14-phase2-evidence-rendering-check.md`.

**Typing needs the tool name.** `compaction_segments` gains a `tool` column next to `role` and
`mseq`, so the renderer knows a block came from `read_file` versus `run_command`. History messages
already carry it (`{"role": "tool_result", "tool": …}`, `chat/controller_loop.py:1816`).

**What compaction sends today** (verified, `memory/compactor.py`):
- **Trigger:** compaction runs when the next request reaches `max(window × 0.65, window − 28k)`
  tokens (`trigger_tokens`, :170).
- **Hot set:** the newest whole turns within `window × 0.4`, capped at 10 messages
  (`CRUCIBLE_MEMORY_HOT_TURNS`). The message cap usually binds.
- **Evicted:** everything older, sent to **one** summarizer call as raw `role: content` text
  (`_render`, :75; `self._summarize(old_text, _render(evicted))`, :262).
- **Segments:** evicted messages are stored whole before summarizing (:240), and the oversized-turn
  backstop stores the full text before trimming the in-context copy (:232–236). Nothing in the
  segments is truncated. Tool output can still be capped upstream, by the tool itself, before it
  ever enters history.

**Historical forecast per window size** (approximate: evicted ≈ trigger minus system prompt
minus a 10-message hot set; assuming 30% retained; not observed call counts or provider usage):

| Window | Trigger | Evicted today, one call | Stripped | Summarizer calls |
|---|---|---|---|---|
| 128k | ~100k | ~70k | ~21k | 1 |
| 200k | ~172k | ~140k | ~42k | 1 |
| 272k (ChatGPT plan catalog) | ~244k | ~210k | ~63k | 1 |
| 1M | ~972k | ~900k+ | ~280k | 2 |

**Compaction summarizer changes:**
- The new path is independently gated by `CRUCIBLE_MEMORY_COMPACT_EVIDENCE`, default false while
  the cap/summary-quality gate remains open.
- It is sent the stripped rendering, not raw `_render`.
- When the prior anchor plus the stripped evicted text exceeds 200k tokens, the evicted turns are
  split on turn boundaries and folded in order: `anchor₁ = summarize(anchor₀, chunk₁)`,
  `anchor₂ = summarize(anchor₁, chunk₂)`, …
- Each fold is one call within budget. A failed fold degrades exactly as today: the anchor stays
  at its last good version, and the segments keep the raw text.
- The raw evicted text stays in `compaction_segments`, so nothing is lost to stripping.

**The curator** uses the same renderer, prioritised as described in its section, in chunks of at
most 24k tokens, well under the budget.

### Curator (`memory/consolidator.py` → `memory/curator.py`)

There are two write paths:
1. **The main agent's `remember`.** Saves the user asks for (`user_said`) and lessons the agent
   proposes (`agent_inferred`). It publishes immediately, in any thread.
2. **The curator.** Runs in the background when a thread goes idle. Both existing consolidation
   triggers are removed: the post-compaction hook (`harness.py:146`) and the `submit_changes` call
   (`chat/controller.py:1282`).

This follows Codex, which extracts memories per idle thread, separately from compaction
(`openai/codex` `memories/README.md`, `memories/write/src/rollout_input.rs`).

**Trigger.**
- A background sweep every 5 minutes, and once at backend start, finds threads with uncurated
  turns that have been idle for 30 minutes or more. A thread with a turn in flight is never picked.
- **Safeguard for very long sessions:** a thread that keeps working without going idle is also
  picked once its uncurated stripped text exceeds 100k chars.
- At most 2 threads are curated concurrently per backend.
- Children's runs are never curated, as today.

**Input: the whole thread since the watermark, minus what isn't evidence.**
- **Sources:** both are read since the watermark.
  1. Evicted turns from `compaction_segments`: the raw turns compaction removed, stored before it
     summarized them (`compactor.py:240`).
  2. Hot turns still in the controller history.
- **Excluded,** because each is a restatement or would loop memory back into itself:
  - compaction summaries (`[MEMORY] Summary of earlier conversation…`, live or re-evicted);
  - injected memory messages ("Possibly relevant memories…") and legacy recalled-memory blocks;
  - skill bodies and `AGENTS.md` text;
  - `<<<agent-content>>>` frames, which stay attributed and are never treated as instructions
    (`consolidator.py:103`).
- **Stable message sequence.** Every controller history message gets a monotonic `mseq` when it is
  appended. `compaction_segments` gains `mseq` and `role` columns, filled at eviction (today only
  the text is kept: `_make_segment(run_id, seq, str(m.get("content")))`). The watermark is the last
  curated `mseq`, so a hot turn curated now and evicted later is never curated twice.
- **Prioritised stripping,** in the shape of Codex's tiered input:
  - Rows are filled in priority order: user messages; the agent's `answer`, `submit_changes` and
    `clarify` texts and its `thought`s; sub-agent and team reports; then tool calls and outputs.
    Within a tier, newest first.
  - Each row is capped by the per-type rules in "Evidence rendering and per-call budget".
  - Omitted spans are marked `[… omitted …]`, and rows are emitted in their original order.
  - The thread recap and title are prepended as context.
- **Chunks:** at most 24k tokens (≈72k chars at the conservative estimate) per call, split on turn boundaries only. On this
  workspace's histories, a stripped thread has a median of ~11k chars (one chunk) and a maximum of
  ~263k (3 chunks).
- **Existing entries:** for each chunk, the related entries come from the same candidate search
  recall uses (top 20, by the chunk's user messages), replacing today's importance/recency top-20
  (`consolidator.py:169`). They are sent as catalog lines with IDs, so the curator can merge or
  revise.
- **Watermark:** `curation_progress(run_id, last_mseq)` advances only after a chunk succeeds. A
  failed chunk is retried at the next sweep, with backoff of 5 minutes, then 30 minutes, then 3
  hours. Rewind deletes the rewound span's segments, moves the watermark back to the rewind point,
  and retires the span's memories (`retire_since`).
- **Model:** `CRUCIBLE_MEMORY_CURATOR_MODEL` if set, else the configured model, on the configured
  provider. Codex uses a cheaper model for this step (`gpt-5.6-luna` at low reasoning effort;
  `model-provider/src/provider.rs:97`). Failures are logged and visible in the inspector, never
  shown in chat.

**What it may write.**
- **Kinds:** `semantic | procedural` only. The schema enum is narrowed, and Python drops anything
  else.
- **Prompt rules:**
  - Keep a lesson only if it would help a future session, with its condition and evidence.
  - Prefer what the code cannot tell a future agent: decisions and their reasons, failed
    approaches, user preferences and constraints. Skip facts a future agent can read from the code
    itself; Claude Code's auto memory skips those too.
  - Reject task status: test or verification results, branch/commit/uncommitted state, work in
    progress, "partially added".
  - Rewrite events into the lessons they imply ("X fails because Y").
  - Return nothing rather than infer an unsupported cause.
  - Use a short `content` (≤140 chars) with the rest in `detail`, reuse existing topics, and cite
    the evidence turn's `mseq` in `source_message_id`.
  - Include 3–4 bad examples from today's corpus, each paired with what should have been stored.
- **Python checks:** length limits, required `topic` and `applies_when`, and a source `mseq` inside
  the chunk. A candidate that fails is dropped and counted, not repaired.

**Merging.**
- The curator may revise an `agent_inferred` entry (new revision, audit row). It never revises a
  `user_said` entry.
- Supersession requires a live target in the same scope.
- **Dedup:** exact normalized content always, and cosine ≥ 0.92 when the embedder is ready.

**Remaining gaps.**

| Gap | Handling in v1 |
|---|---|
| A lesson reaches other threads only after its thread has been idle for 30 minutes | Accepted. It is still in context in its own thread, and the agent's `remember` publishes immediately when it matters. |
| A lesson split across two chunks | Chunks split only on turn boundaries, and the second chunk sees the first chunk's entries among its related entries. |
| Compaction and curation both read evicted text, at different times | They do different jobs and run separately; neither waits for the other. Both use the same stripped rendering, so neither sends raw tool output. |

### Removal of the old path (in the final phase)

- **Recall code:** fused scoring with importance and recency, the 0.15 floor, the rerank count
  gate, the query-keyed recall cache, `recall_grounded`, `plan_context["recalled_memories"]` in
  the per-turn tail, and the `recall` tool.
- **Write code:** the `submit_changes` consolidation call (`chat/controller.py:1282`), the old
  `_CONSOLIDATION_SYSTEM` prompt, and the importance/recency existing-memory context.
- **Env vars:** `CRUCIBLE_MEMORY_WEIGHTS`, `_RERANK_MIN_CANDIDATES`, `_GRAPH_GROUNDING`,
  `_RECALL_TOKEN_BUDGET`. `CRUCIBLE_MEMORY_RERANKER` and `CRUCIBLE_SEMANTIC_RETRIEVAL` are folded
  into `CRUCIBLE_LOCAL_MODELS`.
- **Extension:** the `crucible.memory.reranker` setting is replaced by `crucible.localModels`
  (`package.json:212`, `settings-deps.ts:20`, `vscode-runtime.ts:56`,
  `webview-ui/src/settings/types.ts:228`). The managed spawn's hard-coded
  `CRUCIBLE_SEMANTIC_RETRIEVAL: "true"` (`backend-process.ts:152`) is replaced by the stored
  choice.
- **Docs:** the CLAUDE.md memory, retrieval and Key Configuration sections are rewritten to
  match.

## Evaluation (Claude runs it)

1. **Freeze a baseline** before any change: copy `.crucible/state/memory.sqlite3` and the trace
   index into `.crucible/state/eval/memory-baseline-2026-10-14/`. For the new pipeline, the
   baseline corpus is filtered offline (in the eval copy) by the curator's rules and given
   topics, applicability and short claims.
2. **Scenario matrix** for the three signals, with relevance to Q, C and R each yes or no:

   | Q | C | R | Typical case |
   |---|---|---|---|
   | ✓ | ✗ | ✗ | topic switch; the memory matches only the new request |
   | ✗ | ✓ | ✓ | "ok do it"; the memory matches what just happened |
   | ✗ | ✓ | ✗ | follow-up on the last turn's detail, outside the thread's main theme |
   | ✗ | ✗ | ✓ | the thread's theme, but not this turn |
   | ✓ | ✓ | ✓ | continuing the same work |
   | ✓ | ✓ | ✗ | the request continues the last turn, off the thread's theme |
   | ✓ | ✗ | ✓ | the request returns to the thread's theme after a detour |
   | ✗ | ✗ | ✗ | the memory is unrelated; must not be injected |

   - Each pattern gets at least 5 cases: real turns from the traces where they exist, written by
     hand where they don't.
   - Historical threads get recaps generated offline, one call per sampled turn, during
     evaluation only.
   - Each (case, memory) pair is labelled *needed*, *harmless* or *harmful if applied*.
   - Codex labels 10 cases independently, and agreement is reported.
3. **Reranker:** fit the combination rule and thresholds on a development split. Candidate rule
   families to compare:
   - max over the signals;
   - a weighted sum;
   - Q-gated (Q above a threshold, or C and R both above a higher one).

   Report precision of what is injected and miss rate of what is needed, on an untouched split.
4. **LLM judge:** run it on the same cases with each provider's representative model. Report the
   same two numbers; only its prompt may be adjusted. The models:

   | Provider | Model |
   |---|---|
   | NIM | `nvidia/nemotron-3-ultra-550b-a55b` |
   | ChatGPT plan | the configured account model |
   | Gemini | `gemini-flash-latest` |
   | Claude | subscription route, default |

5. **Baselines:** no memory, and the old recall (from the frozen traces).
6. **Live checks** per provider:
   - a needed memory, injected and applied;
   - no memory on unrelated turns;
   - a lesson that only becomes relevant after a file read: found by a mid-turn trigger (reranker
     path) or by the agent's own `search_memory` (no local models);
   - a correction, then `forget`;
   - scope isolation across two repositories;
   - `cached_tokens` across a turn with injections (confirming no invalidation).
7. **The new pipeline becomes the default only after this review.** Until then it sits behind
   `CRUCIBLE_MEMORY_RECALL_V2=1`, and the old path stays wired.

### Curator evaluation (gate for phase 5)

1. **Inputs:** real threads, built exactly as in production: segments plus hot history, excluding
   summaries and injected memories, stripped and chunked. Sample 15 chunks from at least 10
   threads, covering both compacted and never-compacted threads.
2. **Gold labels:**
   - For each chunk, Claude lists the lessons a careful reader would keep (claim, condition,
     evidence turn) and marks everything else in it as status or transient.
   - Codex labels 5 of the chunks independently, and agreement is reported.
   - Labels are written before either curator is run, so they are not shaped by its output.
3. **Run** the old consolidator and the new curator on the same chunks, against a copy of the
   store (nothing written to the real one), with two models: the configured ChatGPT plan model
   and `nvidia/nemotron-3-ultra-550b-a55b`.

   | Measure | Pass bar for the new curator |
   |---|---|
   | Status notes among what it writes | ≤ 1 in 20 |
   | Precision (written entries a reviewer would keep) | ≥ 4 in 5 |
   | Recall (gold lessons it writes) | ≥ 2 in 3 |
   | Claims with an unsupported cause | 0 in the sample |
   | Format (claim ≤140 chars, topic, applies_when, source) | all; Python drops the rest, and drops are counted |
   | Duplicates of entries already in the copy of the store | reported, no bar |

   Any adjustment after seeing results is to the prompt only, and is re-checked on 5 chunks set
   aside beforehand.
4. **Stripping check:** for the same chunks, compare gold lessons found in the stripped input with
   those found in the unstripped input. A lesson that appears only in unstripped text means a cap
   is too tight.
5. **Compaction summary check:** for 5 real compaction rounds, summarize once from the raw
   rendering and once from the stripped rendering (with folding where needed). Check that the
   stripped summary keeps the same files, decisions, errors and next step. A loss means a cap is
   too tight.
6. **Cost** per provider and model: input and output tokens per chunk and calls per curated
   thread, with the configured model and with a cheaper `CRUCIBLE_MEMORY_CURATOR_MODEL` where the
   provider offers one.

### Cost summary

Estimates; the evaluation replaces them with measurements.

| Component | When | Cost |
|---|---|---|
| Recap | every `answer` / `submit_changes` | ~100 output tokens on the main call; no extra call |
| Recall, reranker path | turn start + up to 6 mid-turn triggers | local: one cross-encoder pair per candidate and up to 3 embeddings per trigger; target under 1 s on Apple GPU and CPU |
| Recall, LLM judge path | turn start only | one call per user message: ~3–4k input tokens (3 signals + 20 candidate lines), ~200 output |
| Injection | per trigger with results | ≤4 lines × ~70 tokens, appended once; cached with the rest of the history afterwards |
| Curator | each idle thread with uncurated turns | one call per ≤24k-token chunk of stripped text: one chunk for a median thread here (~3k tokens), at most 3 seen; a few hundred output tokens each; optionally a cheaper model |
| Compaction summarizer | each compaction | current diagnostic: about 57% fewer characters in typed histories; target 200k estimated input, hard/context limit ≤500k, folds as needed |
| Embedding writes | each new or revised entry | one local embedding (none without local models) |

## Testing

- **Unit:**
  - `resolve_repo_identity`;
  - scope filtering on every read and write path;
  - the local-models capability states and each consumer's downgrade;
  - recap validation and persistence;
  - trigger detection and the per-turn cap;
  - signal assembly (first turn, continuation, child);
  - candidate union and the shown-set exclusion;
  - combined-context/body scoring, warning filtering, token limits, cutoff, cap and judge fallback;
  - LLM judge ID validation, empty result and timeout;
  - injection as one appended history message, and re-append after compaction;
  - the trace v2 statuses;
  - tool statuses;
  - `remember` and `forget` rules;
  - curator rules, the same-scope supersede check and dedup;
  - the evidence renderer: every per-type cap, head-and-tail for command output, the
    exclusions, and the 200k-token target and 500k hard limit;
  - compaction folding: chunks on turn boundaries, the anchor carried through each fold, a
    failed fold degrading as today, and raw segments unchanged;
  - the idle sweep (idle threshold, in-flight threads skipped, the long-session safeguard);
  - input assembly: segments plus hot history by `mseq`, the exclusions (summaries, injected
    memories, skill and `AGENTS.md` bodies), tier ordering, caps and omission markers;
  - chunking on turn boundaries, the `mseq` watermark (a hot turn curated then evicted is not
    curated again), retry with backoff, and rewind moving the watermark back;
  - audit rows.
- **Integration:**
  - a scripted controller turn with a mid-turn trigger and an injected memory;
  - rewind restoring the recap and the shown set;
  - a child with recall but no `remember`;
  - a no-local-models run (keyword retrieval seed, no `search_semantic`, LLM judge).
- **Cache:** a test that the plan-route input stays append-only across a turn with injections.
- **Concurrency:** two stores writing the shared database.
- **Goldens:** controller prompt and schema goldens updated.

## Delivery phases (each its own plan)

**Implementation status (Codex, 2026-10-14):** phase 1 is implemented behind the default-off
`CRUCIBLE_MEMORY_RECALL_V2` flag. It supplies the fresh scoped durable store, audited mutations,
scoped vectors and pending embedding publication, stable source sequences, private evidence
metadata, provider projection and rewind support. Existing recall and consolidation remain the
active pipeline. The phase 1 plan records implementation details and validation:
`../plans/2026-10-14-memory-redesign-phase1-store-scopes.md`. Phase 2 adds capped evidence rendering
and sequential anchor-aware folds behind its own default-off flag; see
`../plans/2026-10-14-memory-redesign-phase2-evidence-compaction.md`. Its mechanics and real-data
reduction are checked, while summary-quality/continuation remain unverified. Phases 3 and 4 now implement the shared local-model capability, setup/settings choice, bounded
recap, scoped tools and gated recall pipeline. See their phase plans; judge thresholds remain
provisional. Phase 5 now implements idle curation behind the independent default-off
`CRUCIBLE_MEMORY_CURATOR_V2` gate; see `../plans/2026-10-15-memory-redesign-phase5-curator.md`.
Phase 6 has frozen synthetic labels, offline real-model rule comparisons, hosted judge checks
and approved redacted-corpus curator/compaction comparisons. Local production validation and
prompt retests improve curator output, but provisional recall is 8/15 and the five-chunk
retest precision is 7/9; these do not pass release bars. ChatGPT controller checks also verify
file-driven search and two-repository isolation in temporary fixtures. Actual production
compaction/task-success, remaining provider/cost and independent review gates stay open; see
`../plans/2026-10-15-memory-redesign-phase6-evaluation.md` and its linked continuation results.
Defaults and the old path are retained until those gates pass.

1. **Store and scopes:** repository identity, the machine-wide store, new fields, audit, the
   `mseq`/`role`/`tool` columns on segments and `mseq` on history messages, FTS tokenizer, and
   scope fixes on every path.
2. **Evidence rendering and compaction budget:** the shared renderer with per-type caps, the
   200k/500k per-call budget, and the compaction summarizer switched to stripped, folded input.
   It is independent of recall. The implemented renderer reduced the sampled typed controller
   histories by about 57% with the revised cap profile; older untyped evidence and summary
   quality have separate limits.
3. **Local-models capability:** setup option, installer extra, `CRUCIBLE_LOCAL_MODELS`, the
   capability module, `/v1/config`, and the three consumer downgrades (`search_semantic`,
   retrieval seed, memory).
4. **Recap and recall pipeline:** the recap field; triggers, signals, candidates, both judges,
   injection, trace v2 and the tools. Behind `CRUCIBLE_MEMORY_RECALL_V2`.
5. **Curator:** the idle sweep, input assembly with exclusions and stripping, chunking, the
   watermark, the new prompt, Python checks and merging. It ships only after the curator
   evaluation passes.
6. **Evaluation and switch:** the scenario matrix, thresholds, judge results and live checks, then
   the default flip and removal of the old path.

## Deferred

- The small-local-model conditional.
- One combined call for compaction summary and curation.
- The LLM judge on machines that have the reranker, if the evaluation shows it clearly better.
- Jev or OpenAI Decisions as the judge (Decisions is not documented on the ChatGPT plan route).
- Symbol-level relevance hints using indexer line ranges.
- File-hash revalidation, expiring project notes, and an `AGENTS.md` promotion UI.

## Risks

- **The cross-encoder matches topic rather than need.** It is mitigated by the cleaner corpus,
  the three-signal rule and the cap of 4, not removed. The evaluation measures what remains.
- **The LLM judge adds one extra call per user message** on machines without local models. That
  counts against provider rate limits (for example Gemini's free tier), adds latency at turn
  start, and on the ChatGPT plan uses plan quota.
- **Mid-turn discovery without local models depends on the agent searching.** If it doesn't,
  lessons that only become relevant mid-turn are missed there. The live checks cover it.
- **Curation costs provider calls in the background,** about one per idle thread with new turns,
  counting against rate limits and ChatGPT plan quota. A cheaper curator model reduces it.
- **Stripping can drop the evidence for a lesson.** Evaluation step 4 checks the caps.
- **A recap written by the agent can drift or be vague.** The evaluation's generated recaps and
  the live checks show whether it is useful as a signal.
- **The machine-wide store is shared by all backends;** WAL mode plus busy timeouts, with a
  concurrency test.
- **Labels come from one agent and one user's corpus.** Codex's overlap measures agreement, not
  truth.


## Owner-approved catalog recall revision — October 15

This section supersedes the runtime relevance-judge and delivery-cap rules above.
Global consolidation remains deferred. Existing curation and compaction are unchanged.

Retrieval produces scoped, live keyword/entity candidates, optionally supplemented by
semantic search. If the existing local BGE-reranker-base model is ready, it orders at most 20
retrieved candidates and the catalog offers up to 10; no score cutoff rejects memories.
Otherwise offer up to 15 in retrieval order, including when all local models are disabled.
There is no runtime hosted LLM relevance judge or fallback. The main working model decides
what applies and uses the existing `read_memory(id)` tool to fetch full entries.

Every catalog labels its actual selection method, warns about false positives, and tells
the model to check each claim and applies_when, ignore unrelated entries, and read entries
before relying on them. IDs, revisions, topic, claim and applicability are supplied; full
detail is available through the scoped read tool. Scope/lifecycle validation stays in code.

Append-only delivery, shown-ID deduplication, six bounded working-set refreshes per turn,
rewind and compaction revalidation remain. Working-set refreshes also work without local
models. Inspector traces distinguish offered catalog IDs from relevance judgments, full
reads and actual use. BGE failures fall back to retrieval order, never to a provider call.

Use the existing BGE loader and bounded R → C → Q / claim-plus-detail inputs.
No new model download, local service or dependency is introduced. BGE is an ordering
signal; the main model receives applies_when in the catalog and makes the relevance
decision. This replaces the previously discussed optional Kev ordering.

Implementation/verification checklist: `../plans/2026-10-15-memory-catalog-recall.md`.

Explicit `search_memory` uses the same candidate ordering, liveness checks, per-page
caps and false-positive disclaimer as automatic catalogs. It supplies its explicit
query as Q, rather than borrowing unrelated closing/recap signals. Topic/file filters
apply before the bounded ordering pool. Query-less browsing lists scoped memories
without semantic scoring. Pagination rejects cursors if ordering, IDs or revisions
change. `read_memory` remains the exact-ID scoped detail lookup.

Owner-approved small-pool optimization: eight or fewer candidates bypass BGE entirely
on both automatic and explicit recall. All candidates remain offered in retrieval
order with the standard disclaimer, without claiming BGE was used. BGE ordering may
run starting at nine candidates, subject to readiness and the existing input bound.
