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

// Selection/Catalog shapes, mirrored from Sources.tsx, so this module has no
// dependency on the component tree.
type SelectionInput = {
  documentIds: Set<string>;
  connectorNames: Set<string>;
};

type CatalogInput = {
  loaded: boolean;
  documentIds: string[];
  connectorNames: string[];
};

// Computes the exact body fields Chat should hand to askStream.
//
// Documents (`sourceIds`) use `effectiveSelection`: `undefined` means
// "search all documents", which is the correct and desired default.
//
// Connectors are DIFFERENT: the backend only federates to connectors whose
// names are explicitly listed in the request. `undefined`/`[]`/an omitted
// `connectors` field all mean "query no connectors". So connectors must
// NEVER be collapsed to `undefined` just because every known connector is
// checked — that would silently disable federation entirely. Instead we
// always send the explicit list of checked connector names (once the
// catalog has loaded); an empty array is a legitimate, honest "none".
export function askPayload(
  selection: SelectionInput,
  catalog: CatalogInput,
): { sourceIds: string[] | undefined; connectors: string[] | undefined } {
  const sourceIds = effectiveSelection(
    selection.documentIds,
    catalog.documentIds,
    catalog.loaded,
  );
  const connectors = catalog.loaded
    ? catalog.connectorNames.filter((name) => selection.connectorNames.has(name))
    : undefined;
  return { sourceIds, connectors };
}
