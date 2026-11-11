/**
 * The voice model the user picked, per provider: a choice made on the ChatGPT plan must never
 * be sent on the Claude plan. null is "Automatic" (the backend's per-provider default).
 */
const KEY = "crucible.voiceModel.";
const MODEL_ID = /^[A-Za-z0-9._:/-]{1,128}$/;

export function readVoiceModel(backend: string): string | null {
  try {
    const stored = localStorage.getItem(KEY + backend);
    return stored && MODEL_ID.test(stored) ? stored : null;
  } catch {
    return null; // storage unavailable: Automatic
  }
}

export function writeVoiceModel(backend: string, model: string | null): void {
  try {
    if (model === null) localStorage.removeItem(KEY + backend);
    else localStorage.setItem(KEY + backend, model);
  } catch {
    // Storage unavailable: the choice still applies to this session.
  }
}

type CatalogEntry = { backend: string; model: string; display?: string };
export type VoiceModelChoice = { value: string; label: string };

/** "Automatic" (value "") plus the current provider's models; a saved choice the catalog no
 *  longer lists stays selectable, so the select never shows a value it cannot name. */
export function voiceModelChoices(backend: string, options: CatalogEntry[], saved: string | null): VoiceModelChoice[] {
  const choices: VoiceModelChoice[] = [{ value: "", label: "Automatic" }];
  for (const option of options) {
    if (option.backend === backend && !choices.some(choice => choice.value === option.model)) {
      choices.push({ value: option.model, label: option.display ?? option.model });
    }
  }
  if (saved && !choices.some(choice => choice.value === saved)) choices.push({ value: saved, label: saved });
  return choices;
}
