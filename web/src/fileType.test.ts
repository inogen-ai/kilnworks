import { describe, expect, it } from "vitest";

import { extensionOf, fileKind } from "./fileType";

describe("extensionOf", () => {
  it("reads the extension from a file:// URI, lowercased", () => {
    expect(extensionOf("file:///data/uploads/Report.PDF")).toBe("pdf");
    expect(extensionOf("kiln-basics.md")).toBe("md");
  });

  it("returns empty string when there is no extension", () => {
    expect(extensionOf("file:///data/uploads/README")).toBe("");
  });
});

describe("fileKind", () => {
  it("maps each supported type to its category and label", () => {
    expect(fileKind("a.pdf")).toEqual({ category: "document", label: "PDF document" });
    expect(fileKind("a.csv")).toEqual({ category: "table", label: "CSV table" });
    expect(fileKind("a.png")).toEqual({ category: "image", label: "PNG image" });
    expect(fileKind("a.mp3")).toEqual({ category: "audio", label: "MP3 audio" });
    expect(fileKind("a.mov")).toEqual({ category: "video", label: "QuickTime video" });
  });

  it("falls back to a generic file kind for unknown extensions", () => {
    expect(fileKind("a.xyz")).toEqual({ category: "file", label: "File" });
  });
});
