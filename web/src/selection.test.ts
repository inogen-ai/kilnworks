import { describe, expect, it } from "vitest";

import { askPayload, effectiveSelection } from "./selection";

describe("effectiveSelection", () => {
  it("returns undefined before the catalog has loaded, regardless of selection", () => {
    expect(effectiveSelection(new Set(), [], false)).toBeUndefined();
    expect(effectiveSelection(new Set(["a"]), ["a", "b"], false)).toBeUndefined();
  });

  it("returns undefined once the selection covers every known item", () => {
    expect(effectiveSelection(new Set(["a", "b"]), ["a", "b"], true)).toBeUndefined();
  });

  it("returns an empty array when nothing is selected", () => {
    expect(effectiveSelection(new Set(), ["a", "b"], true)).toEqual([]);
  });

  it("returns the explicit subset when genuinely narrowed", () => {
    expect(effectiveSelection(new Set(["a"]), ["a", "b"], true)).toEqual(["a"]);
  });

  it("drops a stale selected id that is no longer known", () => {
    expect(effectiveSelection(new Set(["a", "stale"]), ["a", "b"], true)).toEqual(["a"]);
  });

  it("treats an empty known set as trivially fully-covered", () => {
    expect(effectiveSelection(new Set(), [], true)).toBeUndefined();
  });
});

describe("askPayload", () => {
  // Regression guard for the Critical: with exactly one connector
  // configured and checked (the default state), the old code computed
  // `connectors` via `effectiveSelection`, which collapses a full-coverage
  // selection to `undefined`. The backend treats an omitted/`undefined`
  // `connectors` field as "query no connectors" — so federation was
  // unreachable from the UI by default. `askPayload` must instead send the
  // explicit list, `["crm"]`, not `undefined`.
  it("sends the explicit connector name when the sole configured connector is checked (default state)", () => {
    const selection = {
      documentIds: new Set<string>(),
      connectorNames: new Set(["crm"]),
    };
    const catalog = {
      loaded: true,
      documentIds: [],
      connectorNames: ["crm"],
    };
    const { connectors } = askPayload(selection, catalog);
    expect(connectors).toEqual(["crm"]);
    expect(connectors).not.toBeUndefined();
    // Sanity check against the bug: the old (wrong) implementation reused
    // effectiveSelection for connectors too, which — because the single
    // known connector is fully covered by the selection — returns
    // undefined here. Assert that's a different value than what we send.
    const buggyConnectors = effectiveSelection(
      selection.connectorNames,
      catalog.connectorNames,
      catalog.loaded,
    );
    expect(buggyConnectors).toBeUndefined();
    expect(buggyConnectors).not.toEqual(connectors);
  });

  it("excludes an unchecked connector from the explicit list", () => {
    const selection = {
      documentIds: new Set<string>(),
      connectorNames: new Set(["crm"]),
    };
    const catalog = {
      loaded: true,
      documentIds: [],
      connectorNames: ["crm", "helpdesk"],
    };
    const { connectors } = askPayload(selection, catalog);
    expect(connectors).toEqual(["crm"]);
  });

  it("sends an empty array when no connectors are checked", () => {
    const selection = {
      documentIds: new Set<string>(),
      connectorNames: new Set<string>(),
    };
    const catalog = {
      loaded: true,
      documentIds: [],
      connectorNames: ["crm", "helpdesk"],
    };
    const { connectors } = askPayload(selection, catalog);
    expect(connectors).toEqual([]);
  });

  it("documents: sends undefined (search-all) when every document is selected", () => {
    const selection = {
      documentIds: new Set(["a", "b"]),
      connectorNames: new Set<string>(),
    };
    const catalog = {
      loaded: true,
      documentIds: ["a", "b"],
      connectorNames: [],
    };
    const { sourceIds } = askPayload(selection, catalog);
    expect(sourceIds).toBeUndefined();
  });

  it("documents: sends the explicit subset when genuinely narrowed", () => {
    const selection = {
      documentIds: new Set(["a"]),
      connectorNames: new Set<string>(),
    };
    const catalog = {
      loaded: true,
      documentIds: ["a", "b"],
      connectorNames: [],
    };
    const { sourceIds } = askPayload(selection, catalog);
    expect(sourceIds).toEqual(["a"]);
  });

  it("documents: sends an empty array when no documents are selected", () => {
    const selection = {
      documentIds: new Set<string>(),
      connectorNames: new Set<string>(),
    };
    const catalog = {
      loaded: true,
      documentIds: ["a", "b"],
      connectorNames: [],
    };
    const { sourceIds } = askPayload(selection, catalog);
    expect(sourceIds).toEqual([]);
  });

  it("sends undefined for both fields before the catalog has loaded", () => {
    const selection = {
      documentIds: new Set(["a"]),
      connectorNames: new Set(["crm"]),
    };
    const catalog = {
      loaded: false,
      documentIds: ["a", "b"],
      connectorNames: ["crm"],
    };
    const { sourceIds, connectors } = askPayload(selection, catalog);
    expect(sourceIds).toBeUndefined();
    expect(connectors).toBeUndefined();
  });
});
