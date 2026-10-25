import { realpath, stat } from "node:fs/promises";
import { isAbsolute, relative, sep } from "node:path";
export class NativeEditorError extends Error {}
/** A repository cannot supply an executable for the editor action. */
export async function validateEditor(root: string, selected: string, platform: string): Promise<string> {
  if (!isAbsolute(selected)) throw new NativeEditorError("Choose an editor application with an absolute path.");
  const editor = await realpath(selected), relation = relative(root, editor);
  if (!relation || relation !== ".." && !relation.startsWith(`..${sep}`) && !isAbsolute(relation)) throw new NativeEditorError("Choose an installed editor outside this workspace.");
  const info = await stat(editor);
  if (!info.isFile() && !(platform === "darwin" && info.isDirectory() && editor.endsWith(".app"))) throw new NativeEditorError("Choose an editor application or executable.");
  return editor;
}
/** Keep ordinary OS integration while excluding host flags, tokens and code-injection variables. */
export function editorEnvironment(source: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const result: NodeJS.ProcessEnv = {};
  for (const key of ["PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TEMP", "TMP", "SystemRoot", "WINDIR", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "DBUS_SESSION_BUS_ADDRESS"])
    if (typeof source[key] === "string") result[key] = source[key];
  return result;
}
