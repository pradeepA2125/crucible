# Python suite performance — 17 October 2026

The baseline Python suite spent minutes waiting inside tests with fake provider responses and in teardown of sessions whose children were never stopped. The changes in this report affect tests and CI setup; production retry and execution-session behavior is unchanged.

## CI baseline

The [last successful v0.7.0 release job](https://github.com/pradeepA2125/crucible/actions/runs/38944309339/job/117120441779) took 11m11s overall:

| Work | Time |
|---|---:|
| JavaScript dependencies, build, tests and typecheck | 1m56s |
| Python environment and dependency installation | About 41s |
| Python tests | 7m17.57s; 3,920 passed, 12 skipped |
| Rust compilation and tests | About 1m |

These checks run sequentially in one job. The job duration includes all three languages.

## Causes and changes

- `test_exec_sessions_tool_source.py` created 60-second PTY children without shutting down their `SessionManager`. The test body passed, but pytest-asyncio then waited for the default executor's blocking `proc.wait` threads. The two affected teardown phases took 57.76s and 59.77s locally. An async fixture now shuts down every session in `finally`, including on assertion failures.
- Ollama, OpenAI-compatible/OpenRouter and TurboQuant retry tests used actual 5s/10s backoff delays with fake providers. An opt-in fixture now records requested sleeps and yields once. It replaces each transport module's asyncio binding, preserving real global sleeps, timeouts and stream deadlines. The optional live TurboQuant smoke retains real retries.
- Retry assertions now check the requested exponential schedule, cap and jitter range; no retries or existing test assertions were removed.
- CI now caches pip downloads, names installation and test execution separately, and prints the 30 slowest test phases. This makes future regressions visible without downloading or executing a profiler.

## Local verification

The baseline and optimized full suites use the same Python 3.12.12 / pytest 9.0.3 environment and command options:

```sh
cd services/agentd-py
.venv/bin/python -m pytest -vv --durations=40 \
  --junitxml=/tmp/crucible-pytest-<baseline-or-optimized>.xml \
  -o faulthandler_timeout=60
```

Localhost access is required for the server and authentication integration tests. The restricted sandbox blocks those socket tests, so both measured runs used the same unsandboxed permissions. No optional dependency or test selection changed between runs.

| Measured work | Before | After |
|---|---:|---:|
| Full Python suite | 364.90s (6m05s) | 142.68s (2m23s) |
| Session tool-source tests, including teardown | 122.86s | 5.34s |
| Five provider/retry test files combined | 98.04s | 0.79s |
| Passed / skipped | 3,920 / 12 | 3,923 / 12 |

The full suite used **222.22s less time (60.9% reduction; 2.56x faster)**. The three additional cases check zero-retry behavior, capped exponential backoff and jitter bounds. The 12 existing skips and two dependency deprecation warnings are unchanged. Both complete runs passed.

The five provider files are `test_ollama_transport.py`, `test_openai_compatible_transport.py`, `test_openrouter_transport.py`, `test_openai_compat_stream_deadline.py` and `test_turboquant_transport.py`. Times for groups include all test phases reported by JUnit, not just sleeping.

Remaining slow cases include the real server/SSE shutdown (6.44s), reload (5.64s), team-stop/quorum behavior (5.05s), and command-pipeline timeout/group cleanup (3.01s). Their timing behavior remains intact.

CI cache savings require a future run with a populated cache and are not included in the local comparison. These measurements are single sequential runs on the same local machine, not a guarantee of identical CI wall time.

Additional validation: workflow syntax passes `actionlint -shellcheck=''`; `git diff --check` passes; changed Python files introduce no new Ruff diagnostics. The existing Python lint and workflow shellcheck findings are outside this timing change.

The release tag `v0.7.1` was already pushed before this investigation. These improvements are separate working-tree changes and do not alter that tag or its running workflow.
