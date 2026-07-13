// Decides what to send the backend for a source/connector selection.
//
// Semantics (see api.ts#askStream): `undefined` means "search everything",
// `[]` means "search nothing", and `[a, b]` means "search just these".
//
// We only ever want to send an explicit array once the user has genuinely
// narrowed the selection to a proper subset of what we know about. Before
// the initial catalog load completes, or once the selection happens to
// cover every known item again, we fall back to `undefined` so the backend
// searches everything currently on file — including documents that finish
// ingesting after the page loaded.
export function effectiveSelection(
  selected: Set<string>,
  known: string[],
  loaded: boolean,
): string[] | undefined {
  if (!loaded) return undefined;
  const coversAll = known.every((id) => selected.has(id));
  if (coversAll) return undefined;
  // Filter to known items so a stale selected id (e.g. a deleted document
  // still lingering in the Set) never leaks into the request.
  return known.filter((id) => selected.has(id));
}
