# Repeated tool-call investigation — 11 October 2026

## Established findings

The three performance switches are tool batching, adaptive reads, and compact context.
Turning all three off does **not** restore main's controller protocol. The array-only
migration remains active: the prompt teaches `tool_calls`, the provider exposes that
function, and the controller schedules even a one-member array through the new path.
The switch changes `calls.maxItems` from eight to one. This is consistent with the
previous request to remove the separate singleton action, but means the switches
cannot be used as a complete branch rollback or a clean main-equivalence experiment.

An offline comparison of the actual main schema and this checkout's schemas is saved
in [controller-schema-comparison-2026-10-11.json](controller-schema-comparison-2026-10-11.json).

| Native contract | Main | Current, batching off | Current, batching on |
|---|---|---|---|
| Workspace action | `tool_call` | `tool_calls` | `tool_calls` |
| Fields | thought, tool, args | thought, calls array | thought, calls array |
| Maximum array members | — | 1 | 8 |
| Native tool choice | required | required | required |
| Terminal answer exposed | yes | yes | yes |

`tool_choice: required` already exists on working main. It does not by itself require
a workspace tool: the model can call `answer`. The regression cannot responsibly be
attributed to that setting alone.

Saved failing captures show successful recall results and growing history, rather
than results being dropped and a controller retry automatically re-executing recall.
The model chooses successive unnecessary tools, with thoughts describing checks
"before greeting." The latest array-only failing editor capture had seven completed
tool actions with no batch parser corrections and no greeting after about 69 seconds.
Its raw trace is `.crucible/state/debug/array-only-hello-2026-10-10.json`.

There are also automatic memory-harness recall traces for inference preparation.
Those are distinct from model-requested `recall` tool calls and must not be counted
as additional visible tool executions.

The controller permits 500 iterations by default. Valid tool actions reset the
malformed-response counter before tool execution; duplicate-result blocks therefore
do not provide a semantic no-progress stopping rule. Varying recall queries also
evade exact-argument deduplication. These explain why the behavior can continue for
minutes, but neither establishes the initial regression trigger.

## Controlled replays

The diagnostic runner defaults to offline preparation. Its three variants have
identical input items and differ in the native response contract or descriptions:

1. Current contract, all performance flags off.
2. One forced response function containing the complete action union.
3. Current contract with explicit descriptions for the answer and workspace actions.

The source context is the previous fresh array-only hello capture. Each live request
uses a fresh transport; no returned workspace tools execute. This is a step-one
comparison, not a full controller-loop or installed-editor test.

Account selection was checked against the live Crucible settings. VS Code's persisted
state database can lag current selection, so reading that database alone is insufficient.
The initially selected Gmail account reached its Subscription Sharing limit. Four
successful Luna outputs before that limit included one unchanged-contract tool call
whose thought said a repository inspection was unnecessary but a first workspace
action was required. This supports an intermittent model interpretation problem;
it is not proof of an infinite loop.

After the user selected the Yahoo account, all six probes succeeded:

| GPT-5.6-Luna, Medium, Yahoo account | Trial 1 | Trial 2 |
|---|---|---|
| Current contract, switches off | answer | answer |
| Response envelope | answer | answer |
| Explicit descriptions | answer | answer |

See [controller-contract-probe-2026-10-11.json](controller-contract-probe-2026-10-11.json).
The unchanged contract also passed, so these samples do **not** establish an envelope
or description fix. The earlier failures used GPT-5.6-Sol/Terra; model, account, prompt,
and context must be held constant before attributing a change in behavior to a repair.

## Repair and validation

106 focused local tests passed: native adapter/transport, batching, memory wiring,
controller exploration/answer, resilience, and report validation. These use scripted
responses and verify plumbing, not the live model's choice to terminate.

The installed Yahoo/Luna editor hello completed after five unnecessary tools
(`read_env_profile`, `list_directory`, three `recall` calls), then an answer. It used
six provider requests and 311,446 input tokens, including 252,032 cached tokens.
The UI was still working at 53 seconds and subsequently displayed the greeting.
The capture is
`.crucible/state/artifacts/chat/chat-36d3070c0cbe/78269f45653644729a74ca2b84dc2dd7`.
Its schema had `maxItems: 1`; batch guidance was absent, the catalog was uncompressed,
and the seed retained its full tree. The unnecessary calls therefore reproduced with
the conservative settings.

The first harness omitted the production current-step label/session header and moved
the batching-off note to the end of the system prompt. An exact replay of the new live
capture corrected these differences. Results in
[controller-contract-exact-probe-2026-10-11.json](controller-contract-exact-probe-2026-10-11.json):
the original descriptions produced one answer and one unnecessary workspace call;
the response envelope and explicit descriptions each produced two immediate answers.

A candidate repair now gives `answer` and `tool_calls` explicit native descriptions
of when to finish and when workspace evidence is needed, replacing their generic
"Take the action" descriptions. This is the smaller of the two promising changes.
It preserves schema-specific descriptions, all existing function schemas, array
execution, permissions, and required native calling. Two adapter tests check the
description contract and preservation of caller-provided guidance. Existing
uncommitted work was preserved. The backend was restarted for live validation.

After the backend restart, the installed editor loaded the new descriptions, as
confirmed by its saved native provider request. The same selected Yahoo plan and
GPT-5.6-Luna with Medium reasoning were used for the following completed tests:

| Installed-editor request | Before descriptions | After descriptions |
|---|---|---|
| Fresh `hello` | Five workspace tools, then answer; six requests | Direct answer; one request, no workspace tools |
| Read `batch_history.py` and explain `interrupted_history` | Not measured | Search, read, confirmation search, then correct answer; four requests |

The greeting used 49,277 input tokens after the repair versus 311,446 across the
six requests before it. The file explanation followed the greeting in the same
thread, successfully received the complete 19-line file, and correctly explained
which unfinished batch entries become interrupted. Its final confirmation search
was redundant, so this result establishes termination and preserved file reading,
not optimal tool efficiency. Neither test reached the eight-action stop bound;
both finished normally and were verified in the UI and saved traces.

Sanitized action sequences and editor usage totals are saved in
[controller-live-validation-2026-10-11.json](controller-live-validation-2026-10-11.json).
The post-repair greeting capture is
`.crucible/state/artifacts/chat/chat-f3c2dff2ec98/33dc157b1be94396bc45825596fd123c`;
the file-explanation capture is the same thread's
`66c47dca8f854427b2848900d20e8cdb` directory.

The evidence supports a native action-selection ambiguity: the model sometimes
treats a required controller function as requiring a workspace tool before it can
answer, even when its own thought recognizes that repository work is unnecessary.
The descriptions address that interpretation directly. Successful tool outputs
were already reaching the model; no history-delivery repair was made.

This is a tested mitigation with a successful live before/after pair, not proof of
the exact migration commit that introduced the behavior or a guarantee across
models. The unchanged contract also answered some replays. Sol/Terra and every
performance-setting combination were not retested. A future causal comparison
should keep model, account, and captured context fixed across main and this branch.
A guard against repeated no-progress actions would bound damage but is separate
from this repair and was not added.

## Batching enabled: follow-up live test

At the user's request, tool batching was enabled in VS Code settings and the backend
restarted. Adaptive reads and compact context remained off. The first installed-editor
capture confirmed `calls.maxItems: 8`. The same Yahoo/Luna/Medium configuration was used.

- A fresh `hello` answered directly in one request with no workspace tools.
- A follow-up explicitly requested two independent `read_file` calls in one batch:
  all of `batch_history.py` and lines 1–45 of `tools/batching.py`. The model emitted
  both members in a single `tool_calls` action with `execution: parallel`. Both
  executed successfully and their results, with the same batch identity, appeared
  in the next model request.
- The model then made four extra checks: one narrow repeat read and three graph
  queries. It finally answered correctly on action six. Six workspace calls ran in
  total, because the first action contained two members. The eight-action bound was
  not reached and the final answer was verified in the editor.

This validates batch execution, delivery of both results, and eventual termination
for this request. It also shows that the description patch does **not** fully remove
unnecessary tool use. The test does not validate dependency scheduling, commands,
edits, or other models. Batching remains enabled at the user's request.

Evidence is saved in
[controller-batching-live-validation-2026-10-11.json](controller-batching-live-validation-2026-10-11.json).
The thread is `.crucible/state/artifacts/chat/chat-1c208b265699`; its greeting turn is
`2fb0d9bcfc1949f5807e45429bf016ce` and file-read turn is
`c0c69cd33a574c51b40134fc22fa3820`.

## Schema-owned action descriptions

Following the user's request, all eight action descriptions now live alongside the
action fields in `controller_prompts.py`'s `_VARIANT_SPECS`. Union schemas include
each description on its branch. Flat schemas include only the allowed actions'
descriptions on the `type` selector. The native adapter transfers the branch
description to the function without an action-specific description mapping or
duplicating it in the function's parameter schema. The validated `answer` and
`tool_calls` wording was retained.

Workspace tools already carry separate descriptions in their catalog, which is
embedded in the system prompt. `read_file`, `search_code`, and `recall` are members
selected inside the `tool_calls` action, rather than separate native controller
functions. This change does not alter that tool-catalog path.

224 focused tests passed across controller schemas, native and other provider
transports, batching, loop behavior, strict-schema encoding, and prompt goldens.
Ruff and `git diff --check` passed. Earlier live results above refer to the previous
two-action adapter mapping; live validation after restarting with the schema-owned
descriptions is recorded below.

## Adaptive reads and updated catalog guidance: follow-up live test

At the user's request, adaptive reads was enabled alongside batching and the
backend restarted with schema-owned descriptions, including the new `tool_calls`
sentence directing selection and arguments to the available tool catalog. Compact
context remained off; the model was still Yahoo/Luna/Medium.

The saved outbound native requests confirmed that the complete `tool_calls`
description reached the function definition. All six exposed Active-mode function
descriptions matched their schema branches. The new catalog sentence was present
in the greeting and file-test requests. Adaptive guidance for direct reads of known
paths and the tool catalog's 2,000-line ceiling were also present. Descriptions are
function-definition metadata, not fields in the model's returned call arguments.

The fresh greeting answered in one request without tools. A follow-up requested a
batch of two independent reads: `controller_prompts.py` lines 1–700 and the complete
`batch_history.py`. Both succeeded in one action and their results were delivered
to the next request. Delivery metadata confirmed all 700 requested lines, exceeding
the old 500-line limit, and all 19 lines of the second file, without truncation.

The model then made three redundant smaller reads of regions already visible in
the 700-line result and answered on model action five. Both tests finished normally
within the eight-action bound and were verified in the editor. This confirms larger
read delivery and description transmission, but unnecessary tool selection remains.
The explanation correctly identified the functions; its additional claim that the
action description is in the prompt should not be treated as evidence of placement.
The actual request establishes schema/function metadata and separate prompt guidance.

Action descriptions and the adaptive setting changed together, and the prompt
explicitly requested batching and the larger range. These samples do not isolate
causality, test autonomous sizing, or establish a latency improvement. Evidence is
saved in
[controller-adaptive-live-validation-2026-10-11.json](controller-adaptive-live-validation-2026-10-11.json).
The thread is `.crucible/state/artifacts/chat/chat-e4e29d7b1d1a`; greeting turn
`62818e0384dd41b8a0c5628222f76678`, read turn
`a80a41f537b94d9c8cda2e5b310bea23`.

Offline preparation:

```sh
services/agentd-py/.venv/bin/python scripts/probe-controller-contract.py
```

For an authorized live replay, add `--live --registration <selected-registration-id>`
and `--model <selected-model>`. Pass `--artifact <fresh-controller-turn-00.json>` to
reuse the captured schema and prompt unchanged; the default older-context mode
rebuilds a batching-off schema and appends its guidance, so is not an exact replay.
The runner stops immediately on an account/access limit and caps the comparison
at six requests. Returned tools never execute.

## Repeat-read instruction analysis

The adaptive trial's three extra reads were not recovering missing results. Every
subsequent request retained the complete original 1–700 result. Each smaller result
matched the corresponding original lines exactly, with the same file revision.
The original result's delivery metadata explicitly reported no truncation.

The closest instruction-to-rationale match is the core rule, "do not re-issue an
IDENTICAL call whose result you already have." The model acknowledged that the
files were already read, then described its first repeat as a "minimal non-duplicate
read" to ground the explanation. Changing the range makes the arguments different,
but adds no evidence when that range is already delivered. The next two actions
were framed as confirming schema surroundings and confirming the final delivered
lines. The exact-argument duplicate guard also permits these narrower requests.
The prompt already contains a stop-exploring rule; the problem is not its absence.

A separate concrete contradiction occurs in the dynamic instruction. All five
requests, including those after successful tool results, say "This is your FIRST
action and nothing is started yet." Their history grows and step counter advances
correctly. `controller_loop.py` computes `active_entry` from absence of todos,
applied edits, edit resumption, and delegation; successful exploration does not
clear it. `controller_prompts.py` renders that state as first-action wording. The
same condition exists on main, so this is a misleading instruction, not established
as the batching regression's origin. An iteration gate alone would discard the
deliberate empty-edit recovery behavior; distinguish first action from pre-edit
continuation instead.

The `query_graph` catalog description also says "After read_file shows you a
function, use query_graph" without conditioning this on a need for relationships.
That is a candidate contributor to the earlier trial's extra graph queries, rather
than an explanation for this trial's three read-only repetitions.

Recommended prompt changes: define rereading in terms of already-delivered coverage
and unchanged revision; tell the model to reuse successful visible evidence and
read only missing portions; distinguish first-action and exploration-continuation
hints; make graph guidance conditional on the question needing callers/callees or
inheritance. No runtime prompt change or additional model request was made for this
analysis. These are concrete candidates; causality still requires a controlled
comparison. Machine-readable evidence is saved in
[controller-repeat-read-analysis-2026-10-11.json](controller-repeat-read-analysis-2026-10-11.json).

## Pre-edit continuation hint repair

After approval, the pre-edit hint now uses the current turn's iteration to distinguish
first action from continuation. Only iteration zero receives "FIRST action". Later
iterations retain todo/direct-edit and empty-edit recovery guidance, while explicitly
directing reuse of successful visible results, reads of missing evidence only, and an
answer when the existing results suffice. `active_entry` calculation and the history
fallback remain unchanged; prior conversation cannot suppress a fresh turn's entry
wording. Delegation and notice branches retain their precedence.

An offline replay of all five captured adaptive-trial histories confirmed first-action
wording only at step zero, continuation guidance at steps one through four, and unchanged
history delivery. No model request was sent. The running backend has not been restarted
for this repair, so these results do not establish changed live model behavior.

Validation: 69 focused tests passed, covering entry/continuation prompts, empty-edit
recovery, payloads, delegation, notices, progress, and exploration/answer behavior.
Ruff and `git diff --check` passed.

## User follow-up: explaining repeats caused more repeats

The next inspected turn in the same thread is
`c12989c5c74a41188ec90cfb0d1122f3`. The user asked why the model reread covered
regions and which prompt text may have caused it. It made three read attempts and
eight search attempts, including one duplicate-blocked search, then answered on
model action twelve. The entire original 1–700 result remained visible throughout;
all three new read results matched its corresponding lines and revision exactly.

The recorded rationales repeatedly acknowledged visible evidence while requesting
another grounding check. One narrower read was framed as avoiding a repeat of the
whole 1–700 range; later rationales varied searches to avoid duplication. This
strengthens the argument-equality loophole finding, rather than indicating missing
tool delivery. The blocked exact search was followed by a different search, so the
guard alone did not terminate exploration.

Every request still contained the old first-action instruction. Capture timestamps
place this turn before the continuation-hint repair. It is not a post-repair test.

An additional instruction conflict is "Cite only files/symbols you have READ this
turn" (`controller_prompts.py:380`). Previous-turn results are fully replayed, but
this wording can require a fresh read merely because the user sent a follow-up.
It does not explain the continued repeats after the relevant code has also been
read in the current turn. Prefer grounding claims in actual code still visible in
successful tool results or verbatim excerpts, including prior turns, with new reads
when missing evidence or freshness requirements warrant them.

The final reply correctly admits unnecessary confirmation and cites static
grounding/stop guidance, but does not identify the dynamic first-action hint. Its
repository reads stayed inside the first 700 lines, while that hint's builder is
beyond line 1200. Its suggestion that one JSON object per step encouraged more reads
is speculative: an answer is a valid action. Furthermore, replayed assistant history
deliberately omits original `thought` fields, so its retrospective explanation does
not directly inspect the previous rationales. That stripping also exists on main;
do not infer that it introduced the regression or restore verbatim thoughts on this
evidence alone.

A secondary tool issue appears at step three: a newline-containing regex failed
in ripgrep without multiline mode, but `search_code` classified its stderr response
as successful "No matches found." This obscures a search failure but does not explain
why already-delivered code was repeatedly checked.

No production code or model requests were changed for this analysis. Structured
evidence is in
[controller-followup-analysis-2026-10-11.json](controller-followup-analysis-2026-10-11.json).

## Citation rule repair

After approval, the core citation rule now accepts actual code still visible in
successful tool results, including earlier turns, and retains main-agent verbatim
seed excerpts as evidence. It explicitly directs reuse of visible evidence and
limits rereads to missing/truncated code or necessary freshness checks. Bare names
and outlines remain insufficient for code-specific claims. The change applies to
main and child prompt renderings. Only the matching citation blocks in all three
existing prompt golden files were updated; their other contents were preserved.

The running backend has not been restarted and no new live model requests were
sent for this change.

Validation: 149 focused tests passed across prompt goldens, tag rendering, child
prompts, prompt-leak checks, and entry/continuation guidance. Ruff and
`git diff --check` passed.

## Live validation after both prompt repairs

The backend was restarted through Crucible settings. At the user's request the
tests ran in a fresh chat, `chat-19aa3da0687e`, using Luna/Medium with batching and
adaptive-read guidance present. Both native captures contained the new citation
rule; all six exposed native action descriptions matched the response-schema
branches. First-action wording appeared only at iteration zero, and later requests
received the new continuation hint.

The same two-file/700-line prompt completed in eight model actions. The fresh chat
had a smaller character budget: successive delivered ranges were 1–128, 129–379,
380–570, and 571–700. The model correctly recovered missing portions without
overlapping them. This actual truncation differs from the earlier full-700-line
delivery, so total action counts are not a clean before/after comparison. Once all
700 lines were visible, however, it made three unnecessary checks: a graph query,
a failed fixed-string search, and a repeat read of lines 238–250, then answered.

The graph query targeted `_VARIANT_SPECS`, which had no graph node. Its result told
the model to confirm the path/symbol with `search_code` and retry. The next search
used escaped quotes with `fixed_strings: true`, found nothing, and was followed by
the redundant narrow read. Together with the catalog's imperative "After read_file
shows you a function, use query_graph", this supplies a plausible remaining
instruction chain. It does not prove that catalog wording initiated the detour.

A follow-up asked which prompt text governs evidence reuse and when rereading is
necessary. It made one read of lines 375–389, then answered on action two. The result
was identical to code still visible from the previous turn, with the same revision,
so that read was unnecessary. No extended follow-up loop occurred. Both tests ended
normally at or below the eight-action bound and their answers were verified in VS
Code. The changes therefore load correctly, but redundant tool selection persists;
this sample does not establish causal improvement.

Structured evidence, native-description checks, delivered ranges, rationales, and
usage observations are saved in
[controller-prompt-repair-live-validation-2026-10-11.json](controller-prompt-repair-live-validation-2026-10-11.json).

## Grounding section rewrite

At the user's request, the full grounding section was rephrased around visible
evidence rather than unconditional read commands. It now starts with reusing actual
code already visible, including prior-turn results, and states that a new message
does not require a new read. Searches are conditional on missing evidence and an
unknown location; known paths can be read directly. Graph queries are tied to a
need for symbol relationships. Truncation recovery requests only missing portions,
and different arguments do not make already-established evidence new. Freshness
checks require a task-dependent reason to suspect the file changed. Outlines and
bare names remain insufficient for describing unseen behavior.

The adaptive-read text replacement was updated to match the new sizing sentence;
main and child renderings were checked with adaptive reads both on and off. Only
the corresponding grounding blocks in the existing prompt goldens were replaced.
168 focused tests passed across goldens, tag rendering, child prompts, prompt-leak
checks, entry/continuation guidance, and web guidance. Ruff and `git diff --check`
passed. No live model request or backend restart was performed for this rewrite;
its behavioral effect remains untested.

The follow-up wording review removed the arbitrary 150-line sizing threshold and
the formatter's separate adaptive sizing sentence. Both modes now say, "When a read
is needed, request the missing function or region within the tool's limits." The
catalog continues to advertise the existing 500-line conservative and 2,000-line
adaptive limits. Main/child renderings were checked in both modes; 149 focused tests,
Ruff, and `git diff --check` passed. This update has not been loaded into the running
backend or tested against a live model.
