import { z } from "zod";
interface MetadataState { get(key: string): unknown; update(key: string, value: unknown): Promise<void>; }
export const ThreadPreferenceSchema = z.object({ pinned: z.boolean().default(false), archived: z.boolean().default(false),
  name: z.string().trim().min(1).max(200).optional(), viewedAt: z.number().finite().nonnegative().optional(), unread: z.boolean().optional() }).strict();
export type ThreadPreference = z.infer<typeof ThreadPreferenceSchema>;
export const ThreadMetadataSchema = z.record(z.string().min(1).max(1024), ThreadPreferenceSchema).refine(value => Object.keys(value).length <= 1000);
export const threadMetadataFields = { threadId: z.string().min(1).max(1024), action: z.enum(["pin", "archive", "name", "viewed", "unread"]),
  value: z.union([z.boolean(), z.string().trim().min(1).max(200), z.null()]).optional() };
export const ThreadMetadataAction = z.object(threadMetadataFields).strict();
/** Canonical-project host state only; never mutates backend titles or chat history. */
export class ThreadMetadata {
  constructor(private readonly state: MetadataState) {}
  read(): Record<string, ThreadPreference> {
    const parsed = ThreadMetadataSchema.safeParse(this.state.get("desktop.threadMetadata"));
    if (parsed.success) return parsed.data;
    if (this.state.get("desktop.threadMetadata") === undefined) return {};
    throw new Error("Conversation preferences could not be read. Their saved data has been preserved.");
  }
  async change(raw: unknown): Promise<Record<string, ThreadPreference>> {
    const { threadId, action, value } = ThreadMetadataAction.parse(raw), all = this.read();
    if (!Object.prototype.hasOwnProperty.call(all, threadId) && Object.keys(all).length >= 1000) throw new Error("The conversation preference limit has been reached.");
    const entry = { ...(Object.prototype.hasOwnProperty.call(all, threadId) ? all[threadId] : { pinned: false, archived: false }) };
    if (action === "pin" || action === "archive") { if (typeof value !== "boolean") throw new Error("Expected a boolean."); entry[action === "pin" ? "pinned" : "archived"] = value; }
    if (action === "name") { if (value === null) delete entry.name; else if (typeof value === "string") entry.name = value; else throw new Error("Expected a name."); }
    if (action === "viewed") { entry.viewedAt = Date.now(); entry.unread = false; }
    if (action === "unread") { entry.unread = true; }
    const next = { ...all, [threadId]: ThreadPreferenceSchema.parse(entry) };
    await this.state.update("desktop.threadMetadata", next); return next;
  }
}
