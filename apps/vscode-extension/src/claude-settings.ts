import type { BackendTaskClient, ClaudeAccount, ClaudeSignIn, ClaudeModel } from "@crucible/editor-client";

export type ClaudeInMsg =
  | { type: "settings/claudeLoad" }
  | { type: "settings/claudeModels"; registrationId: string; refresh?: boolean }
  | { type: "settings/claudeSignIn" }
  | { type: "settings/claudeCancel" }
  | { type: "settings/claudeSignOut"; registrationId: string }
  | { type: "settings/useClaude"; registrationId: string; model: string };

export type ClaudeOutMsg =
  | { type: "settings/claude"; accounts: ClaudeAccount[]; activeRegistrationId: string | null }
  | { type: "settings/claudeSignIn"; signIn: ClaudeSignIn }
  | { type: "settings/claudeError"; message: string; registrationId?: string }
  | { type: "settings/claudeModels"; registrationId: string; models: ClaudeModel[] }
  | { type: "settings/claudeSignedOut"; registrationId: string };

export interface ClaudeDeps {
  client: Pick<BackendTaskClient, "startClaudeSignIn" | "getClaudeSignIn" | "cancelClaudeSignIn"
    | "listClaudeAccounts" | "listClaudeModels" | "signOutClaude" | "setProvider">;
  openExternal(url: string): Promise<void>;
  activeRegistration(): string | undefined;
  saveActiveRegistration(id: string): Promise<void>;
  saveContextWindow(tokens: number): Promise<void>;
  sleep(ms: number): Promise<void>;
  now(): number;
}

export function isClaudeMessage(msg: { type: string }): msg is ClaudeInMsg {
  return msg.type.startsWith("settings/claude") || msg.type === "settings/useClaude";
}

export function createClaudeHandler(deps: ClaudeDeps, post: (msg: ClaudeOutMsg) => void,
                                    afterMutation: (kind: "connection" | "provider") => Promise<void>) {
  let pending: string | null = null;
  let starting = false;
  const accounts = async () => post({ type: "settings/claude",
    accounts: await deps.client.listClaudeAccounts(),
    activeRegistrationId: deps.activeRegistration() ?? null });

  return async (msg: ClaudeInMsg): Promise<void> => {
    try {
      switch (msg.type) {
        case "settings/claudeLoad": await accounts(); return;
        case "settings/claudeModels":
          post({ type: "settings/claudeModels", registrationId: msg.registrationId,
            models: await deps.client.listClaudeModels(msg.registrationId, msg.refresh) }); return;
        case "settings/claudeSignIn": {
          if (starting || pending) return;
          starting = true;
          let started: ClaudeSignIn & { authorizeUrl: string };
          try { started = await deps.client.startClaudeSignIn(); }
          finally { starting = false; }
          pending = started.attemptId;
          const { authorizeUrl: _url, ...publicStatus } = started;
          post({ type: "settings/claudeSignIn", signIn: publicStatus });
          try {
            const url = new URL(started.authorizeUrl);
            if (url.origin !== "https://claude.ai" || url.pathname !== "/oauth/authorize"
                || url.username || url.password) throw new Error("Invalid Claude sign-in destination.");
            await deps.openExternal(started.authorizeUrl);
            const deadline = deps.now() + 11 * 60_000;
            let status: ClaudeSignIn = started;
            while (status.state === "pending" && pending === started.attemptId && deps.now() < deadline) {
              await deps.sleep(1000);
              if (pending !== started.attemptId) return;
              status = await deps.client.getClaudeSignIn(started.attemptId);
            }
            if (pending !== started.attemptId) return;
            if (status.state === "pending") status = await deps.client.cancelClaudeSignIn(started.attemptId);
            pending = null;
            post({ type: "settings/claudeSignIn", signIn: status });
            if (status.state === "succeeded") await afterMutation("connection");
            await accounts();
          } catch (err) {
            pending = null;
            await deps.client.cancelClaudeSignIn(started.attemptId).catch(() => {});
            throw err;
          }
          return;
        }
        case "settings/claudeCancel": {
          const id = pending;
          pending = null;
          if (id) {
            const status = await deps.client.cancelClaudeSignIn(id);
            post({ type: "settings/claudeSignIn", signIn: status });
            // Consent may have completed just before cancellation reached the backend.
            if (status.state === "succeeded") await afterMutation("connection");
            await accounts();
          }
          return;
        }
        case "settings/useClaude": {
          if (!msg.model.trim()) throw new Error("Choose a Claude model.");
          // setProvider already validates before swapping; avoid spending quota on two pings.
          const result = await deps.client.setProvider({ backend: "claude_subscription", model: msg.model.trim(),
            credentials: { CRUCIBLE_CLAUDE_REGISTRATION: msg.registrationId } });
          await deps.saveActiveRegistration(msg.registrationId);
          if (result.contextWindow !== undefined) await deps.saveContextWindow(result.contextWindow);
          await afterMutation("provider");
          await accounts();
          return;
        }
        case "settings/claudeSignOut": {
          await deps.client.signOutClaude(msg.registrationId);
          post({ type: "settings/claudeSignedOut", registrationId: msg.registrationId });
          await afterMutation("connection");
          await accounts();
          return;
        }
      }
    } catch (err) {
      post({ type: "settings/claudeError", message: err instanceof Error ? err.message : String(err),
        ...(msg.type === "settings/claudeModels" ? { registrationId: msg.registrationId } : {}) });
    }
  };
}
