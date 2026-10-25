import { mkdirSync, readFileSync, writeFileSync, renameSync, unlinkSync, readdirSync, lstatSync } from "node:fs";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { z } from "zod";
import type { Encryption } from "./store.js";
const identity = z.string().regex(/^[a-f0-9]{64}$/);
const envelope = z.object({ version:z.literal(1), projectId:identity, drafts:z.unknown() }).strict();
const ownFile = /^([a-f0-9]{64})\.draft(?:\.[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\.tmp)?$/;
const MAX_FILE = 90 * 1024 * 1024, MAX_TOTAL = 256 * 1024 * 1024;
/** Ciphertext only, no automatic restoration/send and no plaintext fallback. */
export class DraftVault {
  private timer: ReturnType<typeof setTimeout> | undefined;
  private pending: (() => unknown) | undefined;
  private failure: Error | undefined;
  private blocked = false;
  constructor(private readonly directory: string, private readonly id: string, private readonly encryption: Encryption,
    private readonly enabled: () => boolean, private readonly report: (error: string) => void = () => {}) { identity.parse(id); }
  static available(encryption: Encryption): boolean { return encryption.isEncryptionAvailable() && encryption.getSelectedStorageBackend?.() !== "basic_text"; }
  private file(): string { return join(this.directory, `${this.id}.draft`); }
  read(): unknown {
    if (!this.enabled()) return undefined;
    try {
      this.secure();
      let info;
      try { info = lstatSync(this.file()); } catch (e) { if ((e as NodeJS.ErrnoException).code === "ENOENT") return undefined; throw e; }
      if (!info.isFile() || info.isSymbolicLink() || info.size > MAX_FILE) throw new Error("Saved draft file is invalid or too large.");
      const value = envelope.parse(JSON.parse(this.encryption.decryptString(readFileSync(this.file()))));
      if (value.projectId !== this.id) throw new Error("Saved drafts belong to a different project.");
      return value.drafts;
    } catch (error) { this.blocked = true; throw error; }
  }
  invalidate(): void { this.blocked = true; }
  changed(read: () => unknown, urgent = false): void {
    if (!this.enabled()) return;
    this.pending = read;
    if (urgent) this.flush();
    else { clearTimeout(this.timer); this.timer = setTimeout(() => { try { this.flush(); } catch (e) { this.report((e as Error).message); } }, 250); }
  }
  flush(): void {
    clearTimeout(this.timer); this.timer = undefined;
    if (!this.enabled()) { this.pending = undefined; this.failure = undefined; return; }
    if (this.blocked) throw new Error("Saved drafts could not be restored. They were kept unchanged; turn encrypted drafts off to reset storage.");
    if (!this.pending) { if (this.failure) throw this.failure; return; }
    let temp: string | undefined;
    try {
      // Freeze exact bytes before any fallible keychain/disk operation. A later
      // teardown or caller mutation must not turn retry into an empty serializer.
      const captured = structuredClone(this.pending());
      this.pending = () => captured;
      this.secure();
      const raw = JSON.stringify({ version:1, projectId:this.id, drafts:captured });
      if (Buffer.byteLength(raw) > 64 * 1024 * 1024) throw new Error("Draft storage limit reached. Drafts remain in memory.");
      const bytes = this.encryption.encryptString(raw);
      if (bytes.length > MAX_FILE) throw new Error("Encrypted drafts exceed the file size limit.");
      mkdirSync(this.directory, {recursive:true,mode:0o700});
      let total = bytes.length;
      for (const name of readdirSync(this.directory)) {
        if (!ownFile.test(name) || name === `${this.id}.draft`) continue;
        const info = lstatSync(join(this.directory,name));
        if (!info.isFile() || info.isSymbolicLink()) throw new Error("Draft storage contains an invalid file.");
        total += info.size;
      }
      if (total > MAX_TOTAL) throw new Error("Encrypted draft storage is full. Existing saved drafts were kept.");
      temp = `${this.file()}.${randomUUID()}.tmp`;
      writeFileSync(temp, bytes, {flag:"wx",mode:0o600}); renameSync(temp,this.file());
      this.pending = undefined; this.failure = undefined;
    } catch (error) { this.failure = error instanceof Error ? error : new Error("Drafts were not saved."); throw this.failure; }
    finally { if (temp) { try { unlinkSync(temp); } catch {} } }
  }
  reset(): void { clearTimeout(this.timer); this.timer=undefined; this.pending=undefined; this.failure=undefined; this.blocked=false; }
  discardPending(): void { clearTimeout(this.timer);this.timer=undefined;this.pending=undefined;this.failure=undefined; }
  dispose(): void { this.flush(); this.reset(); }
  private secure(): void { if (!DraftVault.available(this.encryption)) throw new Error("Unlock the OS keychain to save encrypted drafts. No plaintext copy was written."); }
  static remove(directory: string, id?: string): void {
    if (id) identity.parse(id);
    let names: string[];
    try { names = readdirSync(directory); } catch (e) { if ((e as NodeJS.ErrnoException).code === "ENOENT") return; throw e; }
    for(const name of names){
      const match=ownFile.exec(name);if(!match || id && match[1]!==id)continue;
      const file=join(directory,name),info=lstatSync(file);
      if(!info.isFile() || info.isSymbolicLink())throw new Error("Draft storage contains an invalid file. No outside link was followed.");
      unlinkSync(file);
    }
  }
}
