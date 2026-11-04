import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectTrustStrip } from "./ProjectTrustStrip";
import { ProjectTrustDialog } from "./ProjectTrustDialog";
import { lineDiff, trustNames } from "./projectTrust";
import type { TrustFile } from "./projectTrust";
import { vscode } from "../../vscodeApi";
vi.mock("../../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

const AGENTS: TrustFile = { kind: "agents_md", state: "untrusted", sha256: "a1", content: "use pnpm\nrun tests", diffBase: null };
const MCP: TrustFile = { kind: "mcp", state: "untrusted", sha256: "m1", content: "{\"mcpServers\":{}}", diffBase: null };
const SKILLS: TrustFile = { kind: "skills", state: "trusted", sha256: "s1", content: "x", diffBase: null };

function deliver(files: TrustFile[], error?: string) {
  act(() => window.dispatchEvent(new MessageEvent("message", { data: { type: "projectTrust", files, ...(error ? { error } : {}) } })));
}
beforeEach(() => vi.clearAllMocks());

describe("project trust", () => {
  it("strip lists untrusted files and opens the dialog", () => {
    render(<ProjectTrustStrip />);
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "projectTrustRead" });
    deliver([AGENTS, MCP, SKILLS]);
    expect(screen.getByRole("status")).toHaveTextContent("AGENTS.md and mcp.json aren't trusted yet");
    fireEvent.click(screen.getByRole("button", { name: "Review" }));
    const dialog = screen.getByRole("dialog", { name: "Review this project's files" });
    expect(within(dialog).getAllByRole("region")).toHaveLength(2);
  });

  it("strip hides when nothing is untrusted and can be dismissed", () => {
    const { container } = render(<ProjectTrustStrip />);
    deliver([SKILLS]);
    expect(container).toBeEmptyDOMElement();
    deliver([AGENTS]);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss for this session" }));
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("dialog trusts one section", () => {
    const onTrust = vi.fn();
    render(<ProjectTrustDialog files={[AGENTS, MCP]} onTrust={onTrust} onClose={() => {}} />);
    const section = screen.getByRole("region", { name: "AGENTS.md" });
    expect(section).toHaveTextContent("use pnpm");
    fireEvent.click(within(section).getByRole("button", { name: "Trust AGENTS.md" }));
    expect(onTrust).toHaveBeenCalledWith("agents_md", "a1");
  });

  it("dialog posts the trust message from the strip", () => {
    render(<ProjectTrustStrip />);
    deliver([AGENTS]);
    fireEvent.click(screen.getByRole("button", { name: "Review" }));
    fireEvent.click(screen.getByRole("button", { name: "Trust AGENTS.md" }));
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "projectTrustSet", kind: "agents_md", sha256: "a1" });
  });

  it("dialog shows diff against diff base", () => {
    const changed: TrustFile = { ...AGENTS, content: "use pnpm\nrun curl", diffBase: "use pnpm\nrun tests" };
    render(<ProjectTrustDialog files={[changed]} onTrust={() => {}} onClose={() => {}} />);
    const section = screen.getByRole("region", { name: "AGENTS.md" });
    expect(section).toHaveTextContent("Changed since you trusted it");
    // The compact rendering, styled in both apps (the review rendering is desktop-only CSS).
    expect(section.querySelector(".review-code-line")).toBeNull();
    expect(section).toHaveTextContent("+run curl");
    expect(section).toHaveTextContent("-run tests");
    fireEvent.click(within(section).getByRole("button", { name: "Show full file" }));
    expect(within(section).getByText(/use pnpm\s+run curl/)).toBeInTheDocument();
  });

  it("dialog shows an error and Not now closes", () => {
    const onClose = vi.fn();
    render(<ProjectTrustDialog files={[AGENTS]} error="AGENTS.md changed since you reviewed it; review it again." onTrust={() => {}} onClose={onClose} />);
    expect(screen.getByRole("alert")).toHaveTextContent("changed since you reviewed it");
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    expect(onClose).toHaveBeenCalled();
  });

  it("file names keep their case in section titles", () => {
    render(<ProjectTrustDialog files={[MCP]} onTrust={() => {}} onClose={() => {}} />);
    expect(screen.getByRole("heading", { name: "mcp.json" })).toBeInTheDocument();
  });

  it("names and diffs are plain", () => {
    expect(trustNames([AGENTS])).toBe("AGENTS.md isn't trusted yet");
    expect(trustNames([AGENTS, MCP, { ...SKILLS, state: "untrusted" }]))
      .toBe("AGENTS.md, mcp.json and this project's skills aren't trusted yet");
    expect(lineDiff("a\nb", "a\nc").map(l => l.kind)).toEqual(["ctx", "del", "add"]);
  });
});
