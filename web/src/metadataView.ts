// Turns a document's raw metadata object (from the API) plus its upload time into
// an ordered list of {label, value} rows for display. Only keys that are present
// and well-formed produce a row, so a text file and a video render different sets
// naturally. Pure and unit-tested.
import { strings } from "./strings";

export type MetaRow = { label: string; value: string };

export function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

export function humanDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const ss = String(secs).padStart(2, "0");
  if (hours > 0) return `${hours}:${String(minutes).padStart(2, "0")}:${ss}`;
  return `${minutes}:${ss}`;
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

// `typeLabel` is derived from the extension (see fileType.ts) and passed in so the
// "Type" row leads even though it isn't part of the raw metadata object.
export function metadataRows(
  meta: Record<string, unknown>,
  createdAt: string | null,
  typeLabel: string,
): MetaRow[] {
  const m = strings.sources.meta;
  const rows: MetaRow[] = [];
  const add = (label: string, value: string | null) => {
    if (value) rows.push({ label, value });
  };
  const count = (value: unknown): string | null => {
    const n = asNumber(value);
    return n === null ? null : n.toLocaleString();
  };

  add(m.type, typeLabel);
  const size = asNumber(meta.size_bytes);
  add(m.size, size === null ? null : humanSize(size));
  add(m.pages, count(meta.page_count));
  const width = asNumber(meta.width);
  const height = asNumber(meta.height);
  add(m.dimensions, width !== null && height !== null ? `${width} × ${height}` : null);
  const duration = asNumber(meta.duration_seconds);
  add(m.duration, duration === null ? null : humanDuration(duration));
  add(m.segments, count(meta.segment_count));
  add(m.rows, count(meta.row_count));
  add(m.sheets, count(meta.sheet_count));
  add(m.words, count(meta.word_count));
  add(m.chunks, count(meta.chunk_count));
  add(m.uploaded, createdAt ? formatDate(createdAt) : null);
  return rows;
}
