import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { describe, expect, it } from "vitest";

// These portable coordination roots deliberately have no native runtime access.
// Runtime provisioning modules are Node host code and are outside this boundary.
function runtimeSpecifiers(tree: ts.SourceFile): string[] {
  const imports: string[] = [];
  function visit(node: ts.Node): void {
    if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) {
      const clause = node.importClause;
      const bindings = clause?.namedBindings;
      const typesOnly = clause?.isTypeOnly || (!clause?.name && bindings && ts.isNamedImports(bindings)
        && bindings.elements.every(element => element.isTypeOnly));
      if (!typesOnly) imports.push(node.moduleSpecifier.text);
    }
    if (ts.isExportDeclaration(node) && !node.isTypeOnly && node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) {
      const clause = node.exportClause;
      if (!clause || !ts.isNamedExports(clause) || clause.elements.some(element => !element.isTypeOnly)) imports.push(node.moduleSpecifier.text);
    }
    if (ts.isCallExpression(node) && (node.expression.kind === ts.SyntaxKind.ImportKeyword || node.expression.getText(tree) === "require")) {
      const specifier = node.arguments[0];
      if (specifier && ts.isStringLiteral(specifier)) imports.push(specifier.text);
    }
    ts.forEachChild(node, visit);
  }
  visit(tree);
  return imports;
}

describe("portable shared host dependency boundary", () => {
  it.each(["chat-message-router.ts", "chat-message-handlers.ts", "composer-model-host.ts", "provider-credentials.ts", "chat-view.ts"])("%s has no transitive platform dependency", file => {
    const visited = new Set<string>();
    const forbidden: string[] = [];
    function walk(url: URL): void {
      const path = fileURLToPath(url);
      if (visited.has(path)) return;
      visited.add(path);
      const tree = ts.createSourceFile(path, readFileSync(url, "utf8"), ts.ScriptTarget.Latest, true);
      for (const specifier of runtimeSpecifiers(tree)) {
        if (specifier === "vscode" || specifier === "electron" || specifier.startsWith("node:")) forbidden.push(`${path}: ${specifier}`);
        else if (specifier.startsWith(".")) walk(new URL(specifier.replace(/\.js$/, ".ts"), url));
      }
    }
    walk(new URL(`../src/${file}`, import.meta.url));
    expect(forbidden).toEqual([]);
  });
});


describe("trusted Node runtime dependency boundary", () => {
  it.each(["runtime/runtime-core.ts", "runtime/node-runtime-deps.ts"])("%s never imports an IDE host", file => {
    const visited = new Set<string>(); const forbidden: string[] = [];
    function walk(url: URL): void {
      const path = fileURLToPath(url); if (visited.has(path)) return; visited.add(path);
      const tree = ts.createSourceFile(path, readFileSync(url, "utf8"), ts.ScriptTarget.Latest, true);
      for (const specifier of runtimeSpecifiers(tree)) {
        if (specifier === "vscode" || specifier === "electron") forbidden.push(`${path}: ${specifier}`);
        else if (specifier.startsWith(".")) walk(new URL(specifier.replace(/\.js$/, ".ts"), url));
      }
    }
    walk(new URL(`../src/${file}`, import.meta.url)); expect(forbidden).toEqual([]);
  });
});
