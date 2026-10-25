import { mkdtempSync, realpathSync, writeFileSync, mkdirSync, symlinkSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, it, vi } from "vitest";
import { HttpBackendClient } from "@crucible/editor-client";
import type { BaselineEntry, BaselineFile, CheckpointSummary } from "@crucible/editor-client";
import { createWorkspaceFiles } from "../src/workspace-files.js";
import { createWorkspaceBaseline, type BaselineScope } from "../src/workspace-baseline.js";
const roots:string[]=[];
afterEach(()=>{for(const root of roots.splice(0))rmSync(root,{recursive:true,force:true});});
const ready=(value:BaselineScope)=>{if(value.state!=="ready")throw Error(value.reason);return value;};
async function fixture(entries:BaselineEntry[], before:Record<string,string|Uint8Array>={}, current:Record<string,string|Uint8Array>={}) {
  const root=realpathSync(mkdtempSync(join(tmpdir(),'crucible-baseline-')));roots.push(root);
  for(const [path,value] of Object.entries(current)){mkdirSync(join(root,path,'..'),{recursive:true});writeFileSync(join(root,path),value);}
  let checkpoints:CheckpointSummary[]=[{seq:0,anchorMessageId:'anchor-0',turnId:'turn',createdAt:'date-0',files:[]},{seq:1,anchorMessageId:'anchor-1',turnId:'continuation',createdAt:'date-1',files:[]}];
  const backend={listCheckpoints:vi.fn(async()=>structuredClone(checkpoints)),getCheckpointBaseline:vi.fn(async()=>structuredClone(entries)),getCheckpointBaselineFile:vi.fn(async(_thread:string,_seq:number,path:string):Promise<BaselineFile>=>{
    const entry=entries.find(entry=>entry.path===path)!;const content=typeof before[path]==='string'?new TextEncoder().encode(before[path]):before[path];
    return entry.existed?{path,fromSeq:entry.fromSeq,state:'captured',size:content!.length,content:Uint8Array.from(content!)}:{path,fromSeq:entry.fromSeq,state:'absent',size:null};
  })};
  const files=await createWorkspaceFiles(root), reader=createWorkspaceBaseline(files,backend);
  return {root,files,reader,backend,selected:{seq:0,anchorMessageId:'anchor-0',createdAt:'date-0'},changeCheckpoint:(seq:number)=>{checkpoints=checkpoints.map(cp=>cp.seq===seq?{...cp,anchorMessageId:'reused',createdAt:'new-date'}:cp);}};
}
const entry=(path:string,existed=true,fromSeq=0):BaselineEntry=>({path,existed,oversize:false,fromSeq});
it('compares only retained edit-tool paths in a nonGit workspace, distinguishing unchanged and created-removed',async()=>{
 const f=await fixture([entry('modified'),entry('deleted'),entry('same'),entry('added',false),entry('removed-again',false)],{modified:'old',deleted:'delete',same:'equal'},{modified:'new',same:'equal',added:'created',outside:'untracked user file'});
 const result=ready(await f.reader.scope('thread',f.selected)); expect(result.changes.map(file=>[file.path,file.status])).toEqual([['modified','M'],['deleted','D'],['added','A']]);expect(result.unchanged).toBe(1);expect(result.createdRemoved).toBe(1);expect(result.partial).toBe(false);
 const diff=await f.reader.diff(result.generation,'modified');expect(diff).toMatchObject({kind:'text',additions:1,deletions:1,truncated:false}); if(diff.kind==='text')expect(diff.unified).toContain('+new');
 expect(await f.reader.diff(result.generation,'outside')).toMatchObject({kind:'unavailable'});
});
it.each([0,1])('refuses selected or supplying reused seq=%i and changed folded provenance',async seq=>{
 const f=await fixture([entry('a',true,1)],{a:'old'},{a:'new'}), result=ready(await f.reader.scope('t',f.selected));f.changeCheckpoint(seq);
 expect(await f.reader.diff(result.generation,'a')).toMatchObject({kind:'unavailable',reason:expect.stringContaining('changed or was removed')});
 expect(f.backend.getCheckpointBaselineFile).toHaveBeenCalledTimes(1);
});
it('rejects changed fold membership, returns binary with actual fingerprints and refreshes changed bytes',async()=>{
 const f=await fixture([entry('a')],{a:new Uint8Array([0,1])},{a:new Uint8Array([0,2])}), result=ready(await f.reader.scope('t',f.selected));
 const first=await f.reader.diff(result.generation,'a');writeFileSync(join(f.root,'a'),new Uint8Array([0,3])); const second=await f.reader.diff(result.generation,'a');expect(first.kind).toBe('binary');expect(second.kind).toBe('binary');if(first.kind==='binary'&&second.kind==='binary')expect(first.fingerprint).not.toBe(second.fingerprint);
 f.backend.getCheckpointBaseline.mockResolvedValueOnce([entry('a',true,1)]);expect(await f.reader.diff(result.generation,'a')).toMatchObject({kind:'unavailable'});
});
it('does not misreport protected/link/oversize/unavailable paths as deletions or clean',async()=>{
 const f=await fixture([entry('linked'),entry('.git/config'),entry('big'),entry('lost')],{linked:'old','.git/config':'old',big:'old',lost:'old'},{big:'x'.repeat(1024*1024+1)});
 symlinkSync(join(f.root,'big'),join(f.root,'linked')); f.backend.getCheckpointBaselineFile.mockImplementation(async(_t,_s,path)=>path==='lost'?{path,fromSeq:0,state:'missing',size:null}:{path,fromSeq:0,state:'captured',size:3,content:new TextEncoder().encode('old')});
 const result=ready(await f.reader.scope('t',f.selected));expect(result.changes).toEqual([]);expect(result.uncompared).toHaveLength(4);expect(result.partial).toBe(true);expect(result.unchanged).toBe(0);
 const read=await f.files.compareBytes('big',16);expect(read.content).toHaveLength(16);expect(read.truncated).toBe(true);
 expect(()=>f.files.compareBytes('big',Infinity)).toThrow();
});
it('keeps just two metadata generations, isolates caller mutations and refuses status changes',async()=>{
 const f=await fixture([entry('a')],{a:'old'},{a:'new'}), chosen={...f.selected};const first=ready(await f.reader.scope('t',chosen));chosen.anchorMessageId='caller mutation';first.changes[0]!.supplying.anchorMessageId='mutated result';first.checkpoint.anchorMessageId='mutated result';
 expect(await f.reader.diff(first.generation,'a')).toMatchObject({kind:'text'});await f.reader.scope('t',f.selected);await f.reader.scope('t',f.selected);
 expect(await f.reader.diff(first.generation,'a')).toMatchObject({kind:'unavailable'});
 const latest=ready(await f.reader.scope('t',f.selected));writeFileSync(join(f.root,'a'),'old');expect(await f.reader.diff(latest.generation,'a')).toMatchObject({kind:'unavailable',reason:expect.stringContaining('changed since')});
});
it('caps candidate count and soft work time without claiming inspected omitted paths',async()=>{
 const f=await fixture(Array.from({length:501},(_,n)=>entry('new-'+n,false)));const count=ready(await f.reader.scope('t',f.selected));expect(count.createdRemoved).toBe(500);expect(count.omitted).toBe(1);expect(count.partial).toBe(true);
 let tick=0;const reader=createWorkspaceBaseline(f.files,f.backend,()=>tick++?11000:0);const time=ready(await reader.scope('t',f.selected));expect(time.omitted).toBe(501);expect(time.createdRemoved).toBe(0);
});
it('cancellation prevents late scope publication and neutral backend failure never becomes empty history',async()=>{
 const f=await fixture([entry('a')],{a:'old'},{a:'new'});let finish!:(value:BaselineFile)=>void, entered!:()=>void;const started=new Promise<void>(resolve=>{entered=resolve;});
 f.backend.getCheckpointBaselineFile.mockImplementationOnce(async()=>{entered();return new Promise(resolve=>{finish=resolve;});});
 const controller=new AbortController(), pending=f.reader.scope('t',f.selected,{signal:controller.signal});const refused=expect(pending).rejects.toMatchObject({reason:'cancelled'});await started;controller.abort();await refused;
 finish({path:'a',fromSeq:0,state:'captured',size:3,content:new TextEncoder().encode('old')});await new Promise(resolve=>setImmediate(resolve));
 f.backend.listCheckpoints.mockRejectedValueOnce(Error('404'));expect(await f.reader.listCheckpoints('t')).toEqual({state:'unavailable',reason:"Retained checkpoints aren't available for this conversation."});
});

it('bounds combined inspected content and rendered diff lines, and keeps too-large states explicit',async()=>{
 const paths=Array.from({length:17},(_,n)=>'large-'+n), before=Object.fromEntries(paths.map(path=>[path,'a'.repeat(1024*1024)])), current=Object.fromEntries(paths.map(path=>[path,'b'.repeat(1024*1024)]));
 const f=await fixture(paths.map(path=>entry(path)),before,current);const result=ready(await f.reader.scope('t',f.selected));expect(result.changes).toHaveLength(16);expect(result.omitted).toBe(1);expect(f.backend.getCheckpointBaselineFile).toHaveBeenCalledTimes(16);
 writeFileSync(join(f.root,paths[0]!),'x'.repeat(1024*1024+1));expect(await f.reader.diff(result.generation,paths[0]!)).toEqual({kind:'too-large'});
 const lines=await fixture([entry('new.ts',false)],{}, {'new.ts':'line\n'.repeat(4000)}), added=ready(await lines.reader.scope('t',lines.selected));const diff=await lines.reader.diff(added.generation,'new.ts');
 expect(diff).toMatchObject({kind:'text',truncated:true,additions:4000,deletions:0});if(diff.kind==='text')expect(diff.unified.split('\n').length).toBeLessThanOrEqual(3000);
});
it('rechecks supplying identity even when an inspected file is unchanged',async()=>{
 const f=await fixture([entry('a',true,1)],{a:'same'},{a:'same'});
 f.backend.getCheckpointBaselineFile.mockImplementationOnce(async()=>{f.changeCheckpoint(1);return {path:'a',fromSeq:1,state:'captured',size:4,content:new TextEncoder().encode('same')};});
 expect(await f.reader.scope('t',f.selected)).toMatchObject({state:'unavailable',reason:expect.stringContaining('changed or was removed')});
});

it('maps actual malformed backend validation to neutral reasons instead of exposing internal JSON',async()=>{
 const client=new HttpBackendClient({baseUrl:'http://localhost',fetchFn:async()=>new Response(JSON.stringify({path:'a',from_seq:0,state:'captured',size:3}))});
 const f=await fixture([entry('a')],{a:'old'},{a:'new'});
 f.backend.getCheckpointBaselineFile.mockImplementationOnce(()=>client.getCheckpointBaselineFile('t',0,'a'));
 const malformed=ready(await f.reader.scope('t',f.selected));expect(malformed.uncompared).toEqual([{path:'a',reason:'This file could not be compared.'}]);
 const good=ready(await f.reader.scope('t',f.selected));f.backend.getCheckpointBaselineFile.mockImplementationOnce(()=>client.getCheckpointBaselineFile('t',0,'a'));
 expect(await f.reader.diff(good.generation,'a')).toEqual({kind:'unavailable',reason:'Retained comparison is unavailable. Refresh.'});
 f.backend.listCheckpoints.mockRejectedValueOnce(Error('Retained checkpoint response is too large to inspect.'));
 expect(await f.reader.listCheckpoints('t')).toEqual({state:'unavailable',reason:'Retained checkpoint response is too large to inspect.'});
});
