import { useEffect } from 'react';

type Cue = { target: string; frames: Keyframe[]; start?: number; end?: number };
type Scene = { selector: string; cues: Cue[]; pinned?: boolean };
const clamp = (value: number) => Math.max(0, Math.min(1, value));
const scenes: Scene[] = [
  { selector: '.hero', cues: [
    { target: '.hero-space', frames: [{ transform: 'translate3d(0,0,0) scale(1)' }, { transform: 'translate3d(-4%,8%,0) scale(1.18)' }] },
    { target: '.hero-copy', frames: [{ transform: 'translateY(0)' }, { transform: 'translateY(65px)' }] },
    { target: '.hero-crew', frames: [{ transform: 'perspective(900px) rotateY(-12deg) rotateX(5deg)' }, { transform: 'perspective(900px) rotateY(0deg) rotateX(0deg) translateY(-42px)' }] },
  ] },
  { selector: '#agents', cues: [
    { target: '.dispatch-brief', start: 0, end: .52, frames: [{ transform: 'translate3d(-45px,40px,0) rotate(-13deg)' }, { transform: 'translate3d(0,0,0) rotate(-5deg)' }] },
    { target: '.agent-character:nth-child(1)', start: .08, end: .55, frames: [{ transform: 'translate3d(-65px,52px,0) rotate(-8deg)' }, { transform: 'translate3d(0,0,0) rotate(0deg)' }] },
    { target: '.agent-character:nth-child(2)', start: .18, end: .65, frames: [{ transform: 'translate3d(0,70px,0) rotate(6deg)' }, { transform: 'translate3d(0,0,0) rotate(0deg)' }] },
    { target: '.agent-character:nth-child(3)', start: .28, end: .72, frames: [{ transform: 'translate3d(65px,45px,0) rotate(10deg)' }, { transform: 'translate3d(0,0,0) rotate(0deg)' }] },
    { target: '.scroll-brief', start: .22, end: .82, frames: [{ offsetDistance: '0%', opacity: 0, offset: 0 }, { opacity: 1, offset: .08 }, { opacity: 1, offset: .9 }, { offsetDistance: '100%', opacity: 0, offset: 1 }] },
    { target: '.dispatch-path > svg > path:first-child', start: .1, end: .75, frames: [{ strokeDashoffset: 120 }, { strokeDashoffset: 0 }] },
    { target: '.return-ticket', start: .55, end: .85, frames: [{ transform: 'translateX(22px)', color: '#9E91AF' }, { transform: 'translateX(0)', color: '#8CDEEA' }] },
  ] },
  { selector: '.office-scroll-frame', pinned: true, cues: [
    { target: '.office-world', start: 0, end: .9, frames: [{ transform: 'perspective(1800px) rotateX(8deg) rotateY(-7deg) scale(.9)' }, { transform: 'perspective(1800px) rotateX(0deg) rotateY(0deg) scale(1)' }] },
    { target: '.office-scout', start: .08, end: .4, frames: [{ transform: 'translate(-85px,28px)' }, { transform: 'translate(0,0)' }] },
    { target: '.office-builder', start: .2, end: .52, frames: [{ transform: 'translate(0,-40px)' }, { transform: 'translate(0,0)' }] },
    { target: '.office-reviewer', start: .32, end: .64, frames: [{ transform: 'translate(85px,28px)' }, { transform: 'translate(0,0)' }] },
    { target: '.office-proposal', start: .18, end: .68, frames: [{ transform: 'translate(548px,68px) skewY(11deg) scale(.94)', opacity: .55 }, { transform: 'translate(548px,90px) skewY(11deg) scale(1)', opacity: 1 }] },
    { target: '.office-connection', start: .52, end: .88, frames: [{ strokeDashoffset: 1, opacity: .1 }, { strokeDashoffset: 0, opacity: 1 }] },
    { target: '.office-handoff', start: .82, end: 1, frames: [{ transform: 'translateY(20px)', opacity: 0 }, { transform: 'translateY(0)', opacity: 1 }] },
  ] },
  { selector: '#rewind', cues: [
    { target: '.rewind-paper', start: 0, end: .65, frames: [{ transform: 'perspective(1200px) rotateY(9deg) rotateZ(3deg)' }, { transform: 'perspective(1200px) rotateY(0deg) rotateZ(0deg)' }] },
    { target: '.rewind-arrow-art path:first-child', start: .12, end: .7, frames: [{ strokeDasharray: '400', strokeDashoffset: 400 }, { strokeDasharray: '400', strokeDashoffset: 0 }] },
    { target: '.checkpoint-traveller', start: .18, end: .78, frames: [{ transform: 'translateX(0)' }, { transform: 'translateX(-210%)' }] },
    { target: '.rewind-person', start: .1, end: .65, frames: [{ transform: 'translate(28px,-15px) rotate(14deg)' }, { transform: 'translate(0,0) rotate(7deg)' }] },
    { target: '.prompt-note', start: .25, end: .8, frames: [{ transform: 'translate(25px,32px) rotate(13deg)' }, { transform: 'translate(0,0) rotate(6deg)' }] },
  ] },
  { selector: '#context', cues: [
    { target: '.context-envelope', start: 0, end: .7, frames: [{ transform: 'perspective(1000px) rotateY(-12deg) rotateZ(-4deg)' }, { transform: 'perspective(1000px) rotateY(0) rotateZ(2deg)' }] },
    ...[1, 2, 3].map((i): Cue => ({ target: `.context-papers span:nth-child(${i})`, start: .1 + i * .08, end: .65 + i * .05, frames: [{ transform: `translate(${i * 9}px,${-i * 15}px) rotate(${i * 2}deg)` }, { transform: 'translate(0,0) rotate(0deg)' }] })),
  ] },
  { selector: '#toolkit', cues: [
    { target: '.brief-image', start: .05, end: .65, frames: [{ translate: '-28px 20px', rotate: '-6deg' }, { translate: '0 0', rotate: '0deg' }] },
    { target: '.brief-web', start: .18, end: .75, frames: [{ translate: '35px -24px', rotate: '8deg' }, { translate: '0 0', rotate: '0deg' }] },
    { target: '.brief-terminal', start: .3, end: .83, frames: [{ translate: '-20px 40px' }, { translate: '0 0' }] },
    { target: '.toolkit-pencil', start: .15, end: .8, frames: [{ translate: '20px 28px', rotate: '12deg' }, { translate: '0 0', rotate: '0deg' }] },
  ] },
  { selector: '#axon', cues: [
    { target: '.axon-viewport', start: .05, end: .72, frames: [{ transform: 'perspective(1800px) rotateX(5deg) scale(.96)' }, { transform: 'perspective(1800px) rotateX(0deg) scale(1)' }] },
  ] },
  { selector: '#repository-map', cues: [
    ...[1, 2, 3].map((i): Cue => ({ target: `.index-seed-flow > li:nth-child(${i})`, start: .07 + i * .1, end: .5 + i * .1, frames: [{ transform: 'translateX(22px)', opacity: .65 }, { transform: 'translateX(0)', opacity: 1 }] })),
  ] },
  { selector: '.closing', cues: [
    { target: ':scope > svg', start: 0, end: .65, frames: [{ transform: 'translateY(20px) rotate(-18deg) scale(.8)' }, { transform: 'translateY(0) rotate(0) scale(1)' }] },
  ] },
];

/** One scroll reader, one write phase, no per-scroll React state or endless RAF. */
export function useScrollChoreography(disabled: boolean) {
  useEffect(() => {
    const media = matchMedia('(prefers-reduced-motion: reduce)');
    const compact = matchMedia('(max-width: 800px)');
    let dispose = () => {};
    const setup = () => {
      dispose();
      if (disabled || media.matches) { document.documentElement.dataset.motion = 'still'; return; }
      const desktop = !compact.matches;
      const mounted: { el: HTMLElement; pinned: boolean; animations: { animation: Animation; start: number; end: number }[]; progress: number }[] = [];
      document.documentElement.dataset.motion = desktop ? 'cinematic' : 'compact';
      for (const scene of scenes) {
        const el = document.querySelector<HTMLElement>(scene.selector);
        if (!el) continue;
        const animations = scene.cues.flatMap(cue => {
          // On phones the office still reads as an office, without a pinned camera journey.
          if (!desktop && (scene.pinned || scene.selector === '.hero')) return [];
          return Array.from(el.querySelectorAll<HTMLElement>(cue.target)).map(target => {
            const animation = target.animate(cue.frames, { duration: 1000, fill: 'both', easing: 'linear' });
            animation.pause(); animation.currentTime = 1000;
            return { animation, start: cue.start ?? 0, end: cue.end ?? 1 };
          });
        });
        mounted.push({ el, pinned: Boolean(scene.pinned && desktop), animations, progress: -1 });
      }
      let raf = 0;
      const render = () => {
        raf = 0;
        if (document.hidden) return;
        const height = window.innerHeight;
        // Batch all geometry reads before mutating animation time or attributes.
        const readings = mounted.map(scene => {
          const box = scene.el.getBoundingClientRect();
          let progress = scene.pinned && height >= 760
            ? clamp(-box.top / Math.max(1, box.height - height))
            : scene.el.matches('.hero') ? clamp(-box.top / height)
            : clamp((height * .9 - box.top) / Math.max(1, Math.min(box.height, height * 1.05)));
          if (!desktop) progress = .68 + progress * .32;
          return { scene, progress, active: box.bottom > 0 && box.top < height };
        });
        for (const { scene, progress, active } of readings) {
          if (Math.abs(scene.progress - progress) < .0005) continue;
          scene.progress = progress;
          scene.el.dataset.scrollProgress = progress.toFixed(3);
          scene.el.dataset.scrollBeat = progress < .25 ? 'arrive' : progress < .8 ? 'connect' : 'resolve';
          for (const { animation, start, end } of scene.animations) {
            animation.currentTime = clamp((progress - start) / (end - start)) * 1000;
          }
          scene.el.toggleAttribute('data-motion-active', active);
        }
      };
      const schedule = () => { if (!raf) raf = requestAnimationFrame(render); };
      const observer = new ResizeObserver(schedule);
      mounted.forEach(scene => observer.observe(scene.el));
      window.addEventListener('scroll', schedule, { passive: true });
      window.addEventListener('resize', schedule, { passive: true });
      document.addEventListener('visibilitychange', schedule);
      document.addEventListener('toggle', schedule, true);
      document.fonts.ready.then(schedule);
      render();
      dispose = () => {
        cancelAnimationFrame(raf); observer.disconnect();
        window.removeEventListener('scroll', schedule); window.removeEventListener('resize', schedule);
        document.removeEventListener('visibilitychange', schedule); document.removeEventListener('toggle', schedule, true);
        mounted.forEach(scene => { scene.animations.forEach(({ animation }) => animation.cancel()); delete scene.el.dataset.scrollProgress; delete scene.el.dataset.scrollBeat; scene.el.removeAttribute('data-motion-active'); });
      };
    };
    setup(); media.addEventListener('change', setup); compact.addEventListener('change', setup);
    return () => { dispose(); media.removeEventListener('change', setup); compact.removeEventListener('change', setup); delete document.documentElement.dataset.motion; };
  }, [disabled]);
}
