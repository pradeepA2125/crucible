# Controller context attribution

Source: the retained 13:32:59 implicit Node.js smoke, not the later 69.812s profiled run. Counts use compact JSON with Unicode characters retained; earlier review counts used pretty/default JSON, so their values differ.

Logical input: 185,974 characters.

| Component | Characters | Share |
|---|---:|---:|
| payload.retrieval_seed | 101,195 | 54.41% |
| system.tool_catalog | 44,883 | 24.13% |
| system.other_system_blocks_unattributed | 32,561 | 17.51% |
| payload.instruction | 2,843 | 1.53% |
| response_schema | 2,296 | 1.23% |
| payload.recalled_memories | 1,757 | 0.94% |
| payload.conversation_history | 192 | 0.1% |
| payload.goal | 148 | 0.08% |
| payload.workspace_path | 57 | 0.03% |
| payload.budget_status | 34 | 0.02% |
| payload.framing | 8 | 0.0% |

| Candidate retaining access/semantics | Characters saved | Input share recovered |
|---|---:|---:|
| Compact catalog, retaining every tool/schema | 13,318 | 7.16% |
| Tree candidate: 300 entries / 16k serialized characters | 69,174 | 37.2% |
| Combined catalog and tree (disjoint components) | 82,492 | 44.36% |

Nested retrieval fields (already included above):

- repository_structure: 84,985 characters
- file_outlines: 7,642 characters
- file_contents: 18 characters
- planner_evidence: 7,257 characters
- diagnostics_excerpt: 24 characters
- snapshot_stats: 82 characters
- graph_neighbor_files: 1,162 characters
- framing: 8 characters

Logical character attribution, not physical provider tokens or latency. Nested retrieval parts overlap payload.retrieval_seed. Old artifacts cannot separate individual system blocks. Removal candidates do not authorize dropping instructions or tools. Validate quality and cache behavior live.
