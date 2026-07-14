// Pure helpers for rendering a Citation's heading path.
//
// The backend (kilnworks/core/query.py#_label) already drops a leading heading
// element from the LLM-facing context label when it's just a restatement of the
// document title (e.g. title "kiln-basics" + H1 "Kiln Basics"). It deliberately
// keeps `Citation.heading_path` raw/undeduped on the wire, since that's the
// source-of-truth data. The UI applies the identical dedup rule at render time
// so the citation list doesn't show the same redundant pair.

// Mirrors `_normalize_heading` in kilnworks/core/query.py: lowercase, and strip
// whitespace/hyphens/underscores, so "Kiln Basics", "kiln-basics", and
// "kiln_basics" all compare equal.
export function normalizeHeading(text: string): string {
  return text.replace(/[\s\-_]+/g, "").toLowerCase();
}

// Returns the heading path to render for a citation: the first element is
// dropped when it normalizes-equal to the document title (a redundant
// restatement), otherwise the heading path is returned unchanged.
export function dedupHeadingPath(title: string, headingPath: string[]): string[] {
  if (headingPath.length === 0) return headingPath;
  if (normalizeHeading(headingPath[0]) === normalizeHeading(title)) {
    return headingPath.slice(1);
  }
  return headingPath;
}
