import { describe, expect, it, vi } from "vitest";
import type { ChatGPTSignIn } from "@crucible/editor-client";
import { ProviderAccessError } from "@crucible/editor-client";
import {
  createChatGPTHandler,
  type ChatGPTDeps,
  type ChatGPTOutMsg,
} from "../src/chatgpt-settings.js";

const REG = "reg_aaaaaaaaaaaa";

function attempt(overrides: Partial<ChatGPTSignIn> = {}): ChatGPTSignIn {
  return { attemptId: "att_1", state: "pending", registrationId: null, planEnabled: null,
           firstPlanSignIn: false, reason: null, message: null, ...overrides };
}

const ACCOUNT = { registrationId: REG, label: "a@x.com", email: "a@x.com", name: null,
                  planEnabled: true, signedIn: true };

function setup(overrides: Partial<ChatGPTDeps["client"]> = {}, more: Partial<ChatGPTDeps> = {}) {
  const posted: ChatGPTOutMsg[] = [];
  const opened: string[] = [];
  let welcomed: string[] = [];
  let active: string | undefined;
  const statuses = [attempt(), attempt({
    state: "succeeded", registrationId: REG, planEnabled: true, firstPlanSignIn: true })];
  const deps: ChatGPTDeps = {
    client: {
      startChatGPTSignIn: vi.fn(async () => ({ ...attempt(), authorizeUrl: "https://auth/x" })),
      getChatGPTSignIn: vi.fn(async () => statuses.shift() ?? attempt({ state: "failed" })),
      cancelChatGPTSignIn: vi.fn(async () => attempt({ state: "failed", reason: "cancelled" })),
      listChatGPTAccounts: vi.fn(async () => [ACCOUNT]),
      signOutChatGPT: vi.fn(async () => ({ remoteRevoked: false })),
      listChatGPTModels: vi.fn(async () => [{ slug: "m1", displayName: "Model One", contextWindow: null }]),
      validateProvider: vi.fn(async () => ({ ok: true })),
      setProvider: vi.fn(async () => ({ backend: "chatgpt", model: "m1" })),
      ...overrides,
    },
    openExternal: vi.fn(async (url: string) => { opened.push(url); }),
    activeRegistration: () => active,
    saveActiveRegistration: vi.fn(async (id: string) => { active = id; }),
    saveContextWindow: vi.fn(async () => {}),
    welcomedRegistrations: () => welcomed,
    markWelcomed: vi.fn(async (id: string) => { welcomed = [...welcomed, id]; }),
    sleep: async () => {},
    now: () => 0,
    ...more,
  };
  const afterProviderChange = vi.fn(async () => {});
  const handle = createChatGPTHandler(deps, (m) => posted.push(m), afterProviderChange);
  return { handle, deps, posted, opened, afterProviderChange,
           setWelcomed: (ids: string[]) => { welcomed = ids; } };
}

describe("ChatGPT settings handler", () => {
  it("follows a sign-in to its end and welcomes a first plan sign-in once", async () => {
    const { handle, posted, opened } = setup();
    await handle({ type: "settings/chatgptSignIn" });
    expect(opened).toEqual(["https://auth/x"]);
    const signIns = posted.filter((m) => m.type === "settings/chatgptSignIn");
    expect(signIns.map((m) => m.type === "settings/chatgptSignIn" && m.signIn.state))
      .toEqual(["pending", "succeeded"]);
    // The URL never reaches the webview: it can carry an ID token hint.
    expect(JSON.stringify(posted)).not.toContain("https://auth/x");
    expect(posted).toContainEqual({ type: "settings/chatgptWelcome", registrationId: REG });
    expect(posted.at(-1)).toEqual({ type: "settings/chatgpt", accounts: [ACCOUNT],
                                    activeRegistrationId: null });
  });

  it("does not welcome an account that was already welcomed", async () => {
    const { handle, posted, setWelcomed } = setup();
    setWelcomed([REG]);
    await handle({ type: "settings/chatgptSignIn" });
    expect(posted.some((m) => m.type === "settings/chatgptWelcome")).toBe(false);
  });

  it("passes a returning account and reconsent to the backend", async () => {
    const { handle, deps } = setup();
    await handle({ type: "settings/chatgptSignIn", registrationId: REG, reconsent: true });
    expect(deps.client.startChatGPTSignIn).toHaveBeenCalledWith(
      { registrationId: REG, reconsent: true });
  });

  it("cancel stops following the attempt", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const { handle, posted, deps } = setup(
      { getChatGPTSignIn: vi.fn(async () => attempt()) },
      { sleep: () => gate });
    const running = handle({ type: "settings/chatgptSignIn" });
    await Promise.resolve();
    await handle({ type: "settings/chatgptCancel" });
    release();
    await running;
    expect(deps.client.cancelChatGPTSignIn).toHaveBeenCalledWith("att_1");
    expect(posted).toContainEqual({ type: "settings/chatgptSignIn",
      signIn: attempt({ state: "failed", reason: "cancelled" }) });
  });

  it("puts an account to use only after it validates", async () => {
    const { handle, deps, afterProviderChange } = setup();
    await handle({ type: "settings/useChatGPT", registrationId: REG, model: "m1",
                   contextWindow: 200000 });
    const creds = { CRUCIBLE_CHATGPT_REGISTRATION: REG };
    expect(deps.client.validateProvider).toHaveBeenCalledWith(
      { backend: "chatgpt", model: "m1", credentials: creds });
    expect(deps.client.setProvider).toHaveBeenCalledWith(
      { backend: "chatgpt", model: "m1", credentials: creds, contextWindow: 200000 });
    expect(deps.saveActiveRegistration).toHaveBeenCalledWith(REG);
    expect(afterProviderChange).toHaveBeenCalled();
  });

  it("a failed validation changes nothing", async () => {
    const { handle, deps, posted } = setup(
      { validateProvider: vi.fn(async () => ({ ok: false, error: "plan usage disabled" })) });
    await handle({ type: "settings/useChatGPT", registrationId: REG, model: "m1" });
    expect(deps.client.setProvider).not.toHaveBeenCalled();
    expect(deps.saveActiveRegistration).not.toHaveBeenCalled();
    expect(posted).toEqual([{ type: "settings/chatgptError", message: "plan usage disabled" }]);
  });

  it("reports an account refusal on the catalog with its kind", async () => {
    const { handle, posted } = setup({ listChatGPTModels: vi.fn(async () => {
      throw new ProviderAccessError("session_invalid", "Sign in again"); }) });
    await handle({ type: "settings/chatgptModels", registrationId: REG });
    expect(posted).toEqual([{ type: "settings/chatgptError", message: "Sign in again",
                              kind: "session_invalid" }]);
  });

  it("reports whether sign-out revoked the session remotely", async () => {
    const { handle, posted } = setup();
    await handle({ type: "settings/chatgptSignOut", registrationId: REG });
    expect(posted[0]).toEqual({ type: "settings/chatgptSignedOut", registrationId: REG,
                                remoteRevoked: false });
  });

  it("opens only ChatGPT and OpenAI help links", async () => {
    const { handle, opened, posted } = setup();
    await handle({ type: "settings/openExternal", url: "https://chatgpt.com/settings/usage?tab=overview" });
    await handle({ type: "settings/openExternal", url: "https://evil.example/" });
    expect(opened).toEqual(["https://chatgpt.com/settings/usage?tab=overview"]);
    expect(posted.at(-1)?.type).toBe("settings/chatgptError");
  });
});
