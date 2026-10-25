import { useEffect, useRef, useState } from "react";
import { vscode } from "../vscodeApi";
/** Chromium finds input values too. Temporarily exclude non-content branches from
 * native Find while it computes the current rendered transcript/preview matches.
 * Never change display/layout or mutate source text; restore existing inert state. */
export function renderedFindScope(doc: Document): () => void {
  const targets=[...doc.querySelectorAll<HTMLElement>('[data-testid="transcript-column"],#workspace-content')];
  const ancestors=new Set<Element>();for(const target of targets)for(let node:Element|null=target;node;node=node.parentElement)ancestors.add(node);
  const changed:HTMLElement[]=[];
  const visit=(node:HTMLElement)=>{if(targets.includes(node))return;if(!ancestors.has(node)){if(!node.hasAttribute("inert")){node.setAttribute("inert","");changed.push(node);}return;}for(const child of node.children)if(child instanceof HTMLElement)visit(child);};
  for(const node of doc.body.children)if(node instanceof HTMLElement)visit(node);
  return ()=>{for(const node of changed)node.removeAttribute("inert");};
}
export function FindBar({open,target,close}:{open:boolean;target:string;close():void}) {
  const input=useRef<HTMLInputElement>(null),previous=useRef<HTMLElement|null>(null),request=useRef("");
  const [query,setQuery]=useState(""),[result,setResult]=useState("");
  const releaseScope=useRef<()=>void>(()=>{}),deadline=useRef<ReturnType<typeof setTimeout>|undefined>(undefined),refocus=useRef(false);
  const release=()=>{clearTimeout(deadline.current);releaseScope.current();releaseScope.current=()=>{};};
  const clear=()=>{release();request.current="";setQuery("");setResult("");vscode.postMessage({type:"desktop/findStop"} as never);};
  useEffect(()=>{clear();},[target]);
  useEffect(()=>{const changed=()=>clear();window.addEventListener("desktop/findTargetChanged",changed);return()=>window.removeEventListener("desktop/findTargetChanged",changed);},[]);
  useEffect(()=>{
    if (!open) {clear();return;}
    previous.current=document.activeElement instanceof HTMLElement ? document.activeElement : null; input.current?.focus();
    const escape=(event:KeyboardEvent)=>{if(event.key==="Escape" && !event.defaultPrevented && !document.querySelector('[role="dialog"][aria-modal="true"]')){event.preventDefault();event.stopPropagation();close();}};
    window.addEventListener("keydown",escape);
    return()=>{window.removeEventListener("keydown",escape);release();request.current="";vscode.postMessage({type:"desktop/findStop"} as never);if(previous.current?.isConnected) previous.current.focus();};
  },[open,close]);
  const search=(forward=true,next=false)=>{
    release();if(!query.trim()){request.current="";setResult("");vscode.postMessage({type:"desktop/findStop"} as never);return;}
    const requestId=crypto.randomUUID();request.current=requestId;refocus.current=document.activeElement===input.current;releaseScope.current=renderedFindScope(document);setResult("Finding…");
    deadline.current=setTimeout(()=>{if(request.current!==requestId)return;request.current="";release();setResult("Find took too long. Try again.");if(refocus.current)input.current?.focus();},2000);
    vscode.postMessage({type:"desktop/find",requestId,query,forward,next} as never);
  };
  useEffect(()=>{if(!open)return;const timer=setTimeout(()=>search(),140);return()=>clearTimeout(timer);},[query,open]);
  useEffect(()=>{const receive=(event:MessageEvent)=>{const m=event.data;if(m?.type!=="desktop/findResult" || !request.current || m.requestId!==request.current)return;if(m.error || m.finalUpdate!==false){release();if(refocus.current)input.current?.focus();}setResult(typeof m.error==="string" ? m.error : `${m.ordinal ?? 0} of ${m.matches ?? 0}`);};window.addEventListener("message",receive);return()=>window.removeEventListener("message",receive);},[]);
  if(!open)return null;
  return <div role="search" aria-label="Find in visible view" className="desktop-find"><input ref={input} aria-label="Find text" maxLength={512} value={query} onChange={e=>setQuery(e.target.value)} onKeyDown={e=>{if(e.key==="Enter"){e.preventDefault();search(!e.shiftKey,true);}}}/><button type="button" disabled={!query.trim()} aria-label="Previous match" onClick={()=>search(false,true)}>↑</button><button type="button" disabled={!query.trim()} aria-label="Next match" onClick={()=>search(true,true)}>↓</button><span role="status">{result}</span><button type="button" aria-label="Close find" onClick={close}>×</button><small>Visible transcript and preview only · Source search covers the rendered page (up to 2,000 lines).</small></div>;
}
