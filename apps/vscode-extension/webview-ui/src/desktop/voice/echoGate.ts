/**
 * While a reply plays, Silero scores the reply's own voice coming back through laptop
 * speakers as speech. The app knows what it is playing, so it learns how much of that output
 * reaches the microphone (the coupling) and counts a frame as the user only when the
 * microphone is clearly louder than that expected echo: a double-talk check.
 */
export const ECHO_MARGIN = 3;
export const ECHO_WINDOW_FRAMES = 150; // 3 s of 20 ms frames
export const USER_ONSET_FRAMES = 4; // 80 ms, as for any turn start
export const VAD_USER = 0.5;
const SILENT_OUTPUT = 0.001;
const ABSOLUTE_MARGIN = 0.004;
// Until enough playback has been heard, assume the microphone may hear all of it.
const MIN_SAMPLES = 10;
const UNKNOWN_COUPLING = 1;

export class EchoGate {
  private ratios: number[] = [];
  private run = 0;

  /** One 20 ms frame while output plays; true once the user's voice is established. */
  observe(micRms: number, outputRms: number, speechProb: number): boolean {
    const voiced = speechProb >= VAD_USER;
    let user: boolean;
    if (outputRms <= SILENT_OUTPUT) {
      user = voiced; // headphones or a gap in the reply: nothing to mistake for the user
    } else {
      this.ratios.push(micRms / outputRms);
      if (this.ratios.length > ECHO_WINDOW_FRAMES) this.ratios.shift();
      const expectedEcho = ECHO_MARGIN * this.coupling() * outputRms + ABSOLUTE_MARGIN;
      user = voiced && micRms >= expectedEcho;
    }
    this.run = user ? this.run + 1 : 0;
    return this.run >= USER_ONSET_FRAMES;
  }

  reset() {
    this.ratios = [];
    this.run = 0;
  }

  /** The quieter end of what the microphone heard relative to the output: the echo path. */
  private coupling(): number {
    if (this.ratios.length < MIN_SAMPLES) return UNKNOWN_COUPLING;
    const sorted = [...this.ratios].sort((a, b) => a - b);
    return sorted[Math.floor(sorted.length * 0.2)];
  }
}
