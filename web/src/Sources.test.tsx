import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    listDocuments: vi.fn().mockResolvedValue([]),
    listConnectors: vi.fn().mockResolvedValue([]),
    uploadDocument: vi.fn(),
    getJob: vi.fn(),
  };
});

import { getJob, listConnectors, listDocuments, uploadDocument } from "./api";
import Sources, { emptySelection } from "./Sources";
import { strings } from "./strings";

describe("Sources — connectors", () => {
  it("shows a helpful empty state and hides connector bulk actions when there are none", async () => {
    vi.mocked(listConnectors).mockResolvedValueOnce([]);

    render(
      <Sources
        token="tok"
        onAuthError={vi.fn()}
        selection={emptySelection}
        setSelection={vi.fn()}
        onCatalogChange={vi.fn()}
      />,
    );

    await screen.findByText(/federate live queries/i);

    const link = screen.getByRole("link", { name: /set up connectors/i });
    expect(link).toHaveAttribute(
      "href",
      "https://github.com/inogen-ai/kilnworks#connectors-beta",
    );

    // Documents keeps its own All/None; the Connectors pair must not render
    // for an empty connector list since there's nothing for them to act on.
    expect(screen.getAllByRole("button", { name: strings.sources.all })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: strings.sources.none })).toHaveLength(1);
  });

  it("renders an enabled connector row and toggles selection on click", async () => {
    vi.mocked(listConnectors).mockResolvedValueOnce([
      { name: "salesforce", status: "ready", needs_login: false },
    ]);
    const setSelection = vi.fn();

    render(
      <Sources
        token="tok"
        onAuthError={vi.fn()}
        selection={emptySelection}
        setSelection={setSelection}
        onCatalogChange={vi.fn()}
      />,
    );

    const checkbox = await screen.findByRole("checkbox", { name: "salesforce" });
    expect(checkbox).toBeEnabled();

    setSelection.mockClear();
    await userEvent.click(checkbox);

    expect(setSelection).toHaveBeenCalledTimes(1);
  });
});

describe("Sources — document details", () => {
  const doc = {
    id: "d1",
    source_uri: "file:///data/uploads/manual.pdf",
    title: "manual",
    status: "ready",
    error: null,
    metadata: { size_bytes: 2048, page_count: 3, chunk_count: 7 },
    created_at: "2026-07-16T14:22:00Z",
  };

  it("shows a type icon and expands metadata on Details, collapsing again on Hide", async () => {
    vi.mocked(listDocuments).mockResolvedValueOnce([doc]);

    render(
      <Sources
        token="tok"
        onAuthError={vi.fn()}
        selection={{ documentIds: new Set(["d1"]), connectorNames: new Set() }}
        setSelection={vi.fn()}
        onCatalogChange={vi.fn()}
      />,
    );

    // The row renders a datatype icon (aria-hidden svg) beside the title.
    const row = (await screen.findByText("manual")).closest("li")!;
    expect(row.querySelector("svg.file-icon")).not.toBeNull();

    // Metadata is hidden until Details is clicked.
    expect(screen.queryByText(strings.sources.meta.pages)).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: strings.sources.details }));
    expect(screen.getByText(strings.sources.meta.pages)).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument(); // page_count
    expect(screen.getByText("2.0 KB")).toBeInTheDocument(); // size_bytes formatted

    await userEvent.click(screen.getByRole("button", { name: strings.sources.hideDetails }));
    expect(screen.queryByText(strings.sources.meta.pages)).toBeNull();
  });
});

describe("Sources — multiple upload", () => {
  it("enqueues every selected file, not just the first", async () => {
    vi.mocked(listDocuments).mockResolvedValue([]);
    vi.mocked(uploadDocument).mockResolvedValueOnce(1).mockResolvedValueOnce(2);
    vi.mocked(getJob).mockResolvedValue({
      id: 0,
      kind: "ingest",
      status: "done",
      attempts: 1,
      error: null,
    });

    const { container } = render(
      <Sources
        token="tok"
        onAuthError={vi.fn()}
        selection={emptySelection}
        setSelection={vi.fn()}
        onCatalogChange={vi.fn()}
      />,
    );

    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    expect(input).toHaveAttribute("multiple");

    const a = new File(["a"], "a.md", { type: "text/markdown" });
    const b = new File(["b"], "b.csv", { type: "text/csv" });
    await userEvent.upload(input, [a, b], { applyAccept: false });

    // Both files POST (the old single-file handler would upload only the first).
    await waitFor(() => expect(uploadDocument).toHaveBeenCalledTimes(2));
    expect(uploadDocument).toHaveBeenCalledWith("tok", a);
    expect(uploadDocument).toHaveBeenCalledWith("tok", b);
  });
});

describe("Sources — drag and drop", () => {
  function drop(node: Element, files: File[]) {
    fireEvent.drop(node, { dataTransfer: { files, types: ["Files"], dropEffect: "" } });
  }

  function renderSources() {
    return render(
      <Sources
        token="tok"
        onAuthError={vi.fn()}
        selection={emptySelection}
        setSelection={vi.fn()}
        onCatalogChange={vi.fn()}
      />,
    );
  }

  // NOTE: jsdom dispatches `drop` unconditionally, so these tests exercise the
  // filter → handleUpload path but can't catch a removed handleDragOver
  // preventDefault (which would break the drop in real browsers). Real drag
  // enabling is verified manually in the running app.
  it("uploads only the supported files from a mixed drop", async () => {
    vi.mocked(listDocuments).mockResolvedValue([]);
    vi.mocked(uploadDocument).mockClear();
    vi.mocked(uploadDocument).mockResolvedValueOnce(1);
    vi.mocked(getJob).mockResolvedValue({
      id: 0,
      kind: "ingest",
      status: "done",
      attempts: 1,
      error: null,
    });

    const { container } = renderSources();
    const panel = container.querySelector("aside.sources")!;
    const pdf = new File(["x"], "report.pdf", { type: "application/pdf" });
    const exe = new File(["x"], "tool.exe", { type: "" });
    drop(panel, [pdf, exe]);

    await waitFor(() => expect(uploadDocument).toHaveBeenCalledWith("tok", pdf));
    // the unsupported .exe is dropped from the batch, not uploaded
    expect(uploadDocument).toHaveBeenCalledTimes(1);
  });

  it("rejects an all-unsupported drop with a notice and no upload", async () => {
    vi.mocked(listDocuments).mockResolvedValue([]);
    vi.mocked(uploadDocument).mockClear();

    const { container } = renderSources();
    const panel = container.querySelector("aside.sources")!;
    const exe = new File(["x"], "malware.exe", { type: "" });
    drop(panel, [exe]);

    expect(await screen.findByText(strings.sources.unsupportedDropped)).toBeInTheDocument();
    expect(uploadDocument).not.toHaveBeenCalled();
  });
});
