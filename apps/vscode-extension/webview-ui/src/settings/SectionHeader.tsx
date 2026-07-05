import type { ReactNode } from "react";
import { Icon } from "../components/Icon";
import { FIELD } from "./ui";

interface SearchProps {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}

interface Props {
  title: string;
  description: string;
  search?: SearchProps;
  /** Right-aligned affordance on the search row (e.g. an add button). */
  action?: ReactNode;
}

/** Copilot-style section header: big title, one-line description, optional search+action row. */
export function SectionHeader({ title, description, search, action }: Props) {
  return (
    <header className="mb-4 flex flex-col gap-1">
      <h1 className="text-base font-semibold text-text">{title}</h1>
      <p className="text-xs leading-relaxed text-text-3">{description}</p>
      {(search || action) && (
        <div className="mt-2 flex items-center gap-2">
          {search && (
            <div className="relative flex-1">
              <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-4">
                <Icon name="search" size={11} />
              </span>
              <input
                className={`${FIELD} w-full pl-7`}
                value={search.value}
                placeholder={search.placeholder ?? "Type to search…"}
                onChange={(e) => search.onChange(e.target.value)}
              />
            </div>
          )}
          {action}
        </div>
      )}
    </header>
  );
}
