import { render, screen, fireEvent } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { BrowserTab } from "./BrowserTab";
import type { MemorySnapshot } from "./types";
const { postMessage } = vi.hoisted(() => ({ postMessage: vi.fn() }));
vi.mock("./vscodeApi", () => ({ vscode: { postMessage } }));
const snapshot: MemorySnapshot = { pipeline: "durable", status: "ready", memories: [{
 id: "one", scope_kind: "repo", scope_id: "repo", kind: "procedural", content: "Run checks",
 topic: "testing", applies_when: "Before finishing", detail: "Use the isolated runner", entities: [],
 provenance: "tool_verified", source_thread_id: "thread", source_message_id: 12, revision: 2,
 valid_from: "2026-01-01", valid_to: null, superseded_by: null, created_at: "2026-01-01",
}] };
test("durable search includes body and detail retains provenance and source", () => {
 render(<BrowserTab memories={[]} chains={{}} snapshot={snapshot} />);
 fireEvent.change(screen.getByLabelText("Search memories"), { target: { value: "isolated" } });
 fireEvent.click(screen.getByRole("button", { name: /Run checks/ }));
 expect(screen.getByText("Use the isolated runner")).toBeTruthy();
 expect(screen.getByText("Verified with tools")).toBeTruthy();
 expect(screen.getByRole("heading", { name: "Run checks" })).toBeTruthy();
 expect(screen.getByText(/Source navigation is not available/)).toBeTruthy();
 expect(screen.queryByRole("link")).toBeNull();
 expect(screen.getByText("tool_verified").closest("details")?.open).toBe(false);
 expect(screen.getByText(/message 12/)).toBeTruthy();
 expect(postMessage).not.toHaveBeenCalled();
 fireEvent.change(screen.getByLabelText("State"), { target: { value: "retired" } });
 expect(screen.getByText("No memories match these filters.")).toBeTruthy();
});
test("unavailable durable capability never renders supplied legacy entries", () => {
 render(<BrowserTab memories={[]} chains={{}} snapshot={{ ...snapshot, status: "unavailable" }} />);
 expect(screen.getByText(/No legacy data is substituted/)).toBeTruthy();
 expect(screen.queryByLabelText("Search memories")).toBeNull();
});
