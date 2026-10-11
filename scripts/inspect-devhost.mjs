import { chromium } from "./playwright/node_modules/playwright-core/index.mjs";

const browser = await chromium.connectOverCDP("http://127.0.0.1:9335");
for (const [contextIndex, context] of browser.contexts().entries()) {
  for (const [pageIndex, page] of context.pages().entries()) {
    const title = await page.title();
    const url = page.url();
    const text = await page.locator("body").innerText().catch(() => "");
    console.log(JSON.stringify({ contextIndex, pageIndex, title, url, body: text.slice(0, 6000) }, null, 2));
  }
}
await browser.close();
