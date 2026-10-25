import { useEffect, useRef, useState, type MutableRefObject } from "react";
import { emptyDraft, type Draft, type DraftImage, type DraftPort, type DraftSnapshot, type ReturnedDraft } from "./types";
interface Options {
  port?: DraftPort; threadId: string; text: string; images: DraftImage[];
  mentions: MutableRefObject<string[]>; isReading(): boolean;
  getText?(): string;
  setText(text: string): void; setImages(images: DraftImage[]): void;
}
const equal = (a: Draft, b: Draft) => a.text === b.text && a.imageAttachments.length === b.imageAttachments.length
  && a.imageAttachments.every((image, index) => image.dataUrl === b.imageAttachments[index]?.dataUrl)
  && a.mentionedPaths.join("\n") === b.mentionedPaths.join("\n");
export function useRetainedDraft(options: Options) {
  const current = useRef(options); current.current = options;
  const [notice, setNotice] = useState("");
  const [hydration, setHydration] = useState<{ port: DraftPort; threadId: string }>();
  const [choosing, setChoosing] = useState(false);
  const [returned, setReturned] = useState<ReturnedDraft>();
  const holder = useRef({ revision: 0, version: 0, loaded: false, touched: false, live: false, last: emptyDraft(), loading: Promise.resolve(), timer: undefined as ReturnType<typeof setTimeout> | undefined });
  const renderedThread = useRef(options.threadId);
  const switching = renderedThread.current !== options.threadId; renderedThread.current = options.threadId;
  const actions = useRef({ flush: async () => {}, begin: async (_id: string, _draft: Draft) => false, choose: (_choice: "restore" | "discard") => {} });
  useEffect(() => {
    const port = options.port; if (!port) return;
    const threadId = options.threadId || "new";
    const state = { revision: 0, version: 0, loaded: false, touched: false, live: true, last: emptyDraft(), loading: Promise.resolve(), timer: undefined as ReturnType<typeof setTimeout> | undefined };
    holder.current = state; setHydration(undefined); setNotice(""); setReturned(undefined); setChoosing(false);
    let choicePending: Promise<void> | undefined;
    const value = (): Draft => ({ text: current.current.getText?.() ?? current.current.text, imageAttachments: current.current.images, mentionedPaths: [...current.current.mentions.current] });
    const fail = (error: unknown) => { if (state.live) setNotice(error instanceof Error ? error.message : "Draft could not be saved."); };
    const apply = (draft: Draft) => {
      state.last = draft; current.current.mentions.current = [...draft.mentionedPaths];
      current.current.setImages(draft.imageAttachments); current.current.setText(draft.text);
    };
    const choose = (choice: "restore" | "discard", recovered: ReturnedDraft) => {
      if (choicePending) return;
      const currentDraft = value();
      clearTimeout(state.timer); setChoosing(true);
      choicePending = port.choose(threadId, ++state.revision, state.version, currentDraft, choice).then(result => {
        if (!state.live) return;
        state.revision = Math.max(state.revision, result.revision); state.version = Math.max(state.version, result.version);
        if (choice === "restore") apply(result.draft);
        setReturned(result.returned);
      }, async error => {
        // A timeout can lose the reply after main has already swapped. Reconcile
        // before allowing a retry so it cannot swap the same draft into both slots.
        try {
          const result = await port.read(threadId);
          if (!state.live) return;
          state.revision = Math.max(state.revision, result.revision); state.version = Math.max(state.version, result.version);
          const emptyCurrent = !currentDraft.text && !currentDraft.imageAttachments.length;
          const swapped = choice === "restore" && equal(result.draft, recovered.draft)
            && (result.returned ? equal(result.returned.draft, currentDraft) : emptyCurrent);
          if (swapped) apply(result.draft);
          setReturned(result.returned);
          if (result.notice) setNotice(result.notice);
          if (swapped || (choice === "discard" && !result.returned)) return;
        } catch { /* Keep the visible composer and other-draft card on transport failure. */ }
        fail(error);
      }).finally(() => { choicePending = undefined; if (state.live) setChoosing(false); });
    };
    const receive = (snapshot: DraftSnapshot, initial = false) => {
      if (!state.live || snapshot.threadId !== threadId) return;
      state.revision = Math.max(state.revision, snapshot.revision);
      if (snapshot.version < state.version) return;
      state.version = snapshot.version;
      if (snapshot.notice) setNotice(snapshot.notice);
      const existing = value();
      const empty = !existing.text && !existing.imageAttachments.length && !current.current.isReading();
      if (initial && empty && !state.touched) apply(snapshot.draft);
      setReturned(snapshot.returned);
      // Refusal hydration is immediate; the async acknowledgement never writes
      // over a later edit. Non-empty composers get an explicit choice instead.
      if (snapshot.returned && empty && (!initial || !state.touched)) choose("restore", snapshot.returned);
    };
    state.loading = port.read(threadId).then(result => { state.loaded = true; receive(result, true); if (state.live) setHydration({ port, threadId: options.threadId }); if (state.touched) void save().catch(fail); }, error => { fail(error); throw error; });
    // Flush owns the rejection too; attach a handler immediately to avoid an
    // unhandled rejection when the page remains open after a failed hydrate.
    void state.loading.catch(fail);
    const save = async () => {
      await state.loading; await choicePending; if (!state.live) return;
      clearTimeout(state.timer);
      if (current.current.isReading()) throw new Error("Wait for the pasted images to finish before leaving this conversation.");
      for (let attempt = 0; attempt < 3; ++attempt) {
        const draft = value(), revision = ++state.revision;
        const result = await port.save(threadId, revision, draft);
        if (!state.live) return;
        state.revision = Math.max(state.revision, result.revision); state.version = Math.max(state.version, result.version);
        if (result.notice) setNotice(result.notice);
        if ((result.revision === revision || result.notice) && equal(draft, value())) { state.last = draft; return; }
      }
      throw new Error("The draft is still changing. Retry navigation after typing finishes.");
    };
    actions.current = {
      flush: save,
      begin: async (id, draft) => {
        try {
          await save(); if (!state.live) return false;
          const result = await port.begin(threadId, ++state.revision, draft, id);
          if (!state.live) return false;
          state.revision = Math.max(state.revision, result.revision); state.version = Math.max(state.version, result.version);
          if (equal(value(), draft)) apply(emptyDraft());
          else {
            const latest = value();
            if (latest.text.startsWith(draft.text) && equal({ ...latest, text: draft.text }, draft)) apply({ ...latest, text: latest.text.slice(draft.text.length) });
            await save();
          }
          return true;
        } catch (error) { fail(error); return false; }
      },
      choose: choice => { if (returnedRef.current) choose(choice, returnedRef.current); },
    };
    const stopChanges = port.onChange(receive), stopFlush = port.onFlush(save);
    return () => { state.live = false; clearTimeout(state.timer); stopChanges(); stopFlush(); };
  }, [options.port, options.threadId]);
  const returnedRef = useRef(returned); returnedRef.current = returned;
  useEffect(() => {
    if (!options.port || switching) return;
    const state = holder.current;
    const draft: Draft = { text: options.text, imageAttachments: options.images, mentionedPaths: [...options.mentions.current] };
    if (equal(state.last, draft)) return;
    if (!state.loaded) return;
    clearTimeout(state.timer);
    state.timer = setTimeout(() => { void actions.current.flush().catch(error => { if (state.live) setNotice(error instanceof Error ? error.message : "Draft save failed."); }); }, 250);
  }, [options.port, options.threadId, options.text, options.images, options.mentions]);
  return { loaded: !options.port || hydration?.port === options.port && hydration.threadId === options.threadId, notice, returned, choosing, edited: () => { holder.current.touched = true; }, begin: (id: string, draft: Draft) => actions.current.begin(id, draft), choose: (choice: "restore" | "discard") => actions.current.choose(choice) };
}
