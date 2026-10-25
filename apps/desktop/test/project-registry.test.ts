import {afterEach,expect,it} from "vitest";
import {mkdtempSync,realpathSync,mkdirSync,symlinkSync,rmSync,readFileSync,writeFileSync} from "node:fs";
import {tmpdir} from "node:os";import {join} from "node:path";
import {ProjectRegistry,projectId} from "../src/project-registry.js";
const dirs:string[]=[];afterEach(()=>dirs.splice(0).forEach(d=>rmSync(d,{recursive:true,force:true})));
function setup(){const root=realpathSync(mkdtempSync(join(tmpdir(),"cp9-recents-")));dirs.push(root);return{root,file:join(root,"registry.json")};}
it("canonical aliases share one identity; missing recents survive and removal keeps repository files",async()=>{
 const {root,file}=setup(),folder=join(root,"project"),alias=join(root,"alias");mkdirSync(folder);symlinkSync(folder,alias);writeFileSync(join(folder,"file"),"keep");
 const r=new ProjectRegistry(file);const a=await r.add(folder),b=await r.add(alias);expect(a.id).toBe(b.id);expect(r.list()).toHaveLength(1);
 r.remove(a.id);expect(readFileSync(join(folder,"file"),"utf8")).toBe("keep");
 await r.add(folder);rmSync(folder,{recursive:true});expect((await new ProjectRegistry(file).status())[0]?.missing).toBe(true);
});
it("seeds legacy once, rejects corrupt/duplicate/foreign root identity without overwriting",async()=>{
 const {root,file}=setup();const r=new ProjectRegistry(file,root);expect(r.get(projectId(root)).root).toBe(root);await r.add(root);
 expect(new ProjectRegistry(file,"/other").list()).toHaveLength(1);
 const bad=JSON.stringify({version:1,projects:[{id:projectId("/foreign"),root:"/other",name:"Bad",openedAt:0}]});writeFileSync(file,bad);
 expect(()=>new ProjectRegistry(file)).toThrow();expect(readFileSync(file,"utf8")).toBe(bad);
});
