import * as vscode from "vscode";

import type { BackendTaskClient } from "@crucible/editor-client";
import type { ChatGPTDeps } from "./chatgpt-settings.js";
import type { RuntimeManager } from "./runtime/vscode-runtime.js";

/** The vscode-wired ChatGPT sign-in deps, shared by the Settings panel (and its chat
 * overlay) and the setup wizard. `client` resolves the backend lazily per call. */
export function buildChatGPTDeps(
  runtimeManager: RuntimeManager,
  client: () => BackendTaskClient,
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
    openExternal: async (url) => {
      await vscode.env.openExternal(vscode.Uri.parse(url));
    },
    activeRegistration: () => runtimeManager.chatgptRegistration(),
    saveActiveRegistration: (id) => runtimeManager.saveChatGPTRegistration(id),
    saveContextWindow: (tokens) => runtimeManager.saveContextWindow(tokens),
    welcomedRegistrations: () => runtimeManager.chatgptWelcomed(),
    markWelcomed: (id) => runtimeManager.markChatGPTWelcomed(id),
    sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    now: () => Date.now(),
  };
}
