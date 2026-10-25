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

  export enum ExtensionMode { Production = 1, Development = 2, Test = 3 }

  export interface ExtensionContext {
    readonly extensionMode?: ExtensionMode;
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

  export enum FileType { Unknown = 0, File = 1, Directory = 2, SymbolicLink = 64 }

  export class RelativePattern {
    constructor(base: WorkspaceFolder | Uri | string, pattern: string);
  }

  export interface FileSystemWatcher extends Disposable {
    onDidCreate(listener: (uri: Uri) => unknown): Disposable;
    onDidChange(listener: (uri: Uri) => unknown): Disposable;
    onDidDelete(listener: (uri: Uri) => unknown): Disposable;
  }

  export interface Uri {
    readonly scheme: string;
    readonly authority: string;
    readonly path: string;
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

  export namespace env {
    function openExternal(target: Uri): Thenable<boolean>;
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
    function showErrorMessage(
      message: string, options: { modal?: boolean }, ...items: string[]
    ): Thenable<string | undefined>;
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
    function showTextDocument(
      uriOrDocument: Uri | TextDocument,
      options?: { viewColumn?: ViewColumn }
    ): Thenable<TextEditor>;
  }

  export class Position {
    constructor(line: number, character: number);
    readonly line: number;
    readonly character: number;
  }

  export class Selection {
    constructor(anchor: Position, active: Position);
  }

  export class Range {
    constructor(start: Position, end: Position);
  }

  export enum TextEditorRevealType {
    Default = 0,
    InCenter = 2,
  }

  export interface TextEditor {
    selection: Selection;
    revealRange(range: Range, revealType?: TextEditorRevealType): void;
  }

  export namespace workspace {
    const workspaceFolders: readonly WorkspaceFolder[] | undefined;
    const isTrusted: boolean;
    function onDidGrantWorkspaceTrust(listener: () => unknown): Disposable;
    function getConfiguration(section?: string, scope?: Uri): Configuration;
    function getWorkspaceFolder(uri: Uri): WorkspaceFolder | undefined;
    namespace fs {
      function stat(uri: Uri): Thenable<{ type: FileType }>;
    }
    function createFileSystemWatcher(globPattern: string | RelativePattern): FileSystemWatcher;
    function onDidChangeWorkspaceFolders(listener: (event: {
      added: readonly WorkspaceFolder[]; removed: readonly WorkspaceFolder[];
    }) => unknown): Disposable;
    function onDidChangeConfiguration(listener: (event: {
      affectsConfiguration(section: string): boolean;
    }) => unknown): Disposable;
    function openTextDocument(
      uriOrOptions:
        | Uri
        | {
            language?: string;
            content?: string;
          }
    ): Thenable<TextDocument>;
    function findFiles(include: string | RelativePattern, exclude?: string | RelativePattern | null, maxResults?: number): Thenable<Uri[]>;
    function asRelativePath(pathOrUri: string | Uri, includeWorkspaceFolder?: boolean): string;
  }

  export namespace commands {
    function registerCommand(
      command: string,
      callback: (...args: unknown[]) => unknown
    ): Disposable;
    function executeCommand<T = unknown>(command: string, ...rest: unknown[]): Thenable<T>;
  }
}
