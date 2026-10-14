import { useState } from "react";
import { useAppState } from "./hooks/useAppState";
import { vscode } from "./vscodeApi";
import { HistoryView } from "./components/HistoryView";
import { ThreadView } from "./components/ThreadView";
import { FullAccessFirstSendDialog } from "./components/FullAccessFirstSendDialog";

export default function App() {
  const { state, setView } = useAppState();
  const [dismissedErrorTaskId, setDismissedErrorTaskId] = useState<string | null>(null);

  const navLocked = !state.inputEnabled;

  // Render history when explicitly chosen OR no active thread selected yet.
  // Mounted outside both views: a host question must not be lost when the view switches.
  const firstSendDialog = <FullAccessFirstSendDialog />;
  if (state.view === "history" || !state.activeThreadId) {
    return (
      <>
      {firstSendDialog}
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
      </>
    );
  }

  return (
    <>
    {firstSendDialog}
    <ThreadView
      state={state}
      onBack={() => {
        if (!navLocked) setView("history");
      }}
      dismissedErrorTaskId={dismissedErrorTaskId}
      onDismissError={setDismissedErrorTaskId}
    />
    </>
  );
}
