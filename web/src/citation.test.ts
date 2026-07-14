import { describe, expect, it } from "vitest";

import { dedupHeadingPath, normalizeHeading } from "./citation";

describe("normalizeHeading", () => {
  it("lowercases and strips whitespace/hyphens/underscores", () => {
    expect(normalizeHeading("Kiln Basics")).toBe("kilnbasics");
    expect(normalizeHeading("kiln-basics")).toBe("kilnbasics");
    expect(normalizeHeading("kiln_basics")).toBe("kilnbasics");
  });
});

describe("dedupHeadingPath", () => {
  it("drops the first heading when it restates the title", () => {
    expect(dedupHeadingPath("kiln-basics", ["Kiln Basics", "Firing temperatures"])).toEqual([
      "Firing temperatures",
    ]);
  });

  it("keeps the heading path when the first element does not match the title", () => {
    expect(dedupHeadingPath("handbook", ["Kiln Basics", "Firing temperatures"])).toEqual([
      "Kiln Basics",
      "Firing temperatures",
    ]);
  });

  it("returns an empty array unchanged when the heading path is empty", () => {
    expect(dedupHeadingPath("kiln-basics", [])).toEqual([]);
  });

  it("drops a single restating heading, leaving an empty path", () => {
    expect(dedupHeadingPath("kiln-basics", ["Kiln Basics"])).toEqual([]);
  });
});
