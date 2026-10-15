import { lazy, Suspense, useRef, useState } from "react";
import { useReducedMotion } from "framer-motion";
import { ThemeProvider, useTheme, type ThemeId } from "./theme";
import { LogoGlyph } from "./components/Nav";
import { GITHUB_URL } from "./content";
import "./story.css";

const Sculpture = lazy(() => import("./components/Sculpture"));
const chapters = [
  { id: "explore", label: "01 / Understand", title: <>Find the shape<br />of your <em>code.</em></>, text: "Start with the repository, not a guess. Text search, semantic retrieval and a symbol graph help Crucible follow relationships. Reading the implementation is still the source of truth.", detail: "Explore → read → reason", link: "code-intelligence" },
  { id: "collaborate", label: "02 / Work together", title: <>One intention.<br /><em>Many perspectives.</em></>, text: "Keep the edit in chat, delegate a focused investigation, or bring a team to a shared board. Agents return reports; teams deliberate around a proposal. You keep the thread of the work.", detail: "Delegate → deliberate → implement", link: "agents-and-teams" },
  { id: "review", label: "03 / Make it real", title: <>From shadow<br />to <em>working tree.</em></>, text: "Structured patches are staged in a lightweight shadow workspace. Accepted edits move into your working tree. Choose Ask, Accept edits or Full access; review the authority you grant.", detail: "Stage → accept → verify", link: "trust-and-approvals" },
];

function Page() {
  const story = useRef<HTMLDivElement>(null);
  const reduced = useReducedMotion();
  const { theme, setTheme } = useTheme();
  const [paused, setPaused] = useState(false);
  const [light, setLight] = useState(() => window.matchMedia("(prefers-color-scheme: light)").matches);
  return <div className={`forge-page ${light ? "is-light" : ""}`}>
    <a className="skip-link" href="#main">Skip to content</a>
    <header className="forge-nav">
      <a href="#top" className="wordmark" aria-label="Crucible home"><LogoGlyph className="mark" /><span>cru<em>cible</em></span></a>
      <nav aria-label="Main navigation"><a href="#explore">The process</a><a href="#connect">Your model</a><a href="#install">Install <span aria-hidden="true">↗</span></a></nav>
      <button className="quiet-button" onClick={() => setLight(!light)} aria-label={`Switch to ${light ? "dark" : "light"} theme`}>{light ? "◐" : "◑"}</button>
    </header>
    <main id="main">
      <div ref={story} className="forge-story" id="top">
        <div className="sculpture-stage" aria-hidden="true">
          <div className="sculpture-fallback"><i /><i /><i /></div>
          {!reduced && <Suspense fallback={null}><Sculpture target={story} paused={paused} light={light} accent={theme.accent} secondary={theme.secondary} /></Suspense>}
          <div className="scene-caption"><span>THE SHAPE OF AN EDIT</span><span>Intent / shadow / form</span></div>
        </div>
        <section className="forge-hero">
          <div className="eyebrow">OPEN SOURCE / BUILT FOR VS CODE</div>
          <h1>Every edit<br />is born in<br /><em>shadow.</em></h1>
          <p className="hero-description">An agentic coding assistant that explores your repository, works with agents and teams, and puts the authority in your hands.</p>
          <div className="hero-actions"><a className="primary-link" href="#install">Bring Crucible to VS Code <span aria-hidden="true">↗</span></a><a className="text-link" href={`${GITHUB_URL}#how-it-works`}>Read the story <span aria-hidden="true">↗</span></a></div>
          <div className="hero-foot"><span>YOUR REPOSITORY. YOUR MODEL. YOUR CALL.</span><a href="#explore">Scroll to shape the work ↓</a></div>
        </section>
        {chapters.map(chapter => <section className="forge-chapter" id={chapter.id} key={chapter.id}>
          <div className="chapter-copy"><div className="eyebrow">{chapter.label}</div><h2>{chapter.title}</h2><p>{chapter.text}</p><a className="text-link" href={`${GITHUB_URL}#${chapter.link}`}>{chapter.detail} <span aria-hidden="true">↗</span></a>{chapter.id === "review" && <p className="fine-print">Shadow staging is not a command sandbox. Auto-accepted edits can change files during a turn; shell and external side effects are separate.</p>}</div>
        </section>)}
      </div>
      <section className="connections" id="connect">
        <div className="eyebrow">04 / KEEP YOUR CHOICES OPEN</div>
        <h2>Your workflow.<br /><em>Your intelligence.</em></h2>
        <div className="connection-row"><h3>Choose the model.</h3><div><p>Bring provider credentials, connect an OpenAI-compatible endpoint, or use a local coding model through Ollama or TurboQuant.</p><p className="provider-line">Anthropic / OpenAI / Gemini / Groq / OpenRouter / watsonx / Hugging Face</p></div></div>
        <div className="connection-row"><h3>Connect your plan.</h3><div><p>Sign in with your ChatGPT plan, or try <strong>Claude subscription (experimental)</strong>. Account access and usage limits still apply; agents share the Claude allowance.</p><a className="text-link" href={`${GITHUB_URL}#claude-subscription-setup-experimental`}>Read the account setup and caveats ↗</a></div></div>
        <div className="connection-row"><h3>Carry context forward.</h3><div><p>Context compaction and recall help longer work continue. Experimental scoped memory offers candidates to inspect, not facts to trust blindly. The read-only Memory Inspector makes those distinctions visible.</p><a className="text-link" href={`${GITHUB_URL}#memory`}>Understand memory ↗</a></div></div>
      </section>
      <section className="install-section" id="install"><div className="eyebrow">MAKE SOMETHING WORTH REVIEWING</div><h2>Into the <em>work.</em></h2><p>Install the release VSIX, open a folder in VS Code, then follow the setup wizard or run <strong>Crucible: Run Setup</strong>.</p><div className="hero-actions"><a className="primary-link" href={`${GITHUB_URL}/releases`}>Download a release ↗</a><a className="text-link" href={`${GITHUB_URL}#install`}>Installation guide ↗</a></div><p className="fine-print">The managed extension provisions its backend and indexer. Local inference needs a running model endpoint. Published releases may differ from the current checkout.</p></section>
    </main>
    <footer className="forge-footer"><a href={GITHUB_URL}>Crucible / Apache-2.0 ↗</a><div className="scene-controls"><label>Palette <select aria-label="Accent palette" value={theme.id} onChange={e => setTheme(e.target.value as ThemeId)}><option value="violet">Violet</option><option value="cyan">Cyan</option><option value="ember">Ember</option></select></label><button className="quiet-button" disabled={!!reduced} onClick={() => setPaused(!paused)}>{reduced ? "Reduced motion" : paused ? "Resume 3D motion" : "Pause 3D motion"}</button></div><a href="#top">Back to the surface ↑</a></footer>
  </div>;
}
export default function App() { return <ThemeProvider><Page /></ThemeProvider>; }