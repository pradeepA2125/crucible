import { render, screen, fireEvent, act } from "@testing-library/react";
import { beforeEach, describe, it, expect, vi } from "vitest";
import SetupApp from "./SetupApp";
import { vscode } from "./vscodeApi";

vi.mock("./vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

function deliver(data: unknown) {
  act(() => {
    window.dispatchEvent(new MessageEvent("message", { data }));
  });
}

describe("SetupApp", () => {
  beforeEach(() => vi.clearAllMocks());

  it("walks welcome → install → provider → done", () => {
    render(<SetupApp />);
    expect(screen.getByLabelText(/Step 1 of 4/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Install runtime/ }));
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "setup/install" });
    deliver({ type: "setup/progress", component: "uv", status: "done" });
    deliver({ type: "setup/installDone", ok: true });
    expect(screen.getByLabelText("Model")).toBeTruthy();
    deliver({ type: "setup/ready", port: 8090 });
    expect(screen.getByText(/8090/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Open chat/ }));
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "setup/openChat" });
  });

  it("shows Retry when install fails", () => {
    render(<SetupApp />);
    fireEvent.click(screen.getByRole("button", { name: /Install runtime/ }));
    deliver({ type: "setup/progress", component: "agentd", status: "failed", detail: "pip exploded" });
    deliver({ type: "setup/installDone", ok: false });
    expect(screen.getByText("pip exploded")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Retry/ })).toBeTruthy();
  });
});
