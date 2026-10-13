// Verify actual CSS layout and motion, rather than jsdom's class strings.
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { compile } from "@tailwindcss/node";
import { render, fireEvent } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ActivityLine } from "../components/activity/ActivityLine";
import { TransientActivity } from "../components/activity/TransientActivity";
import { TurnChanges } from "../components/activity/TurnChanges";
import { AgentRow } from "../components/messages/AgentRow";
import { UserMessage } from "../components/messages/UserMessage";
import { MarkdownContent } from "../components/shared/MarkdownContent";

const chrome = process.env.CHROME_BIN ?? [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome",
].find(existsSync);
const token = "long_unbroken_path_".repeat(40);
const event = { id: 1, tool: "search_code", args: { pattern: token }, source: "explore" as const, done: false };

// Own palettes plus the default host fallback and high-contrast override.
for (const theme of ["vscode", "dark", "light", "hc"] as const) {
  describe.skipIf(!chrome)(`transcript layout in ${theme}`, () => {
    it("fits expanded details, preserves accent text contrast, and honors reduced motion", async () => {
      const opened = render(<ActivityLine event={{ ...event, tool: `mcp_${token}`, args: { path: token }, thought: token, done: true, output: token }} />);
      fireEvent.click(opened.getByRole("button", { name: /^Used/ }));
      const detailMarkup = opened.container.innerHTML;
      opened.unmount();
      const markup = renderToStaticMarkup(<>
        {[280, 360, 1024].map(width => (
          <section key={width} data-panel style={{ width }}>
            <div data-column className="mx-auto flex w-full max-w-[760px] flex-col gap-6 px-4 [&>*]:flex-shrink-0">
              <UserMessage content={`Inspect @${token} and \`retry()\`.`} mentionedFiles={[token]} queued />
              <div data-open-detail dangerouslySetInnerHTML={{ __html: detailMarkup }} />
              <MarkdownContent content={`A plain answer.\n\n${token}\n\nUse \`retry()\`.`} />
              <ActivityLine event={event} />
              <ActivityLine event={{ ...event, id: 2, tool: token }} />
              <TransientActivity startedAt={Date.now()} bubble={{ text: "", thinkingEntries: [], activeThinkingChunk: "", toolEvents: [event] }} />
              <AgentRow content={token} streaming hideThinkingBlock />
              <TurnChanges files={Array.from({ length: 4 }, (_, i) => ({ path: `src/${token}${i}.ts`, additions: 2, deletions: 1, applied: true }))} />
            </div>
          </section>
        ))}
      </>);
      const compiler = await compile(readFileSync(resolve("src/index.css"), "utf8"), { base: resolve("src"), onDependency: () => {} });
      const candidates = [...markup.matchAll(/class="([^"]*)"/g)].flatMap(m => m[1].replace(/&amp;/g, "&").replace(/&gt;/g, ">").replace(/&lt;/g, "<").split(/\s+/));
      const css = compiler.build(candidates);
      const dir = mkdtempSync(join(tmpdir(), "transcript-layout-"));
      try {
        const file = join(dir, "test.html");
        writeFileSync(file, `<!doctype html><html data-theme="${theme}" data-tone="${theme === "light" ? "light" : "dark"}" data-accent="blue"><meta charset="utf-8"><style>${css}</style><body>${markup}<script>
          const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
          const ctx = canvas.getContext('2d');
          const painted = (fg, bg, opacity = 1) => {
            ctx.clearRect(0, 0, 1, 1); ctx.globalAlpha = 1; ctx.fillStyle = bg; ctx.fillRect(0, 0, 1, 1);
            ctx.globalAlpha = opacity; ctx.fillStyle = fg; ctx.fillRect(0, 0, 1, 1);
            return [...ctx.getImageData(0, 0, 1, 1).data].slice(0, 3);
          };
          const lum = rgb => rgb.map(x => x / 255).map(x => x <= .04045 ? x / 12.92 : ((x + .055) / 1.055) ** 2.4).reduce((s, x, i) => s + x * [.2126, .7152, .0722][i], 0);
          const contrast = (a, b) => (Math.max(lum(a), lum(b)) + .05) / (Math.min(lum(a), lum(b)) + .05);
          const result = ['blue', 'violet', 'ember'].flatMap(accent => {
            document.documentElement.dataset.accent = accent;
            return [...document.querySelectorAll('[data-panel]')].map(panel => {
            const column = panel.querySelector('[data-column]');
            const rect = column.getBoundingClientRect(), outer = panel.getBoundingClientRect();
            const bubble = panel.querySelector('[data-testid="user-bubble"]');
            const code = panel.querySelector('p code');
            const bs = getComputedStyle(bubble), us = getComputedStyle(bubble.querySelector('code')), qs = getComputedStyle(bubble.querySelector('[data-testid="queued-tag"]'));
            const bg = painted(bs.backgroundColor, bs.backgroundColor), codeBg = painted(us.backgroundColor, bs.backgroundColor);
            const detail = panel.querySelector('[data-open-detail]');
            return {
              accent, width: panel.clientWidth, detailOverflow: detail.scrollWidth - detail.clientWidth,
              bubbleContrast: contrast(painted(bs.color, bs.backgroundColor), bg),
              userCodeContrast: contrast(painted(us.color, 'rgb(' + codeBg.join(',') + ')'), codeBg),
              queuedContrast: contrast(painted(qs.color, bs.backgroundColor, Number(qs.opacity)), bg), overflow: panel.scrollWidth - panel.clientWidth,
              columnWidth: rect.width, centered: Math.abs(rect.left - outer.left - (outer.width - rect.width) / 2),
              bubbleWidth: bubble.getBoundingClientRect().width, bubbleBg: getComputedStyle(bubble).backgroundColor,
              bubbleFg: getComputedStyle(bubble).color, codeBg: getComputedStyle(code).backgroundColor,
              shimmer: getComputedStyle(panel.querySelector('.shimmer-text')).animationName,
              liveVerbCount: panel.querySelector('[data-testid="transient-text"]').querySelectorAll('.shimmer-text').length,
              reduced: matchMedia('(prefers-reduced-motion: reduce)').matches
            };
          }); });
          document.body.innerHTML = '<pre id="result">' + JSON.stringify(result) + '</pre>';
        </script></body></html>`);
        for (const reduced of [false, true]) {
          const output = execFileSync(chrome!, ["--headless", "--no-sandbox", "--disable-gpu", "--no-first-run", "--disable-background-networking",
            ...(reduced ? ["--force-prefers-reduced-motion"] : []), "--dump-dom", pathToFileURL(file).href],
            { encoding: "utf8", timeout: 10000, killSignal: "SIGKILL", stdio: ["ignore", "pipe", "ignore"] });
          const encoded = output.match(/<pre id="result">([\s\S]*?)<\/pre>/)?.[1];
          expect(encoded).toBeTruthy();
          const results = JSON.parse(encoded!.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&"));
          for (const result of results) {
            expect(result.overflow, `${theme}, ${result.width}px`).toBeLessThanOrEqual(1);
            expect(result.columnWidth).toBe(Math.min(result.width, 760));
            expect(result.centered).toBeLessThanOrEqual(1);
            expect(result.bubbleWidth).toBeLessThanOrEqual((result.columnWidth - 32) * 0.8 + 1);
            expect(result.bubbleBg).not.toBe("rgba(0, 0, 0, 0)");
            expect(result.bubbleBg).not.toBe(result.bubbleFg);
            expect(result.detailOverflow, `${theme}/${result.accent}, expanded tool details at ${result.width}px`).toBeLessThanOrEqual(1);
            expect(result.bubbleContrast, `${theme}/${result.accent} bubble`).toBeGreaterThanOrEqual(4.5);
            expect(result.userCodeContrast, `${theme}/${result.accent} inline code`).toBeGreaterThanOrEqual(4.5);
            expect(result.queuedContrast, `${theme}/${result.accent} queued tag`).toBeGreaterThanOrEqual(4.5);
            expect(result.codeBg).not.toBe("rgba(0, 0, 0, 0)");
            expect(result.liveVerbCount).toBe(1);
            expect(result.reduced).toBe(reduced);
            expect(result.shimmer).toBe(reduced ? "none" : "shimmer");
          }
        }
      } finally { rmSync(dir, { recursive: true, force: true }); }
    }, 60000);
  });
}
