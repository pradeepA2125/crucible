import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FileReferenceProvider } from "../file-references/FileReferenceContext";
import { MarkdownContent } from "../components/shared/MarkdownContent";
import { PlanCard } from "../components/messages/PlanCard";
import { PostBody } from "../components/teams/PostBody";
import { vscode } from "../vscodeApi";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));
const entries = ["src/ProviderSection.test.tsx", "src/a.ts", "test/b.ts", "src/é.multi.test.ts"].map(relativePath => ({ workspaceId: "w", workspaceLabel: "workspace", relativePath }));
function publish(value: unknown = entries, revision = 1) {
  act(() => window.dispatchEvent(new MessageEvent("message", {
    data: { type: "fileReferenceCatalog", revision, entries: value },
  })));
}
function mount(content: string) {
  return render(<FileReferenceProvider><MarkdownContent content={content} /></FileReferenceProvider>);
}
beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("live message file references", () => {
  it("links prose and inline code while preserving spelling and punctuation", () => {
    const content = 'See (ProviderSection.test.tsx), then `./src/ProviderSection.test.tsx`.';
    const view = mount(content);
    expect(screen.queryAllByRole("link")).toHaveLength(0);
    publish();
    const links = screen.getAllByRole("link");
    expect(links.map(link => link.textContent)).toEqual(["ProviderSection.test.tsx", "./src/ProviderSection.test.tsx"]);
    expect(links[1]!.querySelector("code")).not.toBeNull();
    expect(view.container.textContent).toBe("See (ProviderSection.test.tsx), then ./src/ProviderSection.test.tsx.");
    fireEvent.click(links[0]!);
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "openFileReference", reference: "ProviderSection.test.tsx" });
  });
  it("links the file-and-line citations shown in chat answers", () => {
    mount("See (`src/a.ts:67–87`) and src/a.ts:222-243."); publish();
    const links = screen.getAllByRole("link");
    expect(links.map(link => link.textContent)).toEqual(["src/a.ts:67–87", "src/a.ts:222-243"]);
    fireEvent.click(links[0]!);
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "openFileReference", reference: "src/a.ts:67–87" });
  });
  it("routes authored workspace links and plan-card references through the host", () => {
    render(<FileReferenceProvider><PlanCard taskId="t" content="See [source](src/a.ts:12) and `b.ts`." /></FileReferenceProvider>);
    publish();
    const links = screen.getAllByRole("link");
    expect(links).toHaveLength(2);
    fireEvent.click(links[0]!);
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "openFileReference", reference: "src/a.ts:12" });
  });
  it("supports keyboard activation with a focusable, visibly styled link", () => {
    mount("a.ts"); publish();
    const link = screen.getByRole("link");
    expect(link.getAttribute("href")).toBe("#workspace-file-reference");
    expect(link.getAttribute("title")).toContain("a.ts");
    expect(link.className).toContain("focus-visible");
    link.focus(); expect(document.activeElement).toBe(link);
    fireEvent.keyDown(link, { key: "Enter" });
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "openFileReference", reference: "a.ts" });
  });
  it("requests one catalog for many chat and board message bodies", () => {
    render(<FileReferenceProvider>
      <MarkdownContent content="a.ts a.ts" /><PostBody text="src/a.ts" />
      <MarkdownContent content="b.ts" />
    </FileReferenceProvider>);
    expect(vscode.postMessage).toHaveBeenCalledTimes(1);
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "requestFileReferenceCatalog" });
    publish(); expect(screen.getAllByRole("link")).toHaveLength(4);
    expect(vscode.postMessage).toHaveBeenCalledTimes(1);
  });
  it("does not request a catalog outside the provider", () => {
    render(<MarkdownContent content="a.ts" />);
    expect(vscode.postMessage).not.toHaveBeenCalled();
    expect(screen.queryAllByRole("link")).toHaveLength(0);
  });
  it("updates existing messages for creation and deletion and ignores stale or malformed snapshots", () => {
    mount("a.ts");
    publish([], 1); expect(screen.queryAllByRole("link")).toHaveLength(0);
    publish(entries, 2); expect(screen.getAllByRole("link")).toHaveLength(1);
    publish([], 1); expect(screen.getAllByRole("link")).toHaveLength(1);
    publish([{ relativePath: "a.ts" }], 3); expect(screen.getAllByRole("link")).toHaveLength(1);
    publish(entries, 2.5); expect(screen.getAllByRole("link")).toHaveLength(1);
    publish([], 4); expect(screen.queryAllByRole("link")).toHaveLength(0);
  });
  it("removes its message listener on unmount and requests anew on remount", () => {
    const remove = vi.spyOn(window, "removeEventListener");
    const view = mount("a.ts"); view.unmount();
    expect(remove.mock.calls.some(call => call[0] === "message")).toBe(true);
    mount("a.ts"); publish();
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(vscode.postMessage).toHaveBeenCalledTimes(2);
    remove.mockRestore();
  });
  it("leaves authored links, images, autolinks, emails and block code unchanged", () => {
    const content = [
      "[a.ts](https://example.com)", "![a.ts](https://example.com/image)",
      "https://example.com/a.ts", "user@a.ts", "<https://a.ts>",
      "```ts\na.ts\n```", "    a.ts",
    ].join("\n\n");
    const view = mount(content); publish();
    expect(view.container.querySelectorAll('[data-file-reference]')).toHaveLength(0);
    expect(view.container.querySelector("a a")).toBeNull();
    expect(screen.getByRole("link", { name: "a.ts" }).getAttribute("href")).toBe("https://example.com");
  });
  it.each(["../a.ts", "/a.ts", "command:a.ts", "a.ts:0", "a.ts:12-1", "https://x/a.ts", "a.ts#L1", "a%2ets", "user@a.ts", "missing/a.ts", "A.ts"])("never links a safe-looking suffix within %s", content => {
    const view = mount(content); publish();
    expect(view.container.querySelectorAll('[data-file-reference]')).toHaveLength(0);
  });
  it("preserves mixed inline-code text, whitespace and non-ASCII multi-dot filenames", () => {
    const view = mount('`a.ts b.ts unknown https://x/a.ts` and "é.multi.test.ts".');
    publish();
    expect(screen.getAllByRole("link").map(link => link.textContent)).toEqual(["a.ts", "b.ts", "é.multi.test.ts"]);
    expect(view.container.textContent).toBe('a.ts b.ts unknown https://x/a.ts and "é.multi.test.ts".');
    expect(view.container.querySelectorAll("a code")).toHaveLength(2);
  });
  it("links list and GFM table cells without changing table wrapping", () => {
    const view = mount("- a.ts\n\n| File |\n| --- |\n| b.ts |"); publish();
    expect(screen.getAllByRole("link")).toHaveLength(2);
    expect(view.container.querySelector("table")?.parentElement?.className).toContain("overflow-x-auto");
  });
});
