import { describe, expect, it } from "vitest";

import { humanDuration, humanSize, metadataRows } from "./metadataView";
import { strings } from "./strings";

describe("humanSize", () => {
  it("formats bytes, KB, and MB", () => {
    expect(humanSize(512)).toBe("512 B");
    expect(humanSize(2048)).toBe("2.0 KB");
    expect(humanSize(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(humanSize(20 * 1024 * 1024)).toBe("20 MB");
  });
});

describe("humanDuration", () => {
  it("formats mm:ss and h:mm:ss", () => {
    expect(humanDuration(72)).toBe("1:12");
    expect(humanDuration(5)).toBe("0:05");
    expect(humanDuration(3661)).toBe("1:01:01");
  });
});

describe("metadataRows", () => {
  it("leads with Type, then only renders keys that are present", () => {
    const rows = metadataRows(
      { size_bytes: 2048, page_count: 3, chunk_count: 7 },
      "2026-07-16T14:22:00Z",
      "PDF document",
    );
    const m = strings.sources.meta;
    const labels = rows.map((r) => r.label);
    expect(labels[0]).toBe(m.type);
    expect(rows.find((r) => r.label === m.type)?.value).toBe("PDF document");
    expect(rows.find((r) => r.label === m.pages)?.value).toBe("3");
    expect(rows.find((r) => r.label === m.chunks)?.value).toBe("7");
    // Keys absent from the metadata object produce no row.
    expect(labels).not.toContain(m.duration);
    expect(labels).not.toContain(m.dimensions);
  });

  it("formats media dimensions and duration", () => {
    const rows = metadataRows(
      { width: 320, height: 200, duration_seconds: 252, segment_count: 9 },
      null,
      "MP4 video",
    );
    const m = strings.sources.meta;
    expect(rows.find((r) => r.label === m.dimensions)?.value).toBe("320 × 200");
    expect(rows.find((r) => r.label === m.duration)?.value).toBe("4:12");
    expect(rows.find((r) => r.label === m.segments)?.value).toBe("9");
    // No createdAt → no Uploaded row.
    expect(rows.map((r) => r.label)).not.toContain(m.uploaded);
  });

  it("ignores malformed values rather than rendering NaN", () => {
    const rows = metadataRows({ size_bytes: "big", page_count: null }, null, "File");
    const labels = rows.map((r) => r.label);
    expect(labels).toEqual([strings.sources.meta.type]);
  });
});
