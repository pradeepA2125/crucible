import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, it, vi } from "vitest";
import { ReviewSurface } from "../desktop/ReviewSurface";
import { ChangedFilesTree, type ReviewFile } from "../desktop/ChangedFilesTree";
import { DiffLines } from "../components/shared/DiffPanes";
const files: ReviewFile[] = [{ path: "apps/desktop/a.ts", status: "Modified" }, { path: "apps/desktop/b.ts", status: "Deleted" }, { path: "docs/new.md", status: "Renamed", description: "Renamed from docs/old.md" }];
it("filters the hierarchical tree without changing the diff, restores closed folders and navigates shown files", async () => {
  const select = vi.fn();
  function Harness() { const [path, setPath] = useState(files[0]!.path); return <ReviewSurface title="Conversation edits" summary="3 files" information="Showing one file at a time" files={files} selected={path} select={value => { select(value); setPath(value); }}>{() => <article aria-label={path}>diff</article>}</ReviewSurface>; }
  render(<Harness />);
  expect(screen.getByRole("button", { name: "Previous changed file" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "apps/desktop" })); expect(screen.queryByRole("button", { name: "apps/desktop/a.ts Modified" })).toBeNull();
  fireEvent.change(screen.getByRole("textbox", { name: "Filter changed files" }), { target: { value: "DESKTOP/A" } });
  expect(screen.getByRole("button", { name: "apps/desktop/a.ts Modified" })).toBeInTheDocument(); expect(select).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Next changed file" })).toBeDisabled();
  fireEvent.change(screen.getByRole("textbox", { name: "Filter changed files" }), { target: { value: "" } });
  expect(screen.queryByRole("button", { name: "apps/desktop/a.ts Modified" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "apps/desktop" }));
  fireEvent.click(screen.getByRole("button", { name: "Next changed file" })); expect(select).toHaveBeenLastCalledWith("apps/desktop/b.ts");
  fireEvent.change(screen.getByRole("textbox", { name: "Filter changed files" }), { target: { value: "new.md" } });
  expect(screen.getByRole("article", { name: "apps/desktop/b.ts" })).toBeInTheDocument(); expect(screen.getByText(/outside the shown tree/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "docs/new.md Renamed" })).toHaveAccessibleDescription("Renamed from docs/old.md");
  fireEvent.click(screen.getByRole("button", { name: "Next changed file" })); expect(select).toHaveBeenLastCalledWith("docs/new.md");
  fireEvent.click(screen.getByRole("button", { name: "Toggle changed files" })); expect(screen.queryByRole("complementary", { name: "Changed files navigator" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Wrap diff lines" })); expect(screen.getByRole("button", { name: "Wrap diff lines" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("article", { name: "docs/new.md" })).toBeInTheDocument();
});
it("caps files, compresses deep folder chains and preserves exact path accessibility", () => {
  const paths = Array.from({ length: 501 }, (_, index) => ({ path: `a/b/c/d/file-${index}.ts` }));
  render(<ChangedFilesTree files={paths} select={vi.fn()} />);
  expect(screen.getByRole("button", { name: "a/b/c/d" })).toHaveAttribute("aria-expanded", "true"); expect(screen.getAllByRole("button", { name: /file-\d+\.ts/ })).toHaveLength(500); expect(screen.queryByRole("button", { name: "a/b/c/d/file-500.ts" })).toBeNull(); expect(screen.getByText("Showing the first 500 changed files.")).toBeInTheDocument();
});
it("folds only available context, keeps uncaptured gaps honest and renders code as escaped text", () => {
  const patch = '@@ -10,13 +10,13 @@\n' + Array.from({ length: 12 }, (_, n) => ` context-${n}`).join('\n') + '\n-old\n+const value = "<script>";\n@@ -50 +50 @@\n-previous\n+next';
  const ui = render(<DiffLines diff={patch} path="a.ts" presentation="review" />);
  expect(ui.container.textContent).not.toContain("context-6"); fireEvent.click(screen.getByRole("button", { name: "6 unchanged lines · expand" })); expect(ui.container.textContent).toContain("context-6");
  expect(screen.getByText("9 lines not included in this diff")).toBeInTheDocument(); expect(screen.getByText("27 lines not included in this diff")).toBeInTheDocument(); expect(document.querySelector('script')).toBeNull(); expect(screen.getByRole("region", { name: "Recorded diff a.ts" })).toHaveTextContent('const value = "<script>";');
  expect(ui.container.querySelector('.review-code-keyword')).toHaveTextContent('const');
  ui.rerender(<DiffLines diff={'@@ -1 +1 @@\n-old\n+' + 'const '.repeat(40000)} path="a.ts" presentation="review" />); expect(ui.container.querySelector('.review-code-keyword')).toBeNull();
  ui.rerender(<DiffLines diff={'@@ -1 +1 @@\n-old\n+const value'} path="a.unknown" presentation="review" />); expect(ui.container.querySelector('.review-code-keyword')).toBeNull();
});
it("keeps default VS Code diff markup stable and the Changes panes as scroll owners", () => {
  const ui = render(<DiffLines diff={"@@ -1 +1 @@\n-old\n+new"} />);
  expect(ui.container.querySelector('.review-code')).toBeNull();
  expect(ui.container.innerHTML).toBe('<div class="flex mono text-caption leading-[1.8] whitespace-pre pr-3 text-text-4 italic"><span class="w-[34px] flex-shrink-0 text-right pr-2.5 text-text-4 select-none tabular-nums"></span><span class="w-3.5 flex-shrink-0 "></span><span>@@ -1 +1 @@</span></div><div class="flex mono text-caption leading-[1.8] whitespace-pre pr-3 bg-[var(--diff-del-bg)] text-text"><span class="w-[34px] flex-shrink-0 text-right pr-2.5 text-text-4 select-none tabular-nums"></span><span class="w-3.5 flex-shrink-0 text-red">-</span><span>old</span></div><div class="flex mono text-caption leading-[1.8] whitespace-pre pr-3 bg-[var(--diff-add-bg)] text-text"><span class="w-[34px] flex-shrink-0 text-right pr-2.5 text-text-4 select-none tabular-nums">1</span><span class="w-3.5 flex-shrink-0 text-green">+</span><span>new</span></div>');
  const css = readFileSync(join(__dirname, '..', 'desktop', 'desktop.css'), 'utf8');
  expect(css).toMatch(/\.review-diff-body\{[^}]*min-height:0[^}]*overflow:auto/); expect(css).toMatch(/\.review-tree-scroll\{[^}]*overflow:auto/); expect(css).not.toMatch(/\.review-code\{[^}]*overflow:auto/); expect(css).not.toMatch(/\.review-file-detail\{[^}]*overflow:auto/);
  expect(css).not.toMatch(/\.review-file-path\{[^}]*unicode-bidi:plaintext/);
  expect(css).toMatch(/\.review-file-path\{[^}]*direction:rtl/);
});

it("keeps review syntax colours readable on plain and changed rows in both built-in themes", () => {
  const css = readFileSync(join(__dirname, '..', 'styles', 'themes.css'), 'utf8');
  const block = (selector: string) => css.slice(css.indexOf(selector + " {"), css.indexOf("}", css.indexOf(selector + " {")));
  const rgb = (hex: string) => [1, 3, 5].map(n => parseInt(hex.slice(n, n + 2), 16));
  const luminance = (value: number[]) => value.map(n => n / 255).map(n => n <= .04045 ? n / 12.92 : ((n + .055) / 1.055) ** 2.4).reduce((total, n, i) => total + n * [.2126, .7152, .0722][i]!, 0);
  const contrast = (a: number[], b: number[]) => { const [hi, lo] = [luminance(a), luminance(b)].sort((a,b) => b-a); return (hi! + .05) / (lo! + .05); };
  for (const tone of ['dark', 'light']) {
    const palette = block(`:root[data-theme="${tone}"]`), value = (name: string) => rgb(new RegExp(name + ': (#[0-9a-f]{6})').exec(palette)![1]!);
    const surface = value('--surface-0'), backgrounds = [surface, surface.map((n,i) => n * .9 + value('--diff-add')[i]! * .1), surface.map((n,i) => n * .91 + value('--diff-del')[i]! * .09)];
    const colours = ['--text-1','--text-3','--success','--warning'].map(value);
    for (const accent of ['blue','violet','ember']) { const selector = `[data-tone="${tone}"][data-accent="${accent}"]`, rule = block(selector); colours.push(rgb(/--accent: (#[0-9a-f]{6})/.exec(rule)![1]!).map((n,i) => n * .94 + value("--text-1")[i]! * .06)); }
    for (const background of backgrounds) for (const colour of colours) expect(contrast(colour,background)).toBeGreaterThanOrEqual(4.5);
  }
});

it("draws one decorative guide per expanded ancestor with 16px indentation and keeps merged folders", () => {
  render(<ChangedFilesTree files={['src/a.ts','src/nested/b.ts','src/nested/deep/c.ts','src/nested/deep/d.ts','other/merged/folder/x.ts'].map(path => ({path}))} select={vi.fn()} />);
  const count = (element: HTMLElement) => { let current = element.parentElement, total = 0; while (current) { if (current.classList.contains('review-tree-children')) total++; current = current.parentElement; } return total; };
  const deep = screen.getByRole('button', {name:'src/nested/deep/c.ts'});
  expect(count(deep)).toBe(3); expect(deep).toHaveStyle({paddingLeft:'56px'});
  expect(count(screen.getByRole('button', {name:'other/merged/folder/x.ts'}))).toBe(1);
  expect(screen.getByRole('button', {name:'other/merged/folder'})).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name:'src/nested'})); expect(screen.queryByRole('button', {name:'src/nested/deep/c.ts'})).toBeNull();
  fireEvent.change(screen.getByRole('textbox', {name:'Filter changed files'}), {target:{value:'deep/c.ts'}});
  expect(count(screen.getByRole('button', {name:'src/nested/deep/c.ts'}))).toBe(1);
  const css = readFileSync(join(__dirname, '..', 'desktop', 'desktop.css'), 'utf8'); expect(css).toContain('border-left:1px solid var(--border);pointer-events:none');
});
