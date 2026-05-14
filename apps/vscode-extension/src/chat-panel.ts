import * as vscode from "vscode";
import type { ChatMessage, ChatThreadSummary } from "@ai-editor/editor-client";

export type ChatMessageHandler = (message: string) => Promise<void>;
export type PlanCardActionHandler = (
  taskId: string,
  action: "implement" | "feedback",
  feedback?: string
) => Promise<void>;
export type NewChatHandler = () => Promise<void>;
export type SwitchThreadHandler = (threadId: string) => Promise<void>;

export class ChatPanel {
  private panel: vscode.WebviewPanel | null = null;

  constructor(
    private readonly onMessage: ChatMessageHandler,
    private readonly onPlanAction: PlanCardActionHandler,
    private readonly onNewChat: NewChatHandler,
    private readonly onSwitchThread: SwitchThreadHandler
  ) {}

  show(): void {
    if (this.panel) {
      this.panel.reveal();
      return;
    }
    this.panel = vscode.window.createWebviewPanel(
      "aiEditorChat",
      "AI Editor Chat",
      vscode.ViewColumn.Two,
      { enableScripts: true, retainContextWhenHidden: true }
    );
    this.panel.webview.html = this.buildHtml();
    this.panel.webview.onDidReceiveMessage(async (msg: unknown) => {
      const m = msg as Record<string, unknown>;
      if (m["type"] === "sendMessage") {
        await this.onMessage(m["text"] as string);
      } else if (m["type"] === "implementPlan") {
        await this.onPlanAction(m["taskId"] as string, "implement");
      } else if (m["type"] === "planFeedback") {
        await this.onPlanAction(m["taskId"] as string, "feedback", m["feedback"] as string);
      } else if (m["type"] === "newChat") {
        await this.onNewChat();
      } else if (m["type"] === "switchThread") {
        await this.onSwitchThread(m["threadId"] as string);
      }
    });
    this.panel.onDidDispose(() => {
      this.panel = null;
    });
  }

  appendMessage(message: ChatMessage): void {
    this.panel?.webview.postMessage({ type: "appendMessage", message });
  }

  appendChunk(chunk: string): void {
    this.panel?.webview.postMessage({ type: "appendChunk", chunk });
  }

  showThinking(message: string): void {
    this.panel?.webview.postMessage({ type: "showThinking", message });
  }

  updateThinking(message: string): void {
    this.panel?.webview.postMessage({ type: "updateThinking", message });
  }

  hideThinking(): void {
    this.panel?.webview.postMessage({ type: "hideThinking" });
  }

  setInputEnabled(enabled: boolean): void {
    this.panel?.webview.postMessage({ type: "setInputEnabled", enabled });
  }

  renderThreadList(
    threads: Array<Pick<ChatThreadSummary, "threadId" | "title">>,
    activeThreadId: string
  ): void {
    this.panel?.webview.postMessage({ type: "renderThreadList", threads, activeThreadId });
  }

  clearThread(): void {
    this.panel?.webview.postMessage({ type: "clearThread" });
  }

  private buildHtml(): string {
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
  body { font-family: var(--vscode-font-family); margin: 0; display: flex;
         flex-direction: column; height: 100vh; background: var(--vscode-editor-background); }
  #thread-list { border-bottom: 1px solid var(--vscode-panel-border); padding: 6px 10px;
                 display: flex; gap: 6px; align-items: center; overflow-x: auto; flex-shrink: 0; }
  .thread-tab { padding: 3px 10px; border-radius: 4px; cursor: pointer; white-space: nowrap;
                border: 1px solid transparent; font-size: 0.85em; background: none;
                color: var(--vscode-foreground); }
  .thread-tab.active { border-color: var(--vscode-focusBorder);
                       background: var(--vscode-editor-inactiveSelectionBackground); }
  #new-chat-btn { margin-left: auto; padding: 3px 10px; border: none; border-radius: 4px;
                  background: var(--vscode-button-secondaryBackground);
                  color: var(--vscode-button-secondaryForeground); cursor: pointer; font-size: 0.85em; }
  #thread { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 8px; }
  .msg { max-width: 85%; padding: 8px 12px; border-radius: 8px; white-space: pre-wrap; word-break: break-word; }
  .user { align-self: flex-end; background: var(--vscode-button-background);
          color: var(--vscode-button-foreground); }
  .agent { align-self: flex-start; background: var(--vscode-editor-inactiveSelectionBackground); }
  .thinking { align-self: flex-start; font-size: 0.8em; color: var(--vscode-descriptionForeground);
              font-style: italic; padding: 4px 8px; display: flex; align-items: center; gap: 6px; }
  .thinking-dot { width: 6px; height: 6px; border-radius: 50%;
                  background: var(--vscode-descriptionForeground);
                  animation: pulse 1.2s ease-in-out infinite; }
  @keyframes pulse { 0%,100% { opacity: 0.3; } 50% { opacity: 1; } }
  .plan-card { border: 1px solid var(--vscode-panel-border); border-radius: 6px; padding: 12px;
               align-self: flex-start; max-width: 85%; }
  .plan-card pre { white-space: pre-wrap; margin: 8px 0; font-size: 0.85em; }
  .plan-actions { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
  .plan-actions button { padding: 6px 14px; border: none; border-radius: 4px; cursor: pointer; }
  .btn-primary { background: var(--vscode-button-background); color: var(--vscode-button-foreground); }
  .btn-secondary { background: var(--vscode-button-secondaryBackground);
                   color: var(--vscode-button-secondaryForeground); }
  .plan-actions textarea { flex: 1; min-width: 140px; padding: 4px;
                           background: var(--vscode-input-background);
                           color: var(--vscode-input-foreground);
                           border: 1px solid var(--vscode-input-border); border-radius: 4px; }
  #input-row { display: flex; gap: 8px; padding: 10px;
               border-top: 1px solid var(--vscode-panel-border); }
  #input { flex: 1; padding: 8px; border: 1px solid var(--vscode-input-border);
           background: var(--vscode-input-background); color: var(--vscode-input-foreground);
           border-radius: 4px; resize: none; font-family: inherit; }
  #send { padding: 8px 16px; background: var(--vscode-button-background);
          color: var(--vscode-button-foreground); border: none; border-radius: 4px; cursor: pointer; }
</style>
</head>
<body>
<div id="thread-list"><button id="new-chat-btn">+ New Chat</button></div>
<div id="thread"></div>
<div id="input-row">
  <textarea id="input" rows="2" placeholder="Ask anything or describe a change…"></textarea>
  <button id="send">Send</button>
</div>
<script>
  const vscode = acquireVsCodeApi();
  const threadEl = document.getElementById('thread');
  const input = document.getElementById('input');
  const sendBtn = document.getElementById('send');
  let currentAgentBubble = null;
  let thinkingEl = null;

  document.getElementById('new-chat-btn').addEventListener('click', () => {
    vscode.postMessage({ type: 'newChat' });
  });

  function send() {
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    vscode.postMessage({ type: 'sendMessage', text });
  }
  sendBtn.addEventListener('click', send);
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  });

  function escHtml(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  function showThinking(message) {
    if (thinkingEl) { thinkingEl.querySelector('span').textContent = message; return; }
    thinkingEl = document.createElement('div');
    thinkingEl.className = 'thinking';
    thinkingEl.innerHTML = '<div class="thinking-dot"></div><span>' + escHtml(message) + '</span>';
    threadEl.appendChild(thinkingEl);
    threadEl.scrollTop = threadEl.scrollHeight;
  }
  function updateThinking(message) {
    if (thinkingEl) thinkingEl.querySelector('span').textContent = message;
  }
  function hideThinking() {
    if (thinkingEl) { thinkingEl.remove(); thinkingEl = null; }
  }

  function renderThreadList(threads, activeId) {
    const list = document.getElementById('thread-list');
    list.querySelectorAll('.thread-tab').forEach(el => el.remove());
    const btn = document.getElementById('new-chat-btn');
    threads.forEach(t => {
      const tab = document.createElement('button');
      tab.className = 'thread-tab' + (t.threadId === activeId ? ' active' : '');
      tab.textContent = t.title;
      tab.onclick = () => vscode.postMessage({ type: 'switchThread', threadId: t.threadId });
      list.insertBefore(tab, btn);
    });
  }

  function appendMessage(msg) {
    currentAgentBubble = null;
    if (msg.type === 'plan_card') {
      const taskId = escHtml(msg.metadata && msg.metadata.taskId ? msg.metadata.taskId : '');
      const div = document.createElement('div');
      div.className = 'plan-card';
      div.innerHTML =
        '<strong>Plan</strong><pre>' + escHtml(msg.content) + '</pre>' +
        '<div class="plan-actions">' +
        '<button class="btn-primary" onclick="implementPlan(\'' + taskId + '\')">Implement Plan</button>' +
        '<textarea id="fb-' + taskId + '" placeholder="Give feedback…" rows="2"></textarea>' +
        '<button class="btn-secondary" onclick="sendFeedback(\'' + taskId + '\')">Send Feedback</button>' +
        '</div>';
      threadEl.appendChild(div);
    } else {
      const div = document.createElement('div');
      div.className = 'msg ' + (msg.role === 'user' ? 'user' : 'agent');
      div.textContent = msg.content;
      threadEl.appendChild(div);
    }
    threadEl.scrollTop = threadEl.scrollHeight;
  }

  function appendChunk(chunk) {
    if (!currentAgentBubble) {
      currentAgentBubble = document.createElement('div');
      currentAgentBubble.className = 'msg agent';
      threadEl.appendChild(currentAgentBubble);
    }
    currentAgentBubble.textContent += chunk;
    threadEl.scrollTop = threadEl.scrollHeight;
  }

  function implementPlan(taskId) {
    vscode.postMessage({ type: 'implementPlan', taskId });
  }
  function sendFeedback(taskId) {
    const el = document.getElementById('fb-' + taskId);
    const fb = el ? el.value.trim() : '';
    if (!fb) return;
    vscode.postMessage({ type: 'planFeedback', taskId, feedback: fb });
  }

  window.addEventListener('message', e => {
    const msg = e.data;
    if (msg.type === 'appendMessage') { hideThinking(); appendMessage(msg.message); }
    else if (msg.type === 'appendChunk') { hideThinking(); appendChunk(msg.chunk); }
    else if (msg.type === 'showThinking') showThinking(msg.message);
    else if (msg.type === 'updateThinking') updateThinking(msg.message);
    else if (msg.type === 'hideThinking') hideThinking();
    else if (msg.type === 'setInputEnabled') {
      input.disabled = !msg.enabled;
      sendBtn.disabled = !msg.enabled;
    } else if (msg.type === 'renderThreadList') {
      renderThreadList(msg.threads, msg.activeThreadId);
    } else if (msg.type === 'clearThread') {
      hideThinking();
      threadEl.innerHTML = '';
      currentAgentBubble = null;
    }
  });
</script>
</body>
</html>`;
  }
}
