import { useReducer, useEffect, useCallback } from "react";
import type { AppState, ExtensionMessage, ChatMsg, StreamingBubble, ToolEventView } from "../types";
import { vscode } from "../vscodeApi";

// ── Stable content signatures ────────────────────────────────────────────────

/** djb2 over a string, base36 — stable content signature (also used by LiveSlot keys). */
export function sig(s: string): string {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36);
}

/** Plan-card version signature: same task + identical content collapses; a new
 *  feedback-regenerated version gets a distinct signature and appends. Mirrors chat.js.
 *  The content length is prefixed so a bare 32-bit djb2 collision can't silently DROP a
 *  genuinely-new plan version — both the length AND the hash must collide. */
export function planSig(taskId: string, content: string): string {
  return `${content.length.toString(36)}.${sig(`${taskId}::${content}`)}`;
}

// ── Initial state ────────────────────────────────────────────────────────────

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
  liveReview: null,
  liveError: null,
  liveTodos: null,
  liveSessions: null,
  sessionTranscripts: {},
  workbar: null,
  retryStatus: null,
  liveStatus: null,
  turnActive: false,
};

// ── Action types ─────────────────────────────────────────────────────────────

type Action =
  | { type: "EXT"; msg: ExtensionMessage; at: string }
  | { type: "SET_VIEW"; view: "history" | "thread" };

// ── Helpers ──────────────────────────────────────────────────────────────────

function ensureStreaming(state: AppState): StreamingBubble {
  return state.streaming ?? {
    text: "",
    thinkingEntries: [],
    activeThinkingChunk: "",
    toolEvents: [],
  };
}

/**
 * Convert the current streaming bubble into a persisted agent message and
 * append it to messages. If the bubble is completely empty (no text, no
 * thinking entries, no tool events), just clear it without appending.
 */
function sealStreaming(state: AppState, at: string): AppState {
  if (!state.streaming) return state;

  const bubble = state.streaming;

  // Seal any trailing activeThinkingChunk as a final thinking_log entry.
  const entries: string[] = bubble.activeThinkingChunk
    ? [...bubble.thinkingEntries, bubble.activeThinkingChunk]
    : [...bubble.thinkingEntries];

  const isEmpty =
    bubble.text === "" && entries.length === 0 && bubble.toolEvents.length === 0;

  if (isEmpty) {
    return { ...state, streaming: null, thinkingStatus: null };
  }

  const metadata: Record<string, unknown> = {};
  if (entries.length > 0) metadata.thinking_log = entries;
  if (bubble.toolEvents.length > 0) metadata.tool_events = bubble.toolEvents;

  const msg: ChatMsg = {
    role: "agent",
    content: bubble.text,
    type: "text",
    timestamp: at,
    metadata,
  };

  return {
    ...state,
    streaming: null,
    thinkingStatus: null,
    messages: [...state.messages, msg],
  };
}

// ── Reducer ──────────────────────────────────────────────────────────────────

function reducer(state: AppState, action: Action): AppState {
  if (action.type === "SET_VIEW") {
    return { ...state, view: action.view };
  }

  // EXT actions — switch on the extension message type
  const { msg, at } = action;

  switch (msg.type) {
    case "renderThreadList":
      return { ...state, threads: msg.threads, activeThreadId: msg.activeThreadId };

    case "clearThread":
      return {
        ...state,
        messages: [],
        streaming: null,
        thinkingStatus: null,
        workbar: null,
        retryStatus: null,
      };

    case "setInputEnabled":
      return { ...state, inputEnabled: msg.enabled };

    case "showThinking":
    case "updateThinking":
      return { ...state, thinkingStatus: msg.message, retryStatus: null };

    case "hideThinking":
      return { ...state, thinkingStatus: null };

    case "appendChunk": {
      const prev = ensureStreaming(state);
      // If this is the first text and there is an open activeThinkingChunk,
      // seal it into thinkingEntries before appending text.
      // Protocol assumption: thinking chunks always precede response text within a turn; a thinking chunk arriving AFTER text would only be sealed at finalize.
      const updatedEntries =
        prev.text === "" && prev.activeThinkingChunk
          ? [...prev.thinkingEntries, prev.activeThinkingChunk]
          : prev.thinkingEntries;
      const sealedChunk = prev.text === "" && prev.activeThinkingChunk ? "" : prev.activeThinkingChunk;
      return {
        ...state,
        thinkingStatus: null,
        retryStatus: null,
        streaming: {
          ...prev,
          text: prev.text + msg.chunk,
          thinkingEntries: updatedEntries,
          activeThinkingChunk: sealedChunk,
        },
      };
    }

    case "appendThinkingEntry": {
      const prev = ensureStreaming(state);
      // Seal any open activeThinkingChunk first, then append the new entry.
      const entries: string[] = prev.activeThinkingChunk
        ? [...prev.thinkingEntries, prev.activeThinkingChunk]
        : [...prev.thinkingEntries];
      return {
        ...state,
        retryStatus: null,
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
        retryStatus: null,
        streaming: {
          ...prev,
          activeThinkingChunk: prev.activeThinkingChunk + msg.chunk,
        },
      };
    }

    case "appendToolEvent": {
      // Dedup against a loaded in-flight pills message (switch-back to an active turn):
      // the resumed live stream replays already-persisted pills, which share the same
      // call_index id. Skipping them prevents a transient tail-duplication; genuinely new
      // pills (ids beyond the persisted set) still render. Scoped to inflight-marked
      // messages so a prior turn's sealed pills (same per-turn ids) never false-match.
      const alreadyPersisted = state.messages.some(
        (m) =>
          (m.metadata?.inflight_turn_id as string | undefined) !== undefined &&
          ((m.metadata?.tool_events as ToolEventView[] | undefined) ?? []).some(
            (t) => t.id === msg.event.id,
          ),
      );
      if (alreadyPersisted) return state;
      const prev = ensureStreaming(state);
      return {
        ...state,
        retryStatus: null,
        streaming: {
          ...prev,
          toolEvents: [...prev.toolEvents, { ...msg.event, done: false }],
        },
      };
    }

    case "appendToolResult": {
      const prev = ensureStreaming(state);
      if (prev.toolEvents.some((t) => t.id === msg.id)) {
        return {
          ...state,
          streaming: {
            ...prev,
            toolEvents: prev.toolEvents.map((t) =>
              t.id === msg.id
                ? { ...t, output: msg.output, isError: msg.isError, done: true }
                : t,
            ),
          },
        };
      }
      // The pill was sealed into a transcript message before its result arrived
      // (a gate breadcrumb seals the bubble mid-command — run_command's result
      // lands after approval). Patch the sealed copy or it spins forever.
      // Extension ids are session-monotonic and persisted pills are always
      // done:true, so matching id && !done cannot hit a reloaded pill.
      for (let i = state.messages.length - 1; i >= 0; i--) {
        const events = state.messages[i].metadata?.tool_events as
          | ToolEventView[]
          | undefined;
        if (!events?.some((t) => t.id === msg.id && !t.done)) continue;
        const messages = [...state.messages];
        messages[i] = {
          ...messages[i],
          metadata: {
            ...messages[i].metadata,
            tool_events: events.map((t) =>
              t.id === msg.id
                ? { ...t, output: msg.output, isError: msg.isError, done: true }
                : t,
            ),
          },
        };
        return { ...state, messages };
      }
      return state;
    }

    case "finalizeAgentMessage":
      return sealStreaming(state, at);

    case "appendMessage": {
      // Any persisted message arriving mid-stream implicitly terminates the open bubble — protocol guarantee: the extension finalizes or appends in order, so sealing here is safe for ALL card types.
      const next = sealStreaming(state, at);
      const m = msg.message;

      if (m.type === "plan_card") {
        const taskId = (m.metadata?.taskId as string) ?? m.taskId ?? "";
        const s = planSig(taskId, m.content);
        // Dedup: if the exact same plan version is already in the transcript, skip.
        if (next.messages.some((existing) => existing._sig === s)) {
          return next;
        }
        return {
          ...next,
          messages: [...next.messages, { ...m, _sig: s }],
        };
      }

      if (m.type === "diff_card") {
        const taskId = m.taskId ?? (m.metadata?.taskId as string) ?? "";
        return {
          ...next,
          messages: [...next.messages, { ...m, taskId }],
        };
      }

      return { ...next, messages: [...next.messages, m] };
    }

    case "resolveInlineChangeCard":
      return {
        ...state,
        messages: state.messages.map((m) => {
          if (
            m.type === "diff_card" &&
            (m.taskId === msg.taskId || m.metadata?.taskId === msg.taskId)
          ) {
            return { ...m, metadata: { ...m.metadata, resolved: msg.resolution } };
          }
          return m;
        }),
      };

    case "thread_title_updated":
      return {
        ...state,
        threads: state.threads.map((t) =>
          t.threadId === msg.payload.thread_id
            ? { ...t, title: msg.payload.title }
            : t,
        ),
      };

    case "renderLiveGate":
      return { ...state, liveGate: msg.gate };

    case "clearLiveGate":
      return { ...state, liveGate: null };

    case "renderLivePlan":
      return { ...state, livePlan: msg.plan };

    case "clearLivePlan":
      return { ...state, livePlan: null };

    case "renderLiveReview":
      return { ...state, liveReview: msg.review };

    case "clearLiveReview":
      return { ...state, liveReview: null };

    case "renderLiveError":
      return { ...state, liveError: msg.error };

    case "clearLiveError":
      return { ...state, liveError: null };

    case "renderLiveTodos":
      return { ...state, liveTodos: msg.todos };

    case "clearLiveTodos":
      return { ...state, liveTodos: null };

    case "renderLiveSessions":
      return { ...state, liveSessions: msg.sessions };

    case "clearLiveSessions":
      // Transcripts are per-session views — stale once the strip empties.
      return { ...state, liveSessions: null, sessionTranscripts: {} };

    case "sessionTranscript":
      return {
        ...state,
        sessionTranscripts: {
          ...state.sessionTranscripts,
          [msg.sessionId]: msg.transcript,
        },
      };

    case "updateWorkbar":
      return { ...state, workbar: msg.info };

    case "updateRetryStatus":
      return { ...state, retryStatus: msg.status };

    case "liveStatus": {
      const turnActive = msg.turnActive ?? false;
      // Durable reconciliation (spec §10): /live is the source of truth for turn
      // liveness. On the live-resume path, a webview reopened mid-turn can MISS the
      // chat_done SSE (it fired during the reload window — before the channel
      // re-subscribe and outside the 50-event replay buffer), leaving the streaming
      // bubble + inputEnabled=false wedged so the composer is stuck on "Agent is
      // working…" forever. When /live reports a CONTROLLER turn has ended (status===null
      // → no task), re-enable input and seal any lingering bubble (sealStreaming
      // preserves text/thinking/pills — no data loss). Re-enable even with NO bubble:
      // a turn that errored before any broadcast leaves inputEnabled=false with nothing
      // to seal, and that case must unwedge too. Gated to status===null so this never
      // touches input during TASK execution — a task keeps turnActive=false throughout
      // and its composer disable is governed by liveStatus precedence, not here.
      const controllerTurnEnded =
        !turnActive && msg.status == null && (state.streaming != null || !state.inputEnabled);
      if (controllerTurnEnded) {
        const sealed = state.streaming ? sealStreaming(state, at) : state;
        return { ...sealed, liveStatus: msg.status, turnActive, inputEnabled: true, retryStatus: null };
      }
      return { ...state, liveStatus: msg.status, turnActive };
    }

    default:
      return state;
  }
}

// ── Hook ─────────────────────────────────────────────────────────────────────

export function useAppState() {
  const [state, dispatch] = useReducer(reducer, INITIAL);

  useEffect(() => {
    const handler = (event: MessageEvent<ExtensionMessage>) => {
      // The window message bus is shared — guard against foreign/malformed
      // posts (devtools, browser extensions) that would crash the reducer.
      const data: unknown = event.data;
      if (data === null || typeof data !== "object" || typeof (data as { type?: unknown }).type !== "string") {
        return;
      }
      dispatch({ type: "EXT", msg: data as ExtensionMessage, at: new Date().toISOString() });
    };
    window.addEventListener("message", handler);
    vscode.postMessage({ type: "webviewReady" });
    return () => window.removeEventListener("message", handler);
  }, []);

  const setView = useCallback(
    (view: "history" | "thread") => dispatch({ type: "SET_VIEW", view }),
    [],
  );

  return { state, setView };
}
