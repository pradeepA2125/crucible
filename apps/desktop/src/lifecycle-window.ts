import { randomUUID } from "node:crypto";
import { APP_ORIGIN, CHANNEL, parseEnvelope, type BridgeContext } from "./contract.js";
import { assertSender } from "./security.js";
import type { QuitPreview } from "./lifecycle-protocol.js";
export interface LifecycleNativeWindow {
  webContents:{id:number;mainFrame:unknown;send(channel:string,value:unknown):void};
  isDestroyed():boolean;loadURL(url:string):Promise<unknown>;show():void;focus():void;destroy():void;
  on(event:string,listener:(...args:any[])=>void):unknown;
}
interface Event {sender:{id:number};senderFrame:{url:string}|null;}
/** Separate document, separate authority: no project commands or inherited host lease. */
export class LifecycleWindow {
  private current: {window:LifecycleNativeWindow;context:BridgeContext;sequence:number;preview:QuitPreview;busy:boolean} | undefined;
  constructor(private create:()=>LifecycleNativeWindow,private theme:()=>Record<string,unknown>,private choose:(choice:string,preview:QuitPreview)=>Promise<void>){ }
  owns(id:number):boolean{return this.current?.window.webContents.id===id;}
  private sender(event:Event){const s=this.current;if(!s||s.window.isDestroyed())throw new Error("Quit preview is closed.");
    assertSender({id:event.sender.id,frameIsMain:event.senderFrame===s.window.webContents.mainFrame,url:event.senderFrame?.url??""},s.window.webContents.id);
    if(new URL(event.senderFrame!.url).pathname!=="/lifecycle.html")throw new Error("Wrong lifecycle document.");return s;}
  bootstrap(event:Event):BridgeContext{return {...this.sender(event).context};}
  async show(preview:QuitPreview):Promise<void>{
    if(this.current){this.current.preview=structuredClone(preview);this.current.busy=false;this.post({type:"desktop/quitPreview",preview});this.current.window.show();this.current.window.focus();return;}
    const window=this.create(),s={window,context:{v:1 as const,clientId:randomUUID(),viewId:randomUUID(),projectId:null,threadId:null,processId:null},sequence:0,preview:structuredClone(preview),busy:false};this.current=s;
    window.on("close",(event:{preventDefault():void})=>{event.preventDefault();if(!s.busy){s.busy=true;void this.choose("cancel",s.preview).catch(error=>{s.busy=false;this.post({type:"desktop/lifecycleError",message:String(error)});});}});
    try{await window.loadURL(`${APP_ORIGIN}/lifecycle.html`);window.show();window.focus();}catch(error){this.close();throw error;}
  }
  private post(payload:unknown):void{const s=this.current;if(s&&!s.window.isDestroyed())s.window.webContents.send(CHANNEL.event,{context:{...s.context},payload});}
  broadcastTheme():void{this.post(this.theme());}
  async handle(event:Event,raw:unknown):Promise<{error?:string}>{
    let acquired: typeof this.current;
    try{const s=this.sender(event),message=parseEnvelope(raw,s.context,s.sequence);s.sequence=message.sequence;
      if(message.payload.type==="themeReady"){this.post(this.theme());return{};}
      if(message.payload.type==="desktop/lifecycleReady"){this.post(this.theme());this.post({type:"desktop/quitPreview",preview:s.preview});return{};}
      if(message.payload.type!=="desktop/lifecycleChoice")throw new Error("This document only controls its quit preview.");
      if(s.busy||message.payload.snapshotId!==s.preview.id)throw new Error("Quit preview changed. Review the current choices.");
      s.busy=true;acquired=s;await this.choose(message.payload.choice as string,structuredClone(s.preview));return{};
    }catch(error){const message=error instanceof Error?error.message:"Quit action failed.";if(acquired && this.current===acquired)acquired.busy=false;this.post({type:"desktop/lifecycleError",message});return{error:message};}
  }
  close():void{const s=this.current;this.current=undefined;if(s&&!s.window.isDestroyed())s.window.destroy();}
}
