/**
 * Scroll arithmetic for the chat's two bounded, auto-following panes.
 *
 * Kept pure and DOM-free for the same reason the tool track's geometry is:
 * jsdom has no layout engine and reports zero for every metric, so a component
 * test can never assert this. Here it can be asserted exactly.
 */

export interface ScrollMetrics {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
}

/** How close to the bottom still counts as "following the stream", in px. */
export const PIN_SLACK = 24;

/**
 * Is the reader still at the bottom of a streaming pane?
 *
 * Auto-scrolling unconditionally would yank someone who has deliberately
 * scrolled up to re-read an earlier step — every token would drag them back.
 * A box too short to scroll counts as pinned: nothing has been scrolled away
 * from, so following is still what the reader expects.
 */
export function isPinnedToBottom(m: ScrollMetrics, threshold: number = PIN_SLACK): boolean {
  return m.scrollHeight - m.scrollTop - m.clientHeight <= threshold;
}

export interface ViewportMetrics {
  scrollTop: number;
  clientHeight: number;
}

export interface ItemMetrics {
  offsetTop: number;
  offsetHeight: number;
}

/**
 * The `scrollTop` a bounded list needs so `item` is fully visible, or `null`
 * when it already is and the list should be left alone.
 *
 * Scrolls the minimum distance rather than centring: capping the todo list is
 * only worth doing if the active row stays put instead of jumping on every
 * status change.
 */
export function scrollOffsetToReveal(view: ViewportMetrics, item: ItemMetrics): number | null {
  // An unmeasured container (jsdom, or a pane rendered while hidden) must never
  // produce a scroll — every offset is zero and the result would be arbitrary.
  if (view.clientHeight <= 0) return null;

  const itemBottom = item.offsetTop + item.offsetHeight;
  if (item.offsetTop < view.scrollTop) return item.offsetTop;
  if (itemBottom > view.scrollTop + view.clientHeight) return itemBottom - view.clientHeight;
  return null;
}
