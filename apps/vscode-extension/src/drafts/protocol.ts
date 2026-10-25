import { z } from "zod";
const text = z.string().max(2 * 1024 * 1024);
const id = z.string().min(1).max(1024);
const strings = z.array(id).max(10000);
const image = z.object({ name: id, mediaType: z.enum(["image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp", "image/tiff"]),
  dataUrl: z.string().max(12 * 1024 * 1024), bytes: z.number().int().min(1).max(8 * 1024 * 1024) }).strict()
  .refine(value => value.dataUrl.startsWith(`data:${value.mediaType};base64,`), "Invalid attachment data URL");
export const images = z.array(image).max(8).refine(values => values.reduce((sum, value) => sum + value.bytes, 0) <= 24 * 1024 * 1024, "Images exceed the total size limit");
export const draft = z.object({ text, imageAttachments: images, mentionedPaths: strings }).strict();
const draftUpdate = draft.extend({ imageAttachments: images.optional() });
const version = z.number().int().nonnegative();
const revision = z.number().int().positive().max(Number.MAX_SAFE_INTEGER);
export const draftShapes: Record<string, z.ZodRawShape> = {};
const add = (types: string[], fields: z.ZodRawShape) => { for (const type of types) draftShapes[type] = { type: z.literal(type), ...fields }; };
add(["draft/read"], { requestId: id, threadId: z.string().min(1).max(4096) });
add(["draft/save"], { requestId: id, threadId: z.string().min(1).max(4096), revision, version, draft: draftUpdate });
add(["draft/begin"], { requestId: id, threadId: z.string().min(1).max(4096), revision, draft, draftId: id });
add(["draft/choose"], { requestId: id, threadId: z.string().min(1).max(4096), revision, version, draft, choice: z.enum(["restore", "discard"]) });
add(["draft/reject"], { requestId: id, draftId: id });
add(["draft/flushAck"], { requestId: id, error: z.string().max(2048).optional() });
export const draftSchemas = new Map(Object.entries(draftShapes).map(([type, fields]) => [type, z.object(fields).strict()]));

export const savedDrafts = z.object({ version:z.literal(1), entries:z.array(z.object({
  threadId:z.string().min(1).max(4096), revision:version, version, draft,
  returned:z.object({id,draft}).strict().optional(), notice:z.string().max(2048).optional(),
}).strict()).max(32), pending:z.object({origin:z.string().min(1).max(4096),id,draft}).strict().optional(), tombstones:z.array(z.object({threadId:z.string().min(1).max(4096),revision:version,version,notice:z.string().max(2048).optional()}).strict()).max(32) }).strict();
