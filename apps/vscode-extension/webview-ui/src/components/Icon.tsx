import type { ReactNode } from "react";

export type IconName =
  | "spark" | "search" | "plus" | "clock" | "chev-r" | "chev-l" | "chev-d"
  | "check" | "x" | "copy" | "file" | "term" | "list" | "diff" | "warn"
  | "send" | "stop" | "retry" | "bolt" | "bug"
  | "home" | "key" | "plug" | "book" | "shield" | "chip" | "gear" | "menu" | "db";

interface Props {
  name: IconName;
  size?: number;
  className?: string;
}

// Each entry is the inner content of the 16×16 viewBox symbol, converted to JSX.
// Colors remain currentColor so CSS drives them — no hardcoded fills/strokes.
const ICONS: Record<IconName, ReactNode> = {
  menu: (
    <path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
  ),

  spark: (
    <path fill="currentColor" d="M8 1l1.7 4.6L14.5 7 9.7 8.7 8 13.5 6.3 8.7 1.5 7l4.8-1.4L8 1z" />
  ),

  search: (
    <>
      <circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M10.5 10.5L14 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </>
  ),

  plus: (
    <path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
  ),

  clock: (
    <>
      <circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <path d="M8 4.5V8l2.4 1.6" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </>
  ),

  "chev-r": (
    <path d="M6 3.5L10.5 8 6 12.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
  ),

  "chev-l": (
    <path d="M10 3.5L5.5 8 10 12.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
  ),

  "chev-d": (
    <path d="M3.5 6L8 10.5 12.5 6" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
  ),

  check: (
    <path d="M3 8.5l3.2 3L13 4.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
  ),

  x: (
    <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
  ),

  copy: (
    <>
      <rect x="5.5" y="5.5" width="8" height="8" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="M3 10.5V3.8C3 3.36 3.36 3 3.8 3h6.7" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </>
  ),

  file: (
    <>
      <path d="M4 1.5h5L13 5.5v8a1 1 0 01-1 1H4a1 1 0 01-1-1V2.5a1 1 0 011-1z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M9 1.5V6h4" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
    </>
  ),

  term: (
    <>
      <rect x="1.5" y="2.5" width="13" height="11" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="M4.5 6l2.5 2-2.5 2M8.5 10.5h3" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
    </>
  ),

  list: (
    <>
      <path d="M5.5 4h8M5.5 8h8M5.5 12h8" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <circle cx="2.5" cy="4" r="1" fill="currentColor" />
      <circle cx="2.5" cy="8" r="1" fill="currentColor" />
      <circle cx="2.5" cy="12" r="1" fill="currentColor" />
    </>
  ),

  diff: (
    <>
      <path d="M5 2v7M2.5 6.5L5 9l2.5-2.5" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M11 14V7M8.5 9.5L11 7l2.5 2.5" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </>
  ),

  warn: (
    <>
      <path d="M8 2L15 13.5H1L8 2z" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
      <path d="M8 6.5v3.2" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <circle cx="8" cy="11.8" r=".9" fill="currentColor" />
    </>
  ),

  send: (
    <>
      <path d="M2.5 8L13.5 2.8 11 13.4 7.8 9.6 2.5 8z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M7.8 9.6L13.5 2.8" fill="none" stroke="currentColor" strokeWidth="1.3" />
    </>
  ),

  stop: (
    <rect x="4.5" y="4.5" width="7" height="7" rx="1.2" fill="currentColor" />
  ),

  retry: (
    <>
      <path d="M13.5 8a5.5 5.5 0 11-1.6-3.9" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      <path d="M13.7 1.8v2.7h-2.7" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </>
  ),

  bolt: (
    <path d="M8.8 1.5L3.5 9h3.4l-.7 5.5L11.5 7H8.1l.7-5.5z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
  ),

  bug: (
    <>
      <circle cx="8" cy="9" r="3.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="M8 5.5V4M5 3l1.2 1.5M11 3L9.8 4.5M3 9h1.5M11.5 9H13M4 12.5l1.3-1.2M12 12.5l-1.3-1.2" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </>
  ),

  home: (
    <>
      <path d="M2.5 7.5L8 2.5l5.5 5" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M4 7v6.5h8V7" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </>
  ),

  key: (
    <>
      <circle cx="5" cy="8" r="2.6" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <path d="M7.6 8h6M11 8v2.4M13.6 8v1.8" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </>
  ),

  plug: (
    <>
      <path d="M5.5 2v3M10.5 2v3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <path d="M4 5h8v2.5a4 4 0 01-8 0V5z" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
      <path d="M8 11.5V14" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </>
  ),

  book: (
    <>
      <path d="M3 3a1.5 1.5 0 011.5-1.5H13v11H4.8A1.8 1.8 0 003 14.3V3z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M13 12.5H4.8A1.8 1.8 0 003 14.3" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      <path d="M6 5h4.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </>
  ),

  shield: (
    <>
      <path d="M8 1.8l5.3 1.9v4.4c0 3.2-2.2 5.4-5.3 6.4-3.1-1-5.3-3.2-5.3-6.4V3.7L8 1.8z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M5.7 8l1.7 1.7L10.6 6.4" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </>
  ),

  chip: (
    <>
      <rect x="4.5" y="4.5" width="7" height="7" rx="1.2" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="M6.5 4.5V2M9.5 4.5V2M6.5 14v-2.5M9.5 14v-2.5M4.5 6.5H2M4.5 9.5H2M14 6.5h-2.5M14 9.5h-2.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </>
  ),

  gear: (
    <>
      <circle cx="8" cy="8" r="2.2" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <path d="M8 1.8v2M8 12.2v2M1.8 8h2M12.2 8h2M3.6 3.6l1.4 1.4M11 11l1.4 1.4M12.4 3.6L11 5M5 11l-1.4 1.4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </>
  ),

  db: (
    <>
      <ellipse cx="8" cy="4" rx="5.5" ry="2" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="M2.5 4v4c0 1.1 2.5 2 5.5 2s5.5-.9 5.5-2V4" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="M2.5 8v4c0 1.1 2.5 2 5.5 2s5.5-.9 5.5-2V8" fill="none" stroke="currentColor" strokeWidth="1.3" />
    </>
  ),
};

export function Icon({ name, size = 12, className }: Props) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      className={className}
      aria-hidden="true"
    >
      {ICONS[name]}
    </svg>
  );
}
