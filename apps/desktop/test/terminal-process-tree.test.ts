import{it,expect,vi}from"vitest";import{createOwnedTree,parseProcesses,type ProcessIdentity}from"../src/terminal-process-tree.js";
const row=(pid:number,parent:number,started="original"):ProcessIdentity=>({pid,parent,started,group:pid,tty:"ttys002"});
it("parses real macOS-style process identities without trusting the hidden sess field",()=>{expect(parseProcesses(" 100 50 100 ttys002 Sat Oct 24 15:00:01 2026\n")[0]).toEqual({pid:100,parent:50,group:100,tty:"ttys002",started:"Sat Oct 24 15:00:01 2026"});});
it("kills foreground and background descendants before root and leaves an unrelated group alive",async()=>{
 let rows=[row(100,50),row(101,100),row(102,100),row(200,70)];const signals:Array<[number,string]>=[];const signal=(pid:number,sig:NodeJS.Signals)=>{signals.push([pid,sig]);if(sig==="SIGKILL"||pid===100)rows=rows.filter(r=>r.pid!==pid);};
 const tree=await createOwnedTree(100,50,async()=>rows,signal);await tree.terminate();expect(rows.map(r=>r.pid)).toEqual([200]);expect(signals.findIndex(([p])=>p===100)).toBeGreaterThan(signals.findIndex(([p,s])=>p===102&&s==="SIGKILL"));
});
it("tracks descendants after root exits/reparents, but never signals a reused PID",async()=>{
 let rows=[row(100,50),row(101,100)],signals:number[]=[];const tree=await createOwnedTree(100,50,async()=>rows,(pid)=>{signals.push(pid);rows=rows.filter(r=>r.pid!==pid);});await tree.refresh();rows=[{...row(101,1),tty:"??"},row(100,999,"replacement")];await tree.terminate();expect(signals).toEqual([101]);expect(rows[0]!.started).toBe("replacement");
});
it("refuses an unproven root/parent instead of killing a foreign process",async()=>{await expect(createOwnedTree(100,50,async()=>[row(100,999)],vi.fn())).rejects.toThrow(/verify/);});

it("verified private supervisor collects reparented same-group jobs and excludes unrelated groups",async()=>{let rows=[row(100,50),{...row(101,1),group:100},row(200,70)];const signals:number[]=[];const tree=await createOwnedTree(100,50,async()=>rows,(pid)=>{signals.push(pid);rows=rows.filter(r=>r.pid!==pid);},true);await tree.terminate();expect(signals).toEqual([101,100]);expect(rows.map(r=>r.pid)).toEqual([200]);await expect(createOwnedTree(100,50,async()=>[{...row(100,50),group:50}],vi.fn(),true)).rejects.toThrow(/group leader/);});
