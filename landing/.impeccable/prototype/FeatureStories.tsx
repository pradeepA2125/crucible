import { useState } from 'react';
import './features.css';
const GH = 'https://github.com/pradeepA2125/crucible';

export function AgentAvatar({ role = 0, x = 0, y = 0, scale = 1 }: { role?: number; x?: number; y?: number; scale?: number }) {
  const shirt = ['#8CDEEA', '#C3B0F5', '#F0BEA5'][role % 3];
  return <g transform={`translate(${x} ${y}) scale(${scale})`} stroke="#29223F" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M23 112 18 147 31 149 42 116M57 112 64 147 77 145 71 111" fill="#423659"/>
    <path d="m17 145-8 8c-3 4 1 6 8 5l17-2-1-9M64 145l-1 10 17 2c7 0 10-4 4-7l-9-6" fill="#EDE7F4"/>
    <path d="M22 68C9 75 4 92 8 111l14-3 5-20M65 68c14 4 21 19 20 35l-15 4-8-25" fill={shirt}/>
    <path d="M22 66c10-8 30-9 43 0l10 53c-20 8-40 8-59 0Z" fill={shirt}/>
    <path d="M36 57v13q9 8 17-1V56" fill="#E7B49D"/>
    <path d="M24 32C24 12 61 9 66 31v20C62 75 25 70 23 48Z" fill="#F3C6AE"/>
    <path d="M23 38c-13-18 5-37 24-29 21-4 30 13 18 30l-6-15c-9 7-21 8-31 1Z" fill={role === 1 ? '#6D5269' : '#29223F'}/>
    {role === 0 ? <><circle cx="35" cy="42" r="8" fill="none"/><circle cx="55" cy="42" r="8" fill="none"/><path d="M43 42h4m-19-3-7-2"/></> : <><path d="M34 40h1m19 0h1" strokeWidth="4"/>{role === 2 && <path d="M28 34q8-6 15 0m5-1 12 2"/>}</>}
    <path d="m45 43-2 7 4 1m-10 5q8 5 15-1" fill="none"/>
    {role === 1 ? <path d="M23 30 9 24l5-12 23-3M65 30l9-5" fill="#C3B0F5"/> : null}
    <path d="m31 87 8 7-8 7m22-14-8 14" fill="none" opacity=".7"/>
    <path d="M8 107c1 10 9 15 14 6l1-7M70 106c4 11 13 7 14-1" fill="#F3C6AE"/>
  </g>;
}
function Arrow() { return <svg className="arrow" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M5 12h14m-6-6 6 6-6 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>; }
function FeatureDetails({ summary, children }: { summary: string; children: React.ReactNode }) {
  return <details className="feature-details"><summary>{summary}<span aria-hidden="true">+</span></summary><div>{children}</div></details>;
}
const agentRoles = [
  { name: 'Scout', job: 'Investigate', text: 'A read-only specialist follows code and brings back source-grounded findings.', color: '#8CDEEA' },
  { name: 'Builder', job: 'Implement', text: 'A focused implementation agent works on its assigned files and reports what changed.', color: '#C3B0F5' },
  { name: 'Reviewer', job: 'Inspect', text: 'Another perspective checks the work and returns a report to the parent conversation.', color: '#F0BEA5' },
];
function AgentDispatch() {
  const [selected, select] = useState(0);
  const [sent, setSent] = useState(false);
  return <section className="feature-agents" id="agents" aria-labelledby="agents-title">
    <div className="feature-heading"><h2 id="agents-title">A little delegation.<br/><em>A lot more perspective.</em></h2><div><p>Give focused work to specialist agents without losing the thread. Investigations, implementation and reports come back to the same conversation.</p><a className="feature-link" href="#agent-walkthrough">Meet your agents <Arrow/></a></div></div>
    <div className={`dispatch-world ${sent ? 'is-dispatched' : ''}`} id="agent-walkthrough">
      <div className="dispatch-brief"><span className="brief-fold"/><span>Your brief</span><p>“Trace the change.<br/>Build the fix.<br/>Review the result.”</p><small>Illustrative assignment</small><button onClick={() => setSent(!sent)}>{sent ? 'Reset illustration' : 'Send the brief'}<Arrow/></button></div>
      <div className="dispatch-path" aria-hidden="true"><svg viewBox="0 0 700 110"><path d="M0 55C110 55 100 10 220 10s100 90 220 90 100-45 260-45"/><path className="scroll-brief" d="m0 0 16-5-7 16-2-8Z"/><path className="moving-brief" d="m0 0 16-5-7 16-2-8Z"/></svg><span>{sent ? 'Focused work. Reports returned.' : 'One request can open several perspectives.'}</span></div>
      <div className="agent-lineup" role="group" aria-label="Illustrative agent roles">{agentRoles.map((agent, i) => <button key={agent.name} className={`agent-character ${selected === i ? 'is-selected' : ''}`} aria-pressed={selected === i} onClick={() => select(i)} style={{ '--agent-color': agent.color } as React.CSSProperties}>
        <svg viewBox="0 0 160 190" aria-hidden="true"><ellipse cx="80" cy="177" rx="49" ry="7" fill="#0002"/><AgentAvatar role={i} x={34} y={8}/>{i === 0 && <g stroke="#8CDEEA" fill="none" strokeWidth="2"><circle cx="118" cy="56" r="14"/><path d="m128 66 12 12m-34-50 4-9m20 13 8-5"/></g>}{i === 1 && <g transform="rotate(9 122 110)" stroke="#29223F" strokeWidth="2"><rect x="99" y="90" width="40" height="48" rx="4" fill="#EDE7F4"/><path d="m106 107 6 5-6 5m18-10-5 10" fill="none"/></g>}{i === 2 && <g stroke="#29223F" strokeWidth="2"><rect x="110" y="72" width="33" height="43" rx="4" fill="#F0BEA5"/><path d="m117 87 4 4 10-9m-13 19h16" fill="none"/></g>}</svg>
        <strong>{agent.name}</strong><span>{agent.job}</span>
      </button>)}</div>
      <div className="agent-role-description" aria-live="polite"><span style={{ color: agentRoles[selected].color }}>{agentRoles[selected].job}</span><p>{agentRoles[selected].text}</p><span className="return-ticket">{sent ? 'Report back to the conversation' : 'Choose a character to explore a role'}</span></div>
    </div>
    <FeatureDetails summary="How agents fit into your workflow"><p>Delegate a bounded task, follow its activity, then use the returned report and changed-file summary. Specialist definitions can constrain tools and editing authority; read-only permissions propagate to children.</p><p>Agents operate within configured concurrency, depth, request budgets and provider access. Parallel work still needs coordination and stale-write checks.</p><a href={`${GH}#agents-and-teams`}>Read the agents documentation <Arrow/></a></FeatureDetails>
  </section>;
}
const officeSteps = [
  { name: 'Brief', board: 'One shared intention', notes: ['Goal', 'Files', 'Constraints'], text: 'A lead and specialist members start with a common goal. File ownership and constraints give the work a shape.' },
  { name: 'Discuss', board: 'A proposal, not a pile of chats', notes: ['Propose', 'Discuss', 'Revise'], text: 'Members deliberate on a shared board. The lead owns the proposal; an optional approval gate puts the plan in your hands.' },
  { name: 'Build', board: 'Separate desks. Connected work.', notes: ['Assign', 'Implement', 'Review'], text: 'Members work on assigned parts, report their results and review the outcome. Budgets, pauses and follow-up controls keep the team bounded.' },
];
function TeamOffice() {
  const [step, setStep] = useState(1);
  return <section className="feature-teams" id="teams" aria-labelledby="teams-title">
    <div className="office-heading"><div><h2 id="teams-title">Not just more agents.<br/><em>A place to work together.</em></h2><p>Teams bring a lead, a shared board and a reviewable proposal to work that needs more than one perspective.</p></div><a className="feature-link" href="#office-controls">Step inside the team room <Arrow/></a></div>
    <div className="office-scroll-frame"><div className="office-sticky-stage">
    <div className={`office-world office-step-${step}`}>
      <svg className="office-art" viewBox="0 0 1000 540" role="img" aria-label="Illustrated team office: three specialist agents at separate desks, connected by a shared proposal board">
        <path d="M55 445 500 255 945 445 500 530Z" fill="#CFC5E6"/>
        <path d="M55 445V125l445-90v220Z" fill="#E8E1F3"/><path d="M500 35 945 125v320L500 255Z" fill="#D9D0EA"/>
        <path d="M55 445 500 255 945 445M500 35v220" fill="none" stroke="#B6A6CF" strokeWidth="2"/>
        <g stroke="#9383AD" strokeWidth="2" fill="none"><path d="m98 415 402-164 402 164M180 474l400-165M325 509l402-166M95 358l405-159 405 159" opacity=".2"/></g>
        <g transform="translate(99 126) skewY(-11)"><rect width="180" height="137" rx="6" fill="#8CDEEA" stroke="#71618D" strokeWidth="3"/><path d="M90 0v137M0 68h180" stroke="#EAF8FB" strokeWidth="5"/><path d="m18 45 30-15 17 8 44-17 25 11" fill="none" stroke="#ECFAFA" strokeWidth="3"/><path d="m22 111 22-12 33 6 21-17 48 12" fill="none" stroke="#ECFAFA" strokeWidth="3"/></g>
        <g className="office-proposal" transform="translate(548 90) skewY(11)"><rect width="266" height="148" rx="8" fill="#FBF8FF" stroke="#79668D" strokeWidth="3"/><path d="M24 32h215" stroke="#C7BCD8" strokeWidth="2"/>{officeSteps[step].notes.map((note, i) => <g key={note} transform={`translate(${22 + i * 78} 49)`}><rect width="65" height="65" rx="2" fill={['#C3B0F5', '#B8E2E8', '#F0BEA5'][i]}/><path d="M12 31h39M12 43h28" stroke="#665477" strokeWidth="2" opacity=".4"/><text x="10" y="20" fontSize="10" fill="#392D4B" stroke="none">{note}</text></g>)}</g>
        <g transform="translate(825 282)"><path d="M12 79h50l-7 65H20Z" fill="#B6A4D0" stroke="#675477" strokeWidth="2"/><path d="M36 84C-5 54 1 18 18 15c24-5 19 52 18 69 3-45 14-85 33-71 19 15-10 58-33 71Z" fill="#7FAD9C" stroke="#507568" strokeWidth="2"/><path d="M36 84V35" stroke="#507568" strokeWidth="2"/></g>
        <g className="office-team-lines" fill="none" stroke="#7A609E" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path className="office-connection" pathLength="1" d="M232 370C280 270 440 290 592 218"/><path className="office-connection" pathLength="1" d="M520 325C541 274 602 260 655 231"/><path className="office-connection" pathLength="1" d="M726 426C852 342 827 285 758 249"/></g>
        <g className="office-person office-scout"><AgentAvatar role={0} x={155} y={247} scale={.88}/><g stroke="#68557E" strokeWidth="2.5" strokeLinejoin="round"><path d="m108 365 113-42 116 43-115 44Z" fill="#B5A0D3"/><path d="M108 365v17l114 44v-16m115-44v17l-115 43" fill="#9A83B9"/><path d="M128 390v70m187-68v54m-93-20v67" fill="none"/><path d="m170 336 63-23 36 42-65 24Z" fill="#29223F"/><path d="m180 339 48-18 25 30-47 17Z" fill="#8CDEEA"/><path d="m204 379 65-24 28 10-65 25Z" fill="#E3D9EE"/></g></g>
        <g className="office-person office-builder"><AgentAvatar role={1} x={449} y={203} scale={.94}/><g stroke="#68557E" strokeWidth="2.5" strokeLinejoin="round"><path d="m394 331 111-42 113 41-114 44Z" fill="#C9B3E8"/><path d="M394 331v17l110 43v-17m114-44v17l-114 44" fill="#AA91C6"/><path d="M413 355v65m184-65v68m-93-32v64" fill="none"/><path d="m459 300 61-23 34 41-61 24Z" fill="#29223F"/><path d="m469 301 46-17 24 30-45 18Z" fill="#C3B0F5"/><path d="m493 342 61-24 28 10-61 24Z" fill="#E3D9EE"/></g></g>
        <g className="office-person office-reviewer"><AgentAvatar role={2} x={661} y={307} scale={.9}/><g stroke="#68557E" strokeWidth="2.5" strokeLinejoin="round"><path d="m604 430 111-42 114 43-114 43Z" fill="#DEC2CE"/><path d="M604 430v17l111 44v-17m114-43v17l-114 43" fill="#BE9BAC"/><path d="M625 457v46m181-48v44m-91-8v31" fill="none"/><path d="m668 399 61-23 34 41-61 24Z" fill="#29223F"/><path d="m678 400 46-17 24 30-45 18Z" fill="#F0BEA5"/><path d="m702 441 61-24 28 10-61 24Z" fill="#E3D9EE"/></g></g>
        <g transform="translate(350 112) rotate(-8)"><path d="M0 0h91v59H49L34 74V59H0Z" fill="#F0BEA5" stroke="#79668D" strokeWidth="2"/><path d="M17 20h56M17 32h38" stroke="#79668D" strokeWidth="2"/></g>
        <path d="m316 288 10-8m7 20 15-2m-5-21 5-11M642 268l12 5m-7-17 9-5" fill="none" stroke="#8C78A4" strokeWidth="3" strokeLinecap="round"/>
      </svg>
      <div className="office-plaque"><span className="plaque-dot"/>Team room <small>Illustrative workspace</small></div>
      <div className="office-board-caption" aria-live="polite">{officeSteps[step].board}</div>
    </div>
    <a className="office-handoff" href="#rewind"><svg viewBox="0 0 36 36" fill="none" aria-hidden="true"><path d="M10 10h15v18H10zM14 6h15v18M7 18H3m0 0 4-4m-4 4 4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg><span>Another approach?<strong>Pick up at a checkpoint.</strong></span><Arrow/></a>
    </div></div>
    <div className="office-controls" id="office-controls"><div role="group" aria-label="Team workflow illustration">{officeSteps.map((s, i) => <button key={s.name} aria-pressed={step === i} onClick={() => setStep(i)}>{s.name}</button>)}</div><p aria-live="polite">{officeSteps[step].text}</p></div>
    <FeatureDetails summary="What a team adds beyond delegation"><p>Agents return focused reports. Teams add shared deliberation, a designated lead, proposals, assignments and review. You can follow the board, provide feedback and return to a completed team with a follow-up.</p><p>Teams pause when budgets or provider limits intervene. Agreement does not grant blanket permission for commands or external tools.</p><a href={`${GH}#agents-and-teams`}>Read the team workflow documentation <Arrow/></a></FeatureDetails>
  </section>;
}
function RewindWorkbench() {
  const [checkpoint, setCheckpoint] = useState(1);
  const [preview, setPreview] = useState(false);
  const [restored, setRestored] = useState(false);
  const names = ['Start here', 'First approach', 'Try another way'];
  return <section className="feature-rewind" id="rewind" aria-labelledby="rewind-title"><div className="rewind-copy"><h2 id="rewind-title">Good ideas take detours.<br/><em>Keep a way back.</em></h2><p>Rewind a conversation to a captured checkpoint—and restore the agent-edited files recorded with it. Bring the prompt back and try a different approach.</p><FeatureDetails summary="What rewind restores—and what it doesn't"><p>Preview the affected messages and captured files before confirming in Crucible. Rewind restores recorded file states, removes captured newly created files and restores the associated conversation state.</p><p>It is not a full repository snapshot or universal undo. Commands, network effects and live processes are not reversed. Uncaptured, oversized or failed file restorations are reported; active work may block rewind.</p><a href={`${GH}#rewind`}>Read the rewind documentation <Arrow/></a></FeatureDetails></div>
    <div className={`rewind-workbench ${restored ? 'is-restored' : ''}`}><div className="rewind-paper"><svg className="rewind-arrow-art" viewBox="0 0 310 95" fill="none" aria-hidden="true"><path d="M281 70C249 5 100-2 40 49m0 0 6-31m-6 31 32-1" stroke="#6C548D" strokeWidth="3" strokeLinecap="round"/><path d="m236 17 9-9m7 22 15-2" stroke="#A48BBB" strokeWidth="2" strokeLinecap="round"/></svg><div className="rewind-person"><svg viewBox="0 0 140 190" aria-hidden="true"><AgentAvatar role={2} x={19} y={10}/></svg></div><div className="checkpoint-strip" role="group" aria-label="Illustrative rewind checkpoints"><span className="checkpoint-traveller" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none"><path d="m8 6-6 6 6 6m8-12-6 6 6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg></span>{names.map((name, i) => <button key={name} onClick={() => { setCheckpoint(i); setPreview(false); setRestored(false); }} aria-pressed={checkpoint === i}><span className="checkpoint-node"/><small>Checkpoint {i + 1}</small><strong>{name}</strong></button>)}</div><div className="rewind-files"><div className={`file-sheet ${restored ? 'prior-state' : ''}`}><span>src / approach.ts</span><code>{restored ? 'captured file state' : 'current working version'}</code><i/><i/><i/><div>{restored ? 'Illustrated checkpoint restored' : 'Captured before the agent edit'}</div></div><div className="prompt-note"><span>Your prompt</span><p>{names[checkpoint]}</p><svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m5 5 14 7-14 7 3-7-3-7Zm3 7h11" stroke="currentColor" strokeWidth="1.5"/></svg></div></div></div>
      <div className="rewind-demo-actions"><button className="demo-primary" onClick={() => { if (preview) { setRestored(true); setPreview(false); } else { setPreview(true); setRestored(false); } }}>{preview ? 'Restore illustration' : 'Preview rewind'}<Arrow/></button><p role="status">{restored ? `Illustration returned to “${names[checkpoint]}”. No real files were changed.` : preview ? `Return to “${names[checkpoint]}”. Only the illustrated checkpoint will change.` : 'Choose a checkpoint to try the illustration.'}</p></div><p className="demo-caption">Interactive illustration only. No conversation or repository data is accessed.</p>
    </div>
  </section>;
}
function ContextStory() {
  const [quality, setQuality] = useState<'estimated' | 'reported'>('estimated');
  return <section className="feature-context" id="context"><div><h2>Know what’s in context.<br/><em>See where the work goes.</em></h2><p>Request occupancy, thread totals and agent/team usage now have their own place. See what’s estimated, what the provider reported and what hasn’t been measured.</p><FeatureDetails summary="Context is not your account balance"><p>The context view describes main-model request input against known capacity. Thread usage is a separate cumulative breakdown. Neither is a billing invoice, remaining plan quota or prediction of the next request.</p><a href={`${GH}#usage-and-context`}>Read usage and context documentation <Arrow/></a></FeatureDetails></div><div className="context-envelope"><div className="envelope-top"><span>One model request</span><span className="envelope-stamp">Context</span></div><div className="context-papers"><span>Instructions</span><span>Conversation</span><span>Tools + evidence</span></div><div className="quality-control" role="group" aria-label="Illustrated context measurement source"><button aria-pressed={quality === 'estimated'} onClick={() => setQuality('estimated')}>Estimated</button><button aria-pressed={quality === 'reported'} onClick={() => setQuality('reported')}>Provider reported</button></div><p aria-live="polite">{quality === 'estimated' ? 'A clearly marked estimate—not a made-up exact reading.' : 'A provider measurement replaces the estimate when available.'}</p><div className="usage-ledger"><strong>Separate thread ledger</strong><span>Main conversation</span><span>Agents</span><span>Teams</span></div><small>Illustrative labels. No fabricated usage numbers.</small></div></section>;
}
function ToolkitStory() {
  const [active, setActive] = useState(0);
  const tools = [
    { title: 'Image context', description: 'Attach or paste screenshots and images. A supported vision model can use them alongside your request.', note: 'Image understanding depends on the model. This is not image generation.', icon: 'image' },
    { title: 'Web research', description: 'Search public sources and fetch pages with native permission-gated tools, without first configuring an MCP server.', note: 'Queries and URLs leave your machine. Fetch is text retrieval, not a full browser.', icon: 'web' },
    { title: 'Background sessions', description: 'Keep an approved server or interactive command running. Send input, poll output and stop the session from the workflow.', note: 'Sessions are real processes, not an execution sandbox. Rewind does not undo their effects.', icon: 'terminal' },
  ];
  return <section className="feature-toolkit" id="toolkit"><div className="toolkit-art" aria-hidden="true"><div className={`brief-image ${active === 0 ? 'tool-active' : ''}`}><svg viewBox="0 0 220 150"><rect x="3" y="3" width="214" height="144" rx="10" fill="#BBDCE8" stroke="#625077" strokeWidth="3"/><circle cx="158" cy="40" r="16" fill="#F5CBAD"/><path d="m3 116 61-55 51 46 30-28 72 68H3Z" fill="#879BBF"/><path d="m3 133 60-49 44 41 38-22 72 44H3Z" fill="#665B88"/></svg><span>Visual context</span></div><div className={`brief-web ${active === 1 ? 'tool-active' : ''}`}><span>Source note</span><p>Read the page.<br/>Keep the evidence.</p><div/><div/><div/></div><div className={`brief-terminal ${active === 2 ? 'tool-active' : ''}`}><span>Interactive session</span><code>$ dev-server<br/><b>listening…</b><br/>stdin ▌</code></div><svg className="toolkit-pencil" viewBox="0 0 34 240"><path d="M8 6h18v186l-9 30-9-30Z" fill="#F0BEA5" stroke="#302640" strokeWidth="2"/><path d="M8 29h18M17 29v164m-9-1h18m-13 18h8" fill="none" stroke="#765B73" strokeWidth="2"/></svg></div><div><h2>More than words<br/><em>in a chat box.</em></h2><p>Bring visual context, outside evidence and live execution into the work.</p><div className="toolkit-tabs" role="group" aria-label="Current context and execution capabilities">{tools.map((tool, i) => <button key={tool.title} aria-pressed={active === i} onClick={() => setActive(i)}>{tool.title}</button>)}</div><div className="toolkit-description" aria-live="polite"><h3>{tools[active].title}</h3><p>{tools[active].description}</p><small>{tools[active].note}</small></div></div></section>;
}
export function FeatureStories() {
  return <><section className="new-chapter" id="whats-new"><div><h2>More ways to move<br/><em>the work forward.</em></h2><p>A closer look at major capabilities added since the actual v0.5.4 tag. Current-source features—not a claim that every change is in a published release.</p></div><nav aria-label="Explore newer Crucible features"><a href="#agents">Meet the agents <Arrow/></a><a href="#teams">Visit the team room <Arrow/></a><a href="#rewind">Try the rewind illustration <Arrow/></a><a href="#connections">Connect your plan <Arrow/></a><a href="#context">Understand context <Arrow/></a><a href="#toolkit">Explore the toolkit <Arrow/></a></nav></section><AgentDispatch/><TeamOffice/><RewindWorkbench/><ContextStory/><ToolkitStory/></>;
}
