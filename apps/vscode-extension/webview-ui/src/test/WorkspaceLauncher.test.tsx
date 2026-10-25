import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import DesktopApp from "../desktop/DesktopApp";
import type { WorkspacePort } from "../desktop/workspaceClient";
vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));
const host = (data: unknown) => act(() => { window.dispatchEvent(new MessageEvent("message", { data })); });
const thread = (id: string) => ({ threadId: id, workspacePath: "/project", title: `Conversation ${id}`, createdAt: "2026-10-21T10:00:00Z" });
function ready(id = "a") { host({ type: "renderThreadList", threads: [thread("a"), thread("b")], activeThreadId: id }); host({ type: "clearThread", threadId: id }); host({ type: "liveStatus", threadId: id, turnActive: false, agentsRunning: 0 }); }
function fixture() {
  const port: WorkspacePort = { list: vi.fn(async path => ({ path, captured: 1, limited: false, warnings: [], entries: [{ path: "sample.ts", name: "sample.ts", kind: "file" as const, hidden: false, ignoreStatus: "included" as const }] })),
    preview: vi.fn(async path => ({ path, kind: "text" as const, text: "source", size: 6, truncated: false })), search: vi.fn(async () => ({ entries: [], scanned: 1, partial: false, warnings: [] })), cancelSearch: vi.fn() };
  render(<DesktopApp fileStore={port} />); ready(); fireEvent.click(screen.getByRole("button", { name: "Open workspace" })); return port;
}
beforeEach(() => { window.innerWidth = 1440; });
it("opens a real launcher with implemented tools, keyboard selection and no empty suggestions", async () => {
  fixture(); const launcher = screen.getByRole("region", { name: "New tab" }), files = within(launcher).getByRole("button", { name: "Files" }), agents = within(launcher).getByRole("button", { name: "Agents" });
  expect(files).toHaveFocus(); expect(within(launcher).queryByText("Suggested")).toBeNull();
  expect(within(launcher).getByRole("button",{name:"Terminal & output"})).toBeInTheDocument();
  expect(within(launcher).queryByText(/Side chat/)).toBeNull();
  const changes = within(launcher).getByRole("button", { name: "Changes" });
  fireEvent.keyDown(files, { key: "ArrowDown" }); expect(changes).toHaveFocus();
  fireEvent.keyDown(changes, { key: "ArrowDown" }); expect(agents).toHaveFocus();
  fireEvent.keyDown(agents, { key: "Home" }); expect(files).toHaveFocus();
  await userEvent.keyboard("{Enter}"); expect(screen.queryByRole("region", { name: "New tab" })).toBeNull();
  expect(await screen.findByRole("complementary", { name: "Workspace files" })).toBeInTheDocument();
});
it("gives Agents the full body and restores the remembered Files navigator without duplicate tool tabs", async () => {
  fixture(); fireEvent.click(within(screen.getByRole("region", { name: "New tab" })).getByRole("button", { name: "Files" }));
  await screen.findByRole("complementary", { name: "Workspace files" });
  fireEvent.click(screen.getByRole("button", { name: "New workspace tab" })); fireEvent.click(within(screen.getByRole("region", { name: "New tab" })).getByRole("button", { name: "Agents" }));
  expect(screen.queryByRole("complementary", { name: "Workspace files" })).toBeNull(); expect(screen.queryByRole("button", { name: "Toggle file navigator" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Files" })); expect(screen.getByRole("complementary", { name: "Workspace files" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "New workspace tab" })); fireEvent.click(within(screen.getByRole("region", { name: "New tab" })).getByRole("button", { name: "Agents" }));
  expect(within(screen.getByRole("toolbar", { name: "Workspace tabs" })).getAllByRole("button", { name: "Agents" })).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Close Files tab" })); fireEvent.click(screen.getByRole("button", { name: "Close Agents tab" })); expect(screen.getByRole("region", { name: "New tab" })).toBeInTheDocument();
});
it("retains project file tabs across thread switches and suggests only actual opens", async () => {
  fixture(); fireEvent.change(screen.getByRole("textbox", { name: "Chat input" }), { target: { value: "shared composer draft" } });
  host({ type: "desktop/fileOpen", path: "sample.ts" }); expect(await screen.findByLabelText("Read-only source sample.ts")).toHaveTextContent("source");
  expect(screen.queryByRole("region", { name: "New tab" })).toBeNull(); ready("b");
  expect(await screen.findByLabelText("Read-only source sample.ts")).toHaveTextContent("source");
  fireEvent.click(screen.getByRole("button", { name: "New workspace tab" })); const launcher = screen.getByRole("region", { name: "New tab" });
  expect(within(launcher).getByText("Suggested")).toBeInTheDocument(); expect(within(launcher).getByRole("button", { name: /sample.ts Recently opened/ })).toBeInTheDocument();
  expect(screen.queryByRole("complementary", { name: "Workspace files" })).toBeNull();
});

it("moves launcher focus only after an explicit request, never from a thread remount", async () => {
  fixture(); const first = within(screen.getByRole("region", { name: "New tab" })).getByRole("button", { name: "Files" }); expect(first).toHaveFocus();
  const conversation = screen.getByRole("button", { name: /Conversation b/ }); await act(async () => { conversation.focus(); }); ready("b");
  expect(within(screen.getByRole("region", { name: "New tab" })).getByRole("button", { name: "Files" })).not.toHaveFocus();
  fireEvent.click(screen.getByRole("button", { name: "New workspace tab" })); expect(within(screen.getByRole("region", { name: "New tab" })).getByRole("button", { name: "Files" })).toHaveFocus();
});
