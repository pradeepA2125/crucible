
import type { BackendTaskClient } from "@crucible/editor-client";
import type { ChatGPTDeps } from "./chatgpt-settings.js";
import type { RuntimeCore } from "./runtime/runtime-core.js";

/** Shared sign-in orchestration. The host opens the browser; client resolves lazily. */
export function createChatGPTDeps(
  runtimeManager: RuntimeCore,
  client: () => BackendTaskClient,
  openExternal: (url: string) => Promise<void>,
): ChatGPTDeps {
  return {
    client: {
      startChatGPTSignIn: (req) => client().startChatGPTSignIn(req),
      getChatGPTSignIn: (id) => client().getChatGPTSignIn(id),
      cancelChatGPTSignIn: (id) => client().cancelChatGPTSignIn(id),
      listChatGPTAccounts: () => client().listChatGPTAccounts(),
      signOutChatGPT: (id) => client().signOutChatGPT(id),
      listChatGPTModels: (id) => client().listChatGPTModels(id),
      validateProvider: (req) => client().validateProvider(req),
      setProvider: async (req) => {
        const result = await client().setProvider(req);
        // Persist for the next managed spawn; the hot-swap only changes this process.
        await runtimeManager.saveProvider(result.backend, result.model);
        return result;
      },
    },
    // The authorize URL can carry an ID-token hint: straight to the browser, never logged.
    openExternal,
    activeRegistration: () => runtimeManager.chatgptRegistration(),
    saveActiveRegistration: (id) => runtimeManager.saveChatGPTRegistration(id),
    saveContextWindow: (tokens) => runtimeManager.saveContextWindow(tokens),
    welcomedRegistrations: () => runtimeManager.chatgptWelcomed(),
    markWelcomed: (id) => runtimeManager.markChatGPTWelcomed(id),
    sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    now: () => Date.now(),
  };
}
