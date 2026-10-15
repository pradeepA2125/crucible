# Compaction evidence spike — resumed from Claude Code

Date: 2026-10-14. Reviewer: Codex.

Code-path citations below are relative to `services/agentd-py/agentd/`.

**Result: retain tool observations. Thoughts, signatures and pass/fail did not preserve as much
resume information as output-bearing input.** Removing outputs saved 69.8% of rendered text
against the captured-output baseline, but missed 60 of 184 scored reference facts, compared
with 20 for capped outputs. Adding thoughts to capped outputs did not improve aggregate
coverage in this run. The current caps also need further validation before production use.

This completes the experiment Pradeep approved immediately after pausing implementation in
Claude's latest transcript. It does not implement the memory redesign or satisfy its separate
real-compaction, curator and recall rollout gates.

## Starting point and provider

The source conversation is
`~/.claude/projects/-Users-pradeepkumar-projects-AI-editor/b807b420-7687-4d4d-acb2-ca4d7d88b037.jsonl`:
line 1078 pauses implementation to test removing tool output; line 1113 describes the experiment;
line 1117 approves it; line 1157 records Claude's usage-limit stop. The current design is
`docs/superpowers/specs/2026-10-14-memory-recall-redesign-design.md`, not the superseded catalog
proposal in the shared dialog.

Claude's NIM process was still running when inspected. Its preserved partial checkpoint has
20 reference lists, 20 A-summary entries and 12 A-scoring entries, but no B/C/D scores. Two
A-summary entries are timeout error strings. Those strings are not summaries and were not
included in this comparison. The isolated NIM spike process was stopped; the live editor
backend was left running.

A read-only check of the live backend's ChatGPT registrations/model catalog followed by
`POST /v1/providers/validate` passed for `gpt-6-luna` in 3.84 seconds. This uses a throwaway
transport rather than changing the live provider (`api/routes.py:378`,
`providers/factory.py:276`). The final health/config check returned `ok` and
`claude_subscription` / `claude-sonnet-5-5`. The experiment used a separate ChatGPT-plan
transport, `gpt-6-luna`, at low reasoning effort.

After automatic approval review rejected uploading project-session content, Pradeep explicitly
approved sending the redacted 20 samples. All variants used the same sanitized source. Four
environment/profile outputs were omitted; three emails, 77 home-path occurrences and two
credential-like fields were masked. The scan found no matching known secret values, API tokens,
JWTs or bearer tokens. This is a description of the scan, not a proof that every possible
secret representation was detected.

## Corpus and controls

The frozen corpus contains **20 spans from 12 threads, with 275 tool calls**. Every selected
span contains at least one tool error flag. Claude's sampler used seed 14, at most two spans
per thread, and preferred spans containing errors. It cut spans on UI-item boundaries with
at least eight tools and approximately 30–90k characters in its original FULL rendering.
Thus these are not 20 distinct threads or 20 observed production compaction rounds.

Source access was read-only:

```python
sqlite3.connect("file:<repo>/.crucible/state/chat.sqlite3?mode=ro", uri=True)
```

```sql
SELECT thread_id, messages_json FROM chat_threads;
```

UI `metadata.tool_events` supply tool name, arguments, thought, captured output and `isError`;
diff cards supply edits. User and agent messages also remain in the input. Captured outputs
are already capped upstream, commonly around 4,000 characters. A is consequently a
**captured-output baseline**, not all the raw evidence production compaction currently sees
(`memory/compactor.py:75`, `memory/compactor.py:240`, `memory/compactor.py:262`).

Claude's original C capped arguments at 300 characters while A used 4,000, and removed edit
diffs that other variants retained. This continuation removes those confounds: all variants
share the same 4,000-character head/tail argument cap, 1,500-character head/tail edit excerpt,
and 8,000-character user/agent-text cap. Only thought and tool-output inclusion differ.

| Variant | Tool evidence |
|---|---|
| A | Captured outputs, without thoughts |
| B | Per-type capped outputs, without thoughts |
| C | Thoughts + signatures + `performed <tool> — pass/fail`; no outputs |
| D | The same capped outputs as B, plus the same thoughts as C |

B/D retain the first 300 characters of file reads, command head 800 + tail 1,200, and other
output head 1,000 + tail 1,000. They also retain file-call arguments and omission markers.
This approximates the proposed caps, rather than implementing the complete evidence renderer:
shared edits use head/tail rather than the spec's first-1,500 rule, and skill/memory-body
exclusions and real `delivered_read` metadata were not reconstructed. The source corpus and
action/text rules are shared; C removes every output body, including any skill bodies.

Every summary uses the unchanged production `_SUMMARY_SYSTEM` and `make_engine_summarizer`
(`memory/harness.py:24`, `memory/harness.py:322`), with an empty prior anchor. There is one
summary per variant per span, without output-based prompt tuning. Folding, repeated compaction
and actual resumed-task success were not tested.

## Reference checking and scoring

Claude's NIM-generated lists contain 256 proposed reference facts. Source validation on
ChatGPT retained 189; five further source-based manual exclusions leave **184 facts** used
identically across all four variants. Five of the initial proposals were unverified because
their claimed source quotes still could not be located after a retry; they are excluded,
not treated as false facts. Other exclusions include unsupported causes, invented next steps,
incorrect counts and claims of unfinished code inferred from a capture cutoff.

These are model-validated reference facts with manual spot checks, not a certified gold set.
Source-quote presence is checked mechanically; semantic entailment remains a model/reviewer
judgment. Many references combine several details, making full coverage a demanding measure.

An initial scorer sometimes paraphrased evidence instead of quoting it. Its incomplete scores
are retained for audit and superseded. **All 80 final summaries were scored uniformly** with
the same full/partial/missing criteria using indexed summary paragraphs. The scorer selects
an existing paragraph ID; Python copies its exact text and checks index coverage. Failed
model calls cannot become summary text or receive a score. Final checks passed for all 20
cases, all 80 summaries and all 80 paired verdict lists.

## Results

Counts below share the same denominator of 184 reference facts. Partial means that some
actionable detail survives while other specifics are absent; it is not equivalent to full
coverage or a successful resume.

| Variant | Input characters | Reduction vs A | Full | Partial | Missing |
|---|---:|---:|---:|---:|---:|
| A | 579,596 | — | 57 (31.0%) | 107 (58.2%) | 20 (10.9%) |
| B | 361,344 | 37.7% | 52 (28.3%) | 112 (60.9%) | 20 (10.9%) |
| C | 175,063 | 69.8% | 26 (14.1%) | 98 (53.3%) | 60 (32.6%) |
| D | 400,643 | 30.9% | 38 (20.7%) | 110 (59.8%) | 36 (19.6%) |

The comparison that isolates output removal is **C versus D**, since both contain identical
thoughts, signatures, edits and messages. C is worse on 46 facts, better on 17 and equal on
121 using `missing < partial < full`. Against the proposed capped-output policy B, C is worse
on 65, better on 15 and equal on 104. This supports retaining observations; it does not show
that every tool output is necessary.

B and A miss the same number of facts, but not necessarily the same facts. B fully preserves
five fewer. D does not beat B in aggregate, so this run does not justify adding thoughts to
compaction by default. Model variation and summarizer triage are possible explanations, but
the cause was not established. Single samples, correlated spans from the same threads and
model-generated labels preclude an equivalence or statistical-significance claim.

There were 275 logged ChatGPT model-call completions across the resumed experiment, including
reference checks, retries and superseded scoring. They include 80 summary calls, whose median
latency was 15.04 seconds; the final 80 scoring calls had median latency 10.2 seconds. Logged
transport calls did not fail. Token/quota consumption was **not measured**: the available
usage records contain zero token counts, which must not be read as zero-token inference.
The throwaway process emitted asynchronous-stream cleanup warnings on shutdown; their root
cause and effect on a long-lived client are unverified. The live backend remained healthy.

## Manual checks and concrete losses

Five spans were spot-checked, with one additional installer check. Keys are `thread:start`
in the frozen sample; item numbers address that span's `items` array.

| Span | Source anchors | Finding |
|---|---|---|
| `chat-fd243a51d06b:363` | items 1, 3, 7, 11 | The captured test edit has six tests, not three. Truncation does not prove unfinished implementation. C loses the literal module-import error that D retains. Both summaries still overstate what that import failure proves about file existence. |
| `chat-8fa48bcdfff2:0` | items 0, 2, 3, 12 | C retains `interrupted_history` behavior because the existing closing answer already states it. This is a positive case for keeping closing text. It loses the duplicate-block reason for a failed search. |
| `chat-679ae719062e:15` | items 0, 1, 2, 11, 12 | Read outputs contain description-bearing tool constructors. C preserves navigation but explicitly says read results were not preserved and asks for rereads. B/D retain observations including MCP-manager delegation. |
| `chat-83f2a7693142:29` | items 8–10, 13 | The captured Exa mode table gives a keyless endpoint, free rate-limited usage and authentication alternatives. C does not preserve those observations. D keeps some setup information but also loses exact endpoint/rate-limit details. Current external service behavior was not independently reverified for this spike. |
| `chat-a372790a8a47:0` | items 2–4, 12, 13 | C keeps the 337-commit count repeated in a later thought but loses the failed git date option and awk/Python errors D retains. The awk result has `error=False` despite a syntax error in its output: pass/fail alone is insufficient. The commit stat says 13 changed files total, not 13 added media assets. |

The additional installer span, `chat-f5ce24caf194:179`, item 18, tells the user to set
`crucible.devSourcePath` and reinstall the existing runtime. C omits those instructions; D
preserves them. The output instructs a settings change rather than proving the script made it.
A successful captured command does not verify later editor/runtime behavior.

## Implementation consequence

1. Do not replace all compaction tool outputs with pass/fail placeholders. Preserve observations
   that closing text and thoughts have not recorded, especially errors, discovered values and
   external-document constraints.
2. Treat the proposed caps as provisional. Tune and test them against actual controller history
   and raw segments, including the existing five-real-compaction-round check. The 37.7% saving
   here does not validate or overturn the design's earlier 70% size measurement on a different
   raw-segment corpus.
3. Preserve thoughts privately for the planned evidence/curator work, but decide compaction's
   use of them from its own evaluation. `reasoning/react_common.py:24` currently removes them.
   Private metadata also needs an explicit provider projection: controller payload construction
   currently passes history dictionaries through (`chat/controller_prompts.py:1282`), so an
   underscore-prefixed field alone would still reach JSON transports.
4. Keep raw segments intact before summarizing, as today (`memory/compactor.py:240`). Curator
   quality and default recall rollout remain separate gates. The next phase's store/scope plan
   is `docs/superpowers/plans/2026-10-14-memory-redesign-phase1-store-scopes.md`.

## Local evidence archive

The redacted corpus, both scoring passes, source-validation records, final statistics, manual
review, production prompt snapshot and replay scripts are in
`.crucible/state/eval/memory-compaction-2026-10-14/` (git-ignored). `indexed-results.json` and
`indexed-statistics.json` are authoritative; `results.json` is the superseded scoring audit.
`sample-redacted.json` and `manual-review.json` support the item references above.

Product code, configuration, tests and production SQLite databases were not changed. The
earlier shared-dialog sections and superseded spec remain unchanged.
