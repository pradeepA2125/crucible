# Chat UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `media/chat.js` (vanilla JS) with a React 18 + TypeScript + Tailwind v4 webview delivering the Linear-inspired design from the spec.

**Architecture:** New standalone Vite app at `apps/vscode-extension/webview-ui/`. `chat-panel.ts` reads its compiled `dist/index.html` instead of inlining HTML. All `vscode.postMessage` ↔ `window.addEventListener('message')` types stay identical — no backend changes.

**Tech Stack:** React 18, TypeScript 5, Tailwind CSS v4 (`@tailwindcss/vite`), Vite 6, `react-markdown`, `@testing-library/react`, `vitest`

**Spec:** `docs/superpowers/specs/2026-06-09-chat-ui-redesign-design.md`

---

## File Map

**Create:**
- `apps/vscode-extension/webview-ui/package.json`
- `apps/vscode-extension/webview-ui/vite.config.ts`
- `apps/vscode-extension/webview-ui/tsconfig.json`
- `apps/vscode-extension/webview-ui/index.html`
- `apps/vscode-extension/webview-ui/src/index.css`
- `apps/vscode-extension/webview-ui/src/vscodeApi.ts`
- `apps/vscode-extension/webview-ui/src/types.ts`
- `apps/vscode-extension/webview-ui/src/main.tsx`
- `apps/vscode-extension/webview-ui/src/App.tsx`
- `apps/vscode-extension/webview-ui/src/hooks/useAppState.ts`
- `apps/vscode-extension/webview-ui/src/components/HistoryView.tsx`
- `apps/vscode-extension/webview-ui/src/components/ThreadView.tsx`
- `apps/vscode-extension/webview-ui/src/components/MessageRow.tsx`
- `apps/vscode-extension/webview-ui/src/components/InputArea.tsx`
- `apps/vscode-extension/webview-ui/src/components/EmptyState.tsx`
- `apps/vscode-extension/webview-ui/src/components/LiveSlot.tsx`
- `apps/vscode-extension/webview-ui/src/components/messages/UserMessage.tsx`
- `apps/vscode-extension/webview-ui/src/components/messages/AgentRow.tsx`
- `apps/vscode-extension/webview-ui/src/components/messages/QAMessage.tsx`
- `apps/vscode-extension/webview-ui/src/components/messages/PlanCard.tsx`
- `apps/vscode-extension/webview-ui/src/components/messages/DiffCard.tsx`
- `apps/vscode-extension/webview-ui/src/components/messages/GateCard.tsx`
- `apps/vscode-extension/webview-ui/src/components/messages/ErrorCard.tsx`
- `apps/vscode-extension/webview-ui/src/components/shared/ThinkingBlock.tsx`
- `apps/vscode-extension/webview-ui/src/components/shared/ToolPill.tsx`
- `apps/vscode-extension/webview-ui/src/test/setup.ts`
- `apps/vscode-extension/webview-ui/src/test/GateCard.test.tsx`
- `apps/vscode-extension/webview-ui/src/test/PlanCard.test.tsx`
- `apps/vscode-extension/webview-ui/src/test/DiffCard.test.tsx`
- `apps/vscode-extension/webview-ui/src/test/useAppState.test.ts`

**Modify:**
- `apps/vscode-extension/src/chat-panel.ts` — `buildHtml()` reads `webview-ui/dist/index.html`, update `localResourceRoots`
- `apps/vscode-extension/src/extension.ts` — remove `ReviewPanel`, remove `showStepReview`, remove `openReviewPanel` command
- `apps/vscode-extension/src/controller.ts` — remove `showStepReview` + `updatePanel` from `ControllerUI`, remove call sites
- `apps/vscode-extension/package.json` — add `webview:build` + `prebuild` scripts

**Delete:**
- `apps/vscode-extension/media/chat.js`
- `apps/vscode-extension/media/marked.umd.js`
- `apps/vscode-extension/src/review-panel.ts`

---

## Task 1: Commit current uncommitted changes (prerequisite)

**Files:** all modified files on `main`

- [ ] **Step 1: Check what's staged and unstaged**

```bash
git status
git diff --stat
```

- [ ] **Step 2: Stage and commit by logical group**

Group the staged/unstaged files visible in `git status` by their area of concern and commit each group separately. Example groupings: `fix(live-state)`, `feat(chat)`, `fix(tools)`. Use `git add <specific files>` — never `git add -A`. Commit each group:

```bash
git add <files for group 1>
git commit -m "group description"
# repeat per group
```

- [ ] **Step 3: Verify clean state**

```bash
git status
```
Expected: `nothing to commit, working tree clean` (or only untracked files that you intentionally skip)

---

## Task 2: Scaffold webview-ui package

**Files:** `webview-ui/package.json`, `vite.config.ts`, `tsconfig.json`, `index.html`

- [ ] **Step 1: Create package.json**

Create `apps/vscode-extension/webview-ui/package.json`:

```json
{
  "name": "webview-ui",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "build": "vite build",
    "dev": "vite",
    "test": "vitest run",
    "typecheck": "tsc --noEmit"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-markdown": "^9.0.1"
  },
  "devDependencies": {
    "@tailwindcss/vite": "^4.0.0",
    "@testing-library/jest-dom": "^6.6.3",
    "@testing-library/react": "^16.1.0",
    "@testing-library/user-event": "^14.5.2",
    "@types/react": "^18.3.12",
    "@types/react-dom": "^18.3.1",
    "@vitejs/plugin-react": "^4.3.4",
    "jsdom": "^25.0.1",
    "tailwindcss": "^4.0.0",
    "typescript": "^5.8.2",
    "vite": "^6.0.5",
    "vitest": "^3.0.8"
  }
}
```

- [ ] **Step 2: Create vite.config.ts**

Create `apps/vscode-extension/webview-ui/vite.config.ts`:

```typescript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "./",
  build: {
    outDir: "dist",
    rollupOptions: {
      output: {
        entryFileNames: "assets/[name].js",
        chunkFileNames: "assets/[name].js",
        assetFileNames: "assets/[name].[ext]",
      },
    },
  },
});
```

`base: "./"` produces relative asset paths in `dist/index.html` so `chat-panel.ts` can rewrite them to webview URIs. Fixed output filenames (no content hash) keep `buildHtml()` simple.

- [ ] **Step 3: Create tsconfig.json**

Create `apps/vscode-extension/webview-ui/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "skipLibCheck": true
  },
  "include": ["src"]
}
```

- [ ] **Step 4: Create index.html**

Create `apps/vscode-extension/webview-ui/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>AI Editor Chat</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 5: Install dependencies**

```bash
cd apps/vscode-extension/webview-ui && npm install
```

Expected: `node_modules/` created, no errors.

- [ ] **Step 6: Verify build compiles**

```bash
cd apps/vscode-extension/webview-ui && npm run build
```

Expected: `dist/index.html` + `dist/assets/index.js` + `dist/assets/index.css` created (even though `src/main.tsx` doesn't exist yet — this will fail; that's fine — come back to verify after Task 3).

Actually, run build AFTER Task 3 step 3. Skip this step now and revisit after Task 3.

- [ ] **Step 7: Commit scaffold**

```bash
git add apps/vscode-extension/webview-ui/package.json apps/vscode-extension/webview-ui/vite.config.ts apps/vscode-extension/webview-ui/tsconfig.json apps/vscode-extension/webview-ui/index.html apps/vscode-extension/webview-ui/package-lock.json
git commit -m "chore(webview-ui): scaffold vite+react+tailwind package"
```

---

## Task 3: CSS tokens + vscodeApi + types

**Files:** `src/index.css`, `src/vscodeApi.ts`, `src/types.ts`

- [ ] **Step 1: Create src/index.css**

Create `apps/vscode-extension/webview-ui/src/index.css`:

```css
@import "tailwindcss";

@theme {
  --color-base: #141414;
  --color-surface: #1a1a1a;
  --color-surface-alt: #1f1f1f;
  --color-border: #2a2a2a;
  --color-text: #e0e0e0;
  --color-text-muted: #888888;
  --color-text-dim: #444444;
  --color-accent: #9d6ff0;
  --color-accent-bg: #2a1a3a;
  --color-accent-border: #3a2a4a;
  --color-success: #4ade80;
  --color-error: #f87171;
  --color-error-bg: #1a1010;
  --color-error-border: #4a2a2a;
  --color-code: #9cdcfe;
}

*,
*::before,
*::after {
  box-sizing: border-box;
}

body {
  margin: 0;
  background: var(--color-base);
  color: var(--color-text);
  font-family: var(--vscode-font-family, system-ui, sans-serif);
  font-size: var(--vscode-font-size, 13px);
  line-height: 1.5;
  height: 100vh;
  overflow: hidden;
}

#root {
  height: 100vh;
  display: flex;
  flex-direction: column;
}

code, pre, .mono {
  font-family: var(--vscode-editor-font-family, "Courier New", monospace);
}

::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--color-border); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--color-text-dim); }
```

- [ ] **Step 2: Create src/vscodeApi.ts**

Create `apps/vscode-extension/webview-ui/src/vscodeApi.ts`:

```typescript
interface VscodeApi {
  postMessage(msg: unknown): void;
}

declare function acquireVsCodeApi(): VscodeApi;

// acquireVsCodeApi() may only be called once per webview lifetime.
// In test environments window.acquireVsCodeApi is stubbed before this module loads.
const _api: VscodeApi =
  typeof acquireVsCodeApi === "function"
    ? acquireVsCodeApi()
    : { postMessage: () => {} };

export const vscode: VscodeApi = _api;
```

- [ ] **Step 3: Create src/types.ts**

Create `apps/vscode-extension/webview-ui/src/types.ts`:

```typescript
// ── Shared sub-types ──────────────────────────────────────────────────────────

export interface DiffEntry {
  path: string;
  additions: number;
  deletions: number;
  temp_path?: string;
  /** Full unified diff string (added in step-review gate payload) */
  unified_diff?: string;
}

export interface Diagnostic {
  level: string;
  message: string;
}

export interface ThreadSummary {
  threadId: string;
  title: string;
}

// ── Chat messages (appendMessage payload) ────────────────────────────────────

export type ChatMsg =
  | { type: "plan_card"; content: string; taskId?: string; metadata?: { taskId?: string } }
  | { type: "scope_card"; metadata: { taskId: string; files: string[]; reason?: string; step_id?: string } }
  | { type: "validation_card"; metadata: { taskId: string; diagnostics: Diagnostic[] } }
  | { type: "command_card"; metadata: { taskId: string; command: string; args: string[] } }
  | { type: "task_card"; taskId?: string; content?: string }
  | {
      type: "diff_card";
      taskId?: string;
      metadata?: {
        taskId?: string;
        diff_entries: DiffEntry[];
        resolved?: "applied" | "discarded";
        thinking_log?: string[];
      };
    }
  | { role: "user"; content: string }
  | {
      role: "agent";
      content: string;
      metadata?: {
        thinking_log?: string[];
        breadcrumb?: boolean;
        taskId?: string;
      };
    };

// ── Live gate/plan (from /live poll via extension) ────────────────────────────

export interface LiveGateView {
  kind: "command" | "scope" | "validation" | "step";
  taskId: string;
  payload: {
    // command
    command?: string;
    args?: string[];
    // validation
    summary?: string;
    diagnostics?: Diagnostic[];
    // scope
    reason?: string;
    files?: string[];
    // step
    step_title?: string;
    diff_entries?: DiffEntry[];
  };
}

export interface LivePlanView {
  taskId: string;
  planMarkdown: string;
}

// ── Messages Extension → Webview ──────────────────────────────────────────────

export type ExtensionMessage =
  | { type: "appendMessage"; message: ChatMsg }
  | { type: "appendChunk"; chunk: string }
  | { type: "appendThinkingEntry"; text: string }
  | { type: "appendThinkingChunk"; chunk: string }
  | { type: "finalizeAgentMessage" }
  | { type: "showThinking"; message: string }
  | { type: "updateThinking"; message: string }
  | { type: "hideThinking" }
  | { type: "setInputEnabled"; enabled: boolean }
  | { type: "renderThreadList"; threads: ThreadSummary[]; activeThreadId: string }
  | { type: "clearThread" }
  | { type: "renderLiveGate"; gate: LiveGateView }
  | { type: "clearLiveGate" }
  | { type: "renderLivePlan"; plan: LivePlanView }
  | { type: "clearLivePlan" }
  | { type: "resolveInlineChangeCard"; taskId: string; resolution: "applied" | "discarded" }
  | { type: "thread_title_updated"; payload: { thread_id: string; title: string } };

// ── Messages Webview → Extension ──────────────────────────────────────────────

export type WebviewMessage =
  | { type: "webviewReady" }
  | { type: "sendMessage"; text: string }
  | { type: "implementPlan"; taskId: string }
  | { type: "planFeedback"; taskId: string; feedback: string }
  | { type: "newChat" }
  | { type: "switchThread"; threadId: string }
  | { type: "applyInlineChange"; taskId: string }
  | { type: "discardInlineChange"; taskId: string }
  | { type: "viewDiffFile"; path: string; shadowPath: string }
  | { type: "scopeDecision"; taskId: string; files: string[]; decision: "approve" | "reject"; remember: boolean }
  | { type: "validationDecision"; taskId: string; decision: "accept" | "reject" }
  | { type: "commandDecision"; taskId: string; approve: boolean; remember?: boolean; scope?: string; ruleValue?: string }
  | { type: "stepDecision"; taskId: string; decision: "accept" | "discard" };

// ── App state ─────────────────────────────────────────────────────────────────

/** A resolved diff_card message with patched-in resolution state. */
export interface ResolvedDiffCard {
  type: "diff_card";
  taskId: string;
  diffEntries: DiffEntry[];
  resolution: "applied" | "discarded" | null;
  thinkingLog: string[];
}

/** Streaming agent bubble built incrementally via appendChunk / appendThinkingEntry. */
export interface StreamingBubble {
  text: string;
  thinkingEntries: string[];
  activeThinkingChunk: string;
}

export interface AppState {
  view: "history" | "thread";
  threads: ThreadSummary[];
  activeThreadId: string;
  messages: ChatMsg[];
  streaming: StreamingBubble | null;
  thinkingStatus: string | null;
  inputEnabled: boolean;
  liveGate: LiveGateView | null;
  livePlan: LivePlanView | null;
}
```

- [ ] **Step 4: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/
git commit -m "feat(webview-ui): add CSS tokens, vscodeApi wrapper, and postMessage types"
```

---

## Task 4: App state reducer + bridge hook

**Files:** `src/hooks/useAppState.ts`

- [ ] **Step 1: Create useAppState.ts**

Create `apps/vscode-extension/webview-ui/src/hooks/useAppState.ts`:

```typescript
import { useReducer, useEffect } from "react";
import type { AppState, ExtensionMessage, ChatMsg, StreamingBubble } from "../types";
import { vscode } from "../vscodeApi";

const TOOL_NAMES = new Set([
  "search_code", "read_file", "list_directory", "run_command",
  "search_semantic", "query_graph", "emit_patch", "verify_done",
]);

function isToolEntry(text: string): boolean {
  const name = text.split(/[(:]/)[0].trim();
  return TOOL_NAMES.has(name);
}

function planSig(taskId: string, content: string): string {
  const s = `${taskId}::${content}`;
  let h = 5381;
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  }
  return String(h >>> 0).toString(36);
}

function hasPlanSig(messages: ChatMsg[], sig: string): boolean {
  return messages.some(
    (m) => m.type === "plan_card" && (m as { _sig?: string })._sig === sig,
  );
}

const INITIAL: AppState = {
  view: "history",
  threads: [],
  activeThreadId: "",
  messages: [],
  streaming: null,
  thinkingStatus: null,
  inputEnabled: true,
  liveGate: null,
  livePlan: null,
};

type Action =
  | { type: "EXT"; msg: ExtensionMessage }
  | { type: "SET_VIEW"; view: "history" | "thread" };

function sealStreaming(state: AppState): AppState {
  if (!state.streaming) return state;
  const bubble = state.streaming;
  const msg: ChatMsg = {
    role: "agent",
    content: bubble.text,
    metadata: {
      thinking_log:
        bubble.thinkingEntries.length > 0 ? bubble.thinkingEntries : undefined,
    },
  };
  return { ...state, streaming: null, thinkingStatus: null, messages: [...state.messages, msg] };
}

function ensureStreaming(state: AppState): StreamingBubble {
  return state.streaming ?? { text: "", thinkingEntries: [], activeThinkingChunk: "" };
}

function reducer(state: AppState, action: Action): AppState {
  if (action.type === "SET_VIEW") return { ...state, view: action.view };

  const { msg } = action;

  switch (msg.type) {
    case "renderThreadList":
      return { ...state, threads: msg.threads, activeThreadId: msg.activeThreadId };

    case "clearThread":
      return { ...state, messages: [], streaming: null, thinkingStatus: null };

    case "setInputEnabled":
      return { ...state, inputEnabled: msg.enabled };

    case "showThinking":
      return { ...state, thinkingStatus: msg.message };

    case "updateThinking":
      return { ...state, thinkingStatus: msg.message };

    case "hideThinking":
      return { ...state, thinkingStatus: null };

    case "appendChunk": {
      const prev = ensureStreaming(state);
      // Seal thinking pane when text starts arriving
      const sealed =
        prev.thinkingEntries.length > 0 && prev.text === ""
          ? { ...prev, activeThinkingChunk: "" }
          : prev;
      return {
        ...state,
        thinkingStatus: null,
        streaming: { ...sealed, text: sealed.text + msg.chunk },
      };
    }

    case "appendThinkingEntry": {
      const prev = ensureStreaming(state);
      const entries = [...prev.thinkingEntries];
      if (prev.activeThinkingChunk) entries[entries.length - 1] = prev.activeThinkingChunk;
      return {
        ...state,
        streaming: {
          ...prev,
          thinkingEntries: [...entries, msg.text],
          activeThinkingChunk: "",
        },
      };
    }

    case "appendThinkingChunk": {
      const prev = ensureStreaming(state);
      return {
        ...state,
        streaming: {
          ...prev,
          activeThinkingChunk: prev.activeThinkingChunk + msg.chunk,
        },
      };
    }

    case "finalizeAgentMessage":
      return sealStreaming(state);

    case "appendMessage": {
      const m = msg.message;
      // Seal any open streaming bubble before appending a persisted message
      const next = sealStreaming(state);

      if (m.type === "plan_card") {
        const taskId = m.metadata?.taskId ?? m.taskId ?? "";
        const sig = planSig(taskId, m.content);
        if (hasPlanSig(next.messages, sig)) return next; // dedup
        const tagged = { ...m, _sig: sig } as ChatMsg;
        return { ...next, messages: [...next.messages, tagged] };
      }

      if (m.type === "diff_card") {
        const taskId = m.taskId ?? m.metadata?.taskId ?? "";
        const tagged = { ...m, taskId } as ChatMsg;
        return { ...next, messages: [...next.messages, tagged] };
      }

      return { ...next, messages: [...next.messages, m] };
    }

    case "resolveInlineChangeCard": {
      const updated = state.messages.map((m) => {
        if (
          m.type === "diff_card" &&
          (m.taskId === msg.taskId || m.metadata?.taskId === msg.taskId)
        ) {
          return {
            ...m,
            metadata: { ...m.metadata, resolved: msg.resolution },
          } as ChatMsg;
        }
        return m;
      });
      return { ...state, messages: updated };
    }

    case "thread_title_updated": {
      const updated = state.threads.map((t) =>
        t.threadId === msg.payload.thread_id ? { ...t, title: msg.payload.title } : t,
      );
      return { ...state, threads: updated };
    }

    case "renderLiveGate":
      return { ...state, liveGate: msg.gate };

    case "clearLiveGate":
      return { ...state, liveGate: null };

    case "renderLivePlan":
      return { ...state, livePlan: msg.plan };

    case "clearLivePlan":
      return { ...state, livePlan: null };

    default:
      return state;
  }
}

export function useAppState() {
  const [state, dispatch] = useReducer(reducer, INITIAL);

  useEffect(() => {
    const handler = (event: MessageEvent<ExtensionMessage>) => {
      dispatch({ type: "EXT", msg: event.data });
    };
    window.addEventListener("message", handler);
    vscode.postMessage({ type: "webviewReady" });
    return () => window.removeEventListener("message", handler);
  }, []);

  const setView = (view: "history" | "thread") => dispatch({ type: "SET_VIEW", view });

  return { state, setView };
}

export { isToolEntry };
```

- [ ] **Step 2: Write tests**

Create `apps/vscode-extension/webview-ui/src/test/setup.ts`:

```typescript
import "@testing-library/jest-dom";
import { vi } from "vitest";

vi.stubGlobal("acquireVsCodeApi", () => ({
  postMessage: vi.fn(),
  getState: vi.fn(),
  setState: vi.fn(),
}));
```

Create `apps/vscode-extension/webview-ui/src/test/useAppState.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useAppState } from "../hooks/useAppState";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

function fireMessage(data: unknown) {
  window.dispatchEvent(new MessageEvent("message", { data }));
}

describe("useAppState", () => {
  it("renders thread list on renderThreadList", () => {
    const { result } = renderHook(() => useAppState());
    act(() => {
      fireMessage({ type: "renderThreadList", threads: [{ threadId: "t1", title: "Test" }], activeThreadId: "t1" });
    });
    expect(result.current.state.threads).toHaveLength(1);
    expect(result.current.state.activeThreadId).toBe("t1");
  });

  it("deduplicates plan_card by task+content signature", () => {
    const { result } = renderHook(() => useAppState());
    const msg = { type: "appendMessage", message: { type: "plan_card", content: "## Plan\n- Step 1", taskId: "task-1" } };
    act(() => { fireMessage(msg); });
    act(() => { fireMessage(msg); });
    expect(result.current.state.messages.filter((m) => m.type === "plan_card")).toHaveLength(1);
  });

  it("accumulates streaming chunks into a bubble", () => {
    const { result } = renderHook(() => useAppState());
    act(() => { fireMessage({ type: "appendChunk", chunk: "Hello" }); });
    act(() => { fireMessage({ type: "appendChunk", chunk: " world" }); });
    expect(result.current.state.streaming?.text).toBe("Hello world");
  });

  it("seals streaming bubble on finalizeAgentMessage", () => {
    const { result } = renderHook(() => useAppState());
    act(() => { fireMessage({ type: "appendChunk", chunk: "Done" }); });
    act(() => { fireMessage({ type: "finalizeAgentMessage" }); });
    expect(result.current.state.streaming).toBeNull();
    expect(result.current.state.messages).toHaveLength(1);
    expect((result.current.state.messages[0] as { content: string }).content).toBe("Done");
  });

  it("resolves diff_card resolution in place", () => {
    const { result } = renderHook(() => useAppState());
    act(() => {
      fireMessage({
        type: "appendMessage",
        message: { type: "diff_card", taskId: "task-2", metadata: { diff_entries: [] } },
      });
    });
    act(() => {
      fireMessage({ type: "resolveInlineChangeCard", taskId: "task-2", resolution: "applied" });
    });
    const card = result.current.state.messages[0] as { type: string; metadata: { resolved: string } };
    expect(card.metadata.resolved).toBe("applied");
  });
});
```

Create `apps/vscode-extension/webview-ui/vitest.config.ts`:

```typescript
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
});
```

- [ ] **Step 3: Run tests**

```bash
cd apps/vscode-extension/webview-ui && npm test
```

Expected: 5 tests pass.

- [ ] **Step 4: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/hooks/ apps/vscode-extension/webview-ui/src/test/ apps/vscode-extension/webview-ui/vitest.config.ts
git commit -m "feat(webview-ui): app state reducer + postMessage bridge + tests"
```

---

## Task 5: Shared components (ThinkingBlock, ToolPill)

**Files:** `src/components/shared/ThinkingBlock.tsx`, `src/components/shared/ToolPill.tsx`

- [ ] **Step 1: Create ThinkingBlock.tsx**

Create `apps/vscode-extension/webview-ui/src/components/shared/ThinkingBlock.tsx`:

```typescript
import { useState } from "react";

interface Props {
  entries: string[];
  activeChunk?: string;
  streaming?: boolean;
}

export function ThinkingBlock({ entries, activeChunk, streaming }: Props) {
  const [open, setOpen] = useState(streaming ?? false);
  const count = entries.length + (activeChunk ? 1 : 0);
  if (count === 0 && !streaming) return null;

  return (
    <div className="mb-1.5 rounded border border-accent-border bg-accent-bg text-xs">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-2 py-1.5 text-left"
      >
        {streaming ? (
          <>
            <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
            <span className="text-accent">Thinking…</span>
          </>
        ) : (
          <span className="text-text-dim">
            {open ? "▾" : "▶"} Thinking ({count} steps)
          </span>
        )}
      </button>
      {open && (
        <ul className="mono max-h-48 overflow-y-auto border-t border-accent-border px-3 py-1.5 text-[11px] text-text-muted">
          {entries.map((e, i) => (
            <li key={i} className="py-0.5">
              {e}
            </li>
          ))}
          {activeChunk && <li className="py-0.5 text-text-dim">{activeChunk}</li>}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Create ToolPill.tsx**

Create `apps/vscode-extension/webview-ui/src/components/shared/ToolPill.tsx`:

```typescript
import { useState } from "react";
import { isToolEntry } from "../../hooks/useAppState";

interface Props {
  text: string;
  streaming?: boolean;
}

// Parse "tool_name(arg1=val, ...)\noutput..." from a thinking entry.
function parseToolEntry(text: string): { name: string; input: string; output: string } {
  const newline = text.indexOf("\n");
  const header = newline === -1 ? text : text.slice(0, newline);
  const output = newline === -1 ? "" : text.slice(newline + 1);
  const paren = header.indexOf("(");
  const name = paren === -1 ? header.trim() : header.slice(0, paren).trim();
  const input = paren === -1 ? "" : header.slice(paren + 1).replace(/\)$/, "").trim();
  return { name, input, output };
}

export function ToolPill({ text, streaming }: Props) {
  const [open, setOpen] = useState(false);
  if (!isToolEntry(text)) return null;

  const { name, input, output } = parseToolEntry(text);

  return (
    <div className="mb-1">
      <button
        onClick={() => setOpen((o) => !o)}
        className={[
          "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] transition-colors",
          open
            ? "border-accent-border bg-accent-bg text-accent"
            : "border-border bg-surface-alt text-text-muted hover:border-accent-border",
          streaming ? "border-accent" : "",
        ]
          .filter(Boolean)
          .join(" ")}
      >
        {streaming ? (
          <span className="inline-block h-2 w-2 animate-spin rounded-full border border-accent-border border-t-accent" />
        ) : (
          <span className="text-[10px]">✓</span>
        )}
        <span>{name}</span>
        {open && <span className="text-[10px]">▴</span>}
      </button>

      {open && (
        <div className="mt-1 rounded border border-accent-border bg-surface text-[11px]">
          <div className="flex items-center justify-between border-b border-accent-border px-2 py-1">
            <span className="font-semibold text-accent">{name}</span>
            <button onClick={() => setOpen(false)} className="text-text-dim hover:text-text-muted">
              collapse ▴
            </button>
          </div>
          {input && (
            <div className="border-b border-border px-2 py-1.5">
              <div className="mb-0.5 text-[10px] uppercase tracking-wide text-text-dim">Input</div>
              <pre className="mono whitespace-pre-wrap break-all text-text-muted">{input}</pre>
            </div>
          )}
          {output && (
            <div className="px-2 py-1.5">
              <div className="mb-0.5 text-[10px] uppercase tracking-wide text-text-dim">Output</div>
              <pre className="mono max-h-28 overflow-y-auto whitespace-pre-wrap break-all text-text-muted">
                {output.length > 3000 ? output.slice(0, 3000) + "\n… truncated" : output}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/components/
git commit -m "feat(webview-ui): ThinkingBlock and ToolPill shared components"
```

---

## Task 6: Message components — UserMessage, QAMessage, AgentRow

**Files:** `messages/UserMessage.tsx`, `messages/QAMessage.tsx`, `messages/AgentRow.tsx`

- [ ] **Step 1: Create UserMessage.tsx**

Create `apps/vscode-extension/webview-ui/src/components/messages/UserMessage.tsx`:

```typescript
interface Props {
  content: string;
}

export function UserMessage({ content }: Props) {
  return (
    <div className="flex justify-end">
      <div
        className="max-w-[85%] rounded-[10px_10px_2px_10px] border border-border bg-surface-alt px-3 py-2 text-sm text-text"
        style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}
      >
        {content}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Create QAMessage.tsx**

Create `apps/vscode-extension/webview-ui/src/components/messages/QAMessage.tsx`:

```typescript
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import { ThinkingBlock } from "../shared/ThinkingBlock";
import { vscode } from "../../vscodeApi";

interface Props {
  content: string;
  thinkingLog?: string[];
}

export function QAMessage({ content, thinkingLog }: Props) {
  const [copied, setCopied] = useState(false);

  function copy() {
    void navigator.clipboard.writeText(content).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }

  return (
    <div className="group relative flex gap-2">
      <div className="mt-0.5 flex h-[18px] w-[18px] flex-shrink-0 items-center justify-center rounded border border-accent-border bg-accent-bg text-[7px] font-semibold text-accent">
        AI
      </div>
      <div className="min-w-0 flex-1">
        {thinkingLog && thinkingLog.length > 0 && (
          <ThinkingBlock entries={thinkingLog} />
        )}
        <div className="prose-sm prose max-w-none text-sm text-text [&_code]:mono [&_code]:rounded [&_code]:bg-surface-alt [&_code]:px-1 [&_code]:text-code [&_pre]:mono [&_pre]:overflow-x-auto [&_pre]:rounded [&_pre]:bg-surface-alt [&_pre]:p-2">
          <ReactMarkdown>{content}</ReactMarkdown>
        </div>
      </div>
      <button
        onClick={copy}
        className="absolute right-0 top-0 rounded border border-border bg-surface-alt px-1.5 py-0.5 text-[10px] text-text-dim opacity-0 transition-opacity group-hover:opacity-100 hover:text-text-muted"
        title="Copy"
      >
        {copied ? "✓" : "⎘"}
      </button>
    </div>
  );
}
```

- [ ] **Step 3: Create AgentRow.tsx**

AgentRow renders tool pills (from thinking entries that look like tool calls), remaining thinking entries in a ThinkingBlock, breadcrumb text lines, and a copy button.

Create `apps/vscode-extension/webview-ui/src/components/messages/AgentRow.tsx`:

```typescript
import { useState } from "react";
import { ThinkingBlock } from "../shared/ThinkingBlock";
import { ToolPill } from "../shared/ToolPill";
import { isToolEntry } from "../../hooks/useAppState";

interface Props {
  content: string;
  thinkingLog?: string[];
  breadcrumb?: boolean;
  streaming?: boolean;
  streamingThinkingEntries?: string[];
  streamingThinkingChunk?: string;
}

export function AgentRow({
  content,
  thinkingLog,
  breadcrumb,
  streaming,
  streamingThinkingEntries,
  streamingThinkingChunk,
}: Props) {
  const [copied, setCopied] = useState(false);

  const entries = thinkingLog ?? streamingThinkingEntries ?? [];
  const toolEntries = entries.filter(isToolEntry);
  const thoughtEntries = entries.filter((e) => !isToolEntry(e));

  function copy() {
    const text = [
      ...toolEntries.map((e) => e.split("(")[0].trim() + " ✓"),
      content,
    ].join("\n");
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }

  return (
    <div className="group relative flex gap-2">
      <div className="mt-0.5 flex h-[18px] w-[18px] flex-shrink-0 items-center justify-center rounded border border-accent-border bg-accent-bg text-[7px] font-semibold text-accent">
        AI
      </div>
      <div className="min-w-0 flex-1">
        {thoughtEntries.length > 0 && (
          <ThinkingBlock
            entries={thoughtEntries}
            activeChunk={streamingThinkingChunk}
            streaming={streaming}
          />
        )}
        {toolEntries.length > 0 && (
          <div className="mb-1.5 flex flex-wrap gap-1">
            {toolEntries.map((e, i) => (
              <ToolPill
                key={i}
                text={e}
                streaming={streaming && i === toolEntries.length - 1}
              />
            ))}
          </div>
        )}
        {content && (
          <div
            className={[
              "text-sm",
              breadcrumb ? "text-success" : "text-text-muted",
            ].join(" ")}
            style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}
          >
            {content}
          </div>
        )}
        {streaming && (
          <span className="inline-block h-3 w-px animate-pulse bg-accent align-middle" />
        )}
      </div>
      {!streaming && (
        <button
          onClick={copy}
          className="absolute right-0 top-0 rounded border border-border bg-surface-alt px-1.5 py-0.5 text-[10px] text-text-dim opacity-0 transition-opacity group-hover:opacity-100 hover:text-text-muted"
          title="Copy"
        >
          {copied ? "✓" : "⎘"}
        </button>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/components/messages/
git commit -m "feat(webview-ui): UserMessage, QAMessage, AgentRow components"
```

---

## Task 7: PlanCard

**Files:** `messages/PlanCard.tsx`, `test/PlanCard.test.tsx`

- [ ] **Step 1: Create PlanCard.tsx**

Create `apps/vscode-extension/webview-ui/src/components/messages/PlanCard.tsx`:

```typescript
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import { vscode } from "../../vscodeApi";

interface Props {
  content: string;
  taskId: string;
  readOnly?: boolean;
}

export function PlanCard({ content, taskId, readOnly }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [resolved, setResolved] = useState<"implemented" | "feedback" | null>(null);
  const [feedbackMode, setFeedbackMode] = useState(false);
  const [feedbackText, setFeedbackText] = useState("");

  function implement() {
    setResolved("implemented");
    vscode.postMessage({ type: "implementPlan", taskId });
  }

  function sendFeedback() {
    if (!feedbackText.trim()) return;
    setResolved("feedback");
    vscode.postMessage({ type: "planFeedback", taskId, feedback: feedbackText.trim() });
  }

  return (
    <div
      className={[
        "rounded-lg border text-sm",
        expanded ? "border-accent-border" : "border-border",
        "bg-surface overflow-hidden",
      ].join(" ")}
    >
      {/* Header */}
      <button
        onClick={() => setExpanded((o) => !o)}
        className={[
          "flex w-full items-center gap-2 px-3 py-2 text-left",
          expanded ? "bg-accent-bg" : "",
        ].join(" ")}
      >
        <span className="font-semibold text-text">📋 Plan</span>
        <span className="flex-1 text-xs text-text-dim" />
        <span className="text-xs text-accent">{expanded ? "▴ collapse" : "▾ expand"}</span>
      </button>

      {/* Body */}
      {!expanded ? (
        /* Faded preview */
        <div className="relative overflow-hidden border-t border-border" style={{ maxHeight: 72 }}>
          <div className="px-3 py-2 text-xs text-text-muted">
            <ReactMarkdown>{content}</ReactMarkdown>
          </div>
          <div
            className="pointer-events-none absolute bottom-0 left-0 right-0"
            style={{
              height: 44,
              background: "linear-gradient(to bottom, transparent, var(--color-surface))",
            }}
          />
        </div>
      ) : (
        <div className="border-t border-border px-3 py-2 text-xs text-text-muted">
          <ReactMarkdown>{content}</ReactMarkdown>
        </div>
      )}

      {/* Actions */}
      {!readOnly && (
        <div className="flex flex-wrap items-center gap-2 border-t border-border px-3 py-2">
          {resolved === "implemented" ? (
            <span className="text-xs text-success">✓ Plan approved — starting execution</span>
          ) : resolved === "feedback" ? (
            <span className="text-xs text-text-muted">↻ Feedback submitted — regenerating…</span>
          ) : feedbackMode ? (
            <>
              <textarea
                className="mono flex-1 rounded border border-border bg-surface-alt px-2 py-1 text-xs text-text placeholder-text-dim focus:border-accent-border focus:outline-none"
                rows={2}
                placeholder="Describe what to change…"
                value={feedbackText}
                onChange={(e) => setFeedbackText(e.target.value)}
              />
              <button
                onClick={sendFeedback}
                className="rounded border border-accent-border bg-accent-bg px-3 py-1 text-xs font-medium text-accent hover:bg-accent/10"
              >
                Send
              </button>
              <button
                onClick={() => setFeedbackMode(false)}
                className="rounded border border-border px-3 py-1 text-xs text-text-dim hover:text-text-muted"
              >
                Cancel
              </button>
            </>
          ) : (
            <>
              <button
                onClick={implement}
                className="flex-1 rounded border border-accent-border bg-accent-bg px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/10"
              >
                Implement
              </button>
              <button
                onClick={() => setFeedbackMode(true)}
                className="rounded border border-border px-3 py-1.5 text-xs text-text-dim hover:text-text-muted"
              >
                Give feedback
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Write tests**

Create `apps/vscode-extension/webview-ui/src/test/PlanCard.test.tsx`:

```typescript
import { describe, it, expect, vi, type Mock } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { PlanCard } from "../components/messages/PlanCard";
import { vscode } from "../vscodeApi";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

describe("PlanCard", () => {
  it("renders collapsed by default with faded preview", () => {
    render(<PlanCard content="## Plan\n- Step 1" taskId="task-1" />);
    expect(screen.getByText("▾ expand")).toBeInTheDocument();
  });

  it("expands on header click", () => {
    render(<PlanCard content="## Plan\n- Step 1" taskId="task-1" />);
    fireEvent.click(screen.getByText("📋 Plan", { exact: false }));
    expect(screen.getByText("▴ collapse")).toBeInTheDocument();
  });

  it("posts implementPlan on Implement click", () => {
    render(<PlanCard content="## Plan" taskId="task-1" />);
    fireEvent.click(screen.getByText("Implement"));
    expect(vscode.postMessage as Mock).toHaveBeenCalledWith({ type: "implementPlan", taskId: "task-1" });
  });

  it("shows feedback input on Give feedback click", () => {
    render(<PlanCard content="## Plan" taskId="task-1" />);
    fireEvent.click(screen.getByText("Give feedback"));
    expect(screen.getByPlaceholderText("Describe what to change…")).toBeInTheDocument();
  });

  it("posts planFeedback on Send with non-empty text", () => {
    render(<PlanCard content="## Plan" taskId="task-1" />);
    fireEvent.click(screen.getByText("Give feedback"));
    fireEvent.change(screen.getByPlaceholderText("Describe what to change…"), {
      target: { value: "Add error handling" },
    });
    fireEvent.click(screen.getByText("Send"));
    expect(vscode.postMessage as Mock).toHaveBeenCalledWith({
      type: "planFeedback",
      taskId: "task-1",
      feedback: "Add error handling",
    });
  });

  it("shows no action buttons when readOnly=true", () => {
    render(<PlanCard content="## Plan" taskId="task-1" readOnly />);
    expect(screen.queryByText("Implement")).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run tests**

```bash
cd apps/vscode-extension/webview-ui && npm test -- --reporter=verbose
```

Expected: PlanCard 6 tests pass.

- [ ] **Step 4: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/components/messages/PlanCard.tsx apps/vscode-extension/webview-ui/src/test/PlanCard.test.tsx
git commit -m "feat(webview-ui): PlanCard — faded preview, expand, implement, feedback"
```

---

## Task 8: DiffCard

**Files:** `messages/DiffCard.tsx`, `test/DiffCard.test.tsx`

- [ ] **Step 1: Create DiffCard.tsx**

Create `apps/vscode-extension/webview-ui/src/components/messages/DiffCard.tsx`:

```typescript
import { useState } from "react";
import type { DiffEntry } from "../../types";
import { vscode } from "../../vscodeApi";

interface Props {
  taskId: string;
  diffEntries: DiffEntry[];
  resolved?: "applied" | "discarded" | null;
  thinkingLog?: string[];
}

function DiffLine({ line }: { line: string }) {
  const isAdd = line.startsWith("+") && !line.startsWith("+++");
  const isDel = line.startsWith("-") && !line.startsWith("---");
  const isHunk = line.startsWith("@@");
  return (
    <div
      className={[
        "mono px-2 text-[11px] leading-relaxed whitespace-pre",
        isAdd ? "bg-[#0d2010] text-success" : "",
        isDel ? "bg-[#200808] text-error" : "",
        isHunk ? "text-text-dim" : "",
        !isAdd && !isDel && !isHunk ? "text-text-dim" : "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      {line}
    </div>
  );
}

export function DiffCard({ taskId, diffEntries, resolved, thinkingLog }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [activeTab, setActiveTab] = useState(0);
  const [localResolved, setLocalResolved] = useState<"applied" | "discarded" | null>(
    resolved ?? null,
  );

  function apply() {
    setLocalResolved("applied");
    vscode.postMessage({ type: "applyInlineChange", taskId });
  }

  function discard() {
    setLocalResolved("discarded");
    vscode.postMessage({ type: "discardInlineChange", taskId });
  }

  function viewFile(entry: DiffEntry) {
    vscode.postMessage({ type: "viewDiffFile", path: entry.path, shadowPath: entry.temp_path ?? "" });
  }

  const activeEntry = diffEntries[activeTab];

  return (
    <div
      className={[
        "rounded-lg border text-sm overflow-hidden",
        localResolved === "applied"
          ? "border-success/30"
          : localResolved === "discarded"
            ? "border-error/30"
            : "border-border",
        "bg-surface",
      ].join(" ")}
    >
      {/* Header */}
      <button
        onClick={() => setExpanded((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <span className="font-semibold text-text">📁 Changes ready</span>
        <span className="rounded-full border border-accent-border bg-accent-bg px-1.5 py-0.5 text-[10px] text-accent">
          {diffEntries.length}
        </span>
        <span className="flex-1" />
        <span className="text-xs text-text-dim">{expanded ? "▴" : "▾"}</span>
      </button>

      {/* Expanded diff */}
      {expanded && (
        <div className="border-t border-border">
          {/* File tabs */}
          <div className="flex overflow-x-auto border-b border-border">
            {diffEntries.map((e, i) => (
              <button
                key={i}
                onClick={() => setActiveTab(i)}
                className={[
                  "mono flex-shrink-0 border-b-2 px-3 py-1.5 text-[11px] transition-colors",
                  i === activeTab
                    ? "border-accent text-accent"
                    : "border-transparent text-text-dim hover:text-text-muted",
                ].join(" ")}
              >
                {e.path.split("/").pop()}
              </button>
            ))}
          </div>
          {/* Diff content */}
          {activeEntry && (
            <div className="max-h-48 overflow-y-auto">
              {activeEntry.unified_diff ? (
                activeEntry.unified_diff
                  .split("\n")
                  .map((line, i) => <DiffLine key={i} line={line} />)
              ) : (
                <div className="px-3 py-2 text-xs text-text-dim">
                  <span
                    className="cursor-pointer text-accent underline"
                    onClick={() => viewFile(activeEntry)}
                  >
                    View {activeEntry.path.split("/").pop()} in diff editor
                  </span>
                  <span className="ml-3 text-success">+{activeEntry.additions}</span>
                  <span className="ml-1 text-error">-{activeEntry.deletions}</span>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Actions */}
      <div className="flex flex-wrap items-center gap-2 border-t border-border px-3 py-2">
        {localResolved ? (
          <span className={`text-xs ${localResolved === "applied" ? "text-success" : "text-error"}`}>
            {localResolved === "applied" ? "✓ Applied" : "✗ Discarded"}
          </span>
        ) : (
          <>
            <button
              onClick={apply}
              className="flex-1 rounded border border-accent-border bg-accent-bg px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/10"
            >
              Accept all
            </button>
            <button
              onClick={discard}
              className="rounded border border-border px-3 py-1.5 text-xs text-text-dim hover:text-text-muted"
            >
              Reject
            </button>
          </>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Write tests**

Create `apps/vscode-extension/webview-ui/src/test/DiffCard.test.tsx`:

```typescript
import { describe, it, expect, vi, type Mock } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DiffCard } from "../components/messages/DiffCard";
import { vscode } from "../vscodeApi";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

const entries = [
  { path: "src/foo.ts", additions: 3, deletions: 1 },
  { path: "src/bar.ts", additions: 1, deletions: 0 },
];

describe("DiffCard", () => {
  it("renders file count badge", () => {
    render(<DiffCard taskId="t1" diffEntries={entries} />);
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("expands to show file tabs", () => {
    render(<DiffCard taskId="t1" diffEntries={entries} />);
    fireEvent.click(screen.getByText("📁 Changes ready", { exact: false }));
    expect(screen.getByText("foo.ts")).toBeInTheDocument();
    expect(screen.getByText("bar.ts")).toBeInTheDocument();
  });

  it("posts applyInlineChange on Accept all", () => {
    render(<DiffCard taskId="t1" diffEntries={entries} />);
    fireEvent.click(screen.getByText("Accept all"));
    expect(vscode.postMessage as Mock).toHaveBeenCalledWith({ type: "applyInlineChange", taskId: "t1" });
  });

  it("posts discardInlineChange on Reject", () => {
    render(<DiffCard taskId="t1" diffEntries={entries} />);
    fireEvent.click(screen.getByText("Reject"));
    expect(vscode.postMessage as Mock).toHaveBeenCalledWith({ type: "discardInlineChange", taskId: "t1" });
  });

  it("shows resolved state when resolved='applied'", () => {
    render(<DiffCard taskId="t1" diffEntries={entries} resolved="applied" />);
    expect(screen.getByText("✓ Applied")).toBeInTheDocument();
    expect(screen.queryByText("Accept all")).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run tests**

```bash
cd apps/vscode-extension/webview-ui && npm test
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/components/messages/DiffCard.tsx apps/vscode-extension/webview-ui/src/test/DiffCard.test.tsx
git commit -m "feat(webview-ui): DiffCard — file tabs, inline diff, accept/reject"
```

---

## Task 9: GateCard (4 variants)

**Files:** `messages/GateCard.tsx`, `test/GateCard.test.tsx`

- [ ] **Step 1: Create GateCard.tsx**

Create `apps/vscode-extension/webview-ui/src/components/messages/GateCard.tsx`:

```typescript
import { useState } from "react";
import type { LiveGateView, DiffEntry, Diagnostic } from "../../types";
import { vscode } from "../../vscodeApi";
import { DiffCard } from "./DiffCard";

type Props = LiveGateView & { isLive?: boolean };

function Resolved({ label, ok }: { label: string; ok: boolean }) {
  return (
    <span className={`text-xs ${ok ? "text-success" : "text-error"}`}>{label}</span>
  );
}

export function GateCard({ kind, taskId, payload, isLive }: Props) {
  const [resolved, setResolved] = useState<string | null>(null);

  // ── Command gate ────────────────────────────────────────────────────────────
  if (kind === "command") {
    const [scope, setScope] = useState<"exact" | "prefix" | "binary">("exact");
    const [prefixCount, setPrefixCount] = useState(1);
    const cmd = payload.command ?? "";
    const args = payload.args ?? [];
    const tokens = [cmd, ...args].filter(Boolean);
    const binary = cmd.split("/").pop() ?? cmd;

    function ruleValue() {
      if (scope === "binary") return binary;
      if (scope === "prefix") return tokens.slice(0, prefixCount).join(" ");
      return tokens.join(" ");
    }

    function decide(approve: boolean, remember: boolean) {
      setResolved(approve ? (remember ? "✓ Accepted & remembered" : "✓ Accepted once") : "✗ Rejected");
      vscode.postMessage({
        type: "commandDecision",
        taskId,
        approve,
        remember,
        scope: remember ? scope : "exact",
        ruleValue: remember ? ruleValue() : undefined,
      });
    }

    return (
      <div className="rounded-lg border border-border bg-surface text-sm overflow-hidden">
        <div className="border-b border-border px-3 py-2 font-semibold text-text">⚙ Run command?</div>
        <div className="border-b border-border px-3 py-2">
          <pre className="mono rounded bg-surface-alt px-2 py-1.5 text-xs text-text-muted">
            {tokens.join(" ")}
          </pre>
        </div>
        <div className="border-b border-border px-3 py-2 text-xs text-text-muted space-y-1">
          {(["exact", "prefix", "binary"] as const).map((s) => (
            <label key={s} className="flex items-center gap-2 cursor-pointer">
              <input
                type="radio"
                name={`scope-${taskId}`}
                value={s}
                checked={scope === s}
                onChange={() => setScope(s)}
              />
              {s === "exact" && "Exact — this command only"}
              {s === "prefix" && (
                <>
                  Prefix — first{" "}
                  <input
                    type="number"
                    min={1}
                    max={tokens.length}
                    value={prefixCount}
                    onChange={(e) => setPrefixCount(Number(e.target.value))}
                    className="w-10 rounded border border-border bg-surface-alt px-1 text-center text-text"
                  />{" "}
                  token(s)
                </>
              )}
              {s === "binary" && `Any "${binary} …"`}
            </label>
          ))}
        </div>
        <div className="flex flex-wrap gap-2 px-3 py-2">
          {resolved ? (
            <Resolved label={resolved} ok={resolved.startsWith("✓")} />
          ) : (
            <>
              <button onClick={() => decide(false, false)} className="rounded border border-border px-3 py-1.5 text-xs text-text-dim hover:text-error">Reject</button>
              <button onClick={() => decide(true, false)} className="rounded border border-border px-3 py-1.5 text-xs text-text-muted hover:text-text">Accept once</button>
              <button onClick={() => decide(true, true)} className="flex-1 rounded border border-accent-border bg-accent-bg px-3 py-1.5 text-xs font-medium text-accent">Accept &amp; remember</button>
            </>
          )}
        </div>
      </div>
    );
  }

  // ── Scope gate ──────────────────────────────────────────────────────────────
  if (kind === "scope") {
    const files = payload.files ?? [];
    function decide(approve: boolean, remember: boolean) {
      setResolved(approve ? "✓ Approved" : "✗ Rejected");
      vscode.postMessage({ type: "scopeDecision", taskId, files, decision: approve ? "approve" : "reject", remember });
    }
    return (
      <div className="rounded-lg border border-border bg-surface text-sm overflow-hidden">
        <div className="border-b border-border px-3 py-2 font-semibold text-text">📁 Scope extension requested</div>
        {payload.reason && (
          <div className="border-b border-border px-3 py-2 text-xs text-text-muted">{payload.reason}</div>
        )}
        <ul className="mono border-b border-border px-3 py-2 text-xs text-code list-none m-0">
          {files.map((f) => <li key={f}>{f}</li>)}
        </ul>
        <div className="flex flex-wrap gap-2 px-3 py-2">
          {resolved ? (
            <Resolved label={resolved} ok={resolved.startsWith("✓")} />
          ) : (
            <>
              <button onClick={() => decide(true, false)} className="flex-1 rounded border border-accent-border bg-accent-bg px-3 py-1.5 text-xs font-medium text-accent">Approve</button>
              <button onClick={() => decide(true, true)} className="rounded border border-border px-3 py-1.5 text-xs text-text-muted">Approve &amp; remember</button>
              <button onClick={() => decide(false, false)} className="rounded border border-border px-3 py-1.5 text-xs text-text-dim hover:text-error">Reject</button>
            </>
          )}
        </div>
      </div>
    );
  }

  // ── Validation gate ─────────────────────────────────────────────────────────
  if (kind === "validation") {
    const diags: Diagnostic[] = payload.diagnostics ?? [];
    function decide(accept: boolean) {
      setResolved(accept ? "✓ Accepted" : "✗ Rejected");
      vscode.postMessage({ type: "validationDecision", taskId, decision: accept ? "accept" : "reject" });
    }
    return (
      <div className="rounded-lg border border-border bg-surface text-sm overflow-hidden">
        <div className="border-b border-border px-3 py-2 font-semibold text-text">⚠ Validation failed — review</div>
        <div className="border-b border-border px-3 py-2 text-xs text-text-muted">
          These errors remained after auto-repair. Accept to proceed to review, or reject to fail.
        </div>
        {diags.length > 0 && (
          <div className="mono border-b border-border max-h-32 overflow-y-auto px-3 py-2 text-[11px] text-text-muted space-y-0.5">
            {diags.map((d, i) => (
              <div key={i}>
                <span className={d.level === "error" ? "text-error" : "text-text-dim"}>[{d.level}]</span>{" "}
                {d.message.slice(0, 400)}
              </div>
            ))}
          </div>
        )}
        <div className="flex gap-2 px-3 py-2">
          {resolved ? (
            <Resolved label={resolved} ok={resolved.startsWith("✓")} />
          ) : (
            <>
              <button onClick={() => decide(true)} className="flex-1 rounded border border-accent-border bg-accent-bg px-3 py-1.5 text-xs font-medium text-accent">Accept</button>
              <button onClick={() => decide(false)} className="rounded border border-border px-3 py-1.5 text-xs text-text-dim hover:text-error">Reject</button>
            </>
          )}
        </div>
      </div>
    );
  }

  // ── Step review gate ────────────────────────────────────────────────────────
  if (kind === "step") {
    const entries: DiffEntry[] = payload.diff_entries ?? [];
    function decide(accept: boolean) {
      setResolved(accept ? "✓ Accepted" : "✗ Discarded");
      vscode.postMessage({ type: "stepDecision", taskId, decision: accept ? "accept" : "discard" });
    }
    return (
      <div className="rounded-lg border border-border bg-surface text-sm overflow-hidden">
        <div className="border-b border-border px-3 py-2 font-semibold text-text">
          📝 Review step — {payload.step_title ?? ""}
        </div>
        <div className="px-3 py-2">
          <DiffCard taskId={taskId} diffEntries={entries} resolved={null} />
        </div>
        <div className="flex gap-2 border-t border-border px-3 py-2">
          {resolved ? (
            <Resolved label={resolved} ok={resolved.startsWith("✓")} />
          ) : (
            <>
              <button onClick={() => decide(true)} className="flex-1 rounded border border-accent-border bg-accent-bg px-3 py-1.5 text-xs font-medium text-accent">Accept</button>
              <button onClick={() => decide(false)} className="rounded border border-border px-3 py-1.5 text-xs text-text-dim hover:text-error">Discard</button>
            </>
          )}
        </div>
      </div>
    );
  }

  return null;
}
```

- [ ] **Step 2: Write tests**

Create `apps/vscode-extension/webview-ui/src/test/GateCard.test.tsx`:

```typescript
import { describe, it, expect, vi, type Mock } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { GateCard } from "../components/messages/GateCard";
import { vscode } from "../vscodeApi";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

describe("GateCard — command", () => {
  const base = { kind: "command" as const, taskId: "t1", payload: { command: "npm", args: ["test"] } };

  it("renders command text", () => {
    render(<GateCard {...base} />);
    expect(screen.getByText("npm test")).toBeInTheDocument();
  });

  it("posts approve=false on Reject", () => {
    render(<GateCard {...base} />);
    fireEvent.click(screen.getByText("Reject"));
    expect(vscode.postMessage as Mock).toHaveBeenCalledWith(
      expect.objectContaining({ type: "commandDecision", approve: false }),
    );
  });

  it("posts approve=true remember=false on Accept once", () => {
    render(<GateCard {...base} />);
    fireEvent.click(screen.getByText("Accept once"));
    expect(vscode.postMessage as Mock).toHaveBeenCalledWith(
      expect.objectContaining({ approve: true, remember: false }),
    );
  });
});

describe("GateCard — scope", () => {
  const base = { kind: "scope" as const, taskId: "t2", payload: { reason: "needs helper", files: ["src/helper.ts"] } };

  it("renders file list", () => {
    render(<GateCard {...base} />);
    expect(screen.getByText("src/helper.ts")).toBeInTheDocument();
  });

  it("posts scopeDecision approve on Approve", () => {
    render(<GateCard {...base} />);
    fireEvent.click(screen.getByText("Approve"));
    expect(vscode.postMessage as Mock).toHaveBeenCalledWith(
      expect.objectContaining({ type: "scopeDecision", decision: "approve", remember: false }),
    );
  });
});

describe("GateCard — validation", () => {
  const base = {
    kind: "validation" as const,
    taskId: "t3",
    payload: { diagnostics: [{ level: "error", message: "TS2322: type mismatch" }] },
  };

  it("renders diagnostic message", () => {
    render(<GateCard {...base} />);
    expect(screen.getByText(/TS2322/)).toBeInTheDocument();
  });

  it("posts accept=true on Accept", () => {
    render(<GateCard {...base} />);
    fireEvent.click(screen.getByText("Accept"));
    expect(vscode.postMessage as Mock).toHaveBeenCalledWith(
      expect.objectContaining({ type: "validationDecision", decision: "accept" }),
    );
  });
});

describe("GateCard — step", () => {
  const base = {
    kind: "step" as const,
    taskId: "t4",
    payload: { step_title: "Add auth check", diff_entries: [{ path: "src/auth.ts", additions: 5, deletions: 2 }] },
  };

  it("renders step title", () => {
    render(<GateCard {...base} />);
    expect(screen.getByText(/Add auth check/)).toBeInTheDocument();
  });

  it("posts stepDecision accept on Accept", () => {
    render(<GateCard {...base} />);
    fireEvent.click(screen.getByText("Accept"));
    expect(vscode.postMessage as Mock).toHaveBeenCalledWith(
      expect.objectContaining({ type: "stepDecision", decision: "accept" }),
    );
  });
});
```

- [ ] **Step 3: Run tests**

```bash
cd apps/vscode-extension/webview-ui && npm test
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/components/messages/GateCard.tsx apps/vscode-extension/webview-ui/src/test/GateCard.test.tsx
git commit -m "feat(webview-ui): GateCard — command/scope/validation/step variants"
```

---

## Task 10: ErrorCard, LiveSlot, HistoryView, InputArea, EmptyState

**Files:** 5 remaining components

- [ ] **Step 1: Create ErrorCard.tsx**

Create `apps/vscode-extension/webview-ui/src/components/messages/ErrorCard.tsx`:

```typescript
import { useState } from "react";
import { vscode } from "../../vscodeApi";

interface Props {
  taskId: string;
  stepName?: string;
  errorClass?: string;
  errorMessage?: string;
  resumeFromStep?: number;
}

export function ErrorCard({ taskId, stepName, errorClass, errorMessage, resumeFromStep }: Props) {
  const [open, setOpen] = useState(false);

  return (
    <div className="rounded-lg border border-error-border bg-error-bg text-sm overflow-hidden">
      <div className="flex items-center gap-2 border-b border-error-border px-3 py-2">
        <span>⚠️</span>
        <span className="font-semibold text-error">Execution failed</span>
      </div>
      {(stepName || errorMessage) && (
        <div className="border-b border-error-border px-3 py-2">
          <button
            onClick={() => setOpen((o) => !o)}
            className="flex items-center gap-1 text-xs text-text-muted"
          >
            <span>{open ? "▾" : "▶"}</span>
            <span>{stepName ?? "Unknown step"}{errorClass ? ` — ${errorClass}` : ""}</span>
          </button>
          {open && errorMessage && (
            <pre className="mono mt-2 max-h-20 overflow-y-auto rounded bg-error-bg px-2 py-1.5 text-[11px] text-error">
              {errorMessage}
            </pre>
          )}
        </div>
      )}
      <div className="flex flex-wrap gap-2 px-3 py-2">
        <button
          onClick={() => vscode.postMessage({ type: "sendMessage", text: `/resume ${taskId}` })}
          className="flex-1 rounded border border-accent-border bg-accent-bg px-3 py-1.5 text-xs font-medium text-accent"
        >
          ↻ Resume{resumeFromStep != null ? ` from step ${resumeFromStep}` : ""}
        </button>
        <button
          onClick={() => vscode.postMessage({ type: "sendMessage", text: `/replan ${taskId}` })}
          className="rounded border border-border px-3 py-1.5 text-xs text-text-dim hover:text-text-muted"
        >
          Re-plan
        </button>
        <button
          onClick={() => vscode.postMessage({ type: "sendMessage", text: `/discard ${taskId}` })}
          className="rounded border border-error-border px-3 py-1.5 text-xs text-error/70 hover:text-error"
        >
          Discard
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Create LiveSlot.tsx**

Create `apps/vscode-extension/webview-ui/src/components/LiveSlot.tsx`:

```typescript
import type { LiveGateView, LivePlanView } from "../types";
import { GateCard } from "./messages/GateCard";
import { PlanCard } from "./messages/PlanCard";

interface Props {
  liveGate: LiveGateView | null;
  livePlan: LivePlanView | null;
}

export function LiveSlot({ liveGate, livePlan }: Props) {
  if (!liveGate && !livePlan) return null;

  return (
    <div className="flex flex-col gap-2 px-3 py-2 flex-shrink-0">
      {liveGate && <GateCard {...liveGate} isLive />}
      {livePlan && <PlanCard content={livePlan.planMarkdown} taskId={livePlan.taskId} />}
    </div>
  );
}
```

- [ ] **Step 3: Create HistoryView.tsx**

Create `apps/vscode-extension/webview-ui/src/components/HistoryView.tsx`:

```typescript
import { useState } from "react";
import type { ThreadSummary } from "../types";
import { vscode } from "../vscodeApi";

interface Props {
  threads: ThreadSummary[];
  activeThreadId: string;
  onSelect: (threadId: string) => void;
  onNewChat: () => void;
}

export function HistoryView({ threads, activeThreadId, onSelect, onNewChat }: Props) {
  const [query, setQuery] = useState("");

  const filtered = query
    ? threads.filter((t) => (t.title || "New Chat").toLowerCase().includes(query.toLowerCase()))
    : threads;

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-3 py-2 flex-shrink-0">
        <span className="font-semibold text-text">AI Editor</span>
        <button
          onClick={onNewChat}
          className="rounded border border-accent-border px-2 py-1 text-xs text-accent hover:bg-accent-bg"
        >
          + New Chat
        </button>
      </div>

      {/* Search */}
      <div className="border-b border-border px-3 py-2 flex-shrink-0">
        <div className="flex items-center gap-2 rounded border border-border bg-surface-alt px-2 py-1.5">
          <span className="text-text-dim text-xs">⌕</span>
          <input
            className="flex-1 bg-transparent text-xs text-text placeholder-text-dim outline-none"
            placeholder="Search chats…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </div>

      {/* Thread list */}
      <div className="flex-1 overflow-y-auto">
        {filtered.length === 0 ? (
          <div className="px-3 py-6 text-center text-xs text-text-dim">
            {query ? "No matching chats" : "No chats yet"}
          </div>
        ) : (
          filtered.map((t) => (
            <button
              key={t.threadId}
              onClick={() => onSelect(t.threadId)}
              className={[
                "flex w-full items-center gap-2 border-b border-border px-3 py-2.5 text-left hover:bg-surface",
                t.threadId === activeThreadId
                  ? "border-l-2 border-l-accent bg-surface"
                  : "border-l-2 border-l-transparent",
              ].join(" ")}
            >
              <span className="flex-1 text-sm text-text line-clamp-2">
                {t.title || "New Chat"}
              </span>
              <span className="text-xs text-accent">›</span>
            </button>
          ))
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Create InputArea.tsx**

Create `apps/vscode-extension/webview-ui/src/components/InputArea.tsx`:

```typescript
import { useRef, useEffect } from "react";
import { vscode } from "../vscodeApi";

interface Props {
  enabled: boolean;
}

export function InputArea({ enabled }: Props) {
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (enabled && ref.current) ref.current.focus();
  }, [enabled]);

  function send() {
    const el = ref.current;
    if (!el) return;
    const text = el.value.trim();
    if (!text) return;
    el.value = "";
    el.style.height = "auto";
    vscode.postMessage({ type: "sendMessage", text });
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  function onInput(e: React.FormEvent<HTMLTextAreaElement>) {
    const el = e.currentTarget;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 120) + "px";
  }

  return (
    <div className="flex-shrink-0 border-t border-border px-3 py-2">
      <div
        className={[
          "flex items-end gap-2 rounded-lg border px-3 py-2 transition-colors",
          enabled ? "border-border bg-surface-alt" : "border-border/50 bg-surface opacity-50",
        ].join(" ")}
      >
        <textarea
          ref={ref}
          rows={1}
          disabled={!enabled}
          placeholder={enabled ? "Ask anything or describe a change…" : "Agent is working…"}
          onKeyDown={onKeyDown}
          onInput={onInput}
          className="mono flex-1 resize-none bg-transparent text-sm text-text placeholder-text-dim outline-none"
          style={{ minHeight: 20, maxHeight: 120 }}
        />
        <span className="flex-shrink-0 text-[10px] text-text-dim">⌘↵</span>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Create EmptyState.tsx**

Create `apps/vscode-extension/webview-ui/src/components/EmptyState.tsx`:

```typescript
import { vscode } from "../vscodeApi";

const SUGGESTIONS = [
  "💡 Add error handling to the API routes",
  "🔍 Where is the planning loop defined?",
  "🛠 Fix the TypeScript errors in editor-client",
];

export function EmptyState() {
  function fillInput(text: string) {
    // Post directly as a message so the controller receives it the same way
    vscode.postMessage({ type: "sendMessage", text });
  }

  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 px-4 py-8">
      <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-accent-border bg-accent-bg text-xl">
        ✦
      </div>
      <div className="text-center">
        <div className="text-sm font-medium text-text-muted">What are we building?</div>
        <div className="mt-1 text-xs text-text-dim">
          Describe a change, ask a question, or explore the codebase.
        </div>
      </div>
      <div className="flex w-full flex-col gap-1.5">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            onClick={() => fillInput(s.replace(/^[^\s]+\s/, ""))}
            className="rounded-md border border-border bg-surface-alt px-3 py-2 text-left text-xs text-text-dim hover:border-accent-border hover:text-text-muted"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/components/
git commit -m "feat(webview-ui): ErrorCard, LiveSlot, HistoryView, InputArea, EmptyState"
```

---

## Task 11: MessageRow, ThreadView, App, main

**Files:** `MessageRow.tsx`, `ThreadView.tsx`, `App.tsx`, `main.tsx`

- [ ] **Step 1: Create MessageRow.tsx**

Create `apps/vscode-extension/webview-ui/src/components/MessageRow.tsx`:

```typescript
import type { ChatMsg } from "../types";
import { UserMessage } from "./messages/UserMessage";
import { AgentRow } from "./messages/AgentRow";
import { QAMessage } from "./messages/QAMessage";
import { PlanCard } from "./messages/PlanCard";
import { DiffCard } from "./messages/DiffCard";
import { GateCard } from "./messages/GateCard";

interface Props {
  msg: ChatMsg;
}

export function MessageRow({ msg }: Props) {
  if ("role" in msg) {
    if (msg.role === "user") return <UserMessage content={msg.content} />;
    // Agent messages: breadcrumbs are plain text, QA answers are markdown
    const isBreadcrumb = msg.metadata?.breadcrumb === true;
    const hasMarkdown = !isBreadcrumb && msg.content.length > 0;
    if (hasMarkdown) {
      return (
        <QAMessage content={msg.content} thinkingLog={msg.metadata?.thinking_log} />
      );
    }
    return (
      <AgentRow
        content={msg.content}
        thinkingLog={msg.metadata?.thinking_log}
        breadcrumb={isBreadcrumb}
      />
    );
  }

  if (msg.type === "plan_card") {
    const taskId = msg.metadata?.taskId ?? msg.taskId ?? "";
    return <PlanCard content={msg.content} taskId={taskId} readOnly />;
  }

  if (msg.type === "diff_card") {
    const taskId = msg.taskId ?? msg.metadata?.taskId ?? "";
    const entries = msg.metadata?.diff_entries ?? [];
    return (
      <DiffCard
        taskId={taskId}
        diffEntries={entries}
        resolved={msg.metadata?.resolved ?? null}
        thinkingLog={msg.metadata?.thinking_log}
      />
    );
  }

  if (msg.type === "scope_card") {
    return (
      <GateCard
        kind="scope"
        taskId={msg.metadata.taskId}
        payload={{ files: msg.metadata.files, reason: msg.metadata.reason }}
      />
    );
  }

  if (msg.type === "validation_card") {
    return (
      <GateCard
        kind="validation"
        taskId={msg.metadata.taskId}
        payload={{ diagnostics: msg.metadata.diagnostics }}
      />
    );
  }

  if (msg.type === "command_card") {
    return (
      <GateCard
        kind="command"
        taskId={msg.metadata.taskId}
        payload={{ command: msg.metadata.command, args: msg.metadata.args }}
      />
    );
  }

  if (msg.type === "task_card") {
    return (
      <div className="rounded border border-border bg-surface px-3 py-2 text-xs text-text-muted">
        <span className="font-medium text-text">Task created</span>
        <span className="mono ml-2 text-code">{msg.taskId ?? msg.content}</span>
      </div>
    );
  }

  return null;
}
```

- [ ] **Step 2: Create ThreadView.tsx**

Create `apps/vscode-extension/webview-ui/src/components/ThreadView.tsx`:

```typescript
import { useEffect, useRef } from "react";
import type { AppState } from "../types";
import { MessageRow } from "./MessageRow";
import { AgentRow } from "./messages/AgentRow";
import { LiveSlot } from "./LiveSlot";
import { InputArea } from "./InputArea";
import { EmptyState } from "./EmptyState";
import { vscode } from "../vscodeApi";

interface Props {
  state: AppState;
  onBack: () => void;
}

export function ThreadView({ state, onBack }: Props) {
  const { messages, streaming, thinkingStatus, inputEnabled, liveGate, livePlan, threads, activeThreadId } = state;
  const bottomRef = useRef<HTMLDivElement>(null);

  const title = threads.find((t) => t.threadId === activeThreadId)?.title ?? "Chat";

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, streaming?.text]);

  function newChat() {
    vscode.postMessage({ type: "newChat" });
  }

  const isEmpty = messages.length === 0 && !streaming && !thinkingStatus;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-border px-3 py-2 flex-shrink-0">
        <button
          onClick={onBack}
          className="text-accent text-sm hover:opacity-80"
          title="Back to history"
        >
          ‹
        </button>
        <span className="flex-1 truncate text-sm font-medium text-text">{title}</span>
        <button
          onClick={newChat}
          className="rounded border border-accent-border px-2 py-1 text-xs text-accent hover:bg-accent-bg"
        >
          + New
        </button>
      </div>

      {/* Message list */}
      <div className="flex-1 overflow-y-auto px-3 py-2">
        {isEmpty ? (
          <EmptyState />
        ) : (
          <div className="flex flex-col gap-3">
            {messages.map((msg, i) => (
              <MessageRow key={i} msg={msg} />
            ))}

            {/* Thinking status (before streaming starts) */}
            {thinkingStatus && !streaming && (
              <div className="flex items-center gap-2 text-xs text-text-dim">
                <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
                {thinkingStatus}
              </div>
            )}

            {/* Streaming agent bubble */}
            {streaming && (
              <AgentRow
                content={streaming.text}
                streamingThinkingEntries={streaming.thinkingEntries}
                streamingThinkingChunk={streaming.activeThinkingChunk}
                streaming
              />
            )}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Live gate/plan slot (pinned above input) */}
      <LiveSlot liveGate={liveGate} livePlan={livePlan} />

      {/* Input */}
      <InputArea enabled={inputEnabled} />
    </div>
  );
}
```

- [ ] **Step 3: Create App.tsx**

Create `apps/vscode-extension/webview-ui/src/App.tsx`:

```typescript
import { useAppState } from "./hooks/useAppState";
import { HistoryView } from "./components/HistoryView";
import { ThreadView } from "./components/ThreadView";
import { vscode } from "./vscodeApi";

export default function App() {
  const { state, setView } = useAppState();

  function handleSelectThread(threadId: string) {
    vscode.postMessage({ type: "switchThread", threadId });
    setView("thread");
  }

  function handleNewChat() {
    vscode.postMessage({ type: "newChat" });
    setView("thread");
  }

  function handleBack() {
    setView("history");
  }

  // Auto-switch to thread view when a thread becomes active
  // (e.g. on first load when extension sends renderThreadList with an activeThreadId)
  const hasActiveThread = state.activeThreadId !== "";

  if (state.view === "history" || !hasActiveThread) {
    return (
      <HistoryView
        threads={state.threads}
        activeThreadId={state.activeThreadId}
        onSelect={handleSelectThread}
        onNewChat={handleNewChat}
      />
    );
  }

  return <ThreadView state={state} onBack={handleBack} />;
}
```

- [ ] **Step 4: Create main.tsx**

Create `apps/vscode-extension/webview-ui/src/main.tsx`:

```typescript
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

- [ ] **Step 5: Build to verify no compile errors**

```bash
cd apps/vscode-extension/webview-ui && npm run typecheck && npm run build
```

Expected: `dist/index.html` and `dist/assets/index.js` created. No TypeScript errors.

- [ ] **Step 6: Run all tests**

```bash
cd apps/vscode-extension/webview-ui && npm test
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/
git commit -m "feat(webview-ui): MessageRow, ThreadView, App, main — full UI assembled"
```

---

## Task 12: Wire up chat-panel.ts

**Files:** `src/chat-panel.ts`

- [ ] **Step 1: Update buildHtml() to load from dist**

In `apps/vscode-extension/src/chat-panel.ts`, add `import * as fs from "fs"` at the top, then replace the entire `buildHtml()` method with:

```typescript
import * as fs from "fs";
```

(Add this import alongside the existing `import * as vscode from "vscode"` line.)

Replace the `buildHtml()` method body:

```typescript
private buildHtml(): string {
  const distPath = vscode.Uri.joinPath(this.extensionUri, "webview-ui", "dist");
  const htmlPath = vscode.Uri.joinPath(distPath, "index.html");
  let html = fs.readFileSync(htmlPath.fsPath, "utf8");

  const nonce = Array.from({ length: 16 }, () =>
    Math.floor(Math.random() * 256).toString(16).padStart(2, "0"),
  ).join("");
  const cspSource = this.panel!.webview.cspSource;

  // Rewrite relative asset paths to VS Code webview URIs.
  // Vite outputs: src="./assets/index.js" href="./assets/index.css"
  html = html.replace(
    /(src|href)="\.\/(assets\/[^"]+)"/g,
    (_match, attr: string, assetPath: string) => {
      const uri = this.panel!.webview.asWebviewUri(
        vscode.Uri.joinPath(distPath, assetPath),
      );
      return `${attr}="${uri}"`;
    },
  );

  // Inject nonce into every <script> tag.
  html = html.replace(/<script /g, `<script nonce="${nonce}" `);

  // Inject CSP meta tag (must come before any other head content).
  html = html.replace(
    "<head>",
    `<head>\n<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline' ${cspSource}; script-src 'nonce-${nonce}' ${cspSource}; img-src ${cspSource} data:;">`,
  );

  return html;
}
```

- [ ] **Step 2: Update show() to use webview-ui dist as resource root**

In `chat-panel.ts`, find the `show()` method's `vscode.window.createWebviewPanel(...)` call and update `localResourceRoots`:

```typescript
localResourceRoots: [
  vscode.Uri.joinPath(this.extensionUri, "media"),
  vscode.Uri.joinPath(this.extensionUri, "webview-ui", "dist"),
],
```

Apply the same change inside `reattach()` — it calls `this.buildHtml()` which now reads from `dist/`, but `localResourceRoots` is set at panel creation time. The panel passed to `reattach()` was created by VS Code with the serialized config, so update `show()` only. (The `reattach()` path inherits the original panel's resource roots; this is a minor limitation — the extension must be reloaded after a first run with the new build.)

- [ ] **Step 3: Add webview:build script to extension package.json**

In `apps/vscode-extension/package.json`, update the `scripts` field:

```json
"scripts": {
  "webview:build": "npm --prefix webview-ui run build",
  "prebuild": "npm --prefix webview-ui install && npm --prefix webview-ui run build",
  "build": "tsc -p tsconfig.json",
  "test": "vitest run",
  "typecheck": "tsc -p tsconfig.json --noEmit"
},
```

- [ ] **Step 4: Typecheck the extension**

```bash
npm run -w @ai-editor/vscode-extension typecheck
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/src/chat-panel.ts apps/vscode-extension/package.json
git commit -m "feat(chat-panel): load React webview from webview-ui/dist instead of inline HTML"
```

---

## Task 13: Remove ReviewPanel + clean up controller and extension

**Files:** `src/extension.ts`, `src/controller.ts`

- [ ] **Step 1: Remove showStepReview + updatePanel from ControllerUI (controller.ts)**

In `apps/vscode-extension/src/controller.ts`:

1. Remove these two lines from the `ControllerUI` interface:
   ```typescript
   updatePanel(model: ReviewPanelViewModel): void;
   showStepReview(taskId: string, stepId: string, stepTitle: string, diffEntries: DiffEntry[]): void;
   ```

2. Remove the `ReviewPanelViewModel` import from line 21.

3. Find the `step_review_requested` event handler (around line 943) and remove the `this.ui.showStepReview(...)` call — the live gate mechanism handles it via the `/live` poll:
   ```typescript
   } else if (event.type === "step_review_requested") {
     // step review surfaces via the live gate (/live poll → renderLiveGate kind:step)
   }
   ```

4. Find `this.ui.updatePanel(this.buildViewModel())` (line 1236) and remove it. Also remove `openReviewPanel()` method body content — replace with a redirect to chat:
   ```typescript
   openReviewPanel(): void {
     this.ui.openChatPanel();
   }
   ```

5. Remove the `buildViewModel()` private method entirely (it exists only to feed `updatePanel`). If it's referenced elsewhere, replace those calls with no-ops. Search:
   ```bash
   grep -n "buildViewModel\|updatePanel" apps/vscode-extension/src/controller.ts
   ```
   Remove all found call sites.

- [ ] **Step 2: Remove ReviewPanel from extension.ts**

In `apps/vscode-extension/src/extension.ts`:

1. Remove `import { ReviewPanel } from "./review-panel.js"` (line 12).
2. Remove the `panel` variable declaration and `new ReviewPanel(...)` block (lines 38–61).
3. Remove `showStepReview` and `updatePanel` from the `ui` object (lines 166–168 and 87–88).
4. Remove the `aiEditor.openReviewPanel` command registration block (lines 195–199).
5. Remove `panel.show()` from the `aiEditor.startTask` handler and `aiEditor.attachToTask` handler.
6. Remove `panel.dispose()` from the dispose subscription.

After changes, the `ui` object will no longer reference `panel` at all.

- [ ] **Step 3: Typecheck**

```bash
npm run -w @ai-editor/vscode-extension typecheck
```

Fix any remaining type errors (typically: unused imports, missing interface members in the mock `ui` object in tests).

- [ ] **Step 4: Run extension tests**

```bash
npm run -w @ai-editor/vscode-extension test
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/src/extension.ts apps/vscode-extension/src/controller.ts
git commit -m "feat(extension): retire ReviewPanel — step review now via live gate only"
```

---

## Task 14: Delete old files

**Files:** `media/chat.js`, `media/marked.umd.js`, `src/review-panel.ts`

- [ ] **Step 1: Delete the three files**

```bash
git rm apps/vscode-extension/media/chat.js
git rm apps/vscode-extension/media/marked.umd.js
git rm apps/vscode-extension/src/review-panel.ts
```

- [ ] **Step 2: Remove marked dependency from extension package.json**

In `apps/vscode-extension/package.json`, remove `"marked": "^18.0.3"` from `dependencies`.

Run `npm install` to update the lockfile:

```bash
npm install
```

- [ ] **Step 3: Typecheck + test**

```bash
npm run typecheck && npm run test
```

Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add apps/vscode-extension/package.json package-lock.json
git commit -m "chore(extension): delete chat.js, marked.umd.js, review-panel.ts"
```

---

## Task 15: Integration smoke test

- [ ] **Step 1: Full monorepo build**

```bash
npm run build
```

Expected: `@ai-editor/editor-client` builds first, then `@ai-editor/vscode-extension` (which triggers `prebuild` → `webview-ui` builds). No errors.

- [ ] **Step 2: Full test suite**

```bash
npm run test
```

Expected: all workspace tests pass (editor-client + vscode-extension + webview-ui).

- [ ] **Step 3: Open in extension dev host**

```bash
code --extensionDevelopmentPath="$PWD/apps/vscode-extension" "$PWD/workspaces/shadow-forge-stress"
```

Open the AI Editor Chat panel (`Cmd+Shift+P` → AI Editor: Open Chat). Verify:
- History view appears with `+ New Chat`
- Sending a message shows the user bubble right-aligned
- Agent streaming shows the pulse dot + typing cursor
- Tool pills appear for tool calls (if backend is running)
- Plan card appears collapsed with faded preview
- Gate cards render and post decisions correctly

- [ ] **Step 4: Final commit if any fixes were needed**

```bash
git add -p   # stage only intentional fixes
git commit -m "fix(webview-ui): integration fixes from dev host smoke test"
```
