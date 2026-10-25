import { editorEnvironment, NativeEditorError } from "./native-editor.js";
import { execFile, spawn } from "node:child_process";
import { isAbsolute } from "node:path";
export interface NativeFileActionsPorts {
  validate(path: string): Promise<string>;
  editor(): string | undefined;
  validateEditor(path: string): Promise<string>;
  chooseEditor(): Promise<string | undefined>;
  saveEditor(path: string): Promise<void>;
  launch(executable: string, args: string[]): Promise<void>;
  reveal(path: string): void;
  copy(text: string): void;
  platform: string;
}
export type NativeFileAction = "desktop/filesReveal" | "desktop/filesCopyPath" | "desktop/filesOpenEditor" | "desktop/filesChooseEditor";
export function createNativeFileActions(ports: NativeFileActionsPorts) {
  return async (type: NativeFileAction, path?: string): Promise<{ message: string }> => {
    if (type === "desktop/filesChooseEditor") {
      const editor = await ports.chooseEditor();
      if (!editor) return { message: "Editor selection cancelled." };
      if (!isAbsolute(editor)) throw new Error("Choose an editor application or executable with an absolute path.");
      await ports.saveEditor(await ports.validateEditor(editor)); return { message: "Editor selected. Open a file with Open in editor." };
    }
    const file = await ports.validate(path ?? "");
    if (type === "desktop/filesCopyPath") { ports.copy(file); return { message: "File path copied." }; }
    if (type === "desktop/filesReveal") { ports.reveal(file); return { message: "File revealed." }; }
    const selected = ports.editor();
    if (!selected || !isAbsolute(selected)) { ports.reveal(file); return { message: "No editor is selected. File revealed; use Choose editor to configure one." }; }
    const editor = await ports.validateEditor(selected);
    if (ports.platform === "darwin" && editor.endsWith(".app")) await ports.launch("/usr/bin/open", ["-a", editor, "--", file]);
    else await ports.launch(editor, [file]);
    return { message: "Opened in the selected editor." };
  };
}

/** External editor lifetime belongs to the user, independently of the Crucible host. */
export function launchEditor(executable: string, args: string[]): Promise<void> {
  const env = editorEnvironment(process.env);
  if (executable === "/usr/bin/open") return new Promise((resolve, reject) => {
    execFile(executable, args, { env, shell: false, timeout: 15000, maxBuffer: 16384 }, error => {
      if (error) reject(new NativeEditorError("The selected editor is unavailable. Choose an editor again or Reveal the file."));
      else resolve();
    });
  });
  return new Promise((resolve, reject) => {
    const child = spawn(executable, args, { detached: true, stdio: "ignore", shell: false, env });
    child.once("error", () => reject(new NativeEditorError("The selected editor could not start. Choose an editor again or Reveal the file.")));
    child.once("spawn", () => { child.unref(); resolve(); });
  });
}
