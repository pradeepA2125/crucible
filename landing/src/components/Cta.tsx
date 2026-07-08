import { GITHUB_URL } from "../content";
import { LogoGlyph } from "./Nav";
import { Em, GitHubIcon, InstallCommand, Reveal } from "./ui";

export function Cta() {
  return (
    <section id="install" className="relative py-32 sm:py-44 overflow-hidden aura">
      <div className="absolute inset-0 pointer-events-none bg-[radial-gradient(50%_60%_at_50%_45%,rgba(124,92,245,0.13),transparent_75%)]" />
      <div className="relative mx-auto max-w-3xl px-5 sm:px-8 text-center">
        <Reveal>
          <h2 className="text-5xl sm:text-6xl lg:text-7xl font-medium tracking-tight leading-[1.02]">
            Fork the <Em>forge.</Em>
          </h2>
        </Reveal>
        <Reveal delay={0.12}>
          <p className="mt-7 text-lg text-ink-2 leading-relaxed max-w-xl mx-auto">
            One command installs the extension from the latest GitHub release.
            The setup wizard provisions the rest — backend, indexer, models —
            on your machine, answering to you.
          </p>
        </Reveal>
        <Reveal delay={0.22}>
          <div className="mt-10 flex flex-col items-center gap-5">
            <InstallCommand large />
            <div className="flex items-center gap-4">
              <a
                href={GITHUB_URL}
                target="_blank"
                rel="noreferrer"
                className="btn-glow inline-flex items-center gap-2.5 rounded-xl px-7 py-3.5 text-[15px] font-medium text-white"
              >
                <GitHubIcon className="w-4.5 h-4.5" />
                shadow-forge on GitHub
              </a>
            </div>
            <p className="font-mono text-[11px] text-ink-4 tracking-wide">
              requires the `code` CLI · works with VS Code, Insiders & Cursor
            </p>
          </div>
        </Reveal>
      </div>
    </section>
  );
}

export function Footer() {
  return (
    <footer className="border-t hairline py-10">
      <div className="mx-auto max-w-6xl px-5 sm:px-8 flex flex-col sm:flex-row items-center justify-between gap-5">
        <div className="flex items-center gap-2.5">
          <LogoGlyph className="w-5 h-5" />
          <span className="font-mono text-xs text-ink-4">
            shadow-forge — an open-source AI code editor
          </span>
        </div>
        <div className="flex items-center gap-6 font-mono text-xs text-ink-4">
          <a href={GITHUB_URL} target="_blank" rel="noreferrer" className="hover:text-ink-2 transition-colors">
            github
          </a>
          <a href={`${GITHUB_URL}/issues`} target="_blank" rel="noreferrer" className="hover:text-ink-2 transition-colors">
            issues
          </a>
          <a href={`${GITHUB_URL}/tree/main/docs`} target="_blank" rel="noreferrer" className="hover:text-ink-2 transition-colors">
            docs
          </a>
        </div>
      </div>
    </footer>
  );
}
