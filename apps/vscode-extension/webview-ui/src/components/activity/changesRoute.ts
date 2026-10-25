import { createContext } from "react";
import type { RecordedEditSource } from "../../transcript/groupTranscript";

/** Set only by hosts with a Changes tool (desktop): applied changed-file links open it there.
 * Absent (VS Code), the transcript keeps its own behaviour: native editor links and inline diffs. */
export const ChangesRoute = createContext<((path: string, source?: RecordedEditSource) => void) | undefined>(undefined);
