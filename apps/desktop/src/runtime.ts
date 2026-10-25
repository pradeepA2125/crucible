import { projectSpawnEnv } from "./project-credentials.js";
import { PROVIDERS } from "../../vscode-extension/src/setup-data.js";
import { constants, openSync, closeSync, fstatSync, readSync } from "node:fs";
import { join } from "node:path";
import { BackendProcess, type ProcessDeps } from "../../vscode-extension/src/runtime/backend-process.js";
import { probeHealth } from "../../vscode-extension/src/runtime/probe-health.js";
import { createNodeProcessDeps } from "../../vscode-extension/src/runtime/node-runtime-deps.js";
import { RuntimeCore, type RuntimePorts } from "../../vscode-extension/src/runtime/runtime-core.js";

export const INSTALL_NOTICE = "This development build requires an installed Crucible runtime. Install or update it through VS Code, close the project there, then reopen it here.";
const IN_USE = "This project is open in another Crucible client, or its backend ownership cannot be verified. Close that client before opening it here.";
/** Read-only admission: bounded reads, no lock deletion and no process signals. */
export async function assertProjectAvailable(workspace: string, deps: Pick<ProcessDeps, "isPidAlive" | "fetchRaw" | "readToken" | "uid">, ownedPid?: number): Promise<void> {
  let fd: number;
  try { fd = openSync(join(workspace, ".crucible/state/agentd.lock"), constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0) | (constants.O_NONBLOCK ?? 0)); }
  catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") return; throw new Error(IN_USE); }
  let lock: { pid: number; port: number; started_at: number };
  try {
    const info = fstatSync(fd);
    if (!info.isFile() || info.nlink !== 1 || info.size > 4096 || (deps.uid !== undefined && info.uid !== deps.uid)) throw new Error(IN_USE);
    const bytes = Buffer.alloc(4097), count = readSync(fd, bytes, 0, bytes.length, null);
    if (count > 4096) throw new Error(IN_USE);
    lock = JSON.parse(bytes.subarray(0, count).toString("utf8")) as typeof lock;
    if (!Number.isSafeInteger(lock.pid) || lock.pid <= 0 || !Number.isSafeInteger(lock.port) || lock.port < 1 || lock.port > 65535 || !Number.isFinite(lock.started_at)) throw new Error(IN_USE);
  } catch { throw new Error(IN_USE); } finally { closeSync(fd); }
  if (lock.pid === ownedPid && deps.isPidAlive(lock.pid)) return;
  const health = await probeHealth(lock.port,deps,{pid:lock.pid,workspace});
  // A proven server may be inspected, but never borrowed for mutation. Owner
  // credential stays with its spawning host; both hosts enforce observer writes.
  if(health==="authed")return;
  if(deps.isPidAlive(lock.pid) || health!=="down")throw new Error(`${IN_USE} Project: ${workspace}; PID ${lock.pid}. No process was stopped.`);

}
export function assertInstalledLocalModels(runtime: Pick<RuntimeCore, "installedRuntime" | "localModels">, requested = runtime.localModels()): void {
  if ((runtime.installedRuntime()?.localModels ?? true) !== requested) throw new Error(INSTALL_NOTICE);
}
let starting = 0;
export const desktopRuntimeStarting = () => starting > 0;
export function createDesktopRuntime(ports: RuntimePorts, install?: (deps: import("../../vscode-extension/src/runtime/installer.js").InstallerDeps, progress: (p: import("../../vscode-extension/src/runtime/installer.js").ComponentProgress) => void) => Promise<import("../../vscode-extension/src/runtime/installer.js").InstallResult>, canStart:()=>void=()=>{}): RuntimeCore {
  let process: BackendProcess | undefined;
  const preflight = () => assertProjectAvailable(project, createNodeProcessDeps(ports.runtimeDir, ports.output), process?.backendHandle?.pid);
  let project = "";
  class DesktopRuntime extends RuntimeCore {
    override async getProviderSettings() {
      const settings = await super.getProviderSettings();
      // Pre-trust desktop projects must never inherit an old allow_all setting.
      if (settings?.extraEnv?.CRUCIBLE_SHELL_POLICY !== undefined) settings.extraEnv.CRUCIBLE_SHELL_POLICY = "ask";
      return settings;
    }
    override async startForWorkspace(workspace: string) {
      project = workspace;
      if (!this.isInstalled()) throw new Error(INSTALL_NOTICE);
      assertInstalledLocalModels(this);
      await preflight(); // before package reconciliation and process construction
      return super.startForWorkspace(workspace);
    }
    override async restart(workspace: string): Promise<void> {
      assertInstalledLocalModels(this);
      await super.restart(workspace);
    }
  }
  const extraFields = new Map(PROVIDERS.map(provider => [provider.id, new Set(provider.extraFields?.map(field => field.envVar) ?? [])]));
  return new DesktopRuntime({ ...ports,
    secrets: { ...ports.secrets,
      get: key => {
        const extra = /^crucible\.providerExtra\.([^.]+)\.(.+)$/.exec(key);
        if (extra && !extraFields.get(extra[1]!)?.has(extra[2]!)) return Promise.resolve(undefined);
        return ports.secrets.get(key);
      },
      store: (key, value) => ports.secrets.store(key, value), delete: key => ports.secrets.delete(key),
    },
    createInstaller: deps => ({ installAll: async progress => { if(!install)throw new Error(INSTALL_NOTICE); return install(deps,progress??(()=>{})); } }),
    createProcess: deps => {
      let captured: import("../../vscode-extension/src/runtime/backend-process.js").BackendSettings | undefined;
      const child = new BackendProcess({...deps,spawn:(command,args,options)=>deps.spawn(command,args,{...options,
        env:projectSpawnEnv(options.env,args.includes("agentd.serve")?captured:undefined)})}); process = child;
      return { get backendHandle() { return child.backendHandle; }, stop: () => child.stop(),
        start: async (workspace, settings) => {
          canStart();++starting;
          try {
          captured=settings;
          await assertProjectAvailable(workspace, deps, child.backendHandle?.pid);
          const result = await child.start(workspace, settings);
          // Reuse is read-only unless this instance already owns the child.
          return result;
          } finally { --starting; }
        } };
    },
  });
}
