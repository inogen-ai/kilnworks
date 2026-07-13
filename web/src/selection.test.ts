import { describe, expect, it } from "vitest";

import { effectiveSelection } from "./selection";

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
