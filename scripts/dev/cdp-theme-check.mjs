#!/usr/bin/env node
// Real-browser theme check (spec 2026-10-13 Testing → computed-colour checks). jsdom
// can't resolve CSS variables, so this reads computed styles from the live Crucible
// webviews over CDP. Usage: node scripts/dev/cdp-theme-check.mjs [expectedTheme] [expectedAccent]
const [expectedTheme, expectedAccent] = process.argv.slice(2);
const PORT = Number(process.env.CDP_PORT ?? 9335);

const targets = await (await fetch(`http://localhost:${PORT}/json/list`)).json();
const webviews = targets.filter((t) => t.type === "iframe" && t.url.startsWith("vscode-webview://"));
if (webviews.length === 0) throw new Error("No webview targets — open a Crucible panel first.");

// Evaluated inside each webview target. VS Code nests the page document in a child
// iframe; pick the document that has #root.
const PROBE = `(() => {
  const docs = [document, ...Array.from(document.querySelectorAll('iframe')).map((f) => { try { return f.contentDocument; } catch { return null; } })];
  const doc = docs.find((d) => d && d.getElementById('root'));
  if (!doc) return null;
  const root = doc.documentElement, cs = getComputedStyle(root), body = getComputedStyle(doc.body);
  const v = (n) => cs.getPropertyValue(n).trim();
  return { title: doc.title, theme: root.dataset.theme, tone: root.dataset.tone, accent: root.dataset.accent,
    bodyClass: doc.body.className, bodyBg: body.backgroundColor, bodyFg: body.color,
    tokens: { surface0: v('--surface-0'), text1: v('--text-1'), accent: v('--accent'), accentFill: v('--accent-fill'), border: v('--border') } };
})()`;

let failures = 0;
for (const target of webviews) {
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener("open", r, { once: true }));
  const result = await new Promise((resolve) => {
    ws.addEventListener("message", (e) => {
      const m = JSON.parse(e.data);
      if (m.id === 1) resolve(m.result?.result?.value ?? null);
    });
    ws.send(JSON.stringify({ id: 1, method: "Runtime.evaluate", params: { expression: PROBE, returnByValue: true } }));
  });
  ws.close();
  if (!result) continue;
  const problems = [];
  if (expectedTheme && result.theme !== expectedTheme) problems.push(`theme ${result.theme} ≠ ${expectedTheme}`);
  if (expectedAccent && result.theme !== "hc" && result.accent !== expectedAccent) problems.push(`accent ${result.accent} ≠ ${expectedAccent}`);
  if (Object.values(result.tokens).some((t) => t === "")) problems.push("an expected token resolved to empty");
  if (result.bodyBg === "rgba(0, 0, 0, 0)") problems.push("body background is transparent");
  failures += problems.length;
  console.log(`${problems.length ? "✗" : "✓"} ${result.title}  theme=${result.theme} tone=${result.tone} accent=${result.accent}  bg=${result.bodyBg} fg=${result.bodyFg}`, problems.join("; "));
}
process.exit(failures ? 1 : 0);
