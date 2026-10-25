import {expect,it,vi} from "vitest";
import {NativeFind} from "../src/native-find.js";
import {desktopShortcutForKey,DESKTOP_SHORTCUTS} from "../src/desktop-shortcuts.js";
import {parsePayload} from "../src/contract.js";
it("fences native search replies on replacement, stop and document navigation",()=>{
 let view="a",id=0;const post=vi.fn(),find=vi.fn(()=>++id),stop=vi.fn();const f=new NativeFind({findInPage:find,stopFindInPage:stop},()=>view,post);
 const q={requestId:"q1",query:"fixture",forward:true,next:false};f.search(q);f.search({...q,requestId:"q2"});
 f.result({requestId:1,matches:99,activeMatchOrdinal:1});expect(post).not.toHaveBeenCalled();
 f.result({requestId:2,matches:2,activeMatchOrdinal:1});expect(post).toHaveBeenLastCalledWith({type:"desktop/findResult",requestId:"q2",matches:2,ordinal:1,finalUpdate:true});
 view="b";post.mockClear();f.result({requestId:2,matches:2,activeMatchOrdinal:1});expect(post).not.toHaveBeenCalled();
 f.search(q);f.stop();f.result({requestId:3,matches:2,activeMatchOrdinal:1});expect(post).not.toHaveBeenCalled();expect(stop).toHaveBeenCalledWith("clearSelection");
 expect(()=>parsePayload({...q,type:"desktop/find",query:"a".repeat(513)})).toThrow();expect(parsePayload({...q,type:"desktop/find"})).toMatchObject({query:"fixture"});
});
it("keeps Mac readline/control, composition and other shortcuts out of the native action map",()=>{
 const key={key:"n",meta:true,control:false,alt:false,shift:false};expect(desktopShortcutForKey(key,"darwin")).toBe("new");
 expect(desktopShortcutForKey({...key,meta:false,control:true},"darwin")).toBeUndefined();expect(desktopShortcutForKey({...key,meta:false,control:true},"win32")).toBe("new");
 expect(desktopShortcutForKey({...key,key:"Enter"},"darwin")).toBeUndefined();expect(desktopShortcutForKey({...key,key:"Escape"},"darwin")).toBeUndefined();expect(desktopShortcutForKey({...key,shift:true},"darwin")).toBeUndefined();
 expect(desktopShortcutForKey({...key,key:"b",alt:true},"darwin")).toBe("workspace");expect(DESKTOP_SHORTCUTS.map(x=>x.action)).toEqual(["new","open","settings","palette","find","sidebar","workspace"]);
});
