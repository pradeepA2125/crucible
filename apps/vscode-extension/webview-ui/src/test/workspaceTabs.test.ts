import { expect, it } from "vitest";
import { INITIAL_WORKSPACE_TABS as initial, workspaceTabs } from "../desktop/workspaceTabs";
it("turns launchers into singleton tools and creates a fresh launcher after the last close", () => {
  const files = workspaceTabs(initial, { type: "tool", kind: "files" }); expect(files.tabs).toEqual([{ id: "files", kind: "files" }]);
  const another = workspaceTabs(files, { type: "new" }); expect(another.tabs).toHaveLength(2);
  const same = workspaceTabs(another, { type: "tool", kind: "files" }); expect(same.tabs).toEqual(files.tabs); expect(same.active).toBe("files");
  const closed = workspaceTabs(same, { type: "close", id: "files" }); expect(closed.tabs[0]?.kind).toBe("new");
});
it("keeps project file tabs while replacing vanished thread inspection panels", () => {
  const file = workspaceTabs(initial, { type: "file", path: "src/a.ts" });
  const panel = workspaceTabs(file, { type: "panels", panels: [{ id: "agent-a", label: "Agent A" }] });
  expect(panel.active).toBe("inspection:agent-a");
  const switched = workspaceTabs(panel, { type: "panels", panels: [] }); expect(switched.tabs).toEqual(file.tabs); expect(switched.active).toBe(file.active);
  const closedFile = workspaceTabs(switched, { type: "syncFiles", paths: [] }); expect(closedFile.tabs[0]?.kind).toBe("new");
});
it("bounds repeated New tab requests and leaves existing tabs reachable", () => {
  let value = initial; for (let index = 0; index < 30; index++) value = workspaceTabs(value, { type: "new" });
  expect(value.tabs).toHaveLength(20); expect(value.notice).toContain("20 maximum");
  expect(workspaceTabs(value, { type: "select", id: "new:1" }).active).toBe("new:1");
});
