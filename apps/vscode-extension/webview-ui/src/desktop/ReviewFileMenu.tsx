import { useState } from "react";
import { Icon } from "../components/Icon";
/** Local path only; clipboard failure is visible, and no executable action is offered. */
export function ReviewFileMenu({ path, open }: { path: string; open?: () => void }) {
  const [notice, setNotice] = useState("");
  return <details className="review-file-menu"><summary aria-label={`More actions for ${path}`} title="File actions"><Icon name="more" size={15} /></summary><div>
    <button type="button" onClick={() => { void navigator.clipboard?.writeText(path).then(() => setNotice("Path copied."), () => setNotice("Could not copy this path.")).catch(() => setNotice("Could not copy this path.")); if (!navigator.clipboard) setNotice("Clipboard is unavailable."); }}>Copy path</button>
    {open && <button type="button" onClick={open}>Open current file</button>}{notice && <p role="status">{notice}</p>}
  </div></details>;
}
