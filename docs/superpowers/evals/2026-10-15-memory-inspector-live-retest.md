# Memory Inspector live retest — 15 October 2026

The inspector polish completed in Crucible. Automatic curation and controlled compaction both ran; the subsequent recall probe retrieved all five new memories but delivered none. This is evidence of working execution paths, a useful compaction handoff, and continuing recall/selectivity problems—not a general quality pass.

## Run and controls

- Original inspector conversation: `chat-00aed1058d4b`; left untouched while a separate polish conversation, `chat-bd041ab8458a`, ran through the normal Crucible message API and the owner-approved connected ChatGPT plan.
- Original last activity remained `2026-10-15T07:43:45.202390+00:00`. Curation finished at `08:15:55.165767+00:00`, about 32 minutes later, without a manual trigger, timing changes or seeded memories.
- Temporary runtime window: 500,000 → 128,000 → 96,000 → **500,000 restored and verified** at `08:17:06.025774+00:00`. Same account, model and reasoning effort. This is shared backend configuration, not a per-conversation setting. The smaller window does not alter curation eligibility.
- Configuration used the normal authenticated provider route and account-summary API. The earlier rejected manual-curator shortcut was not executed or retried. No account-registration extraction from a credential database was used for this run.
- Source/database analysis was read-only (`mode=ro`, `PRAGMA query_only=ON`). The active store's five new memories remain active; the thirteen retired landing-test memories were not restored. No memory algorithms were changed during this retest.

Artifacts are under `.crucible/state/eval/memory-explorer-retest-2026-10-15/`: `polish-run.json`, `polish-context-budget.json`, prompts/events `03`–`07`, `polish-compaction-anchor.json`, `polish-compaction-segments.json`, `automatic-curation-memories.json`, `automatic-curation-sources.json`, and `07-recall-trace.json`.

## Inspector result

Crucible changed seven files under `apps/vscode-extension/webview-ui/src/memory/`: `BrowserTab.tsx`, `MemoryApp.tsx`, `RecallTraceTab.tsx`, `inspector.css`, new `presentation.ts`, `Inspector.test.tsx`, and `RecallTraceTab.test.tsx`. The polish reduced repeated headings, led with the selected claim, grouped explanation/applicability, formatted provenance/times, disclosed technical identifiers, and restored keyboard focus to the selected result after Escape.

Observed tool results in `03-separate-thread-polish.jsonl` show **17 inspector tests across four files passed** and **webview TypeScript checking passed**, including the final rerun. The initial test failure was an ambiguous `/Delivered/` text assertion; Crucible corrected it to an exact assertion. Browser checks used the real components and fictional fixtures at 360px/light and 1280px/dark: search, scope filtering, detail focus/Escape restoration, a 2px keyboard focus outline, recall delivery distinction, curation display, reduced-motion emulation, and no horizontal overflow. Codex separately inspected the saved wide screenshot.

Preview: <http://127.0.0.1:5173/.tmp/memory-inspector-preview/index.html>. It is sample data, with no database connection. Saved screenshots: `.playwright-mcp/memory-wide-detail.png` and `.playwright-mcp/memory-narrow-light.png`.

Codex subsequently ran `npm run build` in the webview package: production build passed, with the existing large graph-scene chunk warning. `git diff --check` passed for the inspector and this report.

Limits: this polish was not installed, no backend restart was performed, and no full test suite was run. Native VS Code themes and live scoped-store integration were not verified in this pass. Source-opening navigation is explicitly unavailable; readable references are not fake links. Landing status remained clean, and the earlier dialog/precision-spec hashes matched their preserved baselines.

## Automatic curation: five grounded entries, mixed durability

The original thread's final curation state was `complete`, `saved=5`, `dropped=0`, `chunks=2`, `failures=0`. The idle check skips active conversations before preparing input, and inactive conversations qualify after thirty minutes or over 100,000 prepared characters (`services/agentd-py/agentd/memory/curator.py:185`, `:202`). No new message reset the original conversation's clock during the polish task.

All five entries cite real user-role source rows, either message sequence 1 or 135. Those rows are **Codex-authored task/preview briefs sent to Crucible**, not independent natural human preference statements. Stored provenance `user_said` reflects the app role and should not be interpreted as proof that Pradeep personally dictated every sentence.

| Extracted claim | Source message | Retrospective judgment |
| --- | ---: | --- |
| Work directly and preserve unrelated existing changes. | 1 | Grounded; its detail explicitly says this is scoped, not permanent. Nevertheless a completed task's no-delegation/work-preservation constraint is weak durable-memory material; prefer thread state. |
| Keep inspector access read-only and scope-isolated. | 1 | Useful conditional feature guidance. It preserves isolation, honest durable-versus-legacy presentation and the absence of mutation controls. It should remain limited to this inspector design. |
| Avoid private data and durable-memory test manipulation. | 1 | Grounded test protocol, not an independently established permanent repository policy. Prefer experiment/thread state. |
| Make inspector previews reproducible across browser sessions. | 135 | Useful conditional preview guidance: real components, disposable harness, automatically loaded fictional fixtures and no database writes. |
| Keep preview support outside committed product paths. | 135 | Grounded but largely a one-task implementation boundary, including the temporary no-reinstall/restart restriction. Prefer thread state or narrower reusable preview guidance. |

Human-review judgment: two conditionally useful entries and three better kept as task/experiment state or narrowed. This is not a labeled accuracy percentage. Positives: no invented implementation-success claims and no permanent visual-preference claim from the explicitly task-specific design brief. Remaining weakness: the curator still saves temporary workflow instructions as repository memories, despite their narrow applicability text. The entries were not automatically retired based on this review.

Read-only source checks:

```sql
SELECT * FROM curation_status WHERE run_id='chat-00aed1058d4b';
SELECT last_mseq FROM curation_progress WHERE run_id='chat-00aed1058d4b';
SELECT content, detail, applies_when, provenance, source_message_id
FROM memories
WHERE valid_to IS NULL AND source_thread_id='chat-00aed1058d4b';
SELECT controller_history_json FROM chat_threads WHERE thread_id='chat-00aed1058d4b';
```

The first two queries use `.crucible/state/memory.sqlite3`, the memory query uses `memory-v2-local.sqlite3`, and source history uses `chat.sqlite3`. Compare memory source IDs with the source history's `mseq` values.

## Compaction: confirmed, with a useful no-tools continuation

The 128,000 window did not yield a saved summary in the observed polish/first-handoff requests, despite the final displayed/measured input reaching roughly 103,000–104,000. After lowering to 96,000, the first continuation's measured input was 74,662; the next continuation emitted `memory_compacted` with `evicted=69`, `anchor_version=1`, and then completed without tool calls.

Do not infer trigger timing from the UI's percentage: ChatGPT's meter uses catalog capacity separately from runtime compaction configuration (`services/agentd-py/agentd/providers/runtime.py:104`). With the source defaults, the trigger formula is `max(window × .65, window − 28000)` (`services/agentd-py/agentd/memory/compactor.py:259`). Image projection happens before compaction and changes can invalidate the previous measured observation (`services/agentd-py/agentd/chat/controller_loop.py:1621`). **Inference, not established root cause:** projection/accounting changes likely contributed to the earlier non-trigger; this run did not isolate the exact 128,000-window cause.

Mechanical evidence:

- Saved anchor at `08:16:23.358866+00:00`, version 1.
- 69 archived source messages, `mseq` 1–69, containing 115,921 raw content characters.
- Summary: 8,518 characters, a **92.65% character reduction for that evicted slice**. This is not an information-retention score or whole-conversation token reduction.
- Consecutive provider-reported main-request inputs: **74,662 → 29,226 tokens**, about **60.86% lower** after compaction. The small user prompts differ, so this is an observed sequential reduction, not an identical-payload benchmark. The earlier 104,250 → 74,662 drop preceded saved compaction and must not be credited entirely to summarization.

The source-backed summary retained the design goal, changed files/helpers, focus restoration, read-only source-navigation limit, actual passed checks, initial test failure and fix, preview isolation, layout/focus/motion observations, and restrictions on algorithms/data/installation. It kept native-editor and live-backend checks explicitly unverified. The anchor's “finish verification/report” next step describes the archived cutoff; later completion and follow-ups remained in hot history, so that is not evidence the current task was falsely left unfinished.

`06-polish-final-check.jsonl` shows the post-compaction answer correctly naming the unavailable source-opening route, the exact 17 tests/four files and TypeScript check, and the lack of installation/native/live-backend verification. It used no tools. This is encouraging evidence for handoff continuity, **not** proof that the summary alone retained every detail: hot history also remained. Visual-pixel retention and a blinded old-versus-new compactor comparison were not tested.

Read-only checks:

```sql
SELECT version, summary_md, updated_at FROM anchored_summaries
WHERE run_id='chat-bd041ab8458a';
SELECT seq, mseq, content FROM compaction_segments
WHERE run_id='chat-bd041ab8458a' ORDER BY seq;
```

## Recall: relevant candidates found, all rejected again

After curation completed and the normal window was restored, probe 07 asked for saved guidance for a future inspector preview, specifically reproducible sample data and active-store isolation, with no tools or edits. This was a **continuation**, not a clean fresh-chat benchmark; the trace still establishes whether durable memories were delivered.

The live `combined_rcq_body_v1` rule retrieved all five new memories via keyword and semantic search. Reranking used recap → previous substantive answer → current request and claim plus detail. Recap/previous answer were shortened, current request was intact, no memory body was shortened, and the maximum pair length was 304 tokens. There was no reranker error or delivery invalidation. The cutoff remains **0.65** (`services/agentd-py/agentd/memory/recall_v2.py:22`).

| Candidate | Combined score | Delivered |
| --- | ---: | --- |
| Reproducible inspector previews | 0.009824 | No |
| Read-only, scope-isolated access | 0.007106 | No |
| Preview outside product paths | 0.000331 | No |
| Avoid private data/test manipulation | 0.000134 | No |
| Work directly/preserve existing changes | 0.000125 | No |

The first two are directly relevant to the request. Trace status was `empty`, judge `reranker`, delivered list empty. The answer truthfully acknowledged that no saved entries were supplied and drew qualified guidance from the task handoff instead. That answer is not evidence of successful durable-memory recall. This reproduces a broad “saved guidance” query weakness under the new rule; it does not undo prior measured improvements on other requests, but it prevents claiming recall is now reliable or a live success here. No cutoff/model/rule change was made in response.

## Assessment

Compaction produced a useful, bounded handoff and supported a correct no-tools continuation. Curation ran automatically at the expected idle opportunity and saved grounded content, but its durability filter remains too permissive. Recall's candidate search worked; its relevance gate still rejected clear positives. The next memory improvement should address that calibrated selection failure and transient-instruction retention, with labeled positives/negatives—not treat successful execution or attractive inspector output as proof of memory quality.
