import * as vscode from "vscode";

import type { DiffEntry } from "@ai-editor/editor-client";
import type { ReviewPanelViewModel } from "./types.js";

export interface ReviewPanelHandlers {
  onOpenDiff: (relativePath: string) => void;
  onRefresh: () => void;
  onAccept: () => void;
  onReject: () => void;
  onProvidePlanFeedback: (feedback: string) => void;
  onStepDecision: (taskId: string, decision: "accept" | "discard") => void;
}

interface PanelMessage {
  type?: string;
  relativePath?: string;
  feedback?: string;
  taskId?: string;
  decision?: "accept" | "discard";
}

export class ReviewPanel {
  private panel: vscode.WebviewPanel | null = null;
  private lastModel: ReviewPanelViewModel = {
    session: null,
    task: null,
    result: null,
    reviewFiles: [],
    patchEvents: [],
  };

  constructor(private readonly handlers: ReviewPanelHandlers) {}

  show(): void {
    const panel = this.ensurePanel();
    panel.reveal(vscode.ViewColumn.One);
  }

  update(model: ReviewPanelViewModel): void {
    const panel = this.ensurePanel();

    // If only new patch events were appended (nothing else changed), skip the
    // full HTML replacement and push each new event via postMessage instead.
    // This lets the WebView append <li> rows in place without a page reload.
    if (this.isOnlyNewPatchEvents(model)) {
      const newEvents = model.patchEvents.slice(this.lastModel.patchEvents.length);
      for (const event of newEvents) {
        void panel.webview.postMessage({ type: "appendPatchEvent", event });
      }
      this.lastModel = model;
      return;
    }

    this.lastModel = model;
    panel.webview.html = renderPanelHtml(model);
  }

  private isOnlyNewPatchEvents(model: ReviewPanelViewModel): boolean {
    return (
      this.panel !== null &&
      this.lastModel.session === model.session &&
      this.lastModel.task === model.task &&
      this.lastModel.result === model.result &&
      this.lastModel.reviewFiles === model.reviewFiles &&
      model.patchEvents.length > this.lastModel.patchEvents.length
    );
  }

  showStepReview(taskId: string, stepId: string, stepTitle: string, diffEntries: DiffEntry[]): void {
    const panel = this.ensurePanel();
    panel.reveal(vscode.ViewColumn.One);
    void panel.webview.postMessage({ type: "showStepReview", taskId, stepId, stepTitle, diffEntries });
  }

  dispose(): void {
    this.panel?.dispose();
    this.panel = null;
  }

  private ensurePanel(): vscode.WebviewPanel {
    if (this.panel) {
      return this.panel;
    }

    const panel = vscode.window.createWebviewPanel(
      "aiEditorReview",
      "AI Editor Review",
      vscode.ViewColumn.One,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
      }
    );

    panel.onDidDispose(() => {
      this.panel = null;
    });

    panel.webview.onDidReceiveMessage((rawMessage: unknown) => {
      const message = rawMessage as PanelMessage;
      if (message.type === "openDiff" && message.relativePath) {
        this.handlers.onOpenDiff(message.relativePath);
        return;
      }
      if (message.type === "refresh") {
        this.handlers.onRefresh();
        return;
      }
      if (message.type === "accept") {
        this.handlers.onAccept();
        return;
      }
      if (message.type === "reject") {
        this.handlers.onReject();
        return;
      }
      if (message.type === "providePlanFeedback" && message.feedback !== undefined) {
        this.handlers.onProvidePlanFeedback(message.feedback);
      }
      if (message.type === "stepDecision" && message.taskId && message.decision) {
        this.handlers.onStepDecision(message.taskId, message.decision);
      }
    });

    this.panel = panel;
    panel.webview.html = renderPanelHtml(this.lastModel);
    return panel;
  }
}

export function renderPanelHtml(model: ReviewPanelViewModel): string {
  const status = model.task?.status ?? model.session?.status ?? "No active task";
  const taskId = model.session?.taskId ?? "None";
  const workspacePath = model.session?.workspacePath ?? "N/A";
  const canReview = model.task?.status === "READY_FOR_REVIEW";
  const isAwaitingPlan = model.task?.status === "AWAITING_PLAN_APPROVAL";
  const isExecuting = model.task?.status === "EXECUTING" || model.task?.status === "VALIDATING" || model.task?.status === "REPAIRING";
  const diagnostics = model.task?.diagnostics ?? [];
  const planMarkdown = model.task?.planMarkdown ?? "";
  const patchEvents = model.patchEvents ?? [];

  const opEvents = patchEvents.filter((e) => e.type !== "done");
  const activityRows = opEvents
    .map((ev) => {
      if (ev.type === "operation_success") {
        return `<li><span class="ev-ok">✓</span> ${escapeHtml(ev.payload.op_type)} — <code>${escapeHtml(ev.payload.path)}</code></li>`;
      }
      if (ev.type === "operation_error") {
        return `<li><span class="ev-err">✗</span> ${escapeHtml(ev.payload.op_type)} — <code>${escapeHtml(ev.payload.path)}</code>: ${escapeHtml(ev.payload.error)}</li>`;
      }
      return "";
    })
    .join("\n");

  const fileRows = model.reviewFiles
    .map((entry) => {
      const flags = [
        entry.existsReal ? "real" : "real missing",
        entry.existsShadow ? "shadow" : "shadow missing",
      ].join(" | ");

      return `<li><code>${escapeHtml(entry.relativePath)}</code> <button data-open-diff="${escapeHtml(
        entry.relativePath
      )}">Open Diff</button> <span>${escapeHtml(flags)}</span></li>`;
    })
    .join("\n");

  const diagnosticRows = diagnostics
    .map((item) => {
      const location = [item.file, item.line, item.column].filter(Boolean).join(":");
      const head = location ? `${location} - ` : "";
      return `<li><strong>${escapeHtml(item.level)}</strong>: ${escapeHtml(head + item.message)}</li>`;
    })
    .join("\n");

  const planJson = model.result?.plan ? JSON.stringify(model.result.plan, null, 2) : "null";
  const patchJson = model.result?.patch ? JSON.stringify(model.result.patch, null, 2) : "null";

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <style>
    body { font-family: sans-serif; padding: 16px; line-height: 1.5; }
    .toolbar { display: flex; gap: 8px; margin-bottom: 12px; }
    .meta { margin: 8px 0; }
    ul { padding-left: 20px; }
    code { background: #f4f4f4; padding: 1px 4px; border-radius: 4px; }
    pre { white-space: pre-wrap; word-break: break-word; background: #f8f8f8; padding: 8px; border-radius: 4px; border: 1px solid #ddd; }
    .plan-container { margin-top: 20px; padding: 15px; border: 1px solid #007acc; border-radius: 6px; background: #f0f7ff; }
    .feedback-area { width: 100%; min-height: 80px; margin-top: 10px; font-family: inherit; padding: 8px; box-sizing: border-box; }
    .activity-log { margin-top: 16px; padding: 12px; border: 1px solid #ccc; border-radius: 6px; background: #fafafa; }
    .activity-log h3 { margin: 0 0 8px 0; font-size: 0.95em; color: #555; }
    .activity-list { list-style: none; padding: 0; margin: 0; max-height: 240px; overflow-y: auto; font-family: monospace; font-size: 0.85em; }
    .activity-list li { padding: 2px 0; border-bottom: 1px solid #eee; }
    .activity-list li:last-child { border-bottom: none; }
    .ev-ok { color: #2a7d2a; }
    .ev-err { color: #c0392b; }
    .ev-wait { color: #888; font-style: italic; }
  </style>
</head>
<body>
  <h2>AI Editor Review</h2>
  <div class="toolbar">
    <button data-action="refresh">Refresh</button>
    <button data-action="accept" ${canReview ? "" : "disabled"}>Accept Patch</button>
    <button data-action="reject" ${canReview ? "" : "disabled"}>Reject Patch</button>
  </div>

  <div class="meta"><strong>Status:</strong> ${escapeHtml(status)}</div>
  <div class="meta"><strong>Task:</strong> ${escapeHtml(taskId)}</div>
  <div class="meta"><strong>Workspace:</strong> ${escapeHtml(workspacePath)}</div>

  ${isAwaitingPlan ? `
  <div class="plan-container">
    <h3>Engineering Plan (Proposed)</h3>
    <pre>${escapeHtml(planMarkdown || "No markdown plan available.")}</pre>
    
    <div class="feedback-section">
      <label for="feedback"><strong>Comments / Corrections:</strong></label>
      <textarea id="feedback" class="feedback-area" placeholder="Enter feedback to regenerate plan, or leave empty to approve..."></textarea>
      <div class="toolbar" style="margin-top: 10px;">
        <button data-action="submit-plan">Approve / Regenerate Plan</button>
      </div>
    </div>
  </div>
  ` : ""}

  ${isExecuting || opEvents.length > 0 ? `
  <div class="activity-log">
    <h3>Patch Operations ${isExecuting ? "⏳" : "✓"}</h3>
    <ul id="activity-list" class="activity-list">
      ${activityRows || '<li class="ev-wait">Waiting for patch operations…</li>'}
    </ul>
  </div>
  ` : ""}

  <h3>Modified Files</h3>
  <ul>${fileRows || "<li>No modified files yet.</li>"}</ul>

  <h3>Diagnostics</h3>
  <ul>${diagnosticRows || "<li>No diagnostics.</li>"}</ul>

  <details>
    <summary>Plan JSON (Execution blueprint)</summary>
    <pre>${escapeHtml(planJson)}</pre>
  </details>

  <details>
    <summary>Patch JSON</summary>
    <pre>${escapeHtml(patchJson)}</pre>
  </details>

  <script>
    const vscode = acquireVsCodeApi();
    document.querySelector('[data-action="refresh"]').addEventListener('click', () => {
      vscode.postMessage({ type: 'refresh' });
    });
    document.querySelector('[data-action="accept"]').addEventListener('click', () => {
      vscode.postMessage({ type: 'accept' });
    });
    document.querySelector('[data-action="reject"]').addEventListener('click', () => {
      vscode.postMessage({ type: 'reject' });
    });
    
    const submitBtn = document.querySelector('[data-action="submit-plan"]');
    if (submitBtn) {
      submitBtn.addEventListener('click', () => {
        const feedback = document.getElementById('feedback').value;
        vscode.postMessage({ type: 'providePlanFeedback', feedback });
      });
    }

    for (const button of document.querySelectorAll('[data-open-diff]')) {
      button.addEventListener('click', () => {
        vscode.postMessage({ type: 'openDiff', relativePath: button.dataset.openDiff });
      });
    }

    window.addEventListener('message', (e) => {
      const msg = e.data;
      if (msg.type !== 'appendPatchEvent') return;
      const ev = msg.event;
      if (ev.type === 'done') return;
      const list = document.getElementById('activity-list');
      if (!list) return;
      const waiting = list.querySelector('.ev-wait');
      if (waiting) waiting.remove();
      const li = document.createElement('li');
      if (ev.type === 'operation_success') {
        li.innerHTML = '<span class="ev-ok">&#x2713;</span> ' + ev.op_type + ' \u2014 <code>' + ev.path + '</code>';
      } else if (ev.type === 'operation_error') {
        li.innerHTML = '<span class="ev-err">&#x2717;</span> ' + ev.op_type + ' \u2014 <code>' + ev.path + '</code>: ' + ev.error;
      } else {
        return;
      }
      list.appendChild(li);
      list.scrollTop = list.scrollHeight;
    });
  </script>
</body>
</html>`;
}

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
