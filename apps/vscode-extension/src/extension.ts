import { HttpBackendClient } from "@ai-editor/editor-client";
import * as vscode from "vscode";

import { ChatPanel } from "./chat-panel.js";
import { MemoryPanel } from "./memory-panel.js";
import {
  AiEditorController,
  type BackendClientFactory,
  type ControllerUI,
} from "./controller.js";
import { buildMcpEntry, type McpEntryInput } from "./mcp-quickpick.js";
import { buildModelOptions } from "./composer-models.js";
import { PROVIDERS } from "./setup-data.js";
import { openReviewDiff } from "./review-diff.js";
import { PROVIDER_KEY_ENV, RuntimeManager } from "./runtime/vscode-runtime.js";
import { SettingsPanel } from "./settings-panel.js";
import { createSettingsHandler } from "./settings-data.js";
import { buildSettingsDeps } from "./settings-deps.js";
import { asSettingsSectionId } from "./settings-sections.js";
import { SetupPanel } from "./setup-panel.js";
import { VscodeSessionStore } from "./vscode-session-store.js";
import {
  checkBackendHealth,
  isBackendBaseUrlUserSet,
  VscodeSettingsProvider,
} from "./settings.js";

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const settings = new VscodeSettingsProvider();
  const sessionStore = new VscodeSessionStore(context.workspaceState);

  const runtimeOutput = vscode.window.createOutputChannel("AI Editor Runtime");
  context.subscriptions.push(runtimeOutput);
  const runtimeManager = new RuntimeManager(context, runtimeOutput);
  context.subscriptions.push({
    dispose: () => {
      void runtimeManager.dispose();
    },
  });

  // Managed runtime: spawn/reuse a per-workspace backend unless the user pinned
  // an explicit backendBaseUrl (the dev flow) or turned the manager off.
  const workspaceFolder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? null;
  const managedRuntimeActive =
    vscode.workspace.getConfiguration("aiEditor").get<boolean>("managedRuntime.enabled", true) &&
    !isBackendBaseUrlUserSet() &&
    workspaceFolder !== null;
  let managedBackendStarted = false;
  if (managedRuntimeActive && runtimeManager.isInstalled() && workspaceFolder) {
    // Upgrade prompt: bundled manifest newer than the installed runtime.
    const installed = runtimeManager.installedRuntime();
    const bundled = runtimeManager.bundledManifest();
    if (
      installed && bundled &&
      bundled.releaseTag !== "dev-unpinned" &&
      bundled.releaseTag !== installed.releaseTag
    ) {
      void vscode.window
        .showInformationMessage(
          `AI Editor runtime ${bundled.releaseTag} is available (installed: ${installed.releaseTag}). Install now?`,
          "Install",
        )
        .then(async (choice: string | undefined) => {
          if (choice !== "Install") return;
          const result = await runtimeManager.install(() => {});
          if (result.ok && workspaceFolder) {
            await runtimeManager.restart(workspaceFolder);
          }
        });
    }
    try {
      await runtimeManager.startForWorkspace(workspaceFolder);
      const url = runtimeManager.backendUrl(workspaceFolder);
      if (url) {
        settings.setManagedBackendUrl(url);
        managedBackendStarted = true;
      }
    } catch (err) {
      runtimeOutput.appendLine(`[runtime] managed start failed: ${String(err)}`);
    }
  }

  let controller: AiEditorController;

  // Composer model quick-swap: the current provider + the set of providers with a
  // stored key (offered for hot-swap). Rebuilt after every swap.
  const composerModelState = async () => {
    const config = await controller.configClient().getConfig();
    const keyed: string[] = [];
    for (const p of PROVIDERS) {
      if (p.keyEnvVar && (await runtimeManager.getProviderKey(p.id)) !== undefined) keyed.push(p.id);
    }
    const current = config.provider ?? null;
    return { current, options: buildModelOptions(current, keyed, PROVIDERS) };
  };

  const chatPanel = new ChatPanel(
    context.extensionUri,
    (message, stepReview, forcedSkills) => controller.sendChatMessage(message, stepReview, forcedSkills),
    (taskId, action, feedback) => controller.handlePlanCardAction(taskId, action, feedback),
    () => controller.newChatThread(),
    (threadId) => controller.switchChatThread(threadId),
    (taskId) => controller.applyInlineChange(taskId),
    (taskId) => controller.discardInlineChange(taskId),
    (relativePath, shadowPath) => controller.openInlineDiff(relativePath, shadowPath),
    (taskId, files, decision, remember) => controller.handleScopeDecisionFromChat(taskId, files, decision, remember),
    (taskId, decision) => controller.handleValidationDecisionFromChat(taskId, decision),
    (taskId, decision) => controller.handleCommandDecisionFromChat(taskId, decision),
    (taskId, decision) =>
      decision === "accept" ? controller.acceptStep(taskId) : controller.discardStep(taskId),
    (threadId, mode) => controller.handleModeDecisionFromChat(threadId, mode),
    (threadId, answer) => controller.handleClarifyDecisionFromChat(threadId, answer),
    (threadId, decision, reason) => controller.handleEditDecisionFromChat(threadId, decision, reason),
    (taskId) => controller.acceptTaskPatch(taskId),
    (taskId, reason) => controller.rejectTaskPatch(taskId, reason),
    (taskId, stage) => controller.resumeTaskById(taskId, stage),
    () => controller.stopActiveTurn(),
    (revert) => controller.abortActiveTask(revert),
    (autoAccept) => controller.setReviewPref(autoAccept),
    () => controller.listPrompts(),
    (name: string, args: string) => controller.expandPrompt(name, args),
    () => controller.listSkills(),
    () => controller.openChat(),
    (threadId, decision) => controller.handleMcpDecisionFromChat(threadId, decision),
    (threadId, decision) => controller.handleDocDecisionFromChat(threadId, decision),
    () => composerModelState(),
    async (backend, model) => {
      // Pass the stored key as request credentials: the running backend's env may
      // predate this key (factory.py: request credentials override process env).
      const key = await runtimeManager.getProviderKey(backend);
      const envVar = PROVIDER_KEY_ENV[backend];
      const credentials = envVar && key ? { [envVar]: key } : undefined;
      await controller.configClient().setProvider({ backend, model, ...(credentials ? { credentials } : {}) });
      await runtimeManager.saveProvider(backend, model);
      return composerModelState();
    },
    (section?: string) => {
      void vscode.commands.executeCommand("aiEditor.openSettingsPanel", section);
    },
    () => {
      void vscode.commands.executeCommand("aiEditor.openMemoryPanel");
    }
  );

  const ui: ControllerUI = {
    getWorkspacePath: () => vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? null,
    promptForGoal: () =>
      vscode.window.showInputBox({
        prompt: "Describe what you want AI Editor to do",
        placeHolder: "Example: Refactor auth middleware to support refresh tokens",
        ignoreFocusOut: true,
      }),
    promptForRejectReason: () =>
      vscode.window.showInputBox({
        prompt: "Why are you rejecting this patch?",
        value: "Needs revision",
        ignoreFocusOut: true,
      }),
    showInfo: (message) => {
      void vscode.window.showInformationMessage(message);
    },
    showWarning: (message) => {
      void vscode.window.showWarningMessage(message);
    },
    showError: (message) => {
      void vscode.window.showErrorMessage(message);
    },
    promptSetup: (message) => {
      // Actionable first-run nudge: a Run Setup button that opens the wizard.
      void vscode.window
        .showInformationMessage(message, "Run Setup")
        .then((choice: string | undefined) => {
          if (choice === "Run Setup") {
            void vscode.commands.executeCommand("aiEditor.runSetup");
          }
        });
    },
    promptForResumeStage: () =>
      vscode.window.showQuickPick(
        ["plan", "feedback", "execute"] as const,
        { placeHolder: "Select stage to resume from" },
      ) as Promise<"plan" | "feedback" | "execute" | undefined>,
    promptForMaxIterationsOverride: async () => {
      const value = await vscode.window.showInputBox({
        prompt: "Override max iterations? (leave blank to keep current)",
        placeHolder: "e.g. 10",
        validateInput: (v) =>
          v === "" || /^\d+$/.test(v) ? null : "Enter a positive integer or leave blank",
      });
      return value === "" || value === undefined ? undefined : parseInt(value, 10);
    },
    promptForTaskId: () =>
      vscode.window.showInputBox({
        prompt: "Enter the task ID to attach to",
        placeHolder: "task-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
        ignoreFocusOut: true,
      }),
    promptForScopeDecision: async ({ files, reason, stepId }) => {
      const fileList = files.length === 1 ? files[0] : `${files.length} files (${files.join(", ")})`;
      const choice = await vscode.window.showInformationMessage(
        `[Step ${stepId}] Agent wants to also modify ${fileList}.\n\nReason: ${reason}`,
        { modal: true },
        "Approve",
        "Approve & Remember",
        "Reject"
      );
      if (!choice) return undefined;
      return {
        decision: choice.startsWith("Approve") ? "approve" : "reject",
        remember: choice === "Approve & Remember",
      };
    },
    openChatPanel: () => {
      chatPanel.show();
    },
    appendChatMessage: (message) => {
      chatPanel.appendMessage(message);
    },
    appendChatChunk: (chunk) => {
      chatPanel.appendChunk(chunk);
    },
    showChatThinking: (message) => {
      chatPanel.showThinking(message);
    },
    updateChatThinking: (message) => {
      chatPanel.updateThinking(message);
    },
    hideChatThinking: () => {
      chatPanel.hideThinking();
    },
    setChatInputEnabled: (enabled) => {
      chatPanel.setInputEnabled(enabled);
    },
    renderChatThreadList: (threads, activeThreadId) => {
      chatPanel.renderThreadList(threads, activeThreadId);
    },
    clearChatThread: () => {
      chatPanel.clearThread();
    },
    resolveInlineChangeCard: (taskId, resolution) => {
      chatPanel.resolveInlineChangeCard(taskId, resolution);
    },
    updateThreadTitle: (threadId, title) => {
      chatPanel.updateThreadTitle(threadId, title);
    },
    appendChatThinkingEntry: (text) => {
      chatPanel.appendThinkingEntry(text);
    },
    appendChatThinkingChunk: (chunk) => {
      chatPanel.appendThinkingChunk(chunk);
    },
    finalizeAgentMessage: () => {
      chatPanel.finalizeAgentMessage();
    },
    renderLiveGate: (gate) => {
      chatPanel.renderLiveGate(gate);
    },
    clearLiveGate: () => {
      chatPanel.clearLiveGate();
    },
    renderLivePlan: (plan) => {
      chatPanel.renderLivePlan(plan);
    },
    clearLivePlan: () => {
      chatPanel.clearLivePlan();
    },
    appendToolEvent: (event) => {
      chatPanel.appendToolEvent(event);
    },
    appendToolResult: (id, output, isError) => {
      chatPanel.appendToolResult(id, output, isError);
    },
    updateWorkbar: (info) => {
      chatPanel.updateWorkbar(info);
    },
    renderLiveReview: (review) => {
      chatPanel.renderLiveReview(review);
    },
    clearLiveReview: () => {
      chatPanel.clearLiveReview();
    },
    renderLiveError: (error) => {
      chatPanel.renderLiveError(error);
    },
    clearLiveError: () => {
      chatPanel.clearLiveError();
    },
    renderLiveTodos: (todos) => {
      chatPanel.renderLiveTodos(todos);
    },
    clearLiveTodos: () => {
      chatPanel.clearLiveTodos();
    },
    sendLiveStatus: (status, turnActive) => {
      chatPanel.sendLiveStatus(status, turnActive);
    },
  };

  const clientFactory: BackendClientFactory = (baseUrl) => new HttpBackendClient({ baseUrl });

  // The chat webview embeds the settings UI (floating overlay); give it a settings
  // handler that shares the exact host wiring the standalone SettingsPanel uses.
  // Bound per webview mount (registerHandlers) so workspace/backend are resolved live.
  chatPanel.setSettingsHandlerFactory((post) =>
    createSettingsHandler(
      buildSettingsDeps({
        runtimeManager,
        workspacePath: vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? "",
        clientFactory,
        resolveBackendUrl: () => settings.getBackendBaseUrl(),
      }),
      post,
    ),
  );

  controller = new AiEditorController(clientFactory, sessionStore, settings, ui, {
    openDiff: openReviewDiff,
  });

  // Task-subsystem flag (GET /v1/config): when off, the controller handles changes inline
  // and the task path (startTask + task_card) is hidden. Set a `when`-context key so the
  // command palette + webview can gate on it. Default hidden if the backend is unreachable.
  let taskSubsystemEnabled = false;
  let memoryEnabled = false;
  let skillsEnabled = false;
  try {
    const cfg = await clientFactory(settings.getBackendBaseUrl()).getConfig();
    taskSubsystemEnabled = cfg.taskSubsystemEnabled;
    memoryEnabled = cfg.memoryEnabled;
    skillsEnabled = cfg.skillsEnabled;
  } catch {
    // backend unreachable at activation — leave hidden.
  }
  await vscode.commands.executeCommand(
    "setContext", "aiEditor.taskSubsystemEnabled", taskSubsystemEnabled);
  await vscode.commands.executeCommand(
    "setContext", "aiEditor.memoryEnabled", memoryEnabled);
  await vscode.commands.executeCommand(
    "setContext", "aiEditor.skillsEnabled", skillsEnabled);

  context.subscriptions.push(
    vscode.commands.registerCommand("aiEditor.startTask", async () => {
      if (!taskSubsystemEnabled) {
        void vscode.window.showInformationMessage(
          "The task path is disabled (AI_EDITOR_TASK_SUBSYSTEM=0). Use the chat to make changes inline.");
        return;
      }
      await controller.startTask();
    })
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("aiEditor.openReviewPanel", () => {
      controller.openReviewPanel();
    })
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("aiEditor.acceptPatch", () => controller.acceptPatch())
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("aiEditor.rejectPatch", () => controller.rejectPatch())
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("aiEditor.refreshTask", () => controller.refreshTask())
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("aiEditor.attachToTask", async () => {
      await controller.attachToTask();
    })
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("aiEditor.openChat", () => {
      void controller.openChat();
    })
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("aiEditor.restartBackend", async () => {
      const folder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
      if (!folder) {
        void vscode.window.showWarningMessage("Open a folder to restart its backend.");
        return;
      }
      try {
        await runtimeManager.restart(folder);
        const url = runtimeManager.backendUrl(folder);
        if (url) settings.setManagedBackendUrl(url);
        void vscode.window.showInformationMessage(
          `AI Editor backend restarted (${url ?? "unknown"}).`);
      } catch (err) {
        void vscode.window.showErrorMessage(
          `Restart failed: ${err instanceof Error ? err.message : String(err)}`);
      }
    })
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("aiEditor.openMemoryPanel", () => {
      if (!memoryEnabled) {
        void vscode.window.showInformationMessage(
          "The memory inspector is disabled (AI_EDITOR_MEMORY_ENABLED=0).");
        return;
      }
      new MemoryPanel(
        context.extensionUri,
        controller.memoryDataSource(),
        controller.memoryThreadId(),
        controller.memoryWorkspacePath()
      ).open();
    })
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("aiEditor.runSetup", () => {
      const folder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
      if (!folder) {
        void vscode.window.showWarningMessage("Open a folder to run setup.");
        return;
      }
      new SetupPanel(context.extensionUri, runtimeManager, folder, clientFactory, () => {
        void controller.openChat();
      }, (url) => settings.setManagedBackendUrl(url)).open();
    })
  );
  // Retained singleton so a tree-row click (or repeat command) reveals + deep-links
  // the existing panel instead of spawning a duplicate webview tab.
  let settingsPanel: SettingsPanel | null = null;
  context.subscriptions.push(
    vscode.commands.registerCommand(
      "aiEditor.openSettingsPanel",
      (sectionArg?: unknown) => {
        const folder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
        if (!folder) {
          void vscode.window.showWarningMessage("Open a folder to view AI Editor settings.");
          return;
        }
        if (!settingsPanel) {
          settingsPanel = new SettingsPanel(
            context.extensionUri,
            runtimeManager,
            folder,
            clientFactory,
            () => settings.getBackendBaseUrl(),
          );
        }
        settingsPanel.open(asSettingsSectionId(sectionArg));
      },
    )
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("aiEditor.mcpAddServer", async () => {
      const folder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
      if (!folder) {
        void vscode.window.showWarningMessage("Open a folder to add an MCP server.");
        return;
      }
      const url = runtimeManager.backendUrl(folder);
      if (!url) {
        void vscode.window.showWarningMessage(
          "Backend not started yet — run \"AI Editor: Run Setup\" or restart the backend.");
        return;
      }
      const transportPick = await vscode.window.showQuickPick(
        [
          { label: "stdio", description: "spawn a local process (command + args)" },
          { label: "http", description: "streamable HTTP endpoint" },
          { label: "sse", description: "server-sent events endpoint" },
        ],
        { placeHolder: "Transport" },
      );
      if (!transportPick) return;
      const transport = transportPick.label as McpEntryInput["transport"];

      const commandOrUrl = await vscode.window.showInputBox({
        prompt: transport === "stdio" ? "Command line" : "Server URL",
        placeHolder: transport === "stdio" ? "uv run server.py" : "https://example.com/mcp",
        ignoreFocusOut: true,
      });
      if (!commandOrUrl) return;

      const name = await vscode.window.showInputBox({
        prompt: "Server name",
        placeHolder: "e.g. github",
        ignoreFocusOut: true,
      });
      if (!name) return;

      const envVarsRaw = (await vscode.window.showInputBox({
        prompt: "Env var names (comma-separated, optional)",
        placeHolder: "e.g. GITHUB_PAT",
        ignoreFocusOut: true,
      })) as string | undefined;
      const envVarNames = (envVarsRaw ?? "")
        .split(",")
        .map((v: string) => v.trim())
        .filter(Boolean);

      const entry = buildMcpEntry({
        transport,
        ...(transport === "stdio" ? { commandLine: commandOrUrl } : { url: commandOrUrl }),
        envVarNames,
      });

      try {
        const result = await clientFactory(url).upsertMcpServer(
          name, entry, runtimeManager.mcpDisabled());
        void vscode.window.showInformationMessage(
          `MCP server "${name}" added (${result.servers.length} configured).`);
      } catch (err) {
        void vscode.window.showErrorMessage(
          `Failed to add MCP server: ${err instanceof Error ? err.message : String(err)}`);
      }
    })
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("aiEditor.mcpListServers", async () => {
      const folder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
      if (!folder) {
        void vscode.window.showWarningMessage("Open a folder to list MCP servers.");
        return;
      }
      const url = runtimeManager.backendUrl(folder);
      if (!url) {
        void vscode.window.showWarningMessage(
          "Backend not started yet — run \"AI Editor: Run Setup\" or restart the backend.");
        return;
      }
      const client = clientFactory(url);
      let list;
      try {
        list = await client.listMcpServers();
      } catch (err) {
        void vscode.window.showErrorMessage(
          `Failed to list MCP servers: ${err instanceof Error ? err.message : String(err)}`);
        return;
      }
      if (list.servers.length === 0) {
        void vscode.window.showInformationMessage("No MCP servers configured.");
        return;
      }
      const serverPick = await vscode.window.showQuickPick(
        list.servers.map((s) => ({
          label: `${s.state === "connected" ? "$(check)" : "$(error)"} ${s.name}`,
          description: `${s.transport} · ${s.toolCount} tools · ${s.state}`,
          server: s,
        })),
        { placeHolder: "Select an MCP server" },
      );
      if (!serverPick) return;
      const { server } = serverPick;
      const disabledNow = runtimeManager.mcpDisabled();
      const isDisabled = disabledNow.includes(server.name);

      const actionPick = await vscode.window.showQuickPick(
        [
          { label: isDisabled ? "Enable" : "Disable" },
          { label: "Reconnect" },
          { label: "Remove" },
        ],
        { placeHolder: `Action for "${server.name}"` },
      );
      if (!actionPick) return;

      try {
        if (actionPick.label === "Enable" || actionPick.label === "Disable") {
          const next = actionPick.label === "Disable"
            ? Array.from(new Set([...disabledNow, server.name]))
            : disabledNow.filter((n) => n !== server.name);
          await runtimeManager.setMcpDisabled(next);
          const result = await client.reconnectMcpServer(server.name, next);
          void vscode.window.showInformationMessage(
            `"${server.name}" ${actionPick.label === "Disable" ? "disabled" : "enabled"} (${result.servers.length} configured).`);
        } else if (actionPick.label === "Reconnect") {
          const result = await client.reconnectMcpServer(server.name, disabledNow);
          void vscode.window.showInformationMessage(
            `"${server.name}" reconnect requested (${result.servers.length} configured).`);
        } else {
          const result = await client.deleteMcpServer(server.name, disabledNow);
          void vscode.window.showInformationMessage(
            `"${server.name}" removed (${result.servers.length} configured).`);
        }
      } catch (err) {
        void vscode.window.showErrorMessage(
          `MCP action failed: ${err instanceof Error ? err.message : String(err)}`);
      }
    })
  );
  // Re-attach message handler when VS Code restores the chat panel after a
  // host restart (without this, the panel is visible but Send does nothing).
  context.subscriptions.push(
    vscode.window.registerWebviewPanelSerializer("aiEditorChat", {
      deserializeWebviewPanel(restoredPanel: vscode.WebviewPanel) {
        chatPanel.reattach(restoredPanel);
        // Reload thread list + active thread messages after panel is restored.
        void controller.openChat();
        return Promise.resolve();
      },
    })
  );
  context.subscriptions.push({
    dispose: () => {
      controller.dispose();
    },
  });

  const backendBaseUrl = settings.getBackendBaseUrl();
  const healthy = managedBackendStarted || (await checkBackendHealth(backendBaseUrl));
  if (!healthy) {
    void vscode.window.showWarningMessage(
      managedRuntimeActive && !runtimeManager.isInstalled()
        ? "AI Editor runtime is not installed yet. Run \"AI Editor: Run Setup\" to install it."
        : `AI Editor backend is not reachable at ${backendBaseUrl}. Start agentd-py, then run \"AI Editor: Start Task\".`
    );
  }

  await controller.initialize();
}

export function deactivate(): void {
  // disposal is handled through extension subscriptions.
}
