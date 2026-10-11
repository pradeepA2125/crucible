const endpoint = "http://127.0.0.1:9335";
const version = await fetch(`${endpoint}/json/version`).then((response) => response.json());
const socket = new WebSocket(version.webSocketDebuggerUrl);

await new Promise((resolve, reject) => {
  socket.addEventListener("open", resolve, { once: true });
  socket.addEventListener("error", reject, { once: true });
});

let nextId = 1;
const pending = new Map();
socket.addEventListener("message", ({ data }) => {
  const message = JSON.parse(typeof data === "string" ? data : Buffer.from(data).toString());
  const resolve = pending.get(message.id);
  if (!resolve) return;
  pending.delete(message.id);
  resolve(message);
});

function send(method, params = {}, sessionId) {
  const id = nextId++;
  socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  return new Promise((resolve) => pending.set(id, resolve));
}

function resultOf(response) {
  if (response.error) throw new Error(`${response.error.message} (${response.error.code})`);
  if (response.result?.exceptionDetails) throw new Error(response.result.exceptionDetails.text);
  return response.result;
}

const { targetInfos } = resultOf(await send("Target.getTargets"));
const target = targetInfos.find((entry) =>
  entry.type === "iframe" && entry.url.includes("extensionId=crucible-editor.crucible-vscode-extension"),
);
if (!target) throw new Error("Crucible setup webview target is not available.");

const { sessionId } = resultOf(await send("Target.attachToTarget", { targetId: target.targetId, flatten: true }));
const evaluation = resultOf(await send("Runtime.evaluate", {
  expression: `JSON.stringify({
  readyState: document.readyState,
  title: document.title,
  body: document.body?.innerText ?? "",
  controls: [...document.querySelectorAll("button, input, select, textarea")].map((element) => ({
    tag: element.tagName,
    text: element.textContent?.trim() ?? "",
    ariaLabel: element.getAttribute("aria-label"),
    type: element.getAttribute("type"),
    disabled: "disabled" in element && Boolean(element.disabled),
  })),
})`,
  returnByValue: true,
}, sessionId));
const state = JSON.parse(evaluation.result.value);
console.log(JSON.stringify({ target: { targetId: target.targetId, url: target.url }, state }, null, 2));
await send("Target.detachFromTarget", { sessionId });
socket.close();