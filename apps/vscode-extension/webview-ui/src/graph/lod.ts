import type { SpaceDiff, SpaceModel, StarRecord } from "./types";

/** Above this many files, L0 renders directory-level stars (spec: monster repos). */
export const LOD_STAR_THRESHOLD = 5000;

export function applyDiff(model: SpaceModel, diff: SpaceDiff): SpaceModel {
  const removed = new Set(diff.removed);
  const changedById = new Map(diff.changed.map((s) => [s.id, s]));
  const stars = model.stars
    .filter((s) => !removed.has(s.id))
    .map((s) => changedById.get(s.id) ?? s)
    .concat(diff.added);
  return {
    ...model,
    stars,
    packages: diff.packages,
    bundles: diff.bundles,
    intraBundles: diff.intraBundles,
    links: diff.links,
  };
}

/** Collapse each (pkg, dir) group into one aggregate star. Aggregate ids are
 * prefixed "dir:" so a pick handler can route them to package focus. */
export function aggregateToDirs(model: SpaceModel): SpaceModel {
  const groups = new Map<string, StarRecord>();
  for (const s of model.stars) {
    const key = `dir:${s.dir || s.pkg || "root"}`;
    let g = groups.get(key);
    if (!g) {
      g = {
        id: key,
        pkg: s.pkg,
        dir: s.dir,
        symbolCount: 0,
        inDeg: 0,
        outDeg: 0,
        kindMix: {},
        isEntry: false,
        isHub: false,
      };
      groups.set(key, g);
    }
    g.symbolCount += s.symbolCount;
    g.inDeg += s.inDeg;
    g.outDeg += s.outDeg;
    g.isEntry = g.isEntry || s.isEntry;
    g.isHub = g.isHub || s.isHub;
  }
  return { ...model, stars: [...groups.values()], links: [] };
}
