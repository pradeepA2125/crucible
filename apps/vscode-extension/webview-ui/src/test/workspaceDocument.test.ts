import { expect, it } from "vitest";
import { documentPath } from "../desktop/WorkspaceDocument";
it("confines Markdown links and images while preserving ordinary relative and Unicode paths", () => {
  expect(documentPath("docs/readme.md", "../src/file.ts#L5")).toBe("src/file.ts");
  expect(documentPath("docs/readme.md", "images/%E4%BD%A0.png")).toBe("docs/images/你.png");
  for (const reference of ["../../outside", "/outside", "file:///outside", "https://example.com/a", "javascript:alert(1)", "../.git/config", "../.crucible/state/db", "%2foutside", "a%5Cb", "a%00b"]) expect(documentPath("docs/readme.md", reference)).toBeUndefined();
});
