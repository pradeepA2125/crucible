import type { ChatGPTAccount, ChatGPTModel, ChatGPTSignIn } from "@crucible/editor-client";
import { ProviderAccessError } from "@crucible/editor-client";

// vscode-free host side of "Use your ChatGPT plan" (spec 2026-10-06 §6). The backend
// owns OAuth and tokens; this module only starts a sign-in, hands the authorize URL
// to the system browser, follows the attempt to its end, and switches the provider.

/** "Manage usage" opens ChatGPT's Usage page, where the user reviews usage and per-app
 * limits (UI/UX guidelines; URL confirmed from the ChatGPT UI, 2026-10-07). */
export const CHATGPT_USAGE_URL = "https://chatgpt.com/settings/usage?tab=overview";

// Only these destinations can be opened from a webview message: the panel must not
// become a way to open arbitrary URLs.
const EXTERNAL_ALLOWLIST = ["https://chatgpt.com/", "https://help.openai.com/"];

const POLL_INTERVAL_MS = 1000;
// The backend expires an attempt after 10 minutes; stop following shortly after.
const POLL_DEADLINE_MS = 11 * 60 * 1000;
const CHATGPT_BACKEND = "chatgpt";

export type ChatGPTInMsg =
  | { type: "settings/chatgptLoad" }
  | { type: "settings/chatgptSignIn"; registrationId?: string; reconsent?: boolean }
  | { type: "settings/chatgptCancel" }
  | { type: "settings/chatgptModels"; registrationId: string }
  | { type: "settings/useChatGPT"; registrationId: string; model: string; contextWindow?: number }
  | { type: "settings/chatgptSignOut"; registrationId: string }
  | { type: "settings/chatgptWelcomed"; registrationId: string }
  | { type: "settings/openExternal"; url: string };

export type ChatGPTOutMsg =
  | { type: "settings/chatgpt"; accounts: ChatGPTAccount[]; activeRegistrationId: string | null }
  | { type: "settings/chatgptError"; message: string; kind?: string }
  | { type: "settings/chatgptSignIn"; signIn: ChatGPTSignIn }
  | { type: "settings/chatgptModels"; registrationId: string; models: ChatGPTModel[] }
  | { type: "settings/chatgptSignedOut"; registrationId: string; remoteRevoked: boolean }
  // First sign-in with plan usage only (UI/UX guidelines: "Confirm ChatGPT plan use
  // after the first sign-in" — never again on later sign-ins).
  | { type: "settings/chatgptWelcome"; registrationId: string };

export interface ChatGPTDeps {
  client: {
    startChatGPTSignIn(req: { registrationId?: string; reconsent?: boolean }): Promise<ChatGPTSignIn & { authorizeUrl: string }>;
    getChatGPTSignIn(attemptId: string): Promise<ChatGPTSignIn>;
    cancelChatGPTSignIn(attemptId: string): Promise<ChatGPTSignIn>;
    listChatGPTAccounts(): Promise<ChatGPTAccount[]>;
    signOutChatGPT(registrationId: string): Promise<{ remoteRevoked: boolean }>;
    listChatGPTModels(registrationId: string): Promise<ChatGPTModel[]>;
    validateProvider(req: { backend: string; model?: string; credentials?: Record<string, string> }): Promise<{ ok: boolean; error?: string | undefined }>;
    setProvider(req: { backend: string; model?: string; credentials?: Record<string, string>; contextWindow?: number }): Promise<{ backend: string; model: string }>;
  };
  /** Opens the system browser. Never logs the URL (it can carry an ID token hint). */
  openExternal(url: string): Promise<void>;
  /** The registration the managed backend uses (spawn env), if any. */
  activeRegistration(): string | undefined;
  saveActiveRegistration(registrationId: string): Promise<void>;
  saveContextWindow(tokens: number): Promise<void>;
  welcomedRegistrations(): string[];
  markWelcomed(registrationId: string): Promise<void>;
  sleep(ms: number): Promise<void>;
  now(): number;
}

export const REGISTRATION_ENV = "CRUCIBLE_CHATGPT_REGISTRATION";

export function isChatGPTMessage(msg: { type: string }): msg is ChatGPTInMsg {
  return msg.type.startsWith("settings/chatgpt") || msg.type === "settings/useChatGPT"
    || msg.type === "settings/openExternal";
}

/** Handler for the ChatGPT messages. `afterProviderChange` re-posts the settings
 * snapshot (the provider row changes when an account is put to use). */
export function createChatGPTHandler(
  deps: ChatGPTDeps,
  post: (msg: ChatGPTOutMsg) => void,
  afterProviderChange: () => Promise<void>,
): (msg: ChatGPTInMsg) => Promise<void> {
  let pendingAttempt: string | null = null;

  const postAccounts = async (): Promise<void> => {
    post({
      type: "settings/chatgpt",
      accounts: await deps.client.listChatGPTAccounts(),
      activeRegistrationId: deps.activeRegistration() ?? null,
    });
  };

  const fail = (err: unknown): void => {
    post({
      type: "settings/chatgptError",
      message: err instanceof Error ? err.message : String(err),
      ...(err instanceof ProviderAccessError ? { kind: err.kind } : {}),
    });
  };

  const followSignIn = async (started: ChatGPTSignIn): Promise<ChatGPTSignIn> => {
    let status = started;
    const deadline = deps.now() + POLL_DEADLINE_MS;
    while (status.state === "pending" && pendingAttempt === started.attemptId
           && deps.now() < deadline) {
      await deps.sleep(POLL_INTERVAL_MS);
      status = await deps.client.getChatGPTSignIn(started.attemptId);
    }
    return status;
  };

  return async (msg) => {
    try {
      switch (msg.type) {
        case "settings/chatgptLoad": {
          await postAccounts();
          return;
        }
        case "settings/chatgptSignIn": {
          const { authorizeUrl, ...started } = await deps.client.startChatGPTSignIn({
            ...(msg.registrationId ? { registrationId: msg.registrationId } : {}),
            ...(msg.reconsent ? { reconsent: true } : {}),
          });
          pendingAttempt = started.attemptId;
          post({ type: "settings/chatgptSignIn", signIn: started });
          await deps.openExternal(authorizeUrl);
          const done = await followSignIn(started);
          if (pendingAttempt === started.attemptId) pendingAttempt = null;
          post({ type: "settings/chatgptSignIn", signIn: done });
          const reg = done.registrationId;
          if (done.state === "succeeded" && reg && done.planEnabled && done.firstPlanSignIn
              && !deps.welcomedRegistrations().includes(reg)) {
            post({ type: "settings/chatgptWelcome", registrationId: reg });
          }
          await postAccounts();
          return;
        }
        case "settings/chatgptCancel": {
          const attempt = pendingAttempt;
          pendingAttempt = null;
          if (attempt) {
            post({ type: "settings/chatgptSignIn",
                   signIn: await deps.client.cancelChatGPTSignIn(attempt) });
          }
          return;
        }
        case "settings/chatgptModels": {
          post({ type: "settings/chatgptModels", registrationId: msg.registrationId,
                 models: await deps.client.listChatGPTModels(msg.registrationId) });
          return;
        }
        case "settings/useChatGPT": {
          const credentials = { [REGISTRATION_ENV]: msg.registrationId };
          const result = await deps.client.validateProvider({
            backend: CHATGPT_BACKEND, model: msg.model, credentials });
          if (!result.ok) {
            post({ type: "settings/chatgptError", message: result.error ?? "validation failed" });
            return;
          }
          await deps.client.setProvider({
            backend: CHATGPT_BACKEND, model: msg.model, credentials,
            ...(msg.contextWindow !== undefined ? { contextWindow: msg.contextWindow } : {}),
          });
          await deps.saveActiveRegistration(msg.registrationId);
          if (msg.contextWindow !== undefined) await deps.saveContextWindow(msg.contextWindow);
          await afterProviderChange();
          await postAccounts();
          return;
        }
        case "settings/chatgptSignOut": {
          const { remoteRevoked } = await deps.client.signOutChatGPT(msg.registrationId);
          post({ type: "settings/chatgptSignedOut", registrationId: msg.registrationId,
                 remoteRevoked });
          await postAccounts();
          return;
        }
        case "settings/chatgptWelcomed": {
          await deps.markWelcomed(msg.registrationId);
          return;
        }
        case "settings/openExternal": {
          if (!EXTERNAL_ALLOWLIST.some((prefix) => msg.url.startsWith(prefix))) {
            throw new Error("That link can't be opened from settings.");
          }
          await deps.openExternal(msg.url);
          return;
        }
      }
    } catch (err) {
      if (msg.type === "settings/chatgptSignIn") pendingAttempt = null;
      fail(err);
    }
  };
}
