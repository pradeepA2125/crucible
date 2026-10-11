import { chromium } from "./playwright/node_modules/playwright-core/index.mjs";

const browser = await chromium.connectOverCDP("http://127.0.0.1:9335");
const page = browser.contexts()[0]?.pages()[0];
if (!page) throw new Error("No Extension Development Host page is available through CDP.");

const session = await page.context().newCDPSession(page);
const { targetInfos } = await session.send("Target.getTargets");
const targets = targetInfos
  .filter((target) => /webview|crucible|setup/i.test(`${target.type} ${target.url} ${target.title}`))
  .map(({ targetId, type, title, url, attached, openerId, browserContextId }) =>
    ({ targetId, type, title, url, attached, openerId, browserContextId }));
console.log(JSON.stringify({ pageTitle: await page.title(), targets }, null, 2));
await session.detach();
await browser.close();
