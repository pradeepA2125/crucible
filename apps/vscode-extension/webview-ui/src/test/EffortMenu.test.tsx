import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EffortMenu } from "../components/EffortMenu";
import { vscode } from "../vscodeApi";

// window.dispatchEvent is a raw native dispatch (unlike RTL's fireEvent, which
// wraps itself in act()) — without act() here, the resulting setState is
// batched into a microtask and the assertion below races it. Same pattern as
// assembly.test.tsx's window "message" dispatches.
function sendModelList(effort: unknown) {
  act(() => {
    window.dispatchEvent(new MessageEvent("message", { data: { type: "modelList", effort } }));
  });
}

describe("EffortMenu", () => {
  it("shows the current rung on the chip", () => {
    render(<EffortMenu />);
    sendModelList({ level: "high", support: { supported: ["high"], unsupported: {} } });
    expect(screen.getByRole("button", { name: /high/i })).toBeTruthy();
  });

  it("disables an unsupported rung and shows why", () => {
    render(<EffortMenu />);
    sendModelList({
      level: "high",
      support: { supported: ["off", "low", "high"], unsupported: { max: "tops out at high" } },
    });
    fireEvent.click(screen.getByRole("button", { name: /high/i }));
    const max = screen.getByRole("menuitem", { name: /max/i });
    expect(max.hasAttribute("disabled")).toBe(true);
    expect(screen.getByText(/tops out at high/i)).toBeTruthy();
  });

  it("marks an unverified rung selectable but flagged", () => {
    render(<EffortMenu />);
    sendModelList({ level: null, support: { supported: [], unsupported: {} } });
    fireEvent.click(screen.getByRole("button", { name: /effort/i }));
    const medium = screen.getByRole("menuitem", { name: /medium/i });
    expect(medium.hasAttribute("disabled")).toBe(false);
    expect(screen.getAllByText(/unverified/i).length).toBeGreaterThan(0);
  });

  it("surfaces a clamp note on the chip", () => {
    render(<EffortMenu />);
    sendModelList({
      level: "high",
      support: { supported: ["high"], unsupported: { max: "x" } },
      note: "max unavailable here; using high.",
    });
    expect(screen.getByRole("button", { name: /no max/i })).toBeTruthy();
  });

  it("keeps the clamp note visible across a later modelList refresh carrying the same effort", () => {
    // Regression for the note-clearing bug: EffortMenu used to read a top-level
    // `effortNote` field and clear it on EVERY modelList message (e.g. the
    // plain listModels poll ModelMenu fires on mount), so the note vanished the
    // instant anything else refreshed the model list. The note now rides
    // inside `effort.note` (sourced from GET /v1/config), so a second message
    // carrying the identical effort object must still show it.
    render(<EffortMenu />);
    const effort = {
      level: "high",
      support: { supported: ["high"], unsupported: { max: "x" } },
      note: "max unavailable here; using high.",
    };
    sendModelList(effort);
    expect(screen.getByRole("button", { name: /no max/i })).toBeTruthy();

    // A second, unrelated modelList refresh (e.g. ModelMenu's mount-time poll)
    // carrying the SAME effort object must not clear the note.
    sendModelList(effort);
    expect(screen.getByRole("button", { name: /no max/i })).toBeTruthy();
  });

  it("does not close the popover on selection, and posts setReasoningEffort", () => {
    render(<EffortMenu />);
    sendModelList({
      level: "high",
      support: { supported: ["off", "low", "medium", "high"], unsupported: { max: "tops out at high" } },
    });
    fireEvent.click(screen.getByRole("button", { name: /high/i }));
    const postMessage = vi.spyOn(vscode, "postMessage");
    fireEvent.click(screen.getByRole("menuitem", { name: /medium/i }));

    expect(screen.getByRole("menu")).toBeTruthy();
    expect(postMessage).toHaveBeenCalledWith({ type: "setReasoningEffort", level: "medium" });
  });

  it("shows the target rung as in-progress on the chip while a swap is in flight", () => {
    render(<EffortMenu />);
    sendModelList({
      level: "high",
      support: { supported: ["off", "low", "medium", "high"], unsupported: { max: "tops out at high" } },
    });
    fireEvent.click(screen.getByRole("button", { name: /high/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /medium/i }));

    // The chip now surfaces the TARGET rung (medium), not the stale current
    // one (high), and its accessible name announces the in-flight check.
    expect(
      screen.getByRole("button", { name: /switching reasoning effort to medium.*checking with the provider/i }),
    ).toBeTruthy();
  });

  it("regression: stays open and shows the error after a swap fails", () => {
    render(<EffortMenu />);
    sendModelList({
      level: "high",
      support: { supported: ["off", "low", "medium", "high"], unsupported: { max: "tops out at high" } },
    });
    fireEvent.click(screen.getByRole("button", { name: /high/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /medium/i }));

    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", {
          data: { type: "effortSwapError", message: "provider unreachable" },
        }),
      );
    });

    expect(screen.getByRole("menu")).toBeTruthy();
    expect(screen.getByText(/provider unreachable/i)).toBeTruthy();
  });
});

function nativeSupport(values: string[], defaultLevel: string | null = null) {
  return {
    supported: values, unsupported: {},
    control: { source: "catalog", default: defaultLevel, reason: values.length ? null : "Model has no effort control",
      options: values.map((value) => ({ value, label: value === "xhigh" ? "Extra high" : value[0]!.toUpperCase() + value.slice(1),
        description: value === "xhigh" ? "More reasoning depth" : "", state: "supported" })) },
  };
}

describe("provider-owned effort controls", () => {
  it("renders native ChatGPT options and posts the exact native value", () => {
    render(<EffortMenu />);
    sendModelList({ level: "low", support: nativeSupport(["none", "low", "high", "xhigh"], "low") });
    fireEvent.click(screen.getByRole("button", { name: /reasoning effort/i }));
    expect(screen.queryByRole("menuitem", { name: /^off/i })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: /^max/i })).toBeNull();
    expect(screen.getByText("Model default: Low")).toBeTruthy();
    const post = vi.spyOn(vscode, "postMessage");
    fireEvent.click(screen.getByRole("menuitem", { name: /extra high/i }));
    expect(post).toHaveBeenCalledWith({ type: "setReasoningEffort", level: "xhigh" });
  });

  it("changes options with the active model instead of retaining the old provider menu", () => {
    render(<EffortMenu />);
    sendModelList({ level: "high", support: nativeSupport(["low", "medium", "high", "xhigh", "max"]) });
    fireEvent.click(screen.getByRole("button", { name: /reasoning effort/i }));
    expect(screen.getByRole("menuitem", { name: /extra high/i })).toBeTruthy();
    sendModelList({ level: "high", support: nativeSupport(["low", "medium", "high"]) });
    expect(screen.queryByRole("menuitem", { name: /extra high/i })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: /^max/i })).toBeNull();
    expect(screen.getAllByRole("menuitem")).toHaveLength(4); // model options plus Default
  });

  it("disables effort when the catalog explicitly reports no support", () => {
    render(<EffortMenu />);
    sendModelList({ level: null, support: nativeSupport([]) });
    const chip = screen.getByRole("button", { name: /reasoning effort/i });
    expect(chip.hasAttribute("disabled")).toBe(true);
    expect(chip.getAttribute("title")).toBe("Model has no effort control");
    fireEvent.click(chip);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("clears the override through Default, including pending and completion states", () => {
    render(<EffortMenu />);
    sendModelList({ level: "high", support: nativeSupport(["low", "high"], "low") });
    fireEvent.click(screen.getByRole("button", { name: /reasoning effort/i }));
    const post = vi.spyOn(vscode, "postMessage");
    fireEvent.click(screen.getByRole("menuitem", { name: /^default/i }));
    expect(post).toHaveBeenCalledWith({ type: "setReasoningEffort", level: null });
    expect(screen.getByRole("button", { name: /switching reasoning effort to default/i })).toBeTruthy();
    sendModelList({ level: null, support: nativeSupport(["low", "high"], "low") });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(screen.getByRole("button", { name: "Reasoning effort: Default" })).toBeTruthy();
  });

  it("uses the unverified fallback after metadata disappears instead of stale catalog choices", () => {
    render(<EffortMenu />);
    sendModelList({ level: "xhigh", support: nativeSupport(["low", "xhigh"]) });
    sendModelList(undefined);
    fireEvent.click(screen.getByRole("button", { name: /reasoning effort/i }));
    expect(screen.queryByRole("menuitem", { name: /extra high/i })).toBeNull();
    expect(screen.getByRole("menuitem", { name: /^off/i })).toBeTruthy();
    expect(screen.getAllByText("unverified for this endpoint")).toHaveLength(5);
  });
});

it("keeps a long native menu within narrow panels and repositions on resize", () => {
  const initialWidth = window.innerWidth;
  try {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 280 });
    const { container } = render(<EffortMenu />);
    vi.spyOn(container.firstElementChild as HTMLElement, "getBoundingClientRect").mockReturnValue({
      left: 220, right: 280, width: 60, top: 500, bottom: 524, height: 24, x: 220, y: 500, toJSON: () => ({}),
    });
    sendModelList({ level: "high", support: nativeSupport(["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]) });
    fireEvent.click(screen.getByRole("button", { name: /reasoning effort/i }));
    const menu = screen.getByRole("menu");
    expect(menu.style.width).toBe("264px");
    expect(220 + parseFloat(menu.style.left)).toBe(8);
    expect(menu.style.overflowY).toBe("auto");
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 360 });
    fireEvent(window, new Event("resize"));
    expect(220 + parseFloat(menu.style.left) + parseFloat(menu.style.width)).toBe(352);
  } finally {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: initialWidth });
    vi.restoreAllMocks();
  }
});
