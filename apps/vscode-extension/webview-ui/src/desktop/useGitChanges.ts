import { useCallback, useEffect, useRef, useState } from "react";
import type { GitChange, GitDiff, GitScope } from "../../../../desktop/src/workspace-git";
import { ChangesInspectionError, type ChangesPort } from "./changesClient";
type Ready = Extract<GitScope, { state: "ready" }>;
export interface GitDetail { path: string; generation: string; loading: boolean; result?: GitDiff; error?: string; }
interface Known { generation: string; signature: string | undefined; additions?: number; deletions?: number; }
export interface GitReviewModel {
  details?: ReadonlyMap<string, GitDetail>; large?: boolean; load?(path: string): Promise<void>; highlight?(path: string): void;
  enabled: boolean; result: GitScope | undefined; loading: boolean; error: string; selected: string | undefined;
  detail: GitDetail | undefined; known: ReadonlyMap<string, Known>; capturedAt: number | undefined;
  refresh(): Promise<void>; select(path: string): void; isViewed(path: string): boolean; canView(path: string): boolean;
  needsRecheck(path: string): boolean; pendingViewed: number; setViewed(path: string, mark: boolean): void; openFile: ((path: string) => void) | undefined;
}
export function useGitChanges(port?: ChangesPort, openFile?: (path: string) => void): GitReviewModel {
  const [result, setResult] = useState<GitScope>(), [loading, setLoading] = useState(false), [error, setError] = useState("");
  const [selected, setSelected] = useState<string>(), [detail, setDetail] = useState<GitDetail>(), [capturedAt, setCapturedAt] = useState<number>();
  const [known, setKnown] = useState<ReadonlyMap<string, Known>>(new Map()), [marks, setMarks] = useState<ReadonlyMap<string, string>>(new Map());
  const [details, setDetails] = useState<ReadonlyMap<string, GitDetail>>(new Map()), [large, setLarge] = useState(false);
  const cache = useRef(new Map<string, GitDetail>()), oversized = useRef(false);
  const requests = useRef(new Map<string, symbol>());
  const store = useCallback((value: GitDetail) => {
    if (oversized.current) return;
    const next = new Map(cache.current); next.set(value.path, value);
    let bytes = 0, lines = 0;
    for (const item of next.values()) if (item.result?.kind === "text") { bytes += item.result.unified.length * 2; lines += item.result.unified.split("\n").length; }
    if (next.size > 50 || bytes > 8 * 1024 * 1024 || lines > 3000 || value.result?.kind === "text" && value.result.truncated) {
      oversized.current = true; cache.current = new Map(); setDetails(new Map()); setLarge(true);
    } else { cache.current = next; setDetails(next); }
  }, []);
  const context = useRef<{ epoch: number; read: number; project: string | null; scope: Ready | undefined; path: string | undefined; paths: Set<string> }>({
    epoch: 0, read: 0, project: port?.project() ?? null, scope: undefined, path: undefined, paths: new Set(),
  });
  useEffect(() => {
    const reset = (project: string | null) => {
      const current = context.current; current.epoch++; current.read++; current.project = project; current.scope = undefined; current.path = undefined; current.paths = new Set();
      requests.current.clear(); cache.current = new Map(); oversized.current = false; setDetails(new Map()); setLarge(false);
      setResult(undefined); setSelected(undefined); setDetail(undefined); setCapturedAt(undefined); setLoading(false); setError(""); setKnown(new Map()); setMarks(new Map());
    };
    reset(port?.project() ?? null);
    const stop = port?.onProjectChange(reset);
    return () => { context.current.epoch++; context.current.read++; stop?.(); };
  }, [port]);
  const read = useCallback(async (scope: Ready, path: string, epoch: number, foreground = true) => {
    if (!port || !scope.changes.some(change => change.path === path)) return;
    const current = context.current, project = current.project;
    const retained = cache.current.get(path);
    if (retained?.generation === scope.generation && (!retained.error || !foreground)) {
      if (foreground) { current.read++; current.path = path; setSelected(path); setDetail(retained); }
      return;
    }
    const request = Symbol(path); requests.current.set(path, request);
    const pending = { path, generation: scope.generation, loading: true };
    if (foreground) { current.path = path; setSelected(path); setDetail(pending); }
    store(pending);
    const fresh = () => current.epoch === epoch && requests.current.get(path) === request && current.project === project && current.scope?.generation === scope.generation;
    try {
      const value = await port.diff(scope.generation, path); if (!fresh()) return;
      const change: GitChange | undefined = scope.changes.find(entry => entry.path === path);
      const fingerprint = value.kind === "text" || value.kind === "binary" ? value.fingerprint : undefined;
      const signature = fingerprint ? JSON.stringify([scope.head, change?.status, change?.from, fingerprint]) : undefined;
      setMarks(previous => {
        if (!previous.has(path) || previous.get(path) === signature) return previous;
        const next = new Map(previous); next.delete(path); return next;
      });
      setKnown(previous => {
        const next = new Map(previous); next.delete(path);
        next.set(path, { generation: scope.generation, signature, ...(value.kind === "text" ? { additions: value.additions, deletions: value.deletions } : {}) });
        while (next.size > 1000) next.delete(next.keys().next().value!);
        return next;
      });
      const complete = { path, generation: scope.generation, loading: false, result: value }; store(complete);
      if (current.path === path) setDetail(complete);
    } catch (reason) {
      if (fresh() && reason instanceof ChangesInspectionError && ["superseded", "cancelled"].includes(reason.reason)) {
        const next = new Map(cache.current); next.delete(path); cache.current = next; setDetails(next);
        if (current.path === path) setDetail(undefined);
      } else if (fresh()) { const failed = { path, generation: scope.generation, loading: false, error: reason instanceof Error ? reason.message : "Diff unavailable. Refresh to retry." }; store(failed); if (current.path === path) setDetail(failed); }
    } finally { if (requests.current.get(path) === request) requests.current.delete(path); }
  }, [port, store]);
  const refresh = useCallback(async () => {
    if (!port) return;
    const current = context.current, epoch = ++current.epoch, project = current.project; current.read++; current.scope = undefined;
    requests.current.clear(); cache.current = new Map(); oversized.current = false; setDetails(new Map()); setLarge(false);
    setLoading(true); setError(""); setResult(undefined); setCapturedAt(undefined); setDetail(undefined); setKnown(new Map());
    try {
      const value = await port.scope(); if (current.epoch !== epoch || current.project !== project) return;
      setResult(value); setCapturedAt(Date.now()); setLoading(false);
      if (value.state === "ready") {
        oversized.current = value.partial || value.changes.length > 50; setLarge(oversized.current);
        current.scope = value; current.paths = new Set(value.changes.map(change => change.path));
        const path = value.changes.some(entry => entry.path === current.path) ? current.path : value.changes[0]?.path;
        if (path) await read(value, path, epoch);
        else { current.path = undefined; setSelected(undefined); }
      } else { current.path = undefined; setSelected(undefined); }
    } catch (reason) {
      if (current.epoch === epoch && current.project === project) {
        setLoading(false); setError(reason instanceof Error ? reason.message : "Comparison unavailable. Refresh to retry.");
      }
    }
  }, [port, read]);
  const signature = (path: string) => {
    const evidence = known.get(path);
    return evidence?.generation === context.current.scope?.generation ? evidence?.signature : undefined;
  };
  const needsRecheck = (path: string) => !!context.current.scope && context.current.paths.has(path) && marks.has(path) && known.get(path)?.generation !== context.current.scope.generation;
  return {
    highlight: path => {
      const current = context.current; if (!current.paths.has(path)) return;
      current.path = path; setSelected(path); setDetail(cache.current.get(path));
    },
    details, large, load: async path => { const current = context.current; if (!oversized.current && current.scope) await read(current.scope, path, current.epoch, false); },
    needsRecheck, pendingViewed: [...marks.keys()].filter(needsRecheck).length,
    enabled: !!port, result, loading, error, selected, detail, known, capturedAt, refresh, openFile,
    select: path => { const current = context.current; if (current.scope) void read(current.scope, path, current.epoch); },
    canView: path => !!signature(path), isViewed: path => !!signature(path) && marks.get(path) === signature(path),
    setViewed: (path, mark) => {
      const evidence = signature(path); if (!evidence) return;
      setMarks(previous => {
        const next = new Map(previous); next.delete(path); if (mark) next.set(path, evidence);
        while (next.size > 1000) next.delete(next.keys().next().value!); return next;
      });
    },
  };
}
