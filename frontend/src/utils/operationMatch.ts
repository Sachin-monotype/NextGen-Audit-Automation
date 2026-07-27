/** Match Generate scenario keys to stored comparison operation names. */
export function operationHasStoredResult(
  operation: string,
  stored: Record<string, string>,
): boolean {
  const op = (operation || "").trim();
  if (!op || !Object.keys(stored).length) return false;
  if (stored[op]) return true;
  const base = op.split("(", 1)[0];
  for (const key of Object.keys(stored)) {
    if (key === op) return true;
    const keyBase = key.split("(", 1)[0];
    if (keyBase === base && op.includes("(")) return true;
    if (key.startsWith(`${op}(`) || op.startsWith(`${key}(`)) return true;
  }
  return false;
}

export function storedComparedAt(
  operation: string,
  stored: Record<string, string>,
): string {
  const op = (operation || "").trim();
  if (stored[op]) return stored[op];
  for (const [key, ts] of Object.entries(stored)) {
    if (operationHasStoredResult(op, { [key]: ts })) return ts;
  }
  return "";
}
