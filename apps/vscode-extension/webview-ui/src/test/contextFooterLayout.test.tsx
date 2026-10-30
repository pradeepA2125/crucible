// Geometry is measured in installed Chrome, not jsdom (same approach as markdownWrapping).
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";
import { chrome, dumpChromeDom } from "./chromeLayout";
import { compile } from "@tailwindcss/node";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { popoverPosition } from "../components/shared/usePopover";
import { InputArea } from "../components/InputArea";
import { contextUsage } from "./contextUsageFixture";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

describe.skipIf(!chrome)("context composer footer in a real browser", () => {
  const cases = (["send", "stop", "task"] as const).flatMap(mode =>
    (["chatgpt", "claude_subscription"] as const).map(provider => ({ mode, provider })));
  for (const { mode, provider } of cases) {
    it(`wraps ${provider} ${mode} controls and context details without hiding them`, async () => {
      const total = { requests: 1, input: 300000, output: 12000, cached: 9000 };
      const { container } = render(<InputArea
        availability={{ disabled: false, placeholder: "Message", showStop: mode === "stop", taskStop: mode === "task" }}
        draft="hello" onDraftChange={() => {}}
        contextUsage={{ ...contextUsage, context_window: null, capacity_source: "unknown", model: "long_model_identifier_".repeat(10) }}
        contextUsageStale threadUsage={{ total, main: total, agents: {}, teams: {} }} />);
      act(() => window.dispatchEvent(new MessageEvent("message", { data: {
        type: "modelList", usesChatgptPlan: provider === "chatgpt", usesClaudePlan: provider === "claude_subscription",
        current: { backend: provider, model: "long-composer-model-name" }, options: [],
        effort: { level: provider === "claude_subscription" ? "xhigh" : "max", support: {
          supported: ["low", "medium", "high", "xhigh", "max"], unsupported: {},
          control: { source: "catalog", default: null, reason: null,
            options: ["low", "medium", "high", "xhigh", "max"].map(value => ({
              value, label: value === "xhigh" ? "Extra high" : value[0]!.toUpperCase() + value.slice(1),
              description: "", state: "supported",
            })) },
        } },
      } })));
      fireEvent.focus(screen.getByRole("button", { name: /context.*details/i }));
      const markup = ["dark", "light", "hc", "vscode"].flatMap(theme => ["blue", "violet", "ember"].flatMap(accent => [280, 320, 360, 640].map(width =>
        `<section data-panel data-theme="${theme}" data-tone="${theme === "light" ? "light" : "dark"}" data-accent="${accent}" style="width:${width}px;padding-top:500px">${container.innerHTML}</section>`))).join("");
      const compiler = await compile(readFileSync(resolve("src/index.css"), "utf8"), {
        base: resolve("src"), onDependency: () => {},
      });
      const candidates = [...markup.matchAll(/class="([^"]*)"/g)]
        .flatMap(m => m[1].replace(/&amp;/g, "&").split(/\s+/));
      const dir = mkdtempSync(join(tmpdir(), "context-footer-"));
      try {
        const file = join(dir, "test.html");
        writeFileSync(file, `<!doctype html><meta charset="utf-8"><style>${compiler.build(candidates).replace(/:root/g, '[data-panel]').replace(/html\[/g, '[data-panel][')}</style>${markup}
          <script>
            const place = ${popoverPosition.toString()};
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
              const anchor = panel.querySelector('[data-testid="context-meter"]').getBoundingClientRect();
              const panelBounds = panel.getBoundingClientRect();
              const position = place(anchor.left - panelBounds.left, panel.clientWidth, 320);
              details.style.left = position.left + 'px'; details.style.width = position.width + 'px';
              const r = details.getBoundingClientRect();
              return { theme: panel.dataset.theme, accent: panel.dataset.accent, width: panel.clientWidth, overflow: panel.scrollWidth - panel.clientWidth, controls,
                wrapped: new Set(controls.map(c => c.top)).size > 1,
                detailsFit: r.left >= panelBounds.left - 1 && r.right <= panelBounds.right + 1 && details.scrollWidth <= details.clientWidth + 1 };
            });
            document.body.innerHTML = '<pre id="result">' + JSON.stringify(results) + '</pre>';
          </script>`);
        const output = dumpChromeDom(file);
        const encoded = output.match(/<pre id="result">([\s\S]*?)<\/pre>/)?.[1];
        expect(encoded, "browser executes geometry measurements").toBeTruthy();
        const results = JSON.parse(encoded!.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&"));
        for (const result of results) {
          expect(result.overflow, `${mode} panel ${result.width}px: ${JSON.stringify(result.controls.filter((c: { visible: boolean }) => !c.visible))}`).toBeLessThanOrEqual(1);
          expect(result.controls.filter((c: { visible: boolean }) => !c.visible), "all controls fit, not hidden or shrunk away").toEqual([]);
          expect(result.controls.map((c: { name: string }) => c.name)).toEqual(expect.arrayContaining([
            "Add context", "Approval mode: Ask for approval", "Plan Mode", `Model: long-composer-model-name · ${provider === "claude_subscription" ? "Extra high" : "Max"}`, mode === "stop" ? "Stop" : mode === "task" ? "Stop and keep changes" : "Send",
            ...(mode === "task" ? ["Task stop options"] : []),
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
