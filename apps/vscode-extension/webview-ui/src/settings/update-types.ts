export interface DesktopUpdateState {
  phase: "idle"|"checking"|"available"|"downloading"|"ready"|"installing"|"error";
  currentVersion:string; version?:string; notes?:string; message:string; received?:number; total?:number;
  canDownload:boolean; canInstall:boolean; backupAvailable:boolean;
}
