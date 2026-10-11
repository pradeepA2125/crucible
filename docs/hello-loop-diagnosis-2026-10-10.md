# Hello loop diagnosis — 10 October 2026

The observed failure is a mismatch between the full controller prompt and its native response contract. The model repeatedly interprets required function calling as a requirement to perform another workspace tool action instead of completing the greeting. This is the best-supported failure mechanism; the exact commit or model change that first triggered it is not yet established.

## Captured turn

The recorded goal remained `hello`. The prompt explicitly permits direct conversational answers. Tool results were successfully returned with matching provider call IDs, including successful memory lookups. History grew monotonically from 1 to 77 entries across the 39 saved controller-step artifacts; no history compaction erased completed work. The compact tool catalog retains names, descriptions, parameters and required fields; formatting uses JSON separators, not schema removal.

The model requested recall with queries including `project welcome overview`, `current project status`, `welcome greeting`, and `current workspace greeting context`. Its action summaries repeatedly asserted that a workspace check was required before greeting. Fourteen recall executions completed; additional requested calls could be blocked/deduplicated. Separately, the memory middleware prepares recalled background context on each inference iteration; its memory-recall artifacts are not additional visible recall-tool executions.

## Live controlled replays

Replayed the captured step-01 system prompt, payload and schema with GPT-5.6-Terra, Medium reasoning. No returned workspace tools were executed. Account selection used the most recently saved local ChatGPT registration; runtime account equivalence was not independently checked. These are small diagnostic samples, not statistical guarantees.

| Change | Trials | Result |
|---|---:|---|
| Original batching/native contract | 2 | Both chose another tool |
| Remove batch action from response schema | 2 | Both chose recall |
| Add explicit immediate-greeting instruction, batching retained | 2 | Both chose another tool |
| Pretty-print the identical tool catalog | 2 | Both chose read_env_profile |
| Remove batching schema, scheduling fields and batching prompt guidance | 2 | Both chose recall |
| Change native tool_choice from required to auto | 1 | Generated a valid answer JSON containing Hello! How can I help?; transport rejected it for not being a function call |
| Wrap the full controller action schema inside one response function | 1 | Returned an answer action containing Hello! How can I help with the project? |
| Remove namespace wrapper, retain required calling | 1 | Still chose recall |
| Minimal greeting prompt with native required calling | 1 | Returned an answer action |

The full prompt/native contract interaction is therefore implicated. Merely disabling batching, reversing catalog whitespace compaction, changing the namespace, or adding a greeting instruction did not resolve these replays. The model can answer with the same captured context when the response contract changes. Required calling is not universally broken: the minimal greeting prompt worked.

## Relevant code

- providers/openai_native.py:137 requires a function call for every union action request.
- providers/openai_transport.py:474 treats native message output as thinking/preamble; :552 rejects a native reply with no function call, even if its text is a valid terminal answer JSON.
- chat/controller.py:1029 defaults the iteration allowance to 500, so successful but irrelevant tool calls can continue for a long time.
- The batching adapter maps a single tool_call to a tool_calls batch, while its single-call id field remains optional. A null/missing id can therefore become a batch validation failure. One such failure occurred in the capture. This is a separate batching contract defect, not an explanation for the dozens of successfully identified calls that followed.

## Repair direction

Make terminal conversational answers reliable at the provider/controller boundary, with validated terminal output handling or a single native response envelope; retain validation and approval behavior for tool/edit actions. Repair missing-id handling for singleton native batches separately. Add a greeting regression and a bounded no-progress test, plus tool-driven feature tests, before concluding the repair is complete. The response-envelope experiment is evidence for a repair direction, not a deployed fix.

Raw replay outputs are retained in hello-native-ab-2026-10-10.json, hello-native-isolation-2026-10-10.json, and hello-native-formatting-ab-2026-10-10.json. Production behavior was not changed during diagnosis.
