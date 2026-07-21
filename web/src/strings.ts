// Single source of truth for every user-facing string in the Kilnworks web UI.
//
// To localize the UI, translate the values in this object — or create a
// locale-specific variant of this file (e.g. `strings.fr.ts`) and swap the
// import in the components that reference `strings` — this is the one place
// to edit. This module intentionally does NOT implement a locale-switching
// runtime, a `t()` helper, or a language selector; it only centralizes the
// English strings so any future localization work has a single, well-scoped
// starting point.

export const strings = {
  app: {
    logout: "Log out",
    builtBy: "built by",
  },
  login: {
    tagline: "Ask your documents.",
    email: "email",
    password: "password",
    signIn: "Sign in",
    signingIn: "Signing in…",
    ssoSignIn: "Sign in with SSO",
    loginFailed: "login failed",
    hintIntro: "No account? Create one: ",
    hintCommand: "kilnworks create-user you@example.com",
    hintOr: " (or ",
    hintDockerCommand: "docker compose exec api kilnworks create-user you@example.com",
    hintClose: ")",
  },
  sources: {
    documents: "Documents",
    connectors: "Connectors",
    all: "All",
    none: "None",
    upload: "+ Upload",
    uploading: "…",
    details: "Details",
    hideDetails: "Hide",
    detailsTitle: "Show file details",
    hideDetailsTitle: "Hide file details",
    meta: {
      type: "Type",
      size: "Size",
      pages: "Pages",
      dimensions: "Dimensions",
      duration: "Duration",
      segments: "Segments",
      rows: "Rows",
      sheets: "Sheets",
      words: "Words",
      chunks: "Chunks",
      uploaded: "Uploaded",
    },
    deleteTitle: "Delete",
    deleteSymbol: "×",
    noConnectors:
      "Connectors let you federate live queries to systems like Salesforce, Microsoft 365, ServiceNow, and HubSpot.",
    setUpConnectors: "Set up connectors →",
    couldntLoadDocuments: "couldn't load documents",
    couldntLoadConnectors: "couldn't load connectors",
    needsLogin: "needs login",
    down: "down",
    noDocuments: "No documents yet.",
    dropHint: "Drop files to upload",
    unsupportedDropped: "none of those file types are supported",
    uploadBusy: "wait for the current upload to finish",
    uploadingFile: (name: string) => `uploading ${name}…`,
    processingCount: (remaining: number, total: number) =>
      `processing ${remaining} of ${total}…`,
    someUploadsFailed: (count: number) =>
      `${count} file${count === 1 ? "" : "s"} couldn't be uploaded`,
    stillProcessing: "still processing — refresh to check status",
    uploadFailed: "upload failed",
    ingestionFailed: "ingestion failed",
    deleteFailed: "delete failed",
    confirmDelete: (title: string) => `Delete "${title}"? This cannot be undone.`,
  },
  chat: {
    placeholder: "Ask your documents…",
    ask: "Ask",
    busy: "…",
    empty: "Upload a document, then ask it a question.",
    requestFailed: "request failed",
    citationHeadingPath: (headingPath: string[]) => ` › ${headingPath.join(" › ")}`,
    // Renders both a PDF page locator ("p. 3") and a media timestamp ("02:15") cleanly
    // on one line after the title/heading, e.g. "kiln-basics › … · p. 3".
    citationLocator: (locator: string) => ` · ${locator}`,
  },
} as const;
