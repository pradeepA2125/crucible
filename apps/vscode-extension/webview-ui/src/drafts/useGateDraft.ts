import { useEffect, useMemo, useRef, useState } from "react";
import type { DraftImage, DraftPort } from "./types";
import { useRetainedDraft } from "./useRetainedDraft";
/** Same retained store/hook, with a separate feedback lifetime and flush registration. */
export function useGateDraft(port: DraftPort | undefined, threadId: string, gateId: string, kind: "mode" | "clarify") {
  const key = `@gate:${JSON.stringify([threadId, gateId, kind])}`;
  const [value, setValue] = useState({ key, text: "" });
  const text = value.key === key ? value.text : "";
  const textValue = useRef({ key, text });
  if (textValue.current.key !== key) textValue.current = { key, text: "" };
  const updateText = (text: string) => { textValue.current = { key, text }; setValue({ key, text }); };
  const [hostNotice, setHostNotice] = useState("");
  const images = useMemo<DraftImage[]>(() => [], []), mentions = useRef<string[]>([]);
  const binding = useRef({ port, key, token: {} });
  if (binding.current.port !== port || binding.current.key !== key) binding.current = { port, key, token: {} };
  const token = binding.current.token;
  const gatePort = useMemo(() => {
    if (!port) return undefined;
    const valid = () => binding.current.token === token;
    const assert = () => { if (!valid()) throw new Error("The feedback card changed. Retry its current draft."); };
    return { ...port,
      read: async (origin: string) => { assert(); const result = await port.read(origin); assert(); return result; },
      save: async (...args: Parameters<DraftPort["save"]>) => { assert(); const result = await port.save(...args); assert(); return result; },
      onChange: (receive: Parameters<DraftPort["onChange"]>[0]) => port.onChange(snapshot => { if (valid() && snapshot.threadId === key) receive(snapshot); }),
      onFlush: (save: () => Promise<void>) => port.onAuxiliaryFlush?.(async () => { if (valid()) await save(); }) ?? (() => {}),
    };
  }, [port, key, token]);
  const retained = useRetainedDraft({ port: gatePort, threadId: key, text, images, mentions,
    isReading: () => false, getText: () => textValue.current.key === key ? textValue.current.text : "", setText: updateText, setImages: () => {} });
  const lifetime = useRef({ active: true });
  useEffect(() => { const current = { active: true }; lifetime.current = current; return () => { current.active = false; }; }, [token]);
  useEffect(() => {
    setHostNotice("");
    const notice = (event: MessageEvent) => { const m = event.data;
      if (m?.type === "gateDecisionNotice" && m.threadId === threadId && m.gateId === gateId && typeof m.message === "string") setHostNotice(m.message);
    };
    window.addEventListener("message", notice); return () => window.removeEventListener("message", notice);
  }, [threadId, gateId]);
  return { text, isCurrent: () => lifetime.current.active && binding.current.token === token, notice: hostNotice || retained.notice, loaded: retained.loaded,
    setText: (text: string) => { retained.edited(); updateText(text); },
    prepare: async () => {
      const mounted = lifetime.current;
      if (!mounted.active || !retained.loaded || binding.current.token !== token) throw new Error("Wait for the saved feedback to load.");
      if (!port) return undefined;
      await port.flush?.();
      const snapshot = await port.read(key);
      if (!mounted.active || binding.current.token !== token || snapshot.draft.text !== text) throw new Error("Feedback changed. Retry its current text.");
      return { revision: snapshot.revision, version: snapshot.version };
    },
  };
}
