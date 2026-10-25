import { updateFields } from "./update-protocol.js";
import { restoreFields } from "./restore-protocol.js";
import { lifecycleCommands } from "./lifecycle-protocol.js";
import { terminalFields } from "./terminal-protocol.js";
import { inspectorPayload, inspectorTypes } from "./inspector-protocol.js";
import { threadMetadataFields } from "./thread-metadata.js";
import { gateActionFields } from "../../vscode-extension/src/gate-actions.js";
import { z } from "zod";
import { draftShapes, images } from "../../vscode-extension/src/drafts/protocol.js";
import { baselineRequest } from "./baseline-protocol.js";
import { gitGeneration } from "./git-protocol.js";
import { isWorkspacePath } from "./workspace-path.js";
import { LAYOUT_BOUNDS } from "./layout.js";
import { PROVIDERS } from "../../vscode-extension/src/setup-data.js";

export const APP_ORIGIN = "app://app";
export const CHANNEL = { bootstrap: "crucible:bootstrap", command: "crucible:command", event: "crucible:event" } as const;
// Existing composer permits eight images, 8 MiB each and 24 MiB total raw.
// Base64 + text fits in 48 MiB.
export const MAX_MESSAGE_BYTES = 48 * 1024 * 1024;
const text = z.string().max(2 * 1024 * 1024);
const id = z.string().min(1).max(1024);
const strings = z.array(id).max(10000);
const flag = z.boolean();
const optionalFlag = flag.optional();
const provider = z.enum(["anthropic", "gemini", "huggingface", "groq", "openrouter", "watsonx", "ollama", "turboquant", "openai", "openai_compatible", "chatgpt", "claude_subscription"]);
const credentials = z.record(z.string().regex(/^[A-Z][A-Z0-9_]{0,127}$/), z.string().max(65536)).optional();
const providerFields = { backend: provider, model: text, apiKey: z.string().max(65536).optional(), extraCredentials: credentials };
const agent = z.object({ description: text, persona: text, tools: strings.nullable(), disallowedTools: strings,
  permission: id, model: text, maxTurns: z.number().int().positive().nullable(), skills: strings, renameFrom: id.optional() }).strict();
const shapes: Record<string, z.ZodRawShape> = {};
const add = (types: string[], fields: z.ZodRawShape = {}) => { for (const type of types) shapes[type] = { type: z.literal(type), ...fields }; };
add(["desktop/update"],updateFields);
add(["desktop/restore"],restoreFields);
add(["desktop/findStop", "desktop/accessRead"]);
add(["desktop/trust"], {processId:id,trusted:flag});
add(["desktop/renameShared"], {threadId:id,title:z.string().min(1).max(200)});
add(["desktop/find"], {requestId:id,query:z.string().max(512),forward:flag,next:flag});
add(["desktop/threadsRead"], { requestId: id });
add(["desktop/threadsUpdate"], { requestId: id, ...threadMetadataFields });
add(["checkChatReceipt", "webviewReady", "themeReady", "newChat", "stopTurn", "stopAllAgents", "listPrompts", "listSkills", "listModels", "listWorkspaceFiles", "requestFileReferenceCatalog", "openMemoryPanel", "openGraphPanel"]);
add(["sendMessage"], { text, originalDraftText: text.optional(), draftId: id.optional(), stepReview: optionalFlag,
  forcedSkills: strings.optional(), mentionedPaths: strings.optional(), imageAttachments: images.optional(), planMode: optionalFlag });
add(["switchThread"], { threadId: id });
add(["setPlanMode"], { enabled: flag });
add(["setReviewPref"], { autoAccept: flag });
add(["setApprovalMode"], { mode: z.enum(["ask", "accept_edits", "full_access"]), confirmedFullAccess: optionalFlag, dontShowAgain: optionalFlag });
add(["fullAccessFirstSendChoice"], { requestId: id, choice: z.enum(["continue", "accept_edits", "cancel"]) });
add(["commandDecision"], { taskId: id, gateId: id, approve: flag, remember: optionalFlag, scope: z.enum(["exact", "prefix", "binary"]).optional(), ruleValue: text.optional() });
add(["mcpDecision"], { threadId: id, gateId: id, approve: flag, remember: flag });
add(["editDecision"], { threadId: id, gateId: id, decision: z.enum(["accept", "reject"]), reason: text });
add(["teamPlanDecision"], { threadId: id, gateId: id, decision: z.enum(["approve", "feedback", "reject"]), feedback: text.optional() });
add(["gateDecision"], gateActionFields);
add(["modeDecision"], { threadId: id, mode: z.literal("implement") });
add(["clarifyDecision"], { threadId: id, answer: text });
add(["rewindPreview", "rewindConfirm"], { messageId: id });
add(["viewDiffFile"], { path: id, shadowPath: text.optional() });
add(["openFile"], { path: id });
add(["openFileReference"], { reference: id });
add(["fetchSessionTranscript"], { sessionId: id });
add(["desktop/terminal"],terminalFields);
add(["setOpenAgents"], { agentIds: strings });
add(["setOpenTeams"], { teamIds: strings });
add(["stopAgent"], { agentId: id });
add(["disbandTeam"], { teamId: id });
add(["expandPrompt"], { requestId: id, name: id, args: text });
add(["setModel"], { backend: provider, model: id });
add(["setReasoningEffort"], { level: z.enum(["off", "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]).nullable() });
add(["openSettings"], { section: id.optional() });
add(["settings/load", "settings/loadInstructions", "settings/restartBackend", "settings/listAgents", "settings/listModels", "settings/chatgptLoad", "settings/chatgptCancel", "settings/claudeLoad", "settings/claudeSignIn", "settings/claudeCancel"]);
add(["settings/setAppearance"], { appearance: z.enum(["vscode", "dark", "light"]), accent: z.enum(["blue", "violet", "ember"]) });
add(["settings/setProvider"], { ...providerFields, contextWindow: z.number().int().positive().optional() });
add(["settings/testContextWindow"], { ...providerFields, contextWindow: z.number().int().positive() });
add(["settings/clearProviderKey"], { backend: provider });
add(["settings/mcpUpsert"], { name: id, entry: z.record(z.unknown()) });
add(["settings/mcpDelete", "settings/mcpReconnect", "settings/deleteAgent"], { name: id });
add(["settings/mcpToggle", "settings/skillToggle"], { name: id, enabled: flag });
add(["settings/mcpTools"], { name: id, enabledTools: strings.nullable(), disabledTools: strings });
add(["settings/setEnvFlag"], { key: z.enum(["crucible.policy.shell", "crucible.policy.scope", "crucible.webSearch.enabled", "crucible.webFetch.enabled", "crucible.memory.enabled", "crucible.localModels"]), value: id });
add(["settings/saveInstructions"], { content: text });
add(["settings/saveAgent"], { name: id, input: agent });
add(["settings/trustAgent"], { path: id, sha256: z.string().regex(/^[a-f0-9]{64}$/) });
add(["settings/openFile"], { path: id });
add(["settings/chatgptSignIn"], { registrationId: id.optional(), reconsent: optionalFlag });
add(["settings/chatgptModels", "settings/chatgptSignOut", "settings/chatgptWelcomed", "settings/claudeSignOut"], { registrationId: id });
add(["settings/claudeModels"], { registrationId: id, refresh: optionalFlag });
add(["settings/useChatGPT"], { registrationId: id, model: id, contextWindow: z.number().int().positive().optional() });
add(["settings/useClaude"], { registrationId: id, model: id });
add(["settings/openExternal"], { url: z.string().url().max(16384) });
add(["setup/preferences", "setup/openChat", "setup/startForChatGPT", "setup/startForClaude"]);
add(["setup/install"], { localModels: optionalFlag });
add(["setup/validate", "setup/save"], providerFields);
add(["desktop/openProject", "desktop/openSetup", "desktop/projectsRead", "desktop/persistenceRead"]);
add(["desktop/selectProject"],{projectId:z.string().regex(/^[a-f0-9]{64}$/),threadId:id.optional()});
add(["desktop/locateProject"],{projectId:z.string().regex(/^[a-f0-9]{64}$/)});
add(["desktop/recoverDraft", "desktop/importLegacyKeys"],{confirmed:z.literal(true)});
add(["desktop/removeProject"],{projectId:z.string().regex(/^[a-f0-9]{64}$/),confirmed:z.literal(true)});
add(["desktop/persistenceSet"],{enabled:flag});
add(["desktop/layoutRead"], { requestId: id });
add(["desktop/layoutSave"], { requestId: id, layout: z.object({
  sidebarWidth: z.number().int().min(LAYOUT_BOUNDS.sidebarMin).max(LAYOUT_BOUNDS.sidebarMax),
  workspaceWidth: z.number().int().min(LAYOUT_BOUNDS.workspaceMin).max(LAYOUT_BOUNDS.workspaceMax),
  sidebarVisible: flag, groupExpanded: flag,
}).strict() });
for (const [type, fields] of Object.entries(lifecycleCommands)) add([type], fields);
const workspacePath = z.string().max(4096).refine(value => isWorkspacePath(value), "Choose a relative workspace path.");
add(["desktop/filesList"], { requestId: id, path: workspacePath, cursor: z.string().max(160).regex(/^[A-Za-z0-9_-]+$/).optional() });
add(["desktop/filesPreview", "desktop/filesImage"], { requestId: id, path: workspacePath.refine(value => !!value) });
add(["desktop/filesReveal", "desktop/filesCopyPath", "desktop/filesOpenEditor"], { requestId: id, path: workspacePath.refine(value => !!value) });
add(["desktop/filesChooseEditor"], { requestId: id });
add(["desktop/filesMetadata"], { requestId: id, paths: z.array(workspacePath.refine(value => !!value)).min(1).max(12) });
add(["desktop/filesSearch"], { requestId: id, query: z.string().min(1).max(256).refine(value => !!value.trim() && !/[\x00-\x1f\x7f]/.test(value)), includeIgnored: flag });
add(["desktop/filesCancelSearch"], { requestId: id });
add(["desktop/gitScope"], { requestId: id });
add(["desktop/gitDiff"], { requestId: id, generation: gitGeneration, path: workspacePath.refine(value => !!value) });
Object.assign(shapes, draftShapes);
const schemas = new Map(Object.entries(shapes).map(([type, fields]) => [type, z.object(fields).strict()]));
const providerExtraFields = new Map(PROVIDERS.map(entry => [entry.id, new Set(entry.extraFields?.map(field => field.envVar) ?? [])]));
export type Payload = Record<string, unknown> & { type: string };
export function parsePayload(raw: unknown): Payload {
  const kind = z.object({ type: id }).parse(raw).type;
  if (inspectorTypes.has(kind as never)) return inspectorPayload.parse(raw);
  if (kind.startsWith("desktop/baseline")) return baselineRequest.parse(raw);
  const schema = schemas.get(kind);
  if (!schema) throw new Error("Unsupported desktop command.");
  const payload = schema.parse(raw) as Payload;
  // Extra credentials become backend environment variables: admit only the
  // selected provider's declared fields, never arbitrary runtime controls.
  if (payload.extraCredentials && Object.keys(payload.extraCredentials).some(key =>
    !providerExtraFields.get(payload.backend as string)?.has(key))) {
    throw new Error("Unsupported provider credential field.");
  }
  if (payload.type === "settings/setEnvFlag") {
    const values = payload.key === "crucible.policy.shell" ? ["ask"]
      : payload.key === "crucible.policy.scope" ? ["ask", "strict", "auto"] : ["true", "false"];
    if (!values.includes(payload.value as string)) throw new Error("This setting value is unavailable in an untrusted desktop project.");
  }
  return payload;
}
export const contextSchema = z.object({ v: z.literal(1), clientId: id, viewId: id, projectId: id.nullable(), threadId: id.nullable(), processId: id.nullable() }).strict();
export type BridgeContext = z.infer<typeof contextSchema>;
const envelopeSchema = contextSchema.extend({ requestId: id, sequence: z.number().int().positive(), payload: z.unknown() }).strict();
export function parseEnvelope(raw: unknown, current: BridgeContext, lastSequence: number) {
  const serialized = JSON.stringify(raw);
  if (!serialized || new TextEncoder().encode(serialized).length > MAX_MESSAGE_BYTES) throw new Error("Desktop message exceeds the size limit.");
  const envelope = envelopeSchema.parse(raw);
  for (const key of ["v", "clientId", "viewId", "projectId", "threadId", "processId"] as const) {
    if (envelope[key] !== current[key]) throw new Error("Stale desktop context. Reload this view before retrying.");
  }
  if (envelope.sequence <= lastSequence) throw new Error("Duplicate desktop command.");
  return { ...envelope, payload: parsePayload(envelope.payload) };
}
