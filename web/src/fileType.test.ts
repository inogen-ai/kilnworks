import { describe, expect, it } from "vitest";

import { extensionOf, fileKind, isSupportedFile, UPLOAD_ACCEPT } from "./fileType";

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

describe("isSupportedFile", () => {
  it("accepts supported types (case-insensitively) and rejects the rest", () => {
    expect(isSupportedFile("notes.md")).toBe(true);
    expect(isSupportedFile("Report.PDF")).toBe(true);
    expect(isSupportedFile("clip.mov")).toBe(true);
    expect(isSupportedFile("malware.exe")).toBe(false);
    expect(isSupportedFile("archive.tar.gz")).toBe(false); // only .gz, unsupported
    expect(isSupportedFile("README")).toBe(false); // no extension
    expect(isSupportedFile("report.")).toBe(false); // trailing dot → no ext
  });

  it("is not fooled by Object prototype keys", () => {
    expect(isSupportedFile("x.constructor")).toBe(false);
    expect(isSupportedFile("x.toString")).toBe(false);
  });
});

describe("UPLOAD_ACCEPT", () => {
  it("stays in sync with what isSupportedFile accepts (no drift)", () => {
    // Every extension advertised to the picker must also pass the drop filter,
    // and vice-versa — they're derived from one map, this locks it in.
    for (const token of UPLOAD_ACCEPT.split(",")) {
      expect(token.startsWith(".")).toBe(true);
      expect(isSupportedFile(`file${token}`)).toBe(true);
    }
    expect(UPLOAD_ACCEPT.split(",").length).toBe(19);
  });
});
