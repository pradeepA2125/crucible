import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import type { RuntimeConfiguration, RuntimeSecrets, RuntimeState } from "../../vscode-extension/src/runtime/runtime-core.js";

// A single main-process writer; atomic private files. No plaintext secret fallback.
export class JsonState implements RuntimeState, RuntimeConfiguration {
  private values: Record<string, unknown>;
  constructor(private readonly file: string) {
    try { const value: unknown = JSON.parse(readFileSync(file, "utf8"));
      if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid desktop state file.");
      this.values = value as Record<string, unknown>; }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; this.values = {}; }
  }
  get<T>(key: string): T | undefined;
  get<T>(key: string, fallback: T): T;
  get<T>(key: string, fallback?: T): T | undefined { return Object.hasOwn(this.values, key) ? this.values[key] as T : fallback; }
  async update(key: string, value: unknown): Promise<void> {
    const next = { ...this.values };
    if (value === undefined) delete next[key]; else next[key] = value;
    mkdirSync(dirname(this.file), { recursive: true, mode: 0o700 });
    writeFileSync(`${this.file}.tmp`, JSON.stringify(next), { mode: 0o600 });
    renameSync(`${this.file}.tmp`, this.file);
    this.values = next;
  }
  inspect<T>(key: string): { globalValue: T } | undefined {
    return Object.hasOwn(this.values, key) ? { globalValue: this.values[key] as T } : undefined;
  }
}
export interface Encryption { isEncryptionAvailable(): boolean; getSelectedStorageBackend?(): string; encryptString(value: string): Buffer; decryptString(value: Buffer): string; }
export class EncryptedSecrets implements RuntimeSecrets {
  constructor(private readonly state: JsonState, private readonly encryption: Encryption) {}
  async get(key: string): Promise<string | undefined> {
    const stored = this.state.get<string>(key);
    if (stored === undefined) return undefined;
    if (!this.encryption.isEncryptionAvailable() || this.encryption.getSelectedStorageBackend?.() === "basic_text") throw new Error("Secure storage is unavailable. Unlock the OS keychain before continuing.");
    return this.encryption.decryptString(Buffer.from(stored, "base64"));
  }
  async store(key: string, value: string): Promise<void> {
    if (!this.encryption.isEncryptionAvailable() || this.encryption.getSelectedStorageBackend?.() === "basic_text") throw new Error("Secure storage is unavailable. Provider keys were not saved.");
    await this.state.update(key, this.encryption.encryptString(value).toString("base64"));
  }
  async delete(key: string): Promise<void> { await this.state.update(key, undefined); }
}
