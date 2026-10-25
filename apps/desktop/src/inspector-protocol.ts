import { z } from "zod";
const id = z.string().min(1).max(4096);
export const inspectorPayload = z.discriminatedUnion("type", [
  z.object({ type: z.literal("ready") }).strict(), z.object({ type: z.literal("refresh") }).strict(),
  z.object({ type: z.literal("browse"), filter: z.object({ scopeKind: z.enum(["workspace", "thread"]), scopeId: z.string().max(4096), kind: z.string().max(128).optional(), includeRetired: z.boolean() }).strict() }).strict(),
  z.object({ type: z.literal("loadChain"), memoryId: id }).strict(),
  z.object({ type: z.literal("fileDetail"), fileId: id }).strict(), z.object({ type: z.literal("symbolDetail"), symbolId: id }).strict(),
  z.object({ type: z.literal("searchSymbols"), query: z.string().max(256) }).strict(),
  z.object({ type: z.literal("openFile"), path: id, line: z.number().int().positive().max(10000000).optional() }).strict(),
  z.object({ type: z.literal("buildIndex") }).strict(),
]);
export const inspectorTypes = new Set(inspectorPayload.options.map(s => s.shape.type.value));
export function parseInspector(raw: unknown, kind: "memory" | "graph") {
  const payload = inspectorPayload.parse(raw);
  const allowed = kind === "memory" ? ["ready", "refresh", "browse", "loadChain"] : ["ready", "refresh", "fileDetail", "symbolDetail", "searchSymbols", "openFile", "buildIndex"];
  if (!allowed.includes(payload.type)) throw new Error("This action is unavailable in this inspector.");
  return payload;
}
