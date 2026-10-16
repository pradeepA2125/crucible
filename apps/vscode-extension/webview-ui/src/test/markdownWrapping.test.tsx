// Layout measurements run in Chrome, not in jsdom.
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";
import { pathToFileURL } from "node:url";
import { compile } from "@tailwindcss/node";
import { renderToStaticMarkup } from "react-dom/server";
import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MarkdownContent } from "../components/shared/MarkdownContent";
import { AgentsContext, type AgentsUi } from "../components/agents/AgentsContext";
import { TeamsContext, type TeamsUi } from "../components/teams/TeamsContext";
import { HandedPost, MemberView } from "../components/teams/MemberView";
import type { TeamPostView } from "../types";
import { PostBody } from "../components/teams/PostBody";
import { ResultCard } from "../components/teams/ResultCard";

// No browser dependency/download: CI can point CHROME_BIN at installed Chromium.
const chrome = process.env.CHROME_BIN ?? [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome",
].find(existsSync);
const token = "long_path_or_identifier_".repeat(30);
const code = `  command --argument=${token}\n    second line`;
const message = [
  "A normal paragraph with enough words to wrap in a narrow panel. ".repeat(8),
  `[${token}](https://example.com/${token})`,
  `Inline path: \`${token}\``,
  `- Nested list\n  - ${token}`,
  `\`\`\`text\n${code}\n\`\`\``,
].join("\n\n");

const memberPost = { teamId: "t", seq: 1, author: "main", text: message, kind: "post",
  recipient: null, mentions: [], refId: null, round: 1, payload: {}, closed: null, createdAt: "2026-10-09T10:00:00Z" } as TeamPostView;
const memberTeams = {
  teams: { t: { members: [{ label: "design", agentId: "a", status: "running" }] } },
  views: { t: { posts: [memberPost, { ...memberPost, seq: 2, author: "design" }], activity: [
    { label: "design", kind: "woke", activation: 1, causeSeq: 1, at: memberPost.createdAt,
      payload: { cause: "kickoff", by: "main", post_seq: 1 } },
    { label: "design", kind: "took_up", activation: 1, at: memberPost.createdAt, payload: { posts: [1] } },
  ] } }, openTeam: () => {},
} as unknown as TeamsUi;
const memberAgents = {
  agents: { a: { status: "running", now: token } },
  views: { a: { messages: [{ role: "agent", type: "text", content: message,
    timestamp: memberPost.createdAt, metadata: { report: true, status: "completed" } }], live: [] } },
} as unknown as AgentsUi;

describe.skipIf(!chrome)("markdown wrapping in a real browser", () => {
  for (const surface of ["board", "agent", "handed", "member", "result"] as const) {
    it(`${surface} wraps prose, paths, and code without widening nested grids or flex boxes`, async () => {
      // Measure the full disclosed post, including long paths and later lines.
      let handedMarkup = "";
      if (surface === "result") {
        const result = render(<ResultCard roster={["design"]} post={{ ...memberPost,
          kind: "result", author: "design", payload: { deliverable_kind: "plan", outcome: "actionable", run_id: 1 } }} />);
        fireEvent.click(result.getByRole("button", { name: "Show more of Plan ready by design" }));
        handedMarkup = result.container.innerHTML;
        result.unmount();
      } else if (surface === "handed") {
        const handed = render(<HandedPost reference="#1" roster={["design"]} post={{ author: "main", text: message } as TeamPostView} />);
        fireEvent.click(handed.getByRole("button", { name: "Show more of handed post #1" }));
        handedMarkup = handed.container.innerHTML;
        handed.unmount();
      }
      const markdown = surface === "board"
        ? <PostBody text={message} /> : surface === "agent"
          ? <MarkdownContent content={message} />
          : surface === "member"
            ? <AgentsContext.Provider value={memberAgents}><TeamsContext.Provider value={memberTeams}>
                <MemberView teamId="t" agentId="a" />
              </TeamsContext.Provider></AgentsContext.Provider>
            : <div dangerouslySetInnerHTML={{ __html: handedMarkup }} />;
      const markup = renderToStaticMarkup(<>
        {[280, 360, 640].map((width) => (
          <section key={width} data-panel style={{ width }} className="overflow-y-auto">
            <div className="grid gap-2 pl-[22px]">
              <article className="grid gap-2 rounded-lg border px-3 py-2">
                <div className="grid gap-2"><div className="flex flex-col">{markdown}</div></div>
              </article>
            </div>
          </section>
        ))}
      </>);
      const compiler = await compile(readFileSync(resolve("src/index.css"), "utf8"), {
        base: resolve("src"), onDependency: () => {},
      });
      const candidates = [...markup.matchAll(/class="([^"]*)"/g)]
        .flatMap((m) => m[1].replace(/&amp;/g, "&").split(/\s+/));
      const css = compiler.build(candidates);
      const dir = mkdtempSync(join(tmpdir(), "markdown-wrap-"));
      try {
        const file = join(dir, "test.html");
        writeFileSync(file, `<!doctype html><meta charset="utf-8"><style>${css}</style>${markup}
          <script>
            const panels = [...document.querySelectorAll('[data-panel]')];
            const result = panels.map(panel => ({
              width: panel.clientWidth,
              handedHeight: panel.querySelector("span.flex-1")?.getBoundingClientRect().height,
              wrap: getComputedStyle(panel.querySelector('p') ?? panel.querySelector('span.flex-1')).overflowWrap,
              layout: getComputedStyle(panel.querySelector('article')).display,
              overflow: panel.scrollWidth - panel.clientWidth,
              preOverflow: panel.querySelector('pre') ? panel.querySelector('pre').scrollWidth - panel.querySelector('pre').clientWidth : 0,
              whiteSpace: getComputedStyle(panel.querySelector('pre') ?? panel.querySelector('span.flex-1')).whiteSpace,
              code: panel.querySelector('pre code')?.textContent,
              clipped: [...panel.querySelectorAll('*')].some(el =>
                ['hidden', 'clip'].includes(getComputedStyle(el).overflowX) && el.scrollWidth > el.clientWidth)
            }));
            document.body.innerHTML = '<pre id="result">' + JSON.stringify(result) + '</pre>';
          </script>`);
        const output = execFileSync(chrome!, [
          "--headless", "--no-sandbox", "--disable-gpu", "--no-first-run", "--disable-background-networking",
          "--dump-dom", pathToFileURL(file).href,
        ], { encoding: "utf8", timeout: 10000, killSignal: "SIGKILL", stdio: ["ignore", "pipe", "ignore"] });
        const encoded = output.match(/<pre id="result">([\s\S]*?)<\/pre>/)?.[1];
        expect(encoded, "browser must execute measurement script").toBeTruthy();
        const results = JSON.parse(encoded!.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&"));
        for (const result of results) {
          if (surface === "handed") expect(result.handedHeight, "handed message must occupy multiple lines").toBeGreaterThan(40);
          expect(result.overflow, `${surface} panel at ${result.width}px`).toBeLessThanOrEqual(1);
          expect(result.preOverflow, "code should wrap, not scroll sideways").toBeLessThanOrEqual(1);
          expect(result.whiteSpace).toBe("pre-wrap");
          if (surface !== "handed") expect(result.code).toBe(`${code}\n`);
          expect(result.clipped, "do not fix overflow by hiding content").toBe(false);
        }
      } finally {
        rmSync(dir, { recursive: true, force: true });
      }
    }, 60000);
  }
});
