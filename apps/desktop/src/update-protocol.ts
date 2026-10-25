import { z } from "zod";
export const updateFields={operation:z.enum(["read","check","download","cancel","reveal","install","releases","backup"]),version:z.string().max(64).optional()};
export const updateRequest=z.object({type:z.literal("desktop/update"),...updateFields}).strict();
export type UpdateRequest=z.infer<typeof updateRequest>;
export type {DesktopUpdateState} from "../../vscode-extension/webview-ui/src/settings/update-types.js";
