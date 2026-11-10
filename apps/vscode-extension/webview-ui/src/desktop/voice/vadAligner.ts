const FRAME_SAMPLES = 320; // 20 ms at 16 kHz
const WINDOW_SAMPLES = 512; // one Silero probability: 32 ms at 16 kHz

/**
 * Maps Silero's 32 ms speech probabilities back onto 20 ms microphone frames. The detector
 * buffers samples across requests, so windows are counted from the first sample sent; a frame
 * is released once every window it overlaps is known, with the highest of their probabilities.
 */
export class VadAligner<T> {
  private readonly pending: { item: T; start: number; done: boolean }[] = [];
  private probs: number[] = [];
  private offset = 0; // window index of probs[0]: older windows are dropped once used
  private sent = 0;

  push(item: T): void {
    this.pending.push({ item, start: this.sent, done: false });
    this.sent += FRAME_SAMPLES;
  }

  addProbs(probs: readonly number[]): void {
    this.probs.push(...probs);
  }

  /** Hands over every waiting frame without a probability. Their samples still count, so
   * frames pushed later keep lining up with the detector's windows. */
  drain(): T[] {
    const waiting = this.pending.filter(entry => !entry.done);
    for (const entry of waiting) entry.done = true;
    return waiting.map(entry => entry.item);
  }

  take(): { item: T; prob: number }[] {
    const known = (this.offset + this.probs.length) * WINDOW_SAMPLES;
    const ready: { item: T; prob: number }[] = [];
    while (this.pending.length && this.pending[0].start + FRAME_SAMPLES <= known) {
      const { item, start, done } = this.pending.shift()!;
      if (done) continue;
      const first = Math.floor(start / WINDOW_SAMPLES);
      const last = Math.floor((start + FRAME_SAMPLES - 1) / WINDOW_SAMPLES);
      ready.push({ item, prob: Math.max(...this.probs.slice(first - this.offset, last - this.offset + 1)) });
    }
    const needed = this.pending.length ? Math.floor(this.pending[0].start / WINDOW_SAMPLES) : this.offset + this.probs.length;
    if (needed > this.offset) {
      this.probs = this.probs.slice(needed - this.offset);
      this.offset = needed;
    }
    return ready;
  }
}
