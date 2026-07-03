declare module "vscode" {
  export interface Disposable {
    dispose(): void;
  }

  export interface Memento {
    get<T>(key: string): T | undefined;
    get<T>(key: string, defaultValue: T): T;
    update(key: string, value: unknown): Thenable<void>;
  }

  export interface SecretStorage {
    get(key: string): Thenable<string | undefined>;
    store(key: string, value: string): Thenable<void>;
    delete(key: string): Thenable<void>;
  }

  export interface ExtensionContext {
    subscriptions: Disposable[];
    workspaceState: Memento;
    globalState: Memento;
    secrets: SecretStorage;
    extensionUri: Uri;
  }

  export interface WorkspaceFolder {
    readonly uri: Uri;
    readonly name: string;
    readonly index: number;
  }

  export interface ConfigurationInspect<T> {
    key: string;
    defaultValue?: T;
    globalValue?: T;
    workspaceValue?: T;
    workspaceFolderValue?: T;
  }

  export interface Configuration {
    get<T>(key: string, defaultValue?: T): T;
    inspect<T>(key: string): ConfigurationInspect<T> | undefined;
    update(key: string, value: unknown, target?: ConfigurationTarget | boolean): Thenable<void>;
  }

  export enum ConfigurationTarget {
    Global = 1,
    Workspace = 2,
    WorkspaceFolder = 3,
  }

  export interface TextDocument {
    readonly uri: Uri;
  }

  export interface Uri {
    readonly fsPath: string;
    toString(skipEncoding?: boolean): string;
  }

  export namespace Uri {
    function file(path: string): Uri;
    function parse(value: string): Uri;
    function joinPath(base: Uri, ...pathSegments: string[]): Uri;
  }

  export interface WebviewOptions {
    enableScripts?: boolean;
    retainContextWhenHidden?: boolean;
    localResourceRoots?: readonly Uri[];
  }

  export interface Webview {
    html: string;
    options: WebviewOptions;
    readonly cspSource: string;
    onDidReceiveMessage(listener: (e: unknown) => unknown): Disposable;
    postMessage(message: unknown): Thenable<boolean>;
    asWebviewUri(localResource: Uri): Uri;
  }

  export interface WebviewPanel {
    readonly webview: Webview;
    title: string;
    reveal(viewColumn?: ViewColumn, preserveFocus?: boolean): void;
    onDidDispose(listener: () => unknown): Disposable;
    dispose(): void;
  }

  export enum ViewColumn {
    One = 1,
    Two = 2,
    Three = 3,
  }

  export interface InputBoxOptions {
    prompt?: string;
    placeHolder?: string;
    value?: string;
    validateInput?(value: string): string | null | undefined;
    ignoreFocusOut?: boolean;
  }

  export interface MessageOptions {
    modal?: boolean;
    detail?: string;
  }

  export interface WebviewPanelSerializer {
    deserializeWebviewPanel(webviewPanel: WebviewPanel, state: unknown): Thenable<void>;
  }

  export enum StatusBarAlignment {
    Left = 1,
    Right = 2,
  }

  export interface StatusBarItem extends Disposable {
    text: string;
    tooltip?: string;
    command?: string;
    show(): void;
    hide(): void;
  }

  export interface OutputChannel extends Disposable {
    readonly name: string;
    append(value: string): void;
    appendLine(value: string): void;
    show(preserveFocus?: boolean): void;
  }

  export interface QuickPickItem {
    label: string;
    description?: string;
    detail?: string;
  }

  export namespace window {
    function showInputBox(options?: InputBoxOptions): Thenable<string | undefined>;
    function showInformationMessage(message: string): Thenable<string | undefined>;
    function showInformationMessage(
      message: string,
      options: MessageOptions,
      ...items: string[]
    ): Thenable<string | undefined>;
    function showInformationMessage(
      message: string,
      ...items: string[]
    ): Thenable<string | undefined>;
    function showWarningMessage(message: string): Thenable<string | undefined>;
    function showErrorMessage(message: string, ...items: string[]): Thenable<string | undefined>;
    function createStatusBarItem(alignment?: StatusBarAlignment, priority?: number): StatusBarItem;
    function createOutputChannel(name: string): OutputChannel;
    function createWebviewPanel(
      viewType: string,
      title: string,
      showOptions: ViewColumn,
      options: { enableScripts?: boolean; retainContextWhenHidden?: boolean; localResourceRoots?: readonly Uri[] }
    ): WebviewPanel;
    function showQuickPick(items: readonly string[], options?: { placeHolder?: string }): Thenable<string | undefined>;
    function showQuickPick<T extends QuickPickItem>(
      items: readonly T[],
      options?: { placeHolder?: string }
    ): Thenable<T | undefined>;
    function setStatusBarMessage(text: string, hideAfterTimeout?: number): Disposable;
    function registerWebviewPanelSerializer(viewType: string, serializer: WebviewPanelSerializer): Disposable;
  }

  export namespace workspace {
    const workspaceFolders: readonly WorkspaceFolder[] | undefined;
    function getConfiguration(section?: string): Configuration;
    function openTextDocument(
      uriOrOptions:
        | Uri
        | {
            language?: string;
            content?: string;
          }
    ): Thenable<TextDocument>;
  }

  export namespace commands {
    function registerCommand(
      command: string,
      callback: (...args: unknown[]) => unknown
    ): Disposable;
    function executeCommand<T = unknown>(command: string, ...rest: unknown[]): Thenable<T>;
  }
}
