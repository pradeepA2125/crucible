export const DESKTOP_SHORTCUTS = [
  {action:"new",label:"New Conversation",accelerator:"CmdOrCtrl+N",key:"n"},
  {action:"open",label:"Open Project…",accelerator:"CmdOrCtrl+O",key:"o"},
  {action:"settings",label:"Settings…",accelerator:"CmdOrCtrl+,",key:","},
  {action:"palette",label:"Search Conversations, Files and Actions",accelerator:"CmdOrCtrl+K",key:"k"},
  {action:"find",label:"Find in Visible View",accelerator:"CmdOrCtrl+F",key:"f"},
  {action:"sidebar",label:"Toggle Conversations",accelerator:"CmdOrCtrl+B",key:"b"},
  {action:"workspace",label:"Toggle Workspace",accelerator:"CmdOrCtrl+Alt+B",key:"b",alt:true},
] as const;
export type DesktopShortcut = typeof DESKTOP_SHORTCUTS[number]["action"];
export function desktopShortcutForKey(input: {key:string;meta:boolean;control:boolean;alt:boolean;shift:boolean}, platform: string): DesktopShortcut | undefined {
  if (!(platform==="darwin" ? input.meta && !input.control : input.control && !input.meta) || input.shift) return;
  return DESKTOP_SHORTCUTS.find(item=>item.key===input.key.toLowerCase() && ("alt" in item ? item.alt : false)===input.alt)?.action;
}
