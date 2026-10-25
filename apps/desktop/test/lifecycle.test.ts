import {describe,it,expect} from "vitest";
import {LifecycleTransitions,quitFingerprint,cleanAll,type Observation} from "../src/lifecycle.js";
const thread=(active=false,gates=0)=>({thread_id:"thread-a",title:"Alpha",turn_active:active,gates,gate_ids:Array.from({length:gates},(_,i)=>`g-${i}`),agents_running:0,sessions_running:0,agents:[],teams:[],sessions:[]});
const project=(active=false,gates=0):Observation=>({id:"a",name:"Alpha",state:active||gates?"busy":"idle",identity:"owner1:process1",owner:true,threads:[thread(active,gates)],shells:[]});
const prefs={notifications:"all" as const,keepAwake:true};
describe("observed background lifecycle",()=>{
 it("does not flood first observation or owner/process restarts; unknown cannot complete work",()=>{
  const s=new LifecycleTransitions();expect(s.update([project(true)],prefs,false).notices).toEqual([]);
  expect(s.update([{...project(),identity:"owner2:process2"}],prefs,false).notices).toEqual([]);
  s.update([project(true)],prefs,false);s.update([{...project(),state:"unknown"}],prefs,false);
  expect(s.update([project()],prefs,false).notices).toEqual([]);
 });
 it("groups attention and completes exact observed thread, once, without command text",()=>{
  const s=new LifecycleTransitions();s.update([project(true)],prefs,false);
  expect(s.update([project(true,2)],prefs,false)).toMatchObject({badge:2,awake:true,notices:[{projectId:"a",threadId:"thread-a",kind:"attention",count:2}]});
  expect(s.update([project(true,2)],prefs,false).notices).toEqual([]);
  expect(s.update([project()],prefs,false).notices).toEqual([{projectId:"a",threadId:"thread-a",kind:"complete",count:1}]);
 });
 it("updates baselines quietly while focused and honors optouts, observer ownership and local shells",()=>{
  const s=new LifecycleTransitions();s.update([project(true)],prefs,false);
  expect(s.update([project()],prefs,true).notices).toEqual([]);expect(s.update([project()],prefs,false).notices).toEqual([]);
  expect(s.update([{...project(true),owner:false}],prefs,false).awake).toBe(false);
  expect(s.update([{...project(),state:"unknown"}],prefs,false).awake).toBe(false);
  expect(s.update([{...project(),shells:[{id:"shell",label:"zsh"}]}],prefs,false).awake).toBe(true);
  expect(s.update([project(true)],{notifications:"off",keepAwake:false},false)).toMatchObject({notices:[],awake:false});
  expect(s.update([],prefs,false)).toEqual({notices:[],badge:0,awake:false});
 });
 it("fingerprints real resource changes, without transient output or title edits",()=>{
  const a=project(true),f=quitFingerprint([a]);expect(quitFingerprint([{...a,name:"Renamed",threads:[{...thread(true),title:"New title"}]}])).toBe(f);
  expect(quitFingerprint([project()])).not.toBe(f);expect(quitFingerprint([{...a,shells:[{id:"shell",label:"zsh"}]}])).not.toBe(f);
 });
 it("attempts all cleanup actions even when synchronous and asynchronous actions fail",async()=>{
  const seen:string[]=[];await expect(cleanAll([()=>{seen.push("reader");throw Error("reader");},async()=>{seen.push("runtime");throw Error("runtime");},()=>{seen.push("draft");}])).rejects.toBeInstanceOf(AggregateError);
  expect(seen).toEqual(["reader","runtime","draft"]);
 });
});

it("only newly identified gates notify; resolving one gate is quiet and replacing a gate invalidates quit review",()=>{
 const transitions=new LifecycleTransitions();const p=project(true,2);transitions.update([p],prefs,false);
 const fewer=project(true,1);expect(transitions.update([fewer],prefs,false).notices).toEqual([]);
 const changed={...fewer,threads:[{...fewer.threads[0]!,gate_ids:["new-gate"]}]};expect(transitions.update([changed],prefs,false).notices).toHaveLength(1);expect(quitFingerprint([changed])).not.toBe(quitFingerprint([fewer]));
});
