import { CardShell } from "../../components/shared/CardShell";
import { BtnPrimary } from "../../components/shared/buttons";

interface Props {
  reason: "missing" | "malformed";
  message: string;
  building: boolean;
  onBuild: () => void;
}

export function EmptyState({ reason, message, building, onBuild }: Props) {
  return (
    <div className="absolute inset-0 z-10 flex items-center justify-center p-6 bg-panel text-text overflow-auto">
      <div className="w-full max-w-md">
        <CardShell icon="folder" title={reason === "missing" ? "No index snapshot" : "Snapshot unreadable"}>
          <div className="p-3 flex flex-col items-start gap-4">
            <p className="text-body text-text-2 break-words">
              {reason === "missing"
                ? "Build your project index to explore its files and dependencies."
                : message}
            </p>
            <BtnPrimary onClick={onBuild} disabled={building}>
              {building ? "Building…" : "Build index"}
            </BtnPrimary>
          </div>
        </CardShell>
      </div>
    </div>
  );
}
