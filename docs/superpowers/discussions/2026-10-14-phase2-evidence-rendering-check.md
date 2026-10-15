# Phase 2 evidence rendering and budget check

Date: 2026-10-14. Implementation: Codex. No hosted model calls or backend restart.

The implemented renderer cuts the sampled typed controller histories by approximately
**70.1%**, including its added source/tool/range/status headers. It does not achieve that
reduction on old untyped segments. The first-300-character file excerpt loses many literal
declaration names, so the new compaction path remains independently default-off pending
summary-quality and resumed-task evaluation.

## Sources and reproducible procedure

The evaluator is `services/agentd-py/agentd/eval/memory_evidence.py`. Both SQLite connections use
`mode=ro` and `PRAGMA query_only=ON`; no raw source text is exported. The report stores aggregate
metrics, case IDs, source-row digests, budget estimates and limitations:
`.crucible/state/eval/memory-evidence-phase2-2026-10-14/report.json`.

Queries:

```sql
SELECT thread_id,controller_history_json FROM chat_threads ORDER BY thread_id;
SELECT * FROM compaction_segments ORDER BY run_id,seq;
```

The first query reads `.crucible/state/chat.sqlite3`; the second reads the frozen legacy store at
`.crucible/state/eval/memory-baseline-2026-10-14/memory.sqlite3`. Segment cases group a run's rows
by their eviction timestamp. The current histories are a snapshot of retained controller
messages, not a complete transcript of every prior compaction.

Reproduction from `services/agentd-py`:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m agentd.eval.memory_evidence \
  --chat-db ../../.crucible/state/chat.sqlite3 \
  --segments-db ../../.crucible/state/eval/memory-baseline-2026-10-14/memory.sqlite3 \
  --output ../../.crucible/state/eval/memory-evidence-phase2-2026-10-14/report.json
```

## Results

Raw counts approximate the previous role/content rendering, using eight separator characters
per message. Rendered counts include omission markers and evidence headers.

| Corpus | Cases | Source rows | Raw characters, approximately | Rendered characters | Reduction |
|---|---:|---:|---:|---:|---:|
| Retained controller histories | 69 | 1,967 | 3,997,215 | 1,195,760 | 70.09% |
| Frozen legacy evicted segments | 66 | 4,474 | 9,702,208 | 8,094,378 | 16.57% |

All frozen segments lack role/tool/source sequence columns. The renderer labels them unknown
and applies the general 8,000-character text cap rather than inventing file/command types.
Thirty summary-prefixed rows are excluded. This explains why the earlier approximate
`9.7M → 2.9M` cap analysis is not the reduction this implementation obtains from that old store.
Newly evicted segments preserve explicit types and call IDs.

The file-read diagnostic finds 552 distinct declaration names counted separately per read;
273 remain literally present somewhere in their case's rendered text. This is an upper-bound
name-retention diagnostic, since a name can occur in another message; it is not a gold set of
necessary facts or a semantic coverage score. It shows a real information tradeoff in the
300-character cap and does not justify enabling it on its own.

## Folding and failure checks

Offline budget replays use a fixed mock anchor, not an LLM summary. No sampled case contains an
indivisible turn that exceeds its allowed budget.

| Corpus | Window | Maximum calls per case | Maximum estimated input tokens per call |
|---|---:|---:|---:|
| Controller history | 128k / 272k / 1M | 1 | 90,676 |
| Legacy segments | 128k | 2 | 99,862 |
| Legacy segments | 272k / 1M | 1 | 187,805 |

Focused tests additionally exercise an 800k-character typed input split across two target-sized
calls, an indivisible batch above the limit, a prior anchor that consumes the budget, a later
fold failing after a successful commit, an oversized summary response, a window reduction
between folds, repeated compaction/source identity, and image tombstone persistence.

The production factory counts the summary system prompt, JSON-escaped transcript, prior anchor
and 2,048 estimated tokens of transport headroom. It uses the existing conservative
three-character estimator, targets 200k, and applies both the estimated 500k hard ceiling and
the configured context window's response reserve. A single complete turn may exceed the
200k target if it fits the hard/context limit; otherwise it is rejected before a model call.
These are estimated counts, not a guarantee about an arbitrary provider's tokenizer.

## Handoff and limitations

`CRUCIBLE_MEMORY_COMPACT_EVIDENCE=1` selects the new path independently of
`CRUCIBLE_MEMORY_RECALL_V2`; its default is false. Triggers, hot-message selection and active
skill/project-instruction injection retain their existing behavior. Evicted skill/AGENTS
bodies are omitted from summary input, and their successful reads retain reload stubs. Raw
segments retain the original bodies.

563 regression tests passed, with ruff and strict mypy checks on the six phase 2 modules.
The unchanged legacy pipeline is included in that regression run. The hosted spike's reference
scores do not transfer automatically to this renderer. LLM summary coverage, tokenizer usage
and successful resumed work remain unverified for the new path; retain the rollout gate.
