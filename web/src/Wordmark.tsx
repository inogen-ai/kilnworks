// The Glaze brand mark + wordmark lockup — a vessel silhouette (ember bowl
// outline, celadon glaze-line) beside "Kilnworks" set in serif, with "Kiln"
// in ember italic. See docs/superpowers/specs/2026-07-13-brand-identity-design.md.
export default function Wordmark({ as: Tag = "span" }: { as?: "span" | "h1" }) {
  return (
    <Tag className="wordmark">
      <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">
        <path
          d="M6.5 3.2 H17.5 L15.8 6.6 a6.6 6.6 0 1 1 -7.6 0 Z"
          fill="none"
          stroke="var(--ember)"
          strokeWidth="1.7"
          strokeLinejoin="round"
        />
        <path
          d="M8.6 12.4 a3.6 3.6 0 0 0 6.8 0"
          fill="none"
          stroke="var(--celadon)"
          strokeWidth="1.7"
          strokeLinecap="round"
        />
      </svg>
      <span className="wm">
        <i>Kiln</i>works
      </span>
    </Tag>
  );
}
