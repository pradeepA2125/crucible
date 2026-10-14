import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ModelMenu } from "../components/ModelMenu";
import { effortStops, type EffortLevel, type EffortSupport } from "../effort";
import { vscode } from "../vscodeApi";
vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));
function native(values: EffortLevel[], defaultLevel: EffortLevel | null = null): EffortSupport {
  return { supported: values, unsupported: {}, control: { source: "catalog", default: defaultLevel, reason: null,
    options: values.map(value => ({ value, label: value.toUpperCase(), description: `About ${value}`, state: "supported" })) } };
}
function deliver(level: EffortLevel | null, support: EffortSupport | null, note?: string) {
  act(() => window.dispatchEvent(new MessageEvent("message", { data: { type: "modelList", current: { backend: "test", model: "test-model" }, options: [], effort: { level, support, note } } })));
}
function open(level: EffortLevel | null, support: EffortSupport | null, note?: string) {
  render(<ModelMenu />); deliver(level, support, note);
  fireEvent.click(screen.getByRole("button", { name: /Model: test-model/ }));
}
describe("effort stops", () => {
  it.each([["none", "low", "medium", "high", "xhigh"], ["minimal", "low", "high", "max"], ["low", "high", "ultra"]] as EffortLevel[][])("preserves provider stop order %j", (...values) => {
    expect(effortStops(native(values as EffortLevel[])).map(row => row.value)).toEqual([null, ...values]);
  });
  it("skips unsupported descriptor stops and keeps unverified stops", () => {
    const support = native(["low", "medium", "high"]);
    support.control!.options[1].state = "unsupported"; support.control!.options[2].state = "unknown";
    expect(effortStops(support).map(row => [row.value, row.state])).toEqual([[null, "supported"], ["low", "supported"], ["high", "unknown"]]);
  });
  it("distinguishes empty descriptors from missing descriptors", () => {
    expect(effortStops(native([]))).toEqual([]);
    expect(effortStops(null).map(row => row.value)).toEqual([null, "off", "low", "medium", "high", "max"]);
  });
  it("classifies the older five-level ladder and skips its unsupported levels", () => {
    expect(effortStops({ supported: ["high"], unsupported: { max: "Unavailable" } }).map(row => [row.value, row.state])).toEqual([[null, "supported"], ["off", "unknown"], ["low", "unknown"], ["medium", "unknown"], ["high", "supported"]]);
  });
});
describe("combined model effort controls", () => {
  it("uses a native notched range, model default description and a real null override", () => {
    open("high", native(["low", "high"], "low"));
    const slider = screen.getByRole("slider", { name: "Reasoning effort" });
    expect(slider).toHaveAttribute("type", "range"); expect(slider).toHaveAttribute("max", "2");
    fireEvent.click(screen.getByRole("button", { name: "Use default effort" }));
    expect(vscode.postMessage).toHaveBeenLastCalledWith({ type: "setReasoningEffort", level: null });
    expect(slider).toBeDisabled(); expect(screen.getByRole("status")).toHaveTextContent("Checking with the provider");
    deliver(null, native(["low", "high"], "low"));
    expect(slider).toBeEnabled(); expect(screen.getByText("Model default: Low")).toBeInTheDocument();
  });
  it("previews a mouse drag across notches and validates only the released level", () => {
    open("low", native(["low", "medium", "high"])); vi.mocked(vscode.postMessage).mockClear();
    const slider = screen.getByRole("slider");
    fireEvent.input(slider, { target: { value: "2" } });
    fireEvent.input(slider, { target: { value: "3" } });
    expect(vscode.postMessage).not.toHaveBeenCalledWith(expect.objectContaining({ type: "setReasoningEffort" }));
    expect(slider).toBeEnabled(); expect(slider).toHaveAttribute("aria-valuetext", "HIGH");
    fireEvent.change(slider, { target: { value: "3" } });
    expect(vscode.postMessage).toHaveBeenCalledTimes(1);
    expect(vscode.postMessage).toHaveBeenLastCalledWith({ type: "setReasoningEffort", level: "high" });
  });
  it("sends nothing when a drag returns to the current level", () => {
    open("low", native(["low", "medium", "high"])); vi.mocked(vscode.postMessage).mockClear();
    const slider = screen.getByRole("slider");
    fireEvent.input(slider, { target: { value: "3" } });
    fireEvent.change(slider, { target: { value: "1" } });
    expect(vscode.postMessage).not.toHaveBeenCalledWith(expect.objectContaining({ type: "setReasoningEffort" }));
    expect(slider).toHaveAttribute("aria-valuetext", "LOW");
  });
  it("keeps the menu open on pending, acknowledgement and provider error", () => {
    open("high", native(["low", "medium", "high"]));
    fireEvent.change(screen.getByRole("slider"), { target: { value: "2" } });
    expect(vscode.postMessage).toHaveBeenLastCalledWith({ type: "setReasoningEffort", level: "medium" });
    expect(screen.getByRole("slider")).toBeDisabled();
    act(() => window.dispatchEvent(new MessageEvent("message", { data: { type: "effortSwapError", message: "Provider unreachable" } })));
    expect(screen.getByRole("alert")).toHaveTextContent("Provider unreachable");
    expect(screen.getByRole("slider")).toBeEnabled(); expect(screen.getByRole("slider")).toHaveValue("3");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
  it("shows unverified selected levels and preserves clamp notes across refreshes", () => {
    const support = native(["low", "high"]); support.control!.options[1].state = "unknown";
    open("high", support, "max unavailable here; using high.");
    expect(screen.getByText("unverified")).toBeInTheDocument();
    expect(screen.getByText("max unavailable here; using high.")).toBeInTheDocument();
    deliver("high", support, "max unavailable here; using high.");
    expect(screen.getByText("max unavailable here; using high.")).toBeInTheDocument();
  });
  it("hides the effort row when the model offers no control", () => {
    open(null, native([])); expect(screen.queryByText("Reasoning effort")).toBeNull();
    expect(screen.getByRole("button", { name: "Model: test-model" })).toBeInTheDocument();
  });
  it("keeps Default usable with one selectable rung", () => {
    const support = native(["off"]); support.control!.options[0].state = "unsupported";
    open("high", support); expect(screen.queryByRole("slider")).toBeNull();
    expect(screen.getByText(/previous level is not offered/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Use default effort" }));
    expect(vscode.postMessage).toHaveBeenLastCalledWith({ type: "setReasoningEffort", level: null });
  });
  it("dismisses outside and by Escape with focus returning to the chip", () => {
    open(null, null); const chip = screen.getByRole("button", { name: /Model: test/ });
    fireEvent.pointerDown(document.body); expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(chip); fireEvent.keyDown(screen.getByRole("slider"), { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull(); expect(chip).toHaveFocus();
  });
});
