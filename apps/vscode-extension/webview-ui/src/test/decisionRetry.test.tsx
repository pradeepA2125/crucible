import { act, fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ModeGate } from "../components/messages/gates/ModeGate";
import { ClarifyGate } from "../components/messages/gates/ClarifyGate";
import { TeamPlanGate } from "../components/messages/gates/TeamPlanGate";
vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));
it.each(["mode", "clarify", "team_plan"])("reopens %s after failed admission and keeps draft text", kind => {
 const payload = kind === "mode" ? { options: [{ mode: "edit", label: "Edit" }] } : kind === "clarify" ? { question: "Which?", options: ["First"] } : { team_name: "Test", proposal: "Plan" };
 render(kind === "mode" ? <ModeGate taskId="t" payload={payload} /> : kind === "clarify" ? <ClarifyGate taskId="t" payload={payload} /> : <TeamPlanGate taskId="t" gateId="g" payload={payload} />);
 const draft = screen.getByRole("textbox"); fireEvent.change(draft, { target: { value: "Keep this feedback" } });
 const pick = kind === "mode" ? "Edit" : kind === "clarify" ? "First" : "Approve";
 fireEvent.click(screen.getByRole("button", { name: pick })); expect(screen.queryByRole("textbox")).toBeNull();
 act(() => window.dispatchEvent(new MessageEvent("message", { data: { type: "decisionRetry", threadId: "other", kind, gateId: "g" } })));
 expect(screen.queryByRole("textbox")).toBeNull();
 act(() => window.dispatchEvent(new MessageEvent("message", { data: { type: "decisionRetry", threadId: "t", kind, gateId: "g" } })));
 expect(screen.getByRole("textbox")).toHaveValue("Keep this feedback"); expect(screen.getByRole("button", { name: pick })).toBeEnabled();
});
