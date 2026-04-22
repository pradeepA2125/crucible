# Competitor Planning Architectures

**Cursor (Plan Mode):** Cursor’s Plan Mode deeply *resembles a design review step*. The agent “researches your codebase to find relevant files, review docs, and ask clarifying questions” before coding【80†L190-L194】. It then produces a **Markdown plan file** listing tasks and file references, which the user can edit. (In effect, it implements an *“ask & spec” loop* where the agent first gathers info and assumptions via question-answering, then outlines a TODO list and file structure.) 【80†L190-L194】

**Windsurf:** The community’s “spec-first” workflow highlights Windsurf’s emphasis on planning. Users instruct the agent to **create a spec `.md` document first**, then review it, and only after approval implement code【84†L102-L110】. Windsurf’s **workflows & memory** features let this spec persist: after editing the spec, the agent *“will know… what your current feature is”* on later chats by recalling it from memory【84†L207-L215】. In practice, Windsurf agents encourage writing design docs as part of the process. (A user shared: “before making changes, first create a spec .md file… I will review it… then implement”【84†L102-L110】. The agent then tracks that spec in memory to resume work【84†L207-L215】.)

**OpenAI Codex App:** Codex app is built for **parallel, long-running workflows**. It treats each agent as a thread: “agents run in separate threads organized by project” and even use *isolated worktrees* so multiple agents can experiment without conflict【88†L75-L84】. Practically, a developer can spin up multiple agents on different tasks (or the same task) and switch contexts. Agents in the Codex app can use “skills” (tool integrations) and run for hours on end. For example, Codex built an entire 3D game over 7+ million tokens by iteratively planning, coding, testing (even playing the game), and revising【88†L105-L114】【88†L75-L84】. In sum, Codex assumes a **multi-agent orchestration**: users delegate sub-goals to different agent threads and supervise their progress via the UI. This is more complex than a single-plan model; it effectively manages an entire project plan across agents.

**Cline (Memory Bank):** Cline doesn’t have a built-in *planning mode*, but it does offer persistent memory. The Memory Bank feature instructs the agent to **write and read markdown docs** (e.g. `projectbrief.md`, `progress.md`) to remember project context【86†L75-L83】. Before doing work, Cline checks these memory files to rebuild its understanding. This is akin to saving and retrieving “scratchpad” notes or specs between sessions. Though not explicitly a planning tool, it fills a similar role: the agent can consult *previously written documentation and notes* to continue a long-horizon task without losing context【86†L75-L83】.

**Academic / Deep-Agent Architectures:** Recent research emphasizes modular “deep agent” designs for long-horizon tasks. A key insight (from LangChain’s Deep Agents and others) is that **planning, memory, and multitasking are separate concerns**【91†L1-L4】. Successful systems combine: 
- **Detailed system prompts** (with instructions and examples)  
- **Planning tools** (even a no-op “todo list” to force planning)  
- **Sub-agents** (spawn specialized agents for parts of the task)  
- **File-system or knowledge-base memory** for persistent context【91†L1-L4】【90†L53-L60】.  
LangChain’s “Deep Agents” article explicitly lists these components【91†L1-L4】.  For example, Claude Code uses a hidden to-do list tool to structure plans, spawns sub-agents per feature, and treats files (and docs) as memory【91†L1-L4】【90†L53-L60】. In research, agents often operate in a **plan→act→observe→revise** loop with explicit memory storage (e.g. writing intermediate results to files)【90†L93-L102】.

# Analysis: Shadow-Forge vs. These Patterns

Shadow-Forge currently uses a **structured Pydantic plan object**. This means plans are **rigidly defined and executed**. In contrast, the above systems treat planning more fluidly:

- **Free-form plan editing:** Cursor/Windsurf let the user edit the plan text (Markdown to-dos). Shadow-Forge’s strict schema doesn’t allow on-the-fly modifications beyond what’s coded.  
- **Clarifying questions:** Cursor explicitly *asks clarifying questions*. Shadow-Forge’s agent might lack this spontaneous Q&A step.  
- **Task decompositions:** Codex and deep agents often *spawn sub-agents* or threads for sub-tasks. Shadow-Forge has one linear agent pipeline.  
- **Memory persistence:** Windsurf and Cline use saved spec/docs as memory across sessions. Shadow-Forge would benefit from a similar memory or task-tracking mechanism.

In sum, Shadow-Forge’s planning is **highly structured but not as flexible**. Industry systems suggest a hybrid approach: start with free-form planning (to capture user intent and ask clarifications), then translate to structured steps for execution.

# Proposed Architecture for Shadow-Forge

1. **Exploration & Questioning:** Before finalizing a plan, allow the agent to perform open-ended exploration of the codebase. Use LLM queries and retrieval to summarize relevant files and pose clarifying questions. (E.g. “Should we support OAuth only via GitHub or other providers?”) Log these Q&A in the plan.

2. **Mixed Plan Representation:** Instead of only a Pydantic schema, use a **Markdown “spec” as the primary plan** (with tasks, descriptions, file references). Then derive the structured plan object from it. This matches Cursor/Windsurf (editable spec) and still lets you have a typed internal plan. For example, generate a `plan.md`, let user approve/edit it, then parse it back into the Pydantic steps.

3. **Sub-Agents / Parallelism:** Architecturally allow multiple concurrent “agents” or threads. For long tasks, split the plan into sub-tasks that can be executed independently (like Codex’s threads). Your orchestrator can queue multiple step-sets that run in isolation (separate worktrees) and merge results later. Even if not fully parallel at MVP, design the plan schema to mark independent subtasks.

4. **Persistent Memory / Knowledge:** Implement a **memory store** for tasks. E.g., after planning, save the spec/plan to disk (like `memory-bank/plan-featureX.md` in Cline’s style). When starting a new session or agent, automatically load relevant memory files. This allows “continuation” prompts: the agent can recall “current feature” or read prior progress【84†L207-L215】【86†L75-L83】.

5. **Plan Revision Loop:** Allow updating the plan mid-execution. Track a **plan version** and let the agent modify its plan (append steps, reprioritize) if something changes. If an operation fails validation, the agent should update the plan accordingly (retry, split tasks, etc.). This mimics the “revise plan” step in deep-agent loops.

6. **System Prompt & Tools:** Use an enriched system prompt to enforce planning. Include a no-op planning tool (like LangChain’s “todo” tool) that the model can invoke to trigger planning mode【90†L75-L82】. For example, make the first step a “plan & review” action. Provide built-in tools for memory lookup and question answering about the codebase.

# Implementation Checklist

- **Memory Repository:** Create a `memory/` directory to store plan and context files (mirroring Cline’s memory bank). Update agent startup logic to load `memory/`.
- **Plan File Output:** Modify the agent to output a `plan.md` file (with tasks and file targets) before generating patches. Provide an editor interface or CLI prompt to review/edit the plan.
- **Sync Plan ↔ Schema:** Build a parser that converts the markdown plan into your Pydantic plan object (and vice versa). Allow manual edits to `plan.md` then re-parse it for execution.
- **Parallel Execution:** Update the orchestrator to support multiple isolated worktrees or threads. For now, you can simulate by queueing independent tasks sequentially, but design the plan format to tag independent subtasks.
- **Sub-Agent Trigger:** Add a “create_subagent” operation in your plan schema. The orchestrator can spawn a child agent with its own plan subset. Ensure memory context and allowed_files propagate.
- **Prompt & Tools:** Enhance the LLM prompts. Explicitly mention a “Plan” step in the system prompt. Provide LLM with a dummy tool (e.g. `TOOL::plan()` as planning no-op) to structure thought. Encourage question-asking. 
- **Error Handling Hooks:** When a patch fails or validation triggers, feed back to the LLM (via a specialized `repair()` call or prompt) so it can adjust the plan or retry. Record these events in plan’s “notes” field.

# API Sketch

Example plan generation flow:

```python
plan = agentd.create_plan(goal="Add OAuth login")  # LLM generates plan.md content
# Editor reviews plan.md (optional)
steps = agentd.parse_plan(plan)
for step in steps:
    agentd.execute_step(step)  # calls underlying patch engine / tools
    if step.failed:
        steps = agentd.revise_plan(step_error=step.error)
        # retry or modify plan as needed
```

Memory integration:

```python
agentd.save_memory("plan_AddOAuth.md", plan.content)
# Later or in new session:
prior_plan = agentd.load_memory("plan_AddOAuth.md")
agentd.include_in_prompt(prior_plan)
```

# Migrating Shadow-Forge

1. **Extend Plan Model:** Update Pydantic models to allow optional free-text or markdown fields for tasks. Possibly use a template for tasks (id, goal, targets, etc.).
2. **New Plan Step Type:** Add a “PLAN” step that performs the outline phase. Instruct the LLM to output a markdown spec during this step.
3. **Memory Hooks:** Implement read/write APIs for memory docs. Ensure every session loads relevant plan/memory files.
4. **Prompt Engineering:** Update system/user prompts to encourage the plan-writing phase (like “Before coding, create a detailed plan”).
5. **Testing:** Add end-to-end tests for long tasks (e.g. a multi-file feature). Validate that the agent can take a plan from spec to execution.
6. **Docs & UX:** Document the new plan workflow for users. Provide commands or UI to review/edit the plan document as part of the process.

# Sources
Cursor Plan Mode【80†L190-L194】, Windsurf spec/workflow community tips【84†L102-L110】【84†L207-L215】, Codex app (multi-agent threads, worktrees)【88†L75-L84】, Cline Memory Bank【86†L75-L83】, LangChain Deep Agent design【91†L1-L4】.