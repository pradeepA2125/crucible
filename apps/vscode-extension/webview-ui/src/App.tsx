import { useState } from "react";
import type { DraftPort } from "./drafts/types";
import { useAppState } from "./hooks/useAppState";
import { vscode } from "./vscodeApi";
import { HistoryView } from "./components/HistoryView";
import { ChatAdmissionStatus, ThreadView } from "./components/ThreadView";
import { FullAccessFirstSendDialog } from "./components/FullAccessFirstSendDialog";

export default function App({ draftStore }: { draftStore?: DraftPort } = {}) {
  const { state, setView, returnToHistory } = useAppState(draftStore);
  const [dismissedErrorTaskId, setDismissedErrorTaskId] = useState<string | null>(null);

  const [leaving, setLeaving] = useState(false);
  const [draftError, setDraftError] = useState("");
  const navigationError = draftError || (state.historyNavigationBlocked ? "The conversation changed while saving. Retry Back from the current conversation." : "");
  const navLocked = (!state.historyState?.navigationSafe && (!state.inputEnabled || state.turnActive)) || !!state.chatAdmission || leaving;

  // Render history when explicitly chosen OR no active thread selected yet.
  // Mounted outside both views: a host question must not be lost when the view switches.
  const firstSendDialog = <FullAccessFirstSendDialog />;
  if (state.view === "history" || !state.activeThreadId) {
    return (
      <>
      {firstSendDialog}
      <div className="flex h-full min-h-0 flex-col">
      <ChatAdmissionStatus admission={state.chatAdmission} />
      <div className="min-h-0 flex-1">
      <HistoryView
        threads={state.threads}
        activeThreadId={state.activeThreadId}
        navLocked={navLocked}
        onSelect={(id) => {
          if (navLocked) return;
          vscode.postMessage({ type: "switchThread", threadId: id });
          setView("thread");
        }}
        onNewChat={() => {
          if (navLocked) return;
          vscode.postMessage({ type: "newChat" });
          setView("thread");
        }}
      />
      </div>
      </div>
      </>
    );
  }

  return (
    <>
    {firstSendDialog}
    {navigationError && <p role="alert" className="px-4 py-2 text-meta text-text-2">{navigationError}</p>}
    <ThreadView
      state={state}
      draftStore={draftStore}
      onBack={() => {
        if (navLocked) return;
        if (!draftStore?.flush) { returnToHistory(state.attachmentGeneration ?? 0); return; }
        setLeaving(true); setDraftError("");
        const attachment = state.attachmentGeneration ?? 0;
        void draftStore.flush().then(() => {
          returnToHistory(attachment);
        }, error => {
          setDraftError(error instanceof Error ? error.message : "Draft could not be saved. Retry before leaving.");
        }).finally(() => setLeaving(false));
      }}
      dismissedErrorTaskId={dismissedErrorTaskId}
      onDismissError={setDismissedErrorTaskId}
    />
    </>
  );
}
