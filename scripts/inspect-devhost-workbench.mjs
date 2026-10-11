import { chromium } from "./playwright/node_modules/playwright-core/index.mjs";

const browser = await chromium.connectOverCDP("http://127.0.0.1:9335");
const page = browser.contexts()[0]?.pages()[0];
if (!page) throw new Error("No Extension Development Host page is available through CDP.");

const state = await page.evaluate(() => {
  const collect = (root, output = []) => {
    for (const element of root.querySelectorAll("button, a, input, [role='button'], [role='option'], [role='alert'], .notification-list-item, .monaco-list-row")) {
      const text = (element.innerText || element.getAttribute("aria-label") || element.getAttribute("title") || "").trim();
      if (/crucible|setup|run setup|chat/i.test(text)) {
        output.push({
          tag: element.tagName,
          role: element.getAttribute("role"),
          text,
          ariaLabel: element.getAttribute("aria-label"),
          title: element.getAttribute("title"),
          classes: element.className,
        });
      }
    }
    for (const element of root.querySelectorAll("*")) {
      if (element.shadowRoot) collect(element.shadowRoot, output);
    }
    return output;
  };
  return { title: document.title, matches: collect(document) };
});
console.log(JSON.stringify(state, null, 2));
await browser.close();
