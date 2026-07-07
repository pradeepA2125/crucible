import { computeLayout } from "./layout";
import type { SpaceModel } from "./types";

self.onmessage = (ev: MessageEvent<SpaceModel>) => {
  const result = computeLayout(ev.data);
  (self as unknown as Worker).postMessage(result, [result.positions.buffer]);
};
