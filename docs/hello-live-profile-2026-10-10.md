# Live hello performance capture — 10 October 2026

Stopped after **354.631 seconds**, with **no assistant response**. GPT-5.6-Terra, Medium reasoning; fresh chat, plan mode and step review off. Backend restarted and allowed 60 seconds to bootstrap. This is a live provider capture, not the synthetic benchmark.

The capture measures HTTP POST start through the stop/completion SSE event. It includes every backend span and all 373 received SSE events in the JSON artifact. Extension IPC and webview rendering are **not measured** because this request was submitted directly to the installed extension’s backend. Remote inference cannot be separated from network time. CPU profiling adds overhead, especially to retrieval; these are instrumented timings.

- HTTP response headers and first SSE event: 27.160s.
- Backend turn: 354.626s.
- Provider: 40 requests started; 39 completed, one cancelled. 38 tool call/result events; one invalid-batch retry. No final greeting.
- Reported usage across 39 completed requests: 1,930,133 input tokens (1,831,168 cached), 6,783 output tokens, 3,975 reasoning tokens. Reasoning is included in output, not added again.

## Time accounting

Self time subtracts child spans; the self column sums to the backend turn. Inclusive times overlap and must not be added. Provider streaming self time includes waiting for remote generation/network between headers and completion.

| Stage | Calls | Self seconds | Inclusive seconds |
|---|---:|---:|---:|
| provider.stream | 40 | 213.287182 | 310.440029 |
| provider.response_headers | 40 | 97.152847 | 97.152847 |
| context.retrieval_seed | 1 | 27.063437 | 27.063437 |
| tool.recall | 14 | 9.201095 | 9.201095 |
| tool.query_graph | 1 | 6.116411 | 6.116411 |
| controller.turn | 1 | 0.730161 | 327.467494 |
| model.step | 40 | 0.342755 | 311.315531 |
| provider.request | 40 | 0.300670 | 310.931403 |
| provider.auth | 40 | 0.190704 | 0.190704 |
| tool.search_semantic | 1 | 0.092969 | 0.092969 |
| controller.setup_and_loop | 1 | 0.092515 | 354.623446 |
| context.system_prompt | 40 | 0.032247 | 0.032247 |
| context.payload | 40 | 0.008485 | 0.008485 |
| tool.list_directory | 10 | 0.004729 | 0.004729 |
| tool.read_file | 4 | 0.004179 | 0.004179 |
| tool.read_env_profile | 1 | 0.002400 | 0.002400 |
| controller.handle_message | 1 | 0.001099 | 354.625465 |
| controller.user_turn | 1 | 0.000920 | 354.624366 |
| provider.rate_limit_wait | 40 | 0.000642 | 0.000642 |
| controller.backend_turn | 1 | 0.000055 | 354.625519 |
| tool.list_sessions | 1 | 0.000020 | 0.000020 |

## Each provider request

| Request | Start after send (s) | Duration (s) | Headers (s) | First provider content (s) | Status |
|---|---:|---:|---:|---:|---|
| 1 | 27.515 | 6.274 | 1.683 | 4.895 | ok |
| 2 | 33.813 | 5.982 | 2.577 | 4.786 | ok |
| 3 | 40.201 | 7.014 | 1.918 | 5.745 | ok |
| 4 | 47.554 | 6.173 | 2.084 | 4.930 | ok |
| 5 | 53.738 | 6.536 | 2.041 | 5.450 | ok |
| 6 | 60.288 | 6.575 | 1.829 | 5.608 | ok |
| 7 | 66.871 | 8.272 | 1.782 | 7.467 | ok |
| 8 | 75.162 | 7.168 | 1.865 | 5.231 | ok |
| 9 | 82.343 | 8.248 | 1.829 | 7.142 | ok |
| 10 | 91.752 | 7.611 | 1.884 | 6.084 | ok |
| 11 | 99.381 | 7.282 | 2.151 | 5.646 | ok |
| 12 | 106.684 | 6.064 | 2.066 | 4.357 | ok |
| 13 | 118.885 | 8.058 | 1.988 | 6.605 | ok |
| 14 | 126.965 | 6.252 | 2.016 | 5.682 | ok |
| 15 | 133.235 | 8.491 | 2.259 | 8.038 | ok |
| 16 | 141.835 | 12.595 | 2.084 | 6.353 | ok |
| 17 | 155.279 | 7.057 | 2.164 | 5.851 | ok |
| 18 | 163.451 | 6.785 | 2.102 | 5.334 | ok |
| 19 | 171.374 | 8.439 | 2.164 | 6.101 | ok |
| 20 | 180.192 | 7.562 | 1.934 | 6.429 | ok |
| 21 | 187.780 | 6.677 | 2.198 | 5.414 | ok |
| 22 | 194.475 | 7.100 | 5.582 | 7.038 | ok |
| 23 | 201.599 | 8.359 | 2.248 | 6.421 | ok |
| 24 | 210.423 | 8.516 | 3.483 | 7.421 | ok |
| 25 | 218.953 | 7.337 | 2.487 | 5.741 | ok |
| 26 | 226.305 | 7.469 | 1.935 | 6.238 | ok |
| 27 | 234.143 | 8.115 | 2.465 | 6.406 | ok |
| 28 | 242.274 | 7.588 | 2.213 | 6.260 | ok |
| 29 | 249.880 | 7.783 | 2.820 | 7.018 | ok |
| 30 | 258.125 | 6.677 | 2.176 | 5.508 | ok |
| 31 | 264.825 | 7.692 | 2.334 | 7.045 | ok |
| 32 | 272.921 | 10.623 | 2.076 | 6.789 | ok |
| 33 | 283.560 | 8.887 | 4.759 | 7.630 | ok |
| 34 | 292.465 | 11.968 | 1.930 | 7.681 | ok |
| 35 | 304.452 | 10.311 | 3.030 | 8.888 | ok |
| 36 | 314.780 | 7.982 | 3.666 | 7.932 | ok |
| 37 | 322.781 | 7.718 | 2.273 | 6.203 | ok |
| 38 | 331.454 | 8.226 | 5.435 | 6.237 | ok |
| 39 | 340.055 | 11.756 | 1.925 | 6.829 | ok |
| 40 | 352.917 | 1.708 | 1.698 | — | error_or_cancelled |

## Each executed tool

| Tool | Start after send (s) | Duration (s) | Status |
|---|---:|---:|---|
| tool.list_directory | 33.797 | 0.001032 | ok |
| tool.recall | 39.799 | 0.392104 | ok |
| tool.recall | 47.222 | 0.323013 | ok |
| tool.list_sessions | 60.279 | 0.000020 | ok |
| tool.read_env_profile | 75.148 | 0.002400 | ok |
| tool.recall | 90.597 | 1.140688 | ok |
| tool.read_file | 99.369 | 0.002002 | ok |
| tool.read_file | 106.672 | 0.000778 | ok |
| tool.query_graph | 112.755 | 6.116411 | ok |
| tool.list_directory | 126.949 | 0.001270 | ok |
| tool.search_semantic | 141.732 | 0.092969 | ok |
| tool.recall | 154.439 | 0.825962 | ok |
| tool.recall | 162.343 | 1.094712 | ok |
| tool.recall | 170.253 | 1.106891 | ok |
| tool.recall | 179.818 | 0.364686 | ok |
| tool.list_directory | 187.762 | 0.000518 | ok |
| tool.list_directory | 194.465 | 0.000293 | ok |
| tool.list_directory | 201.586 | 0.000427 | ok |
| tool.recall | 209.964 | 0.449597 | ok |
| tool.list_directory | 226.296 | 0.000288 | ok |
| tool.recall | 233.781 | 0.352378 | ok |
| tool.read_file | 242.265 | 0.000539 | ok |
| tool.list_directory | 249.869 | 0.000230 | ok |
| tool.recall | 257.669 | 0.390896 | ok |
| tool.recall | 272.525 | 0.383592 | ok |
| tool.list_directory | 283.551 | 0.000191 | ok |
| tool.list_directory | 292.455 | 0.000276 | ok |
| tool.read_file | 304.442 | 0.000860 | ok |
| tool.list_directory | 314.770 | 0.000203 | ok |
| tool.recall | 330.508 | 0.935947 | ok |
| tool.recall | 339.687 | 0.355238 | ok |
| tool.recall | 351.819 | 1.085390 | ok |

## Retrieval CPU attribution

The 27.063s seed-building span includes snapshot loading, path normalization, context construction, and serialization. The retained function records show 86,945 `_normalize_snapshot_path` calls (23.911s inclusive), 863,721 `lstat` calls, and about 3.5 million `Path` constructions. Function inclusive times overlap; this is evidence of repeated path processing, not additive costs.

| Function | Calls | Self seconds | Inclusive seconds |
|---|---:|---:|---:|
| pathlib.py:358:__init__ | 3546647 | 1.952900 | 3.190724 |
| ~:0:<built-in method posix.lstat> | 863721 | 1.860441 | 1.860441 |
| pathlib.py:277:__getitem__ | 2818411 | 1.330050 | 8.653370 |
| pathlib.py:380:with_segments | 3446274 | 1.232977 | 6.132269 |
| pathlib.py:484:_str_normcase | 3518302 | 1.144497 | 2.535419 |
| ~:0:<built-in method builtins.isinstance> | 18891436 | 1.112829 | 1.113031 |
| ~:0:<built-in method builtins.len> | 12922352 | 1.095108 | 1.648259 |
| pathlib.py:387:_parse_path | 815051 | 1.080394 | 2.103009 |
| pathlib.py:1157:__init__ | 3546647 | 0.981887 | 4.172611 |
| pathlib.py:420:_from_parsed_parts | 2557706 | 0.815448 | 5.636572 |
| <frozen posixpath>:71:join | 960505 | 0.750379 | 1.275950 |
| pathlib.py:663:relative_to | 264815 | 0.681403 | 17.046923 |
| agentd/retrieval/artifact_client.py:403:_build_context | 1 | 0.647508 | 19.513364 |
| <frozen posixpath>:432:_joinrealpath | 93526 | 0.635344 | 3.725767 |
| pathlib.py:429:_format_parsed_parts | 3091835 | 0.624435 | 1.050039 |
| pathlib.py:274:__len__ | 5636822 | 0.584645 | 0.856002 |
| <frozen _collections_abc>:1022:__iter__ | 3083225 | 0.577259 | 9.230629 |
| pathlib.py:437:__str__ | 2207759 | 0.573378 | 2.184479 |
| pathlib.py:519:__eq__ | 1759151 | 0.522386 | 3.165540 |
| pathlib.py:1164:__new__ | 3546647 | 0.520040 | 0.861837 |
| <frozen _collections_abc>:1032:__contains__ | 264814 | 0.459871 | 7.196798 |
| ~:0:<built-in method sys.intern> | 4410137 | 0.457943 | 0.457943 |
| pathlib.py:551:drive | 1253142 | 0.439237 | 3.317984 |
| ~:0:<method 'join' of 'str' objects> | 3091906 | 0.399830 | 0.399830 |
| ~:0:<built-in method posix.fspath> | 6479789 | 0.391488 | 0.424997 |
| pathlib.py:407:_load_parts | 815051 | 0.379997 | 2.889818 |
| ~:0:<method 'append' of 'list' objects> | 4352256 | 0.377855 | 0.377855 |
| ~:0:<built-in method __new__ of type object at 0x104cda058> | 3546850 | 0.341817 | 0.341817 |
| ~:0:<method 'get' of 'dict' objects> | 3821721 | 0.331080 | 0.331080 |
| ~:0:<method 'startswith' of 'str' objects> | 3122298 | 0.328020 | 0.328020 |
| <frozen posixpath>:138:splitroot | 815050 | 0.296538 | 0.396265 |
| decoder.py:344:raw_decode | 3 | 0.261427 | 0.261427 |
| agentd/retrieval/artifact_client.py:888:_normalize_snapshot_path | 86945 | 0.252272 | 23.910604 |
| ~:0:<built-in method posix.stat> | 88255 | 0.249898 | 0.873784 |
| pathlib.py:690:is_relative_to | 264815 | 0.210805 | 9.648827 |
| pathlib.py:268:__init__ | 529629 | 0.202793 | 1.124640 |
| ~:0:<method 'split' of 'str' objects> | 902032 | 0.171461 | 0.171461 |
| agentd/retrieval/artifact_client.py:500:_dir_seg_score | 396381 | 0.155904 | 0.279407 |
| ~:0:<method 'endswith' of 'str' objects> | 1304583 | 0.144786 | 0.144786 |
| <frozen posixpath>:41:_get_sep | 1147557 | 0.143254 | 0.211781 |

## Context growth

The prepared request grew from 107,770 to 313,366 serialized characters. First completed request reported 25,620 input tokens; final completed request reported 73,497. The JSON retains logical component sizes, physical request sizes, actual usage/cache counts, batch counters, and each event timestamp.

The measured failure has two main contributors: expensive retrieval before inference, then repeated model/tool requests with no greeting. No latency improvement is claimed from this single instrumented run.
