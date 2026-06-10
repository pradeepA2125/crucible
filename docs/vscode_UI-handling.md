# Executive Summary

AI-driven tools (like Claude Code) can interact with a VS Code extension’s chat UI (implemented as a Webview) via several interfaces. These include the built-in Extension API (commands and messaging), the VS Code *Webview Developer Tools*, Electron’s DevTools Protocol, and end-to-end frameworks like Playwright. Microsoft also provides the `@vscode/test-electron` library for integration testing inside the Extension Development Host. Each approach has trade‐offs in control, complexity, and reliability. For example, the Webview DevTools let you inspect and debug UI manually, whereas Playwright or Chrome DevTools Protocol (CDP) can automate and inspect UI programmatically. A specialized approach is to expose a custom RPC/MCP server inside the extension, letting Claude Code send chat commands (open chat, send message, read state) directly via HTTP or WebSocket. However, that requires writing extra server code.

This report surveys all these options in detail. We compare their capabilities and setup complexity (with a feature matrix). We give step-by-step instructions and code snippets for: launching a VS Code Extension Development Host, opening Webview DevTools, using Playwright/Electron for automation, writing integration tests with `@vscode/test-electron`, adding postMessage hooks to expose state, and building a minimal MCP/RPC server for chat operations. We discuss security implications (e.g. Content Security Policy and local-resource restrictions) and suggest safeguards. Finally, we outline a recommended AI-assisted debugging workflow (tools, commands, CI pipelines) and share tips for common pitfalls. 

# 1. Automation & Debugging Interfaces

**VS Code Extension API and Commands:**  The extension host (`vscode.ExtensionContext`) can programmatically open views and execute commands using the VS Code API. For example, one extension can expose a command like `"myExtension.openChat"`, and another extension (or any code) can trigger it via:

```ts
await vscode.commands.executeCommand('myExtension.openChat');
```

The `vscode.commands.executeCommand` API can run any built-in or extension command. Extensions also use `vscode.window` APIs to create webviews (`window.createWebviewPanel`) and listen for events.  Crucially, the extension host and its Webviews can communicate via the Webview **postMessage API**: the host calls `panel.webview.postMessage({ ... })`, and the Webview (in its HTML/JS) receives messages with `window.addEventListener('message', ...)`.  For example:

```ts
// In extension (TypeScript):
panel.webview.postMessage({ command: 'getState' });
```

```js
// In Webview (JavaScript, after acquireVsCodeApi()):
window.addEventListener('message', event => {
  if (event.data.command === 'getState') {
    const state = /* collect chat UI state */;
    vscode.postMessage({ type: 'state', payload: state });
  }
});
```

This built‐in messaging (and command API) is highly reliable for exchanging data between host and webview, but it requires writing code into the extension. An AI agent can edit these code files (via file I/O) but cannot *natively* inspect the running UI without additional hooks.

**Webview Developer Tools (in-app debugging):**  VS Code includes built-in DevTools to inspect webviews.  In modern VS Code, pressing `Ctrl+Shift+P` and running **“Developer: Toggle Developer Tools”** opens the usual Electron DevTools window (Chrome DevTools) that shows the current focus (normally the main VS Code UI). To specifically debug a webview, use **“Developer: Open Webview Developer Tools”**. This opens a *separate* DevTools window for that webview’s context. The DevTools let you inspect the webview’s DOM, console logs, network, etc., just like a normal webpage. This method is manual (you trigger the command via the Command Palette) and is intended for a developer stepping through code, not an automated agent. However, it fully shows the rendered chat UI (see example below) and lets you set breakpoints in the webview scripts.

 *Screenshot: VS Code Webview Developer Tools inspecting a chat panel (from the official docs). Use the “Open Webview Developer Tools” command to debug Webview HTML/JS.*

**Electron / Chrome DevTools Protocol (CDP):**  Since VS Code is an Electron app (with an embedded Chromium), you can leverage Chrome’s remote debugging protocol to inspect it externally. For instance, launching VS Code with:

```
code --remote-debugging-port=9222
```

opens a debugging port. An external script (or AI agent) can connect via the DevTools Protocol (for example using the `chrome-remote-interface` npm package or custom WebSocket) to list and control targets. This lets you find the webview’s page context and query its DOM or inject scripts. For example, one user built a remote dashboard that periodically captured the chat webview’s HTML over CDP. You can also simulate user input by locating an input element in the webview’s DOM and dispatching events. This approach is powerful (the agent sees exactly what’s on the screen) but requires handling CDP connections and target discovery. 

**Playwright (Electron automation):**  Playwright, a Node.js library for web automation, has experimental support for Electron apps. You can use it to launch VS Code as an Electron application and drive its UI. The typical pattern is:

```js
const { _electron: electron } = require('playwright');
const electronApp = await electron.launch({
  executablePath: '/path/to/VSCodeExecutable',
  args: ['--disable-extensions', '--extensionDevelopmentPath=./my-extension']
});
const [window] = await electronApp.windows();
await window.click('text=Open Chat');  // example: click a button labeled "Open Chat"
```

In this mode, Playwright treats VS Code’s main window and panels as browser windows, allowing selectors and actions (click, fill, evaluate). This can automate end-to-end UI flows (opening the chat, typing messages, clicking suggestions). It requires finding the right CSS or ARIA selectors for VS Code UI elements. The setup is non-trivial (finding the VS Code binary, waiting for windows), but it can exercise both the extension host and its Webview UI from outside.

**VS Code Test Framework (`@vscode/test-electron`):**  Microsoft provides the `@vscode/test-electron` (formerly `vscode-test`) library for integration testing extensions. This API automates launching a special VS Code instance (Extension Development Host) with your extension loaded, running tests written in Mocha (or another framework). It doesn’t drive the UI with clicks, but your tests have full access to the VS Code API – they can call commands, open views, and check extension state. Example usage (from VS Code docs):

```ts
import * as path from 'path';
import { runTests } from '@vscode/test-electron';

async function main() {
  const extensionDevPath = path.resolve(__dirname, '../../');
  const testRunnerPath = path.resolve(__dirname, './suite/index');
  await runTests({ extensionDevelopmentPath: extensionDevPath, extensionTestsPath: testRunnerPath });
}
main();
```

This installs VS Code, opens it with `--extensionDevelopmentPath=...`, then runs your Mocha tests inside that VS Code process. It’s reliable and integrates in CI, but it cannot directly click or read the Webview content unless your extension exposes an API for it. In a test, you might simulate a chat message by calling the extension’s internal functions.

**Custom RPC/MCP Server:**  A more manual but flexible method is to build a local HTTP or WebSocket server inside your extension that exposes “chat” operations. For example, the extension could launch an Express server on a free port and define endpoints like `/open`, `/sendMessage`, `/getMessages`. When hit, these endpoints use `vscode.commands.executeCommand`, `webview.postMessage`, or internal APIs to carry out the request. A Claude Code agent could then make HTTP requests to simulate user actions. This pattern resembles GitHub Copilot’s “MCP” approach. It requires additional code (setting up the server, defining RPC handlers) but gives the AI complete control over the chat context (the AI asks the extension to do things, rather than reading pixels).

# 2. Pros, Cons & Reliability

| **Interface/Method**                   | **Can Inspect UI?** | **Can Automate?**         | **Setup Complexity** | **Reliability**                   | **Pros**                           | **Cons**                            |
|----------------------------------------|---------------------|---------------------------|----------------------|-----------------------------------|------------------------------------|-------------------------------------|
| **VS Code Extension API / Commands**   | No (only code)      | Yes (via commands/data)   | Low (just code)      | Very reliable (official API)      | Stable; uses existing VS Code API. Ideal for logic/state tests. | Cannot see rendered chat; no DOM access. |
| **Webview Developer Tools**            | Yes (manual only)   | No (manual debugging)     | Very low (no code)   | High for manual use               | Full view of DOM, console. Easy breakpoints on webview JS. | Not automatable; must be triggered by dev. |
| **CDP (remote debug port)**            | Yes (programmatic)  | Yes (via protocol)        | Medium (open port)   | Medium (manual target management) | Can remotely snapshot/inspect UI. Works in any VS Code version. | Not officially documented; port must be open. |
| **Playwright/Electron**                | Yes (UI/DOM)        | Yes (UI interactions)     | High (setup + selectors) | Medium-to-high (newer API)        | Full end-to-end testing (UI and API) using browser-like controls. | Complex setup; depends on unofficial usage (targeting VS Code). |
| **@vscode/test-electron (Mocha)**     | Partial (calls API) | Limited (via API calls)   | Low (npm install)    | High (official tool)              | Integrates in CI; easy unit/integration tests with VS Code loaded. | No real DOM or UI clicking; tests run headlessly. |
| **Custom RPC/MCP Server**             | Yes (if implemented)| Yes (via HTTP calls)      | High (must build)    | Depends on implementation         | Directly exposes chat actions to agent (e.g. send/get). | Extra security risk; must handle auth/sync carefully. |

- *Control Granularity:*  Webview DevTools give pixel/DOM-level detail, whereas the Extension API and tests only know about your data structures. CDP/Electron/Playwright fall in between: they see the actual UI elements (Playwright with selectors, CDP by querying the HTML).
- *Setup Complexity:*  Extension API and tests are easiest (just code). Playwright or CDP require extra tooling and careful configuration. Custom RPC requires coding a server.
- *Reliability:*  Official APIs (commands, test-electron) are most stable. Workarounds (CDP, Playwright) can break with VS Code updates or require non-standard flags. For example, `--remote-debugging-port` may not work if VS Code changes its Electron version.

# 3. Step-by-Step Setup & Code Samples

## 3.1 Launching an Extension Development Host

To run your extension in a special host for debugging or testing, VS Code provides two main ways:

- **Debug Mode (F5):**  In VS Code, open your extension project and press F5 or select “Run Extension” debug config. This launches a new *Extension Development Host* window with your extension loaded. You can set breakpoints in your TypeScript/JavaScript code and debug as usual.

- **Command-Line (Extension Tests):**  Use the `@vscode/test-electron` library. First install:

  ```bash
  npm install --save-dev @vscode/test-cli @vscode/test-electron
  ```

  Add a test entry to `package.json`, e.g. `"test": "vscode-test"`. Create a config `.vscode-test.js` if needed. The simplest test script is:

  ```js
  // test/runTest.js
  const path = require('path');
  const { runTests } = require('@vscode/test-electron');

  async function go() {
    try {
      const extensionDevPath = path.resolve(__dirname, '../');  // your extension root
      const extensionTestsPath = path.resolve(__dirname, './suite/index'); // test runner
      await runTests({ extensionDevelopmentPath: extensionDevPath, extensionTestsPath });
    } catch (err) {
      console.error(err);
      process.exit(1);
    }
  }
  go();
  ```

  This script will download VS Code, unzip it, launch with `--extensionDevelopmentPath` and run your Mocha tests. Example usage in README or CI: `node test/runTest.js`.

## 3.2 Opening Webview Developer Tools

Once your extension’s Webview (chat UI) is open in the Development Host, you can debug it:

- In the VS Code window (Extension Host), press `Ctrl+Shift+P` (Windows/Linux) or `Cmd+Shift+P` (macOS) to open the Command Palette.
- Type **“Developer: Open Webview Developer Tools”** and run it. This opens a Chrome DevTools window specific to the active webview. (If the Webview has focus and use of find-widget, VS Code might advise using this dedicated command.)

Alternatively, use **“Developer: Toggle Developer Tools”** which opens the usual dev tools. In VS Code ≥1.56, the standard DevTools works for webviews too.

If you want to automate this from code (e.g., for an extension test), you can try executing the command programmatically:

```ts
await vscode.commands.executeCommand('workbench.action.webview.openDeveloperTools');
```

*(Note: the exact command ID for Webview DevTools may vary; it is not well-documented in the API.)*

Once open, use the **Elements** panel to inspect the DOM, the **Console** to view logs, and **Sources** to place breakpoints in your JS/TS (with source maps). This is invaluable for diagnosing Webview content/layout, but must be done manually by a developer.

## 3.3 Using Playwright to Automate VS Code/Electron

Playwright can treat VS Code as an Electron app. Here’s a sketch of the setup:

1. **Install Playwright:**  
   ```bash
   npm install -D playwright
   npx playwright install --with-deps
   ```
2. **Write a test script:** For example, `test/playwrightTest.js`:

   ```js
   const { _electron: electron } = require('playwright');
   (async () => {
     // Path to VS Code executable:
     const codePath = '/path/to/VisualStudioCode.exe'; // or code CLI via download

     const electronApp = await electron.launch({
       executablePath: codePath,
       args: [
         '--disable-extensions',
         '--disable-updates',
         '--user-data-dir=/tmp/vscode-user-data',
         '--extensionDevelopmentPath=' + __dirname + '/..'  // your extension
       ]
     });

     // Wait for first window (the main VS Code window).
     const window = await electronApp.firstWindow();
     // Optionally print title:
     console.log(await window.title());

     // Example: open a command palette via shortcut (Ctrl+Shift+P)
     await window.keyboard.press('Control+Shift+P');
     // Type some text (depends on locale)
     await window.keyboard.type('My Extension: Open Chat');
     await window.keyboard.press('Enter');

     // Wait for the chat Webview to appear (selector depends on your extension)
     await window.waitForSelector('.webview-ready-indicator');

     // Interact with the Webview's content.
     // Playwright may need to switch to an <iframe> context if the webview is in one:
     const webviewFrame = await window.frame({ url: /webview/ });
     await webviewFrame.fill('textarea', 'Hello from Playwright');
     await webviewFrame.click('text=Send');

     // Capture screenshot or assert something
     await window.screenshot({ path: 'chat.png' });

     await electronApp.close();
   })();
   ```
   
   This example (inspired by the [Playwright Electron docs][36]) shows how to launch VS Code with specific args, open the command palette, run your extension command, and then find the webview frame to interact with it. (Note: in practice, VS Code’s window structure is complex. You may need to find a nested `<iframe>` corresponding to the webview content.)

3. **Run the script:** 
   ```bash
   node test/playwrightTest.js
   ```
   or integrate into your test suite.

Using Playwright offers full E2E testing (both VS Code UI and Webview DOM), but can be brittle if VS Code or Playwright change.

## 3.4 Writing Extension Tests (`@vscode/test-electron`)

Using `@vscode/test-electron`, write Mocha tests that run inside the Extension Host. A typical test suite file (`test/suite/extension.test.ts`) might look like:

```ts
import * as assert from 'assert';
import * as vscode from 'vscode';

suite('Chat Extension Test Suite', () => {
  test('Open Chat Webview', async () => {
    // Invoke our extension command
    await vscode.commands.executeCommand('myExtension.openChat');
    // The chat panel should now be visible
    const panel = vscode.window.activeTextEditor; // or track via onDidChangeActiveTextEditor
    assert.ok(panel, 'Chat webview did not open');
    // Optionally, send a message via message API
    // e.g. simulate a button click or verify a state variable.
  });

  test('Send and Receive Message', async () => {
    // Here you might simulate sending a message by calling the extension API,
    // or by posting a message to the webview.
    // For example, call a function in your extension to handle an incoming message:
    const myExt = vscode.extensions.getExtension('publisher.myExtension')!;
    await myExt.activate();
    (myExt.exports as any).handleIncomingChatMessage('Test');
    // Then verify some state or output (maybe your extension logs or context).
    assert.strictEqual((myExt.exports as any).getLastMessage(), 'Test');
  });
});
```

Run the tests via Mocha (configured in `test/suite/index.ts`). You can launch and debug these tests with a `launch.json` config using type `"extensionHost"`.

## 3.5 Exposing Webview State (postMessage & Global Hooks)

To let an AI agent *view* the chat state, you need to surface it. Options include:

- **postMessage Protocol:**  Enhance your webview code to listen for special commands and reply. For example, in the extension:

  ```ts
  // Periodically or on demand, send a message to the webview to dump its state.
  panel.webview.postMessage({ type: 'getState' });
  // Listen for response
  const disposable = panel.webview.onDidReceiveMessage(msg => {
    if (msg.type === 'state') {
      console.log('Webview state:', msg.data);
    }
  });
  ```

  In the Webview HTML/JS:
  ```js
  const vscodeApi = acquireVsCodeApi();
  window.addEventListener('message', event => {
    const msg = event.data;
    if (msg.type === 'getState') {
      const state = /* gather messages, UI selections, etc */;
      vscodeApi.postMessage({ type: 'state', data: state });
    }
  });
  ```
  Then the extension can log or expose that state (e.g. write to a file, or serve via an RPC).

- **Global Variables:**  For quick debugging, you might attach the chat state to the global `window` of the webview (e.g. `window.__chatState = ...`). Then in DevTools you can inspect it. However, for automated access, global vars alone aren’t enough unless combined with CDP or embedding that webview in a controlled browser.

- **MCP/RPC Server:**  A custom server can directly report state. For example, on `/getState`, your extension could return the current conversation messages. (This essentially extends the above postMessage mechanism with an HTTP interface.)

These techniques require modifying your extension/webview code. The VS Code docs on Webview Messaging outline the use of `postMessage` and `window.addEventListener('message')` for JSON messages between host and webview. Use `acquireVsCodeApi()` inside the webview to get the `postMessage` function.

## 3.6 Implementing an MCP/RPC Server

An “MCP” (multi-client protocol) or RPC server can let an external agent call functions in your extension. For example, use Node’s `http` or `express` inside the extension:

```ts
// In your extension activate():
import * as http from 'http';

const server = http.createServer(async (req, res) => {
  if (req.url === '/openChat' && req.method === 'POST') {
    await vscode.commands.executeCommand('myExtension.openChat');
    res.end(JSON.stringify({ ok: true }));
  } else if (req.url === '/sendMessage' && req.method === 'POST') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      const { text } = JSON.parse(body);
      // Send the text to the webview (e.g. via postMessage)
      panel.webview.postMessage({ type: 'userMessage', text });
      res.end(JSON.stringify({ ok: true }));
    });
  } else if (req.url === '/getMessages') {
    // Return current messages (you’d maintain this state in the extension)
    res.end(JSON.stringify({ messages: chatState.getMessages() }));
  } else {
    res.statusCode = 404;
    res.end();
  }
});
server.listen(3000);
```

Then an AI agent can do `curl http://localhost:3000/sendMessage -d '{"text":"Hi"}'` to programmatically chat. Michael Ruminer’s blog shows a Python example of an MCP server using FastMCP with VS Code. This pattern provides very fine-grained control, but be careful: opening a server port can introduce security risks and sync issues (make sure to validate inputs and avoid race conditions).

# 4. Security & Privacy Implications

Exposing UI state or opening network interfaces in an extension can pose risks. Key safeguards:

- **Content Security Policy (CSP):**  All Webviews should set a strict CSP to prevent injection of untrusted scripts or resources. VS Code itself warns that “all webviews […] should set a content security policy” for defense-in-depth. Include a `<meta http-equiv="Content-Security-Policy" ...>` in your HTML, whitelisting only needed sources (e.g. self).

- **Local Resources:**  By default, Webviews can only load URIs under the extension or workspace via `webview.asWebviewUri`. Make sure to use `asWebviewUri` for local images/CSS, and restrict `localResourceRoots` if possible. This avoids exposing arbitrary file system paths.

- **Command Validation:**  If using custom commands or RPC, ensure inputs are sanitized. Don’t, for example, let a remote client execute arbitrary shell commands or file operations. Grant only the minimal permissions needed.

- **Network Security:**  If you start an HTTP/WS server inside the extension, bind only to localhost and use a random free port. There’s no built-in authentication for such a server, so do not expose it beyond the local machine. Be cautious if the extension could run in a remote workspace.

- **User Consent:**  Let users enable any “AI agent” features explicitly (via settings or prompts). Avoid automatically posting sensitive workspace data (file contents, secrets) into the Webview or RPC responses unless the user approves.

In summary, treat the Webview as an isolated context and minimize what you expose to it or to any external tool. Follow VS Code’s security best practices for webviews (CSP, restricted resource roots).

# 5. Recommended AI-Assisted Debugging Workflow

A practical workflow for developing/debugging a chat extension with AI assistance:

1. **Local Development:**
   - **Run in Dev Host:** Use `F5` in VS Code to launch the Extension Development Host with your extension. This lets you make live edits to TS/JS and see the chat UI.
   - **Open Webview DevTools:** Once the chat panel is visible, open the Webview DevTools (as described above) to inspect DOM or set breakpoints in your Webview script.
   - **Use `console.log` or state dumps:** For quick introspection, have your code `console.log` or expose state via `postMessage` to see it in the Debug Console.

2. **Automated Testing:**
   - **Write Integration Tests:** Add Mocha tests with `@vscode/test-electron` that cover critical logic (e.g. loading data, handling messages). Run them frequently (`npm test`). You can debug these tests too.
   - **Use Playwright for Complex Flows:** For full chat UI flows, write a Playwright script as above to simulate user actions end-to-end. Run it locally to catch UI regressions.
   - **Continuous Integration:** In your CI pipeline (e.g. GitHub Actions, Azure Pipelines) run `npm test` which invokes `@vscode/test-electron`. The VS Code docs have sample Azure setup using this tool. Also consider running your Playwright script on CI (using the `playwright` GitHub Action) if you want UI regression checks.

3. **Developer Tools/Shortcuts:**
   - Use the **Run** panel “Extension Tests” configuration to debug tests directly in VS Code (with breakpoints).
   - Use **Command Palette** commands: e.g. “Developer: Reload Webview” to refresh all open webviews after code changes.
   - Disable other extensions during debugging (`--disable-extensions` flag) to avoid interference.
   - If tests hang or fail, try enabling verbose logging (`--verbose`) or attaching a debugger to the Extension Host.

4. **Claude Code Integration:**
   - Ensure the AI agent has access to your code workspace (so it can edit files).
   - Add custom tools or “skills” to your agent configuration: for example, a “run tests” command that executes `npm test`, a “capture state” command that triggers your postMessage hook, or “open devtools” (though that one is manual).
   - If using an MCP server, configure the agent’s model to call those endpoints (e.g. it can do `GET /getMessages` or `POST /sendMessage`).

5. **CI/CD and Deployment:**
   - Use the **Continuous Integration** guidelines from Microsoft: run extension tests on each commit, and auto-publish on successful builds. The docs explain setting a `VSCE_PAT` secret for publishing.
   - Optionally, add an additional CI job that runs your Playwright/Electron tests on all OSes.

# 6. Troubleshooting & Common Pitfalls

- **“Only supported if no instance is running” Error:**  When using `@vscode/test-electron`, if a VS Code window is already open, the CLI run may fail. The docs note that running tests from the command line on Stable is only allowed if no other Code instance is running. Workarounds: close VS Code, use VS Code Insiders for development, or run tests in debug mode from inside VS Code.

- **Webview not loading content:**  Remember to convert URIs with `asWebviewUri` for local files. If images/CSS don’t appear, check that you set the correct `localResourceRoots` or CSP. 

- **DevTools Target not found:**  If using `--remote-debugging-port`, ensure the port is free and not blocked. VS Code must be started with that flag (e.g. from terminal). Then use Chrome’s `chrome://inspect` or a CDP library to find the *webview* target (it may appear as “webview-host” or similar).

- **Playwright/Electron quirks:**  Sometimes Playwright may open multiple windows or none. Use `firstWindow()` carefully. You may need to disable GPU acceleration (`--disable-gpu`) or use a newer Node/Electron. The Playwright docs mention enabling Node inspect argument if you run into launch timeouts.

- **PostMessage sync issues:**  Messages sent via `panel.webview.postMessage` are asynchronous. Make sure the Webview has loaded and called `acquireVsCodeApi()` before trying to receive. You might use `panel.webview.onDidReceiveMessage` to know when a response comes back.

- **Security alerts:**  If your webview opens sensitive URLs, or your RPC server is exposed incorrectly, VS Code may show warnings. Only use webview content you trust.

- **Version mismatches:**  If you use Electron automation, note that VS Code’s Electron version may differ from the Playwright-supported list. Playwright currently supports Electron >=v12; use a compatible VS Code build.

In general, log everything you can when running tests (use `console.log` liberally). Many failures turn out to be path issues (wrong `extensionDevelopmentPath`) or timing (waiting for webview to be ready). Use `await panel.webview.htmlReady` or similar checks where possible.

# 7. References

- VS Code Webview Guide (official API) – “Inspecting and debugging webviews” (DevTools commands).  
- StackOverflow / Blogs on Webview debugging – e.g. “Open Webview Developer Tools” command.  
- VS Code Extension Testing docs – `@vscode/test-electron`, runTests examples.  
- Continuous Integration guide – using `@vscode/test-electron` on Azure/GitHub Actions.  
- Playwright Electron API docs – example code showing `electron.launch` and window control.  
- Reddit / Community examples – Using Chrome DevTools Protocol (`--remote-debugging-port`) to inspect VS Code webviews.  
- Medium Article – Example MCP server in VS Code using FastMCP (MCP concepts).  
- GitHub Issue – Importance of Content Security Policy in webviews; VS Code Webview security guidance.  

*(All references above are linked from Microsoft’s official docs, GitHub, or community sources, as cited.)*