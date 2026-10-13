#!/usr/bin/env node
// Real-browser theme check (spec 2026-10-13 Testing → computed-colour checks). jsdom
// can't resolve CSS variables, so this reads computed styles from the live Crucible
// webviews over CDP.
//
// Usage: node scripts/dev/cdp-theme-check.mjs [--theme vscode|dark|light|hc]
//          [--tone dark|light] [--accent blue|violet|ember]
//          [--require "Crucible Chat,Crucible Settings"]
// Exits non-zero when a required panel is missing, a probe fails, or any check fails.
const args = Object.fromEntries(
  process.argv.slice(2).reduce((pairs, a, i, all) => (a.startsWith("--") ? [...pairs, [a.slice(2), all[i + 1]]] : pairs), []),
);
const PORT = Number(process.env.CDP_PORT ?? 9335);
const required = (args.require ?? "Crucible Chat").split(",").map((s) => s.trim()).filter(Boolean);
const withTimeout = (promise, ms, what) =>
  Promise.race([promise, new Promise((_, reject) => setTimeout(() => reject(new Error(`timeout: ${what}`)), ms))]);

const targets = await withTimeout((await fetch(`http://localhost:${PORT}/json/list`)).json(), 5000, "target list");
const webviews = targets.filter((t) => t.type === "iframe" && t.url.startsWith("vscode-webview://"));

// Evaluated inside each webview target. VS Code nests the page document in a child
// iframe; pick the document that has #root. Probe elements resolve tokens to
// computed colours so the checks compare real pixels, not variable text.
const PROBE = `(() => {
  const docs = [document, ...Array.from(document.querySelectorAll('iframe')).map((f) => { try { return f.contentDocument; } catch { return null; } })];
  const doc = docs.find((d) => d && d.getElementById('root'));
  if (!doc) return null;
  const root = doc.documentElement;
  const resolve = (prop, value) => { const el = doc.createElement('div'); el.style[prop] = value; doc.body.appendChild(el); const out = getComputedStyle(el)[prop]; el.remove(); return out; };
  const body = getComputedStyle(doc.body);
  return { title: doc.title, theme: root.dataset.theme, tone: root.dataset.tone, accent: root.dataset.accent,
    bodyBg: body.backgroundColor, bodyFg: body.color, colorScheme: getComputedStyle(root).colorScheme,
    surface0: resolve('backgroundColor', 'var(--surface-0)'), legacyPanel: resolve('backgroundColor', 'var(--color-panel)'),
    text1: resolve('color', 'var(--text-1)'), legacyText: resolve('color', 'var(--color-text)'),
    accentFill: resolve('backgroundColor', 'var(--accent-fill)'), onAccent: resolve('color', 'var(--on-accent)') };
})()`;

async function probe(target) {
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await withTimeout(new Promise((r, reject) => { ws.addEventListener("open", r, { once: true }); ws.addEventListener("error", reject, { once: true }); }), 5000, "connect");
  try {
    const reply = await withTimeout(new Promise((resolve) => {
      ws.addEventListener("message", (e) => { const m = JSON.parse(e.data); if (m.id === 1) resolve(m); });
      ws.send(JSON.stringify({ id: 1, method: "Runtime.evaluate", params: { expression: PROBE, returnByValue: true } }));
    }), 5000, "evaluate");
    if (reply.result?.exceptionDetails) throw new Error(`probe threw: ${reply.result.exceptionDetails.text}`);
    return reply.result?.result?.value ?? null;
  } finally {
    ws.close();
  }
}

const seen = new Set();
let failures = 0;
for (const target of webviews) {
  let r;
  try {
    r = await probe(target);
  } catch (err) {
    failures++;
    console.log(`✗ ${target.url.slice(0, 60)}… ${err.message}`);
    continue;
  }
  if (!r) continue; // a non-Crucible webview or a frame without #root
  seen.add(r.title);
  const problems = [];
  if (args.theme && r.theme !== args.theme) problems.push(`theme ${r.theme} ≠ ${args.theme}`);
  if (args.tone && r.tone !== args.tone) problems.push(`tone ${r.tone} ≠ ${args.tone}`);
  if (args.accent && r.theme !== "hc" && r.accent !== args.accent) problems.push(`accent ${r.accent} ≠ ${args.accent}`);
  if (r.bodyBg !== r.surface0) problems.push(`body bg ${r.bodyBg} ≠ --surface-0 ${r.surface0}`);
  if (r.bodyFg !== r.text1) problems.push(`body fg ${r.bodyFg} ≠ --text-1 ${r.text1}`);
  if (r.legacyPanel !== r.surface0) problems.push(`--color-panel ${r.legacyPanel} ≠ --surface-0`);
  if (r.legacyText !== r.text1) problems.push(`--color-text ${r.legacyText} ≠ --text-1`);
  if (r.bodyBg === "rgba(0, 0, 0, 0)") problems.push("body background is transparent");
  if (r.tone && r.colorScheme !== r.tone) problems.push(`color-scheme ${r.colorScheme} ≠ tone ${r.tone}`);
  if (r.accentFill === r.onAccent) problems.push("--on-accent equals --accent-fill");
  failures += problems.length;
  console.log(`${problems.length ? "✗" : "✓"} ${r.title}  theme=${r.theme} tone=${r.tone} accent=${r.accent}  bg=${r.bodyBg} fg=${r.bodyFg}`, problems.join("; "));
}
for (const title of required) {
  if (!seen.has(title)) { failures++; console.log(`✗ required panel not found: ${title}`); }
}
process.exit(failures ? 1 : 0);
