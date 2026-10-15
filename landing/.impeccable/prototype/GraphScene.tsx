import { useEffect, useRef } from 'react';

type Node = { x:number; y:number; z:number; cluster:number };
const colors = ['#b59afa','#72d9e7','#d7a1ed','#9baceb','#8ad8c0'];
const labels = ['Patch staging','Repository search','Agents & teams','VS Code','Model providers'];
const centers = [[.1,.22,0],[1.9,-.85,.8],[2.4,.8,-.5],[-.9,-1.1,.5],[-1.9,.65,-.7]];
const nodes:Node[]=[];
let seed=1307;
function random(){seed=(seed*16807)%2147483647;return (seed-1)/2147483646;}
centers.forEach((c,cluster)=>{for(let i=0;i<30;i++){const a=random()*Math.PI*2;const b=random()*2-1;const r=.18+random()*.87;nodes.push({x:c[0]+Math.cos(a)*r,y:c[1]+Math.sin(a)*r*.7,z:c[2]+b*.85,cluster});}});
const edges:{a:number;b:number;cross:boolean}[]=[];
nodes.forEach((n,i)=>{const nearest=nodes.map((m,j)=>({j,d:(m.x-n.x)**2+(m.y-n.y)**2+(m.z-n.z)**2})).filter(x=>x.j!==i&&nodes[x.j].cluster===n.cluster).sort((a,b)=>a.d-b.d);nearest.slice(0,2).forEach(x=>{if(x.j>i)edges.push({a:i,b:x.j,cross:false});});if(i%9===0)edges.push({a:i,b:(i+34)%nodes.length,cross:true});});

export function GraphScene({progress=0,hero=false,still=false}:{progress?:number;hero?:boolean;still?:boolean}){
 const canvas=useRef<HTMLCanvasElement>(null);
 const state=useRef({progress,pointerX:0,pointerY:0});
 const drawRef=useRef<()=>void>(()=>{});
 useEffect(()=>{state.current.progress=progress;drawRef.current();},[progress]);
 useEffect(()=>{
  const el=canvas.current;if(!el)return;const ctx=el.getContext('2d');if(!ctx)return;
  let width=0,height=0,raf=0;
  const draw=()=>{raf=0;if(!width||!height)return;const {progress:p,pointerX:px,pointerY:py}=state.current;
   ctx.clearRect(0,0,width,height);
   const focus=hero?0:Math.min(p*1.7,1);const fade=hero?1:Math.max(.12,1-Math.max(0,p-.35)*1.55);
   const rx=.35+(still?0:py*.035),ry=-.25+(still?0:px*.055)-focus*.38;
   const scale=Math.min(width/7.5,height/4.4)*(1+focus*.4);
   const cx=width*(hero?.57:.53),cy=height*(hero?.53:.5);
   const project=(n:Node)=>{const x=n.x*Math.cos(ry)+n.z*Math.sin(ry);const z=-n.x*Math.sin(ry)+n.z*Math.cos(ry);const y=n.y*Math.cos(rx)-z*Math.sin(rx);const depth=1+z*.055;return {x:cx+x*scale*depth-focus*scale*.45,y:cy+y*scale*depth,z,r:(1.5+(z+2)*.42)*Math.min(width/700,1.2)};};
   const pts=nodes.map(project);
   edges.forEach(e=>{const a=pts[e.a],b=pts[e.b];const active=nodes[e.a].cluster===0&&!e.cross;ctx.strokeStyle=active?`rgba(190,161,255,${.35*fade})`:`rgba(139,156,213,${(e.cross?.14:.2)*fade})`;ctx.lineWidth=active?1:.65;ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke();});
   pts.forEach((n,i)=>{const selected=nodes[i].cluster===0;ctx.globalAlpha=fade*(selected?1:.6);ctx.fillStyle=colors[nodes[i].cluster];ctx.beginPath();ctx.arc(n.x,n.y,Math.max(.7,n.r),0,Math.PI*2);ctx.fill();if(i%13===0){ctx.globalAlpha=fade*.09;ctx.beginPath();ctx.arc(n.x,n.y,n.r*4,0,Math.PI*2);ctx.fill();}});ctx.globalAlpha=fade;
   centers.forEach((c,i)=>{const n=project({x:c[0],y:c[1]-.84,z:c[2],cluster:i});ctx.font='12px "Crucible Sans", sans-serif';ctx.fillStyle=colors[i];ctx.fillText(labels[i],n.x-28,n.y);});
   if(hero||p<.6){const selected=pts[7];ctx.globalAlpha=fade;ctx.strokeStyle='#eee4ff';ctx.lineWidth=1;ctx.strokeRect(selected.x-7,selected.y-7,14,14);ctx.fillStyle='#e9e0fb';ctx.font='11px "Crucible Mono", monospace';ctx.fillText('edit_session.py',selected.x+15,selected.y+4);}
   ctx.globalAlpha=1;
  };
  const schedule=()=>{if(!raf)raf=requestAnimationFrame(draw);};drawRef.current=schedule;
  const resize=()=>{const r=el.getBoundingClientRect();width=r.width;height=r.height;const dpr=Math.min(window.devicePixelRatio||1,1.8);el.width=Math.round(width*dpr);el.height=Math.round(height*dpr);ctx.setTransform(dpr,0,0,dpr,0,0);schedule();};
  const observer=new ResizeObserver(resize);observer.observe(el);
  const move=(e:PointerEvent)=>{if(still)return;const r=el.getBoundingClientRect();state.current.pointerX=(e.clientX-r.left)/r.width-.5;state.current.pointerY=(e.clientY-r.top)/r.height-.5;schedule();};
  const parent=el.parentElement;parent?.addEventListener('pointermove',move);resize();
  return()=>{observer.disconnect();parent?.removeEventListener('pointermove',move);cancelAnimationFrame(raf);drawRef.current=()=>{};};
 },[hero,still]);
 return <canvas ref={canvas} className="dependency-canvas" aria-hidden="true"/>;
}
