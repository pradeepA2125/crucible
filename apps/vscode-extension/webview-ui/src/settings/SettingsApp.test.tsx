import { render, screen, fireEvent, act, within } from "@testing-library/react";
import { beforeEach, describe, it, expect, vi } from "vitest";
import SettingsApp from "./SettingsApp";
import { vscode } from "./vscodeApi";
import type { SettingsState } from "./types";

/** The Overview grid also renders section buttons, so scope nav clicks to the rail. */
function navClick(name: RegExp) {
  const rail = screen.getByRole("navigation", { name: "Settings sections" });
  fireEvent.click(within(rail).getByRole("button", { name }));
}

vi.mock("./vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

const state: SettingsState = {
  provider: { backend: "gemini", model: "gemini-flash-latest" },
  runtime: null,
  mcp: { enabled: true, servers: [] },
  skills: [{ name: "s1", description: "d", enabled: true }],
  envFlags: {},
  restartRequired: true,
};

function deliver(data: unknown) {
  act(() => {
    window.dispatchEvent(new MessageEvent("message", { data }));
  });
}

describe("SettingsApp shell", () => {
  beforeEach(() => vi.clearAllMocks());

  it("posts settings/load on mount, lands on Overview, and navigates", () => {
    render(<SettingsApp />);
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "settings/load" });
    deliver({ type: "settings/state", state });
    expect(screen.getByRole("heading", { name: "Settings" })).toBeTruthy();
    navClick(/Provider/);
    expect(screen.getByRole("heading", { name: "Provider" })).toBeTruthy();
  });

  it("requests instructions on first visit to the Instructions section", () => {
    render(<SettingsApp />);
    deliver({ type: "settings/state", state });
    navClick(/Instructions/);
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "settings/loadInstructions" });
    deliver({ type: "settings/instructions", content: "# rules", exists: true });
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("# rules");
  });

  it("shows the global restart banner in every section", () => {
    render(<SettingsApp />);
    deliver({ type: "settings/state", state });
    expect(screen.getByText(/require a backend restart/)).toBeTruthy();
    navClick(/Skills/);
    expect(screen.getByText(/require a backend restart/)).toBeTruthy();
  });

  it("surfaces a load error instead of hanging on 'Loading settings…'", () => {
    render(<SettingsApp />);
    // buildState failed on the host (e.g. backend unreachable) before any state arrived.
    deliver({ type: "settings/error", message: "Backend not started yet" });
    expect(screen.getByText(/Backend not started yet/)).toBeTruthy();
    // Retry re-requests the initial load.
    (vscode.postMessage as ReturnType<typeof vi.fn>).mockClear();
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "settings/load" });
  });

  it("deep-links to a section on a settings/navigate message", () => {
    render(<SettingsApp />);
    deliver({ type: "settings/state", state });
    expect(screen.getByRole("heading", { name: "Settings" })).toBeTruthy();
    deliver({ type: "settings/navigate", section: "runtime" });
    expect(screen.getByRole("heading", { name: "Runtime" })).toBeTruthy();
  });

  it("starts on the section named by initialSection instead of Overview", () => {
    // The floating in-chat overlay embeds SettingsApp and opens it directly at the
    // section the user picked in the drawer — no navigate round-trip.
    render(<SettingsApp initialSection="provider" />);
    deliver({ type: "settings/state", state });
    expect(screen.getByRole("heading", { name: "Provider" })).toBeTruthy();
  });
});

describe("SettingsApp offline (backend unreachable)", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders the nav and Appearance from local state; other sections offer a retry", () => {
    render(<SettingsApp />);
    deliver({ type: "settings/localState", appearance: "vscode", accent: "blue" });
    deliver({ type: "settings/error", message: "connect ECONNREFUSED" });
    navClick(/Appearance/);
    expect(screen.getByRole("heading", { name: "Appearance" })).toBeTruthy();
    navClick(/Provider/);
    expect(screen.getByText(/connect ECONNREFUSED/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(vscode.postMessage).toHaveBeenLastCalledWith({ type: "settings/load" });
  });
});

describe("SettingsApp follows appearance changes made elsewhere", () => {
  beforeEach(() => vi.clearAllMocks());

  it("updates the Appearance radios on a themeChanged broadcast", () => {
    render(<SettingsApp />);
    deliver({ type: "settings/localState", appearance: "light", accent: "ember" });
    navClick(/Appearance/);
    expect(screen.getByRole("radio", { name: /Crucible Light/ })).toBeChecked();
    // e.g. the user edited settings.json, or picked a theme from the chat's settings overlay
    deliver({ type: "themeChanged", appearance: "vscode", accent: "blue" });
    expect(screen.getByRole("radio", { name: /Match VS Code/ })).toBeChecked();
    expect(screen.getByRole("radio", { name: /Blue/ })).toBeChecked();
  });
});

describe("SettingsApp shows a failed appearance save while offline", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders the error inside the Appearance section without backend state", () => {
    render(<SettingsApp />);
    deliver({ type: "settings/localState", appearance: "vscode", accent: "blue" });
    navClick(/Appearance/);
    deliver({ type: "settings/error", message: "EACCES: settings.json is read-only" });
    expect(screen.getByText(/EACCES: settings.json is read-only/)).toBeTruthy();
  });
});


describe("desktop draft settings placement", () => {
  beforeEach(() => vi.clearAllMocks());
  it("keeps Overview first and opens Drafts from its card and navigation", () => {
    render(<SettingsApp />);
    deliver({ type: "themeChanged", host: "desktop", appearance: "dark", accent: "blue" });
    deliver({ type: "settings/state", state: { ...state, restartRequired: false } });
    expect(screen.getAllByRole("heading")[0]).toHaveTextContent("Settings");
    expect(screen.queryByRole("checkbox", { name: /Save encrypted drafts/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Drafts Encrypted storage/ }));
    expect(screen.getByRole("heading", { name: "Drafts" })).toBeTruthy();
    deliver({ type: "desktop/persistence", enabled: true, available: true, legacy: false, recovery: false });
    expect(screen.getByRole("checkbox", { name: /Save encrypted drafts/ })).toBeChecked();
    navClick(/Overview/);
    expect(screen.queryByRole("checkbox", { name: /Save encrypted drafts/ })).toBeNull();
    navClick(/^Drafts$/);
    expect(screen.getByRole("checkbox", { name: /Save encrypted drafts/ })).toBeChecked();
  });

  it("allows local Drafts offline and leaves VS Code unchanged", () => {
    render(<SettingsApp />);
    deliver({ type: "settings/localState", appearance: "dark", accent: "blue" });
    expect(screen.queryByRole("button", { name: /^Drafts$/ })).toBeNull();
    expect(vscode.postMessage).not.toHaveBeenCalledWith({ type: "desktop/persistenceRead" });
    deliver({ type: "themeChanged", host: "desktop", appearance: "dark", accent: "blue" });
    deliver({ type: "settings/error", message: "Backend offline" });
    navClick(/^Drafts$/);
    expect(screen.getByRole("heading", { name: "Drafts" })).toBeTruthy();
    expect(screen.queryByText(/this section can’t load/)).toBeNull();
    deliver({ type: "desktop/persistence", enabled: false, available: true, legacy: false, recovery: false });
    expect(screen.getByRole("checkbox", { name: /Save encrypted drafts/ })).not.toBeDisabled();
  });
});


it("desktop update progress never acknowledges an unrelated pending Settings mutation", () => {
  render(<SettingsApp />);
  deliver({ type: "settings/state", state });
  const restart=screen.getByRole("button",{name:"Restart backend"});
  fireEvent.click(restart);
  expect(restart).toBeDisabled();
  deliver({type:"desktop/updateStatus",state:{phase:"downloading",message:"Downloading…",received:50,total:100}});
  expect(restart).toBeDisabled();
  deliver({type:"settings/state",state});
  expect(restart).not.toBeDisabled();
});
