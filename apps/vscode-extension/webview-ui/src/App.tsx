import { useState } from "react";
import { useAppState } from "./hooks/useAppState";
import { vscode } from "./vscodeApi";
import { HistoryView } from "./components/HistoryView";
import { ThreadView } from "./components/ThreadView";

export default function App() {
  const { state, setView } = useAppState();
  const [dismissedErrorTaskId, setDismissedErrorTaskId] = useState<string | null>(null);

  const navLocked = !state.inputEnabled;

  // Render history when explicitly chosen OR no active thread selected yet.
  if (state.view === "history" || !state.activeThreadId) {
    return (
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
    );
  }

  return (
    <ThreadView
      state={state}
      onBack={() => {
        if (!navLocked) setView("history");
      }}
      dismissedErrorTaskId={dismissedErrorTaskId}
      onDismissError={setDismissedErrorTaskId}
    />
  );
}
