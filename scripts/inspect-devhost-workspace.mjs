import { chromium } from "./playwright/node_modules/playwright-core/index.mjs";

const browser = await chromium.connectOverCDP("http://127.0.0.1:9335");
const page = browser.contexts()[0]?.pages()[0];
if (!page) throw new Error("No Extension Development Host page is available through CDP.");

const candidates = await page.getByText("AI editor", { exact: true }).evaluateAll((elements) =>
  elements.map((element) => ({
    tag: element.tagName,
    role: element.getAttribute("role"),
    ariaLabel: element.getAttribute("aria-label"),
    title: element.getAttribute("title"),
    className: element.className,
    text: element.textContent?.trim(),
    parent: element.parentElement?.outerHTML.slice(0, 1800),
  })),
);
const body = await page.locator("body").innerText();
console.log(JSON.stringify({
  title: await page.title(),
  url: page.url(),
  hasOpenFolderWarning: body.includes("Open a folder to run setup."),
  candidates,
}, null, 2));
await browser.close();
