import { useEffect, useRef, type KeyboardEvent, type PointerEvent } from "react";
import { clampWidth } from "../../../../desktop/src/layout";
interface Props { label: string; value: number; min: number; max: number; defaultValue: number; direction?: 1 | -1; preview(value: number): void; commit(value: number): void; }
export function ResizeHandle(props: Props) {
  const current = useRef(props); current.current = props;
  const element = useRef<HTMLDivElement>(null);
  const drag = useRef<{ pointer: number; x: number; start: number; value: number; selection: string }>();
  const frame = useRef<number>();
  const paint = (value: number) => { current.current.preview(value); element.current?.setAttribute("aria-valuenow", String(value)); };
  const stop = (cancel: boolean) => {
    const active = drag.current; if (!active) return;
    drag.current = undefined; cancelAnimationFrame(frame.current ?? 0); frame.current = undefined;
    document.body.style.userSelect = active.selection;
    const value = cancel ? active.start : active.value; paint(value);
    if (!cancel && value !== active.start) current.current.commit(value);
    if (element.current?.hasPointerCapture?.(active.pointer)) element.current.releasePointerCapture(active.pointer);
  };
  useEffect(() => () => { cancelAnimationFrame(frame.current ?? 0); if (drag.current) { document.body.style.userSelect = drag.current.selection; current.current.preview(drag.current.start); } }, []);
  const move = (event: PointerEvent) => {
    const active = drag.current; if (!active || event.pointerId !== active.pointer) return;
    const { min, max, direction = 1 } = current.current;
    active.value = clampWidth(active.start + (event.clientX - active.x) * direction, min, max);
    if (frame.current === undefined) frame.current = requestAnimationFrame(() => { frame.current = undefined; if (drag.current) paint(drag.current.value); });
  };
  const key = (event: KeyboardEvent) => {
    if (event.key === "Escape" && drag.current) { event.preventDefault(); event.stopPropagation(); stop(true); return; }
    const { min, max, value, defaultValue, direction = 1 } = current.current, step = event.shiftKey ? 64 : 16;
    const next = event.key === "ArrowLeft" ? value - step * direction : event.key === "ArrowRight" ? value + step * direction
      : event.key === "Home" ? min : event.key === "End" ? max : event.key === "Enter" ? defaultValue : undefined;
    if (next === undefined) return;
    event.preventDefault(); event.stopPropagation(); const width = clampWidth(next, min, max); paint(width); current.current.commit(width);
  };
  return <div ref={element} role="separator" aria-label={props.label} aria-orientation="vertical" aria-valuemin={props.min} aria-valuemax={props.max} aria-valuenow={props.value}
    tabIndex={0} className="desktop-resizer" title="Drag to resize · Arrow keys adjust · Enter resets · Escape cancels drag" onKeyDown={key}
    onPointerDown={event => {
      if (event.button !== 0 || drag.current) return;
      event.preventDefault(); event.currentTarget.focus();
      drag.current = { pointer: event.pointerId, x: event.clientX, start: current.current.value, value: current.current.value, selection: document.body.style.userSelect };
      document.body.style.userSelect = "none"; event.currentTarget.setPointerCapture?.(event.pointerId);
    }} onPointerMove={move} onPointerUp={event => { if (drag.current?.pointer === event.pointerId) { move(event); stop(false); } }} onPointerCancel={event => { if (drag.current?.pointer === event.pointerId) stop(true); }} onLostPointerCapture={event => { if (drag.current?.pointer === event.pointerId) stop(true); }}
    onDoubleClick={() => { if (drag.current) stop(true); const value = clampWidth(props.defaultValue, props.min, props.max); paint(value); props.commit(value); }} />;
}
