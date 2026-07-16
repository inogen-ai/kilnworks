import type { ReactNode } from "react";

import type { FileCategory } from "./fileType";

// Small line icons (16×16, stroke = currentColor) for each file category, shown
// beside a document's title. currentColor lets them inherit the row's ink color
// and stay legible in both themes.
const PATHS: Record<FileCategory, ReactNode> = {
  document: (
    <>
      <path d="M4 1.5h5L12.5 5v9.5H4z" />
      <path d="M9 1.5V5h3.5" />
      <path d="M6 8h5M6 10.5h5" />
    </>
  ),
  table: (
    <>
      <rect x="2.5" y="3" width="11" height="10" rx="1" />
      <path d="M2.5 6.5h11M2.5 9.75h11M6.25 3v10M9.75 3v10" />
    </>
  ),
  image: (
    <>
      <rect x="2.5" y="3" width="11" height="10" rx="1" />
      <circle cx="6" cy="6.5" r="1.2" />
      <path d="M3 12l3.5-3.5 3 2.5L12 8l1.5 1.5" />
    </>
  ),
  audio: (
    <>
      <path d="M9.5 2.5v7.2" />
      <circle cx="7.7" cy="10" r="1.8" />
      <path d="M9.5 2.5l3 1v3" />
    </>
  ),
  video: (
    <>
      <rect x="2" y="4" width="8.5" height="8" rx="1" />
      <path d="M10.5 7l3.5-2v6l-3.5-2z" />
    </>
  ),
  file: (
    <>
      <path d="M4 1.5h5L12.5 5v9.5H4z" />
      <path d="M9 1.5V5h3.5" />
    </>
  ),
};

export default function FileIcon({ category }: { category: FileCategory }) {
  return (
    <svg
      className="file-icon"
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {PATHS[category]}
    </svg>
  );
}
