/** Browser-safe path authority shared by IPC and Git inspection. Reply paths are display data. */
export function isWorkspacePath(value: string, allowRoot = true): boolean {
  if (value.length > 4096 || (!allowRoot && !value) || value.startsWith("/")
    || value.includes("\\") || value.includes(":") || value.includes("\ufffd")
    || [...value].some(char => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127)) return false;
  return (!value || value.split("/").every(part => !!part && part !== "." && part !== ".."))
    && value.split("/").length <= 128;
}
