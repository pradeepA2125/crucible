import { contextBridge, ipcRenderer } from "electron";
import { APP_ORIGIN, CHANNEL, contextSchema, parsePayload, type BridgeContext } from "./contract.js";

import { refusalMessages } from "./refusal.js";

let context: BridgeContext | null = null;
let sequence = 0;
let state: unknown;
const deliver = (payload: unknown) => window.postMessage(payload, APP_ORIGIN);
const deliverBaselineContext = () => { if (context) deliver({type:"desktop/baselineContext",viewId:context.viewId,projectId:context.projectId,threadId:context.threadId}); };
const ready = ipcRenderer.invoke(CHANNEL.bootstrap).then((value: unknown) => { context = contextSchema.parse(value); });
void ready.catch(() => {}); // Post attempts use the same rejection for visible recovery.
ipcRenderer.on(CHANNEL.event, (_event, envelope: { context: unknown; payload: unknown }) => {
  const next=contextSchema.parse(envelope.context);
  if(context && (context.clientId!==next.clientId || context.viewId!==next.viewId || context.projectId!==next.projectId))return;
  context = next;
  deliverBaselineContext();
  deliver(envelope.payload);
});
// Functions are proxied by contextBridge; no Electron event or IPC object escapes.
contextBridge.exposeInMainWorld("acquireVsCodeApi", () => Object.freeze({
  postMessage: (raw: unknown) => {
    let captured: BridgeContext | null = null;
    const refuse = (message: string) => {
      const source = captured ?? context;
      for (const reply of refusalMessages(raw, message, source?.projectId ?? null, source ? {viewId:source.viewId,threadId:source.threadId} : undefined)) deliver(reply);
    };
    void ready.then(async () => {
      if (!context) return;
      captured = {...context};
      deliverBaselineContext();
      const payload = parsePayload(raw);
      const result = await ipcRenderer.invoke(CHANNEL.command, { ...captured, requestId: `${captured.viewId}:${++sequence}`, sequence, payload });
      if (result?.error) refuse(String(result.error));
    }).catch(() => refuse("Desktop could not admit this action. Retry after the view reconnects."));
  },
  getState: () => state,
  setState: (next: unknown) => { state = structuredClone(next); return state; },
}));
window.addEventListener("dragover", event => { event.preventDefault(); event.stopImmediatePropagation(); }, true);
window.addEventListener("drop", event => { event.preventDefault(); event.stopImmediatePropagation(); }, true);
