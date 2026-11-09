const soundAnnotations = /\([^()\r\n]+\)|\[[^\[\]\r\n]+\]|\*[^*\r\n]+\*/gu;
const explicitPlaybackControl = /^(?:crucible )?(?:(?:can|could|would) you )?(?:please )?stop (?:speaking|talking)(?: please)?$/u;
const contextualPlaybackControl = /^(?:crucible )?(?:please )?(?:stop|pause|wait|hold on)(?: please)?$/u;

function words(text: string): string[] {
  return text.toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? [];
}

export function soundsUnfinished(text: string): boolean {
  const tokens = words(text);
  const last = tokens.at(-1);
  return !/\s/u.test(text) || last === undefined ||
    /^(?:and|or|but|because|with|to|the|a|an|in|of|for|before|after|until|unless|when|if|uh|um)$/u.test(last) ||
    /^(?:before|after|until|unless|when|if) (?:i|you|we|they|it)$/u.test(tokens.slice(-2).join(" "));
}

export function hasConflictingReferences(partial: string, final: string): boolean {
  const references = (text: string): string[] =>
    text.toLowerCase().match(/[\p{L}\p{N}_-]+\.[\p{L}\p{N}_-]+/gu) ?? [];
  const previous = references(partial);
  const current = references(final);
  return current.length > 0 && previous.some(reference => !current.includes(reference));
}

export function isStableTranscript(first: string, second: string): boolean {
  const normalize = (text: string) => text.trim().replace(/[.!?]+$/u, "").replace(/\s+/gu, " ");
  return hasSubstantiveSpeech(first) && normalize(first) === normalize(second);
}

export function clearOverlapRequest(text: string): boolean {
  // A short reply to playback can be misheard as an instruction. Require a
  // recognizable correction cue or instruction after the audio has cleared.
  return /^(?:crucible[\s,]+)?(?:(?:correction|change of plan)\s*[,.:;—-]\s*\S|stop\s+(?:coding|the task)\b|(?:(?:stop|wait|hold on)[\s,.:;—-]+)?(?:use|skip|change|focus|keep|remove|add|switch|run|do)\b\s+\S)/iu
    .test(text.replace(soundAnnotations, " ").trim());
}

export function isPlaybackControl(text: string, duringPlayback: boolean): boolean {
  const phrase = words(text).join(" ");
  return explicitPlaybackControl.test(phrase) || (duringPlayback && contextualPlaybackControl.test(phrase));
}

export function isBackchannel(text: string): boolean {
  return /^(?:yeah|yep|okay|ok|uh huh|mm hmm|mhm|mm)$/u.test(words(text).join(" "));
}

export function hasSubstantiveSpeech(text: string): boolean {
  return spokenWordCount(text) >= 2;
}

export function spokenWordCount(text: string): number {
  const spoken = text.replace(soundAnnotations, " ");
  if (isBackchannel(spoken)) return 0;
  return words(spoken).filter(word => !/^(?:ah|eh|er|hmm|hm|mh|mhm|mm+|oh|uh|um+)$/u.test(word)).length;
}

export function isPlaybackEcho(text: string, playback: string | undefined): boolean {
  if (playback === undefined) return false;
  const heard = words(text.replace(soundAnnotations, " "));
  return heard.length >= 2 && ` ${words(playback).join(" ")} `.includes(` ${heard.join(" ")} `);
}

/** Speech recognizers can describe a sound even when nobody said anything. */
export function spokenMessage(text: string, playback?: string): string {
  const trimmed = text.trim();
  if (!words(trimmed.replace(soundAnnotations, " ")).length || isPlaybackControl(trimmed, playback !== undefined) ||
    isPlaybackEcho(trimmed, playback)) return "";

  return trimmed;
}
