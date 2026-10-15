import { useEffect, useRef, useState } from 'react';
import type { EdgeKind, FileDetail, SceneHandle, SpaceModel } from '../../../apps/vscode-extension/webview-ui/src/graph/types';
import './axon.css';
const GH = 'https://github.com/pradeepA2125/crucible';
const assets: Record<string,string> = {
 'axon-capture.json': new URL('./assets/axon-capture.json', import.meta.url).href,
 'axon-demo.webm': new URL('./assets/axon-demo.webm', import.meta.url).href,
 'axon-preview.png': new URL('./assets/axon-preview.png', import.meta.url).href,
};
const asset = (name: string) => assets[name];
type Capture = { model: SpaceModel; details: Record<string, FileDetail>; provenance: { capturedAt: string; note: string } };
let capturePromise: Promise<Capture> | undefined;
const loadCapture = () => capturePromise ??= fetch(asset('axon-capture.json')).then(r => { if (!r.ok) throw new Error('Capture unavailable'); return r.json(); }).catch(e => { capturePromise = undefined; throw e; });
function Arrow(){return <svg className="arrow" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M5 12h14m-6-6 6 6-6 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>}
export function AxonSection({ suspended = false }: { suspended?: boolean }) {
 const host = useRef<HTMLDivElement>(null), canvas = useRef<HTMLCanvasElement>(null), scene = useRef<SceneHandle | null>(null);
 const [active,setActive]=useState(false),[visible,setVisible]=useState(false),[hidden,setHidden]=useState(document.hidden);
 const [data,setData]=useState<Capture | null>(null),[status,setStatus]=useState('idle'),[error,setError]=useState('');
 const [selected,setSelected]=useState<string | null>(null),[symbols,setSymbols]=useState(false),[query,setQuery]=useState('');
 const [layers,setLayers]=useState<Record<EdgeKind,boolean>>({Imports:true,Calls:true,Inherits:true,References:false});
 const [epoch,setEpoch]=useState(0),[recording,setRecording]=useState(false);
 const detail=selected?data?.details[selected]:null;
 const [selectedPackage,setSelectedPackage]=useState<string|null>(null);
 useEffect(()=>{const observer=new IntersectionObserver(([entry])=>setVisible(entry.isIntersecting),{threshold:.08});if(host.current)observer.observe(host.current);const onVisibility=()=>setHidden(document.hidden);document.addEventListener('visibilitychange',onVisibility);return()=>{observer.disconnect();document.removeEventListener('visibilitychange',onVisibility)}},[]);
 useEffect(()=>{if(suspended||!visible||hidden)setRecording(false)},[suspended,visible,hidden]);
 useEffect(()=>{if(!active)return;let dead=false;setStatus('loading');setError('');loadCapture().then(d=>{if(!dead){setData(d);setStatus('ready')}}).catch(()=>{if(!dead){setStatus('error');setError('The captured graph could not load. Try again or watch the existing recording.')}});return()=>{dead=true}},[active]);
 useEffect(()=>{
  if(!active||!data||!visible||hidden||suspended||!canvas.current)return;
  let dead=false;let current:SceneHandle|null=null;setStatus('loading');
  void(async()=>{try{
   const [{createGraphScene},{computeLayout},{PALETTES}]=await Promise.all([
    import('../../../apps/vscode-extension/webview-ui/src/graph/scene/graph-scene'),
    import('../../../apps/vscode-extension/webview-ui/src/graph/layout'),
    import('../../../apps/vscode-extension/webview-ui/src/graph/palette')
   ]);
   if(dead||!canvas.current)return;
   current=createGraphScene(canvas.current,{
    onPickStar:id=>{setSelected(id);setSymbols(false)},
    onPickPackage:pkg=>{setSelectedPackage(pkg);setSelected(null);setSymbols(false)},
    onPickSatellite:()=>{},onBackgroundClick:()=>{setSelectedPackage(null);setSelected(null);setSymbols(false)}
   },{...PALETTES.void,nebulaAlpha:0.025});
   current.setSpace(data.model,computeLayout(data.model));scene.current=current;
   setStatus('live');setEpoch(e=>e+1);
  }catch{if(current)current.dispose();if(!dead){setStatus('error');setError('WebGL is unavailable here. The static capture and repository details remain available.')}}})();
  return()=>{dead=true;current?.dispose();scene.current=null};
 },[active,data,visible,hidden,suspended]);
 useEffect(()=>{const s=scene.current;if(!s||!data)return;s.setLayers(layers);if(!selected){if(selectedPackage){s.setFocus({level:1,pkg:selectedPackage});s.framePackage(selectedPackage)}else{s.setFocus({level:0});s.resetCamera()}return}const star=data.model.stars.find(x=>x.id===selected);const d=data.details[selected];if(!star||!d)return;s.setFocus(symbols?{level:3,pkg:star.pkg,fileId:selected,symbolId:null}:{level:2,pkg:star.pkg,fileId:selected});s.showFileTrace(d);if(symbols)s.showSatellites(d);s.flyToStar(selected,symbols?260:700)},[selected,selectedPackage,symbols,layers,data,epoch]);
 const choose=(id:string)=>{setSelectedPackage(null);setSelected(id);setSymbols(false)};
 const traceHub=()=>{const hub=data?.model.stars.filter(s=>s.isHub).sort((a,b)=>b.inDeg+b.outDeg-a.inDeg-a.outDeg)[0]??data?.model.stars[0];if(hub)choose(hub.id)};
 const follow=()=>{const edge=detail?.edges.find(e=>e.crossPackage)??detail?.edges[0];if(!edge)return;const target=edge.otherFile;const arrive=()=>choose(target);if(scene.current)scene.current.rideToStar(target,arrive);else arrive()};
 const results=data?.model.stars.filter(s=>s.id.toLowerCase().includes(query.toLowerCase())).slice(0,6)??[];
 return <section className="axon-section" id="axon" aria-labelledby="axon-title">
  <div className="axon-heading"><h2 id="axon-title">Meet Axon.<br/><em>The lay of your repository.</em></h2><div><p>Not a folder tree. A connected space of packages, files and symbols. See the relationships behind the code—and follow them somewhere useful.</p><a href="#repository-map" className="feature-link">How Crucible gets its bearings <Arrow/></a></div></div>
  <div className="axon-viewport" ref={host}>
   <div className="axon-media" onWheelCapture={event=>event.stopPropagation()}>
    <img className="axon-poster" src={asset('axon-preview.png')} alt="Axon’s renderer tracing a file and its relationships in a captured subset of Crucible’s repository" loading="lazy"/>
    {recording&&<video className="axon-recording" src={asset('axon-demo.webm')} poster={asset('axon-preview.png')} aria-label="Recorded Axon exploration: repository overview, file trace and symbol satellites" autoPlay loop muted playsInline preload="none" onError={()=>{setRecording(false);setError('The recording could not load. You can still explore the graph in 3D.')}}/>}
    <canvas ref={canvas} className={`axon-real-canvas ${active&&!recording&&!suspended&&status==='live'?'is-live':''}`} aria-label="Interactive Axon dependency-space demonstration"/>
   </div>
   <div className="axon-nameplate"><strong>Axon</strong><span>Dependency space</span><small>{active&&!suspended&&status==='live'?'Actual product renderer · captured repository subset':recording?'Recorded renderer demo · captured repository subset':'Actual renderer capture · repository subset'}</small></div>
   <div className="axon-stage-actions"><button aria-expanded={active} aria-controls="axon-explorer" onClick={()=>{setActive(!active);setRecording(false);setError('')}}>{active?'Close interactive scene':'Explore in 3D'}<Arrow/></button><button onClick={()=>{setActive(false);setRecording(!recording);setError('')}} disabled={suspended}>{recording?'Stop recording':'Watch Axon recording'}</button></div>
   {!active&&!recording&&<div className="axon-invitation"><p>Follow a connection.<br/><em>Find the next piece.</em></p><span>Activate the actual Axon renderer to orbit, focus a file and enter its symbols.</span></div>}
   {active&&<div className="axon-tools" id="axon-explorer"><div className="axon-tool-row"><button onClick={()=>{setSelectedPackage(null);setSelected(null);setSymbols(false);scene.current?.setFocus({level:0});scene.current?.resetCamera()}}>Overview</button><button onClick={traceHub} disabled={!data}>Trace a hub</button><button onClick={()=>setSymbols(!symbols)} disabled={!selected}>{symbols?'Leave symbols':'Enter symbols'}</button><button onClick={follow} disabled={!detail?.edges.length}>Follow connection</button></div><label className="axon-search">Find a captured file<input value={query} onChange={e=>setQuery(e.target.value)} placeholder="Search source paths"/></label>{data&&<div className="axon-file-options">{results.map(s=><button key={s.id} aria-pressed={selected===s.id} onClick={()=>choose(s.id)} title={s.id}>{s.id.split('/').slice(-2).join('/')}</button>)}</div>}<div className="axon-layers" role="group" aria-label="Axon edge layers">{(['Imports','Calls','Inherits','References'] as EdgeKind[]).map(kind=><button key={kind} aria-pressed={layers[kind]} onClick={()=>setLayers({...layers,[kind]:!layers[kind]})}>{kind}</button>)}</div></div>}
   {(active||error)&&<div className="axon-status" role="status">{error||suspended?'':status==='loading'?'Loading the captured graph…':status==='live'?'Drag to orbit. Page scrolling stays with the page.':''}{suspended?'Motion paused. Browse the captured file details below.':error}</div>}
   {detail&&active&&<div className="axon-selected"><span>Focused file</span><strong>{detail.fileId}</strong><p>{symbols?'Symbols exposed as satellites in the 3D scene.':'Its indexed incoming and outgoing connections are traced in the scene.'}</p></div>}
  </div>
  <p className="axon-provenance">The 3D demo uses Axon’s actual renderer with a captured subset of tracked Crucible source metadata—not a live index or benchmark. The preview and recording were captured from this renderer demo; they are not a live index or a recording of the editor UI.</p>
  {active&&detail&&<div className="axon-accessible-detail"><div><h3>{symbols?'Inside the file':'Follow its relationships'}</h3><p>{detail.fileId}</p></div><div>{symbols?detail.symbols.slice(0,12).map(s=><span key={s.id}><b>{s.kind}</b> {s.name} <small>line {s.line}</small></span>):detail.edges.slice(0,8).map((e,i)=><button key={i} onClick={()=>choose(e.otherFile)}><b>{e.dir==='out'?'Uses':'Used by'} / {e.kind}</b><span>{e.otherFile}</span><Arrow/></button>)}</div></div>}
  <div className="repository-map" id="repository-map"><div className="repository-map-copy"><h3>Start with a map.<br/><em>Follow the evidence.</em></h3><p>The Rust indexer turns source structure into a symbol graph. Parsing and language-server resolution connect calls, imports, references and type relationships, giving Crucible a way into unfamiliar code.</p><p>That map informs an initial repository seed: structure, outlines, selected excerpts and connected files. Searches, source reads and graph queries then guide the work.</p></div><ol className="index-seed-flow"><li><span>Rust indexer</span><p>Symbols and relationships</p><div className="index-relations"><i>Calls</i><i>Imports</i><i>References</i><i>Inherits</i><i>Implements</i></div></li><li><span>Initial context</span><p>A grounded starting point for the thread</p><div className="seed-pages"><span>Repository structure</span><span>Relevant outlines + excerpts</span><span>Connected files</span></div></li><li><span>Continued exploration</span><p>Read. Search. Follow a connection.</p><code>read_file · search_code · query_graph</code></li></ol></div>
  <details className="feature-details axon-details"><summary>Is the initial seed rebuilt for every query?<span aria-hidden="true">+</span></summary><div><p>No. In the chat controller, the goal-informed retrieval seed is computed once per thread and reused, including across a backend restart. It is a starting map, not a fresh read of every file on every message.</p><p>Subsequent source reads, search and graph queries provide further evidence. Index freshness, supported languages and available language servers affect coverage. The graph does not replace reading the implementation.</p><a href={`${GH}#code-intelligence`}>Read repository intelligence documentation <Arrow/></a></div></details>
 </section>;
}
