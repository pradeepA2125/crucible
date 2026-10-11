import { chromium } from "./playwright/node_modules/playwright-core/index.mjs";

const browser = await chromium.connectOverCDP("http://127.0.0.1:9335");
const page = browser.contexts()[0]?.pages()[0];
if (!page) throw new Error("No Extension Development Host page is available through CDP.");

const snapshot = async (stage) => {
  const pages = [];
  for (const [contextIndex, context] of browser.contexts().entries()) {
    for (const [pageIndex, candidate] of context.pages().entries()) {
      pages.push({
        contextIndex,
        pageIndex,
        title: await candidate.title(),
        url: candidate.url(),
        body: (await candidate.locator("body").innerText().catch(() => "")).slice(0, 6000),
      });
    }
  }
  console.log(JSON.stringify({ stage, pages }, null, 2));
};

await snapshot("before-command-palette");
await page.keyboard.press("F1");
await page.waitForTimeout(250);
await page.keyboard.type("Crucible: Run Setup");
await page.waitForTimeout(500);

const paletteText = await page.locator("body").innerText();
if (!paletteText.includes("Crucible: Run Setup")) {
  await snapshot("setup-command-not-found");
  throw new Error("Refusing to invoke setup: the exact 'Crucible: Run Setup' command is not visible in the command palette.");
}

await snapshot("exact-setup-command-visible");
await page.keyboard.press("Enter");
await page.waitForTimeout(1000);
await snapshot("after-invoking-setup");
await browser.close();
