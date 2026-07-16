// Maps a document's file extension to a display category (which drives the icon)
// and a human label (shown as the "Type" metadata row). Pure — no JSX — so it's
// unit-testable on its own.

export type FileCategory = "document" | "table" | "image" | "audio" | "video" | "file";

const BY_EXTENSION: Record<string, { category: FileCategory; label: string }> = {
  md: { category: "document", label: "Markdown" },
  txt: { category: "document", label: "Text" },
  pdf: { category: "document", label: "PDF document" },
  docx: { category: "document", label: "Word document" },
  html: { category: "document", label: "HTML" },
  htm: { category: "document", label: "HTML" },
  csv: { category: "table", label: "CSV table" },
  tsv: { category: "table", label: "TSV table" },
  xlsx: { category: "table", label: "Excel spreadsheet" },
  png: { category: "image", label: "PNG image" },
  jpg: { category: "image", label: "JPEG image" },
  jpeg: { category: "image", label: "JPEG image" },
  gif: { category: "image", label: "GIF image" },
  webp: { category: "image", label: "WebP image" },
  mp3: { category: "audio", label: "MP3 audio" },
  wav: { category: "audio", label: "WAV audio" },
  m4a: { category: "audio", label: "M4A audio" },
  mp4: { category: "video", label: "MP4 video" },
  mov: { category: "video", label: "QuickTime video" },
};

export function extensionOf(sourceUri: string): string {
  const name = sourceUri.split(/[/\\]/).pop() ?? sourceUri;
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
}

export function fileKind(sourceUri: string): { category: FileCategory; label: string } {
  return BY_EXTENSION[extensionOf(sourceUri)] ?? { category: "file", label: "File" };
}
