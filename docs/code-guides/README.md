# Code guides

Guided readings of Crucible's major subsystems, for people learning the code. Each guide explains how one part works, why it was built that way, and every place the rest of the repository calls into it.

Guides are standalone HTML pages. GitHub shows HTML files as source, so clone the repository and open them in a browser. They load their fonts and diagrams from public CDNs; offline, the text still reads but the diagrams show as plain text.

| Guide | Covers |
|---|---|
| [Command sandbox](command-sandbox.html) | `services/agentd-py/agentd/sandbox/`: the policy, the macOS and Linux walls, the network proxy, block detection, escalations, how it combines with the approval modes, and its call sites in the backend and both apps |

Each guide states the commit it was written against. Design specs and implementation plans live in `docs/superpowers/specs/` and `docs/superpowers/plans/`.
