import { z } from "zod";
export const TERMINAL_CAP = 16;
export const TERMINAL_BYTES = 1024 * 1024;
export const TERMINAL_CHUNK = 32768;
export const terminalId = z.string().uuid();
export const terminalFields = {
  requestId: terminalId,
  sessionId: terminalId.optional(),
  operation: z.enum(["list", "start", "read", "input", "resize", "interrupt", "end", "forget", "agentList", "agentRead"]),
  offset: z.number().int().min(0).max(Number.MAX_SAFE_INTEGER).optional(),
  input: z.string().max(4096).optional(),
  cols: z.number().int().min(2).max(500).optional(),
  rows: z.number().int().min(2).max(300).optional(),
  threadId: z.string().min(1).max(200).optional(),
  agentSessionId: z.string().min(1).max(200).optional(),
};
export const terminalRequest = z.object(terminalFields).strict().superRefine((r, ctx) => {
  const missing = (key: keyof typeof terminalFields) => { if (r[key] === undefined) ctx.addIssue({ code: "custom", message: `Missing ${key}` }); };
  if (!["list", "start", "agentList", "agentRead"].includes(r.operation)) missing("sessionId");
  if (r.operation === "read") missing("offset");
  if (r.operation === "input") missing("input");
  if (r.operation === "resize") { missing("cols"); missing("rows"); }
  if (r.operation.startsWith("agent")) missing("threadId");
  if (r.operation === "agentRead") missing("agentSessionId");
});
export type TerminalRequest = z.infer<typeof terminalRequest>;
export const terminalInfo = z.object({ id: terminalId, projectId: z.string(), provenance: z.literal("user"), initialCwd: z.string(), shell: z.string(), status: z.enum(["starting", "running", "stopping", "exited", "error"]), pid: z.number().int().positive().optional(), exitCode: z.number().int().nullable(), hostAlive:z.boolean(), exitSignal:z.number().int().nonnegative().optional(), error: z.string().max(2048).optional(), start: z.number().int().min(0), end: z.number().int().min(0), cols: z.number().int(), rows: z.number().int() }).strict();
export type TerminalInfo = z.infer<typeof terminalInfo>;
export const agentSession = z.object({ id: z.string().max(200), command: z.string().max(65536), status: z.enum(["running", "exited"]), exit_code: z.number().nullable(), started_at: z.number() });
export const agentTranscript = z.object({ output_tail: z.string().max(65536), stdin_history: z.array(z.object({ts:z.number(),chars:z.string().max(4096)})).max(200), status:z.enum(["running","exited"]),exit_code:z.number().nullable() });
export const terminalResult = z.object({ sessions: z.array(terminalInfo).max(TERMINAL_CAP).optional(), session:terminalInfo.optional(), data:z.string().max(90000).optional(), next:z.number().int().min(0).optional(), reset:z.boolean().optional(), agents:z.array(agentSession).max(128).optional(), transcript:agentTranscript.nullable().optional() }).strict();
