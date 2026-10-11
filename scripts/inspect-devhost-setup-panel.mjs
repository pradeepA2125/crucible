import { chromium } from "./playwright/node_modules/playwright-core/index.mjs";

const browser = await chromium.connectOverCDP("http://127.0.0.1:9335");
const page = browser.contexts()[0]?.pages()[0];
if (!page) throw new Error("No Extension Development Host page is available through CDP.");

const iframes = await page.locator("iframe").evaluateAll((elements) =>
  elements.map((element) => ({
    src: element.getAttribute("src"),
    title: element.getAttribute("title"),
    name: element.getAttribute("name"),
    className: element.className,
  })),
);
const frames = [];
for (const [index, frame] of page.frames().entries()) {
  const body = await frame.locator("body").innerText().catch(() => "");
  frames.push({
    index,
    url: frame.url(),
    bodyPreview: body.slice(0, 1200),
    hasSetupText: /Crucible|Install runtime|provider|Save & Start|ChatGPT/i.test(body),
  });
}
console.log(JSON.stringify({ title: await page.title(), iframes, frames }, null, 2));
await browser.close();