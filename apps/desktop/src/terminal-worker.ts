import * as native from "node-pty";
import { createOwnedTree, type OwnedTree } from "./terminal-process-tree.js";
const port=(process as unknown as {parentPort:{on(type:"message",fn:(event:{data:any})=>void):void;postMessage(value:unknown):void}}).parentPort;
let terminal:native.IPty|undefined,tree:OwnedTree|undefined,id="",seq=0,pending=0,queue:Buffer[]=[],size=0,ended:number|undefined,exitSignal=0,stopping=false,clean=false,exitPosted=false,inputQueue=Promise.resolve();
const post=(m:Record<string,unknown>)=>port.postMessage({...m,id});
const fail=(error:unknown)=>{post({type:"error",message:error instanceof Error?error.message:"Terminal cleanup failed. Its slot is retained."});};
const flush=()=>{if(pending)return;const chunk=queue.shift();if(chunk){size-=chunk.length;pending=++seq;post({type:"data",seq:pending,data:chunk.toString("base64")});}else if(ended!==undefined&&clean&&!exitPosted){exitPosted=true;post({type:"exited",exitCode:ended,signal:exitSignal});setTimeout(()=>process.exit(0),20);}else terminal?.resume();};
const cleanup=async()=>{if(tree)await tree.terminate();else if(terminal&&ended===undefined)terminal.kill("SIGKILL");clean=true;flush();};
port.on("message",({data:m})=>{void(async()=>{try{
 if(m.type==="start"&&!terminal&&!id){id=m.id;terminal=native.spawn(m.shell,m.args,{name:"xterm-256color",cwd:m.cwd,env:process.env as Record<string,string>,cols:m.cols,rows:m.rows,encoding:"utf8"});
 terminal.onData(text=>{terminal?.pause();const bytes=Buffer.from(text,"utf8");if(size+bytes.length>262144){fail(new Error("Terminal output exceeded the transport window. Session stopped."));stopping=true;void cleanup().catch(fail);return;}for(let n=0;n<bytes.length;n+=32768)queue.push(bytes.subarray(n,n+32768));size+=bytes.length;flush();});
 terminal.onExit(e=>{ended=e.exitCode;exitSignal=e.signal??0;void cleanup().catch(fail);});
 if(process.platform!=="win32")tree=await createOwnedTree(terminal.pid,process.pid);
 post({type:"ready",pid:terminal.pid});return;}
 if(!terminal||m.id!==id)return;
 if(m.type==="ack"&&m.seq===pending){pending=0;flush();}
 else if(m.type==="input"&&ended===undefined&&!stopping&&typeof m.chars==="string"&&Buffer.byteLength(m.chars)<=4096){inputQueue=inputQueue.then(async()=>{await tree?.refresh();if(!stopping&&ended===undefined)terminal?.write(m.chars);post({type:"inputAck"});});await inputQueue;}
 else if(m.type==="resize"&&ended===undefined&&!stopping&&Number.isInteger(m.cols)&&m.cols>=2&&m.cols<=500&&Number.isInteger(m.rows)&&m.rows>=2&&m.rows<=300)terminal.resize(m.cols,m.rows);
 else if(m.type==="end"){stopping=true;if(process.platform==="win32")terminal.kill();await cleanup();}
 }catch(e){fail(e);stopping=true;try{await cleanup();}catch(error){fail(error);}}})();});
process.on("SIGTERM",()=>{stopping=true;void cleanup().then(()=>process.exit(0),fail);});
process.on("disconnect",()=>{stopping=true;void cleanup().then(()=>process.exit(0),fail);});
