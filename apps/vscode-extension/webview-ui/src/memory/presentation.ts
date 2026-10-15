export function readable(value: string): string {
  return value.replace(/_/g, " ");
}

export function memoryTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

export function provenanceLabel(value: string): string {
  return ({ tool_verified: "Verified with tools", user_requested: "Saved at the user's request",
    agent_nomination: "Suggested by the assistant", user_explicit: "Explicitly provided by the user",
  } as Record<string, string>)[value] ?? readable(value);
}
