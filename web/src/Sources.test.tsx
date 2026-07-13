import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    listDocuments: vi.fn().mockResolvedValue([]),
    listConnectors: vi.fn().mockResolvedValue([]),
  };
});

import { listConnectors } from "./api";
import Sources, { emptySelection } from "./Sources";

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
    expect(screen.getAllByRole("button", { name: "All" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "None" })).toHaveLength(1);
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
