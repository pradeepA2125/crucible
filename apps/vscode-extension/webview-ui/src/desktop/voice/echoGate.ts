/**
 * While a reply plays, Silero scores the reply's own voice coming back through laptop
 * speakers as speech. The app knows what it is playing, so it learns how much of that output
 * reaches the microphone (the coupling) and counts a frame as the user only when the
 * microphone is clearly louder than that expected echo: a double-talk check.
 */
export const ECHO_MARGIN = 3;
export const ECHO_WINDOW_FRAMES = 150; // 3 s of 20 ms frames
// The echo of what played up to 300 ms ago can still be arriving: the output level is per
// reply chunk, a syllable's sound lingers, and capture and playback clocks differ. Compare
// against the loudest output in that span (a double-talk check), not the current chunk alone.
export const REFERENCE_FRAMES = 15;
// The echo path is set by the echo's loud end; the gaps between syllables would set it low.
const COUPLING_PERCENTILE = 0.8;
export const USER_ONSET_FRAMES = 4; // 80 ms, as for any turn start
export const VAD_USER = 0.5;
const SILENT_OUTPUT = 0.001;
const ABSOLUTE_MARGIN = 0.004;
// Until enough playback has been heard, assume the microphone may hear all of it.
const MIN_SAMPLES = 10;
const UNKNOWN_COUPLING = 1;

export class EchoGate {
  private ratios: number[] = [];
  private outputs: number[] = [];
  private run = 0;

  /** One 20 ms frame while output plays; true once the user's voice is established. */
  observe(micRms: number, outputRms: number, speechProb: number): boolean {
    const voiced = speechProb >= VAD_USER;
    this.outputs.push(outputRms);
    if (this.outputs.length > REFERENCE_FRAMES) this.outputs.shift();
    const reference = Math.max(...this.outputs);
    let user: boolean;
    if (reference <= SILENT_OUTPUT) {
      user = voiced; // headphones, or the reply quiet for a while: nothing to mistake for the user
    } else {
      const expectedEcho = ECHO_MARGIN * this.coupling() * reference + ABSOLUTE_MARGIN;
      user = voiced && micRms >= expectedEcho;
      // Learn the echo path only from frames that are not the user: their own voice must
      // never become "the echo" (someone talking from a reply's first frame was muted).
      if (!user) {
        this.ratios.push(micRms / reference);
        if (this.ratios.length > ECHO_WINDOW_FRAMES) this.ratios.shift();
      }
    }
    this.run = user ? this.run + 1 : 0;
    return this.run >= USER_ONSET_FRAMES;
  }

  reset() {
    this.ratios = [];
    this.outputs = [];
    this.run = 0;
  }

  /** How much of the output reaches the microphone, from the echo's loud end. */
  coupling(): number {
    if (this.ratios.length < MIN_SAMPLES) return UNKNOWN_COUPLING;
    const sorted = [...this.ratios].sort((a, b) => a - b);
    return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * COUPLING_PERCENTILE))];
  }
}
