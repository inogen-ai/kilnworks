// Regression guard for the infinite-fetch-loop bug: an un-memoized
// handleLogout in App.tsx used to get a fresh function identity every
// render, which Sources/Chat's data-fetching effects list as a dependency
// (via onAuthError), causing documents/connectors to be re-fetched forever.
// If a future change reintroduces an unstable callback here, the call
// counts below explode and this test fails.
import { act, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    listDocuments: vi.fn().mockResolvedValue([]),
    listConnectors: vi.fn().mockResolvedValue([]),
    getAuthConfig: vi.fn().mockResolvedValue({ sso_enabled: false }),
    askStream: vi.fn(async function* () {}),
  };
});

import { listConnectors, listDocuments } from "./api";
import App from "./App";

describe("App", () => {
  beforeEach(() => {
    sessionStorage.setItem("kilnworks-token", "test-token");
    vi.mocked(listDocuments).mockClear();
    vi.mocked(listConnectors).mockClear();
  });

  afterEach(() => {
    sessionStorage.clear();
  });

  it("fetches documents and connectors a small, bounded number of times (no infinite loop)", async () => {
    render(<App />);

    await waitFor(() => {
      expect(listDocuments).toHaveBeenCalled();
      expect(listConnectors).toHaveBeenCalled();
    });

    // Give a runaway effect loop (unstable callback identity re-triggering
    // the fetch effect every render) a chance to manifest.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 150));
    });

    expect(vi.mocked(listDocuments).mock.calls.length).toBeLessThanOrEqual(2);
    expect(vi.mocked(listConnectors).mock.calls.length).toBeLessThanOrEqual(2);
  });
});
