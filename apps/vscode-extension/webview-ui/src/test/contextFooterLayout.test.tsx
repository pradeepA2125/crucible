// Geometry is measured in installed Chrome, not jsdom (same approach as markdownWrapping).
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";
import { pathToFileURL } from "node:url";
import { compile } from "@tailwindcss/node";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { InputArea } from "../components/InputArea";
import { contextUsage } from "./contextUsageFixture";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));
const chrome = process.env.CHROME_BIN ?? [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome",
].find(existsSync);

describe.skipIf(!chrome)("context composer footer in a real browser", () => {
  for (const mode of ["send", "stop", "task"] as const) {
    it(`wraps ${mode} controls and context details without hiding them`, async () => {
      const total = { requests: 1, input: 300000, output: 12000, cached: 9000 };
      const { container } = render(<InputArea
        availability={{ disabled: false, placeholder: "Message", showStop: mode === "stop", taskStop: mode === "task" }}
        draft="hello" onDraftChange={() => {}}
        contextUsage={{ ...contextUsage, context_window: null, capacity_source: "unknown", model: "long_model_identifier_".repeat(10) }}
        contextUsageStale threadUsage={{ total, main: total, agents: {}, teams: {} }} />);
      act(() => window.dispatchEvent(new MessageEvent("message", { data: {
        type: "modelList", usesChatgptPlan: true,
        current: { backend: "chatgpt", model: "gpt-long-composer-model-name" }, options: [],
        effort: { level: "max", support: null },
      } })));
      fireEvent.focus(screen.getByRole("button", { name: /context.*details/i }));
      const markup = [280, 360, 640].map(width =>
        `<section data-panel style="width:${width}px;padding-top:500px">${container.innerHTML}</section>`).join("");
      const compiler = await compile(readFileSync(resolve("src/index.css"), "utf8"), {
        base: resolve("src"), onDependency: () => {},
      });
      const candidates = [...markup.matchAll(/class="([^"]*)"/g)]
        .flatMap(m => m[1].replace(/&amp;/g, "&").split(/\s+/));
      const dir = mkdtempSync(join(tmpdir(), "context-footer-"));
      try {
        const file = join(dir, "test.html");
        writeFileSync(file, `<!doctype html><meta charset="utf-8"><style>${compiler.build(candidates)}</style>${markup}
          <script>
            const results = [...document.querySelectorAll('[data-panel]')].map(panel => {
              const footer = panel.querySelector('[data-testid="composer-footer"]');
              const bounds = footer.getBoundingClientRect();
              const controls = [...footer.querySelectorAll('button, input:not([type="file"])')].map(el => {
                const r = el.getBoundingClientRect();
                return { name: el.getAttribute('aria-label') || el.textContent.trim(),
                  visible: r.width > 0 && r.height > 0 && r.left >= bounds.left - 1 && r.right <= bounds.right + 1,
                  top: r.top };
              });
              const details = footer.querySelector('[role="tooltip"]');
              const r = details.getBoundingClientRect();
              return { width: panel.clientWidth, overflow: panel.scrollWidth - panel.clientWidth, controls,
                wrapped: new Set(controls.map(c => c.top)).size > 1,
                detailsFit: r.left >= bounds.left - 1 && r.right <= bounds.right + 1 && details.scrollWidth <= details.clientWidth + 1 };
            });
            document.body.innerHTML = '<pre id="result">' + JSON.stringify(results) + '</pre>';
          </script>`);
        const output = execFileSync(chrome!, [
          "--headless", "--no-sandbox", "--disable-gpu", "--no-first-run", "--disable-background-networking",
          "--dump-dom", pathToFileURL(file).href,
        ], { encoding: "utf8", timeout: 10000, killSignal: "SIGKILL", stdio: ["ignore", "pipe", "ignore"] });
        const encoded = output.match(/<pre id="result">([\s\S]*?)<\/pre>/)?.[1];
        expect(encoded, "browser executes geometry measurements").toBeTruthy();
        const results = JSON.parse(encoded!.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&"));
        for (const result of results) {
          expect(result.overflow, `${mode} panel ${result.width}px: ${JSON.stringify(result.controls.filter((c: { visible: boolean }) => !c.visible))}`).toBeLessThanOrEqual(1);
          expect(result.controls.filter((c: { visible: boolean }) => !c.visible), "all controls fit, not hidden or shrunk away").toEqual([]);
          expect(result.controls.map((c: { name: string }) => c.name)).toEqual(expect.arrayContaining([
            "Open settings", "Reasoning effort: Max", "Model: gpt-long-composer-model-name", "Manage usage", mode === "stop" ? "Stop" : "Send",
            ...(mode === "task" ? ["Stop and keep changes", "Stop and revert changes"] : []),
          ]));
          expect(result.detailsFit, "long model provenance tooltip fits footer").toBe(true);
          if (result.width <= 360) expect(result.wrapped).toBe(true);
        }
      } finally {
        rmSync(dir, { recursive: true, force: true });
      }
    }, 60000);
  }
});
