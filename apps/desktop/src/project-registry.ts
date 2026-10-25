import { createHash, randomUUID } from "node:crypto";
import { readFileSync, realpathSync, writeFileSync, mkdirSync, renameSync, unlinkSync } from "node:fs";
import { realpath, stat } from "node:fs/promises";
import { dirname, basename, isAbsolute } from "node:path";
import { z } from "zod";
export const projectId = (root: string) => createHash("sha256").update(root).digest("hex");
const entry = z.object({ id: z.string().regex(/^[a-f0-9]{64}$/), root: z.string().min(1).max(8192), name: z.string().max(8192), openedAt: z.number().finite().nonnegative() }).strict();
const schema = z.object({ version: z.literal(1), projects: z.array(entry).max(32) }).strict();
export type ProjectEntry = z.infer<typeof entry>;
/** Local recents only. Removing an entry never removes its repository. */
export class ProjectRegistry {
  private values: ProjectEntry[];
  constructor(private readonly file: string, legacyRoot?: string) {
    try {
      this.values = schema.parse(JSON.parse(readFileSync(file, "utf8"))).projects;
      if (new Set(this.values.map(p => p.id)).size !== this.values.length || this.values.some(p => !isAbsolute(p.root) || projectId(p.root) !== p.id)) throw new Error("Invalid project registry.");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
      if(legacyRoot){try{legacyRoot=realpathSync(legacyRoot);}catch{}}
      this.values = legacyRoot && isAbsolute(legacyRoot) ? [{ id: projectId(legacyRoot), root: legacyRoot, name: basename(legacyRoot), openedAt: 0 }] : [];
    }
  }
  list(): ProjectEntry[] { return this.values.map(p => ({ ...p })); }
  get(id: string): ProjectEntry { const p = this.values.find(p => p.id === id); if (!p) throw new Error("Project is no longer in Recents."); return { ...p }; }
  async add(path: string, current:()=>boolean = ()=>true): Promise<ProjectEntry> {
    const root = await realpath(path);
    if (!(await stat(root)).isDirectory()) throw new Error("Choose a project folder.");
    if(!current())throw new Error("Project opening was cancelled.");
    const p = { id: projectId(root), root, name: basename(root), openedAt: Date.now() };
    const next = [p, ...this.values.filter(v => v.id !== p.id)];
    if (next.length > 32) throw new Error("Recents is full. Remove an unused project before opening another.");
    this.save(next); return p;
  }
  remove(id: string): void { this.get(id); this.save(this.values.filter(p => p.id !== id)); }
  async status(): Promise<Array<ProjectEntry & { missing: boolean }>> {
    return Promise.all(this.list().map(async p => ({ ...p, missing: await stat(p.root).then(s => !s.isDirectory(), () => true) })));
  }
  private save(next: ProjectEntry[]): void {
    mkdirSync(dirname(this.file), { recursive: true, mode: 0o700 });
    const temp = `${this.file}.${randomUUID()}.tmp`;
    try { writeFileSync(temp, JSON.stringify({version: 1, projects: next}), { mode: 0o600, flag: "wx" }); renameSync(temp, this.file); }
    finally { try { unlinkSync(temp); } catch {} }
    this.values = next;
  }
}
