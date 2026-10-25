import type { BackendTaskClient } from "@crucible/editor-client";
import type { ClaudeDeps } from "./claude-settings.js";
import type { RuntimeCore } from "./runtime/runtime-core.js";

export function createClaudeDeps(runtime: RuntimeCore, client: () => BackendTaskClient, openExternal: (url: string) => Promise<void>): ClaudeDeps {
  return {
    client: {
      startClaudeSignIn: () => client().startClaudeSignIn(),
      getClaudeSignIn: id => client().getClaudeSignIn(id),
      cancelClaudeSignIn: id => client().cancelClaudeSignIn(id),
      listClaudeAccounts: () => client().listClaudeAccounts(),
      listClaudeModels: (id, refresh) => client().listClaudeModels(id, refresh),
      signOutClaude: id => client().signOutClaude(id),
      setProvider: async req => {
        const result = await client().setProvider(req);
        await runtime.saveProvider(result.backend, result.model);
        return result;
      },
    },
    openExternal,
    activeRegistration: () => runtime.claudeRegistration(),
    saveActiveRegistration: id => runtime.saveClaudeRegistration(id),
    saveContextWindow: tokens => runtime.saveContextWindow(tokens),
    sleep: ms => new Promise(resolve => setTimeout(resolve, ms)),
    now: () => Date.now(),
  };
}
