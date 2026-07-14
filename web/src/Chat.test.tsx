import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    askStream: vi.fn(),
  };
});

import { askStream } from "./api";
import Chat from "./Chat";
import { emptyCatalog, emptySelection } from "./Sources";
import type { SseEvent } from "./sse";
import { strings } from "./strings";

// Minimal async-generator helper: yields one "answer" SSE event carrying the
// given citations, mirroring what askStream (web/src/api.ts) produces once
// the backend's terminal `answer` event lands.
function fakeAskStream(citations: unknown[]) {
  return async function* (): AsyncGenerator<SseEvent> {
    yield {
      event: "answer",
      data: { text: "Fired at high heat.", citations, model: "fake" },
    };
  };
}

async function ask(question: string) {
  const input = screen.getByPlaceholderText(/ask your documents/i);
  await userEvent.type(input, question);
  await userEvent.click(screen.getByRole("button", { name: /ask/i }));
}

describe("Chat — strings wiring", () => {
  it("sources its placeholder, empty state, and Ask button text from strings.ts", () => {
    render(
      <Chat
        token="tok"
        onAuthError={vi.fn()}
        selection={emptySelection}
        catalog={emptyCatalog}
      />,
    );

    expect(screen.getByPlaceholderText(strings.chat.placeholder)).toBeInTheDocument();
    expect(screen.getByText(strings.chat.empty)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: strings.chat.ask })).toBeInTheDocument();
  });
});

describe("Chat — citation rendering", () => {
  it("renders heading path and media locator alongside the citation", async () => {
    vi.mocked(askStream).mockImplementation(
      fakeAskStream([
        {
          index: 1,
          chunk_id: "c1",
          source_uri: "file:///kiln-basics.md",
          title: "kiln-basics",
          heading_path: ["Firing temperatures"],
          locator: null,
        },
        {
          index: 3,
          chunk_id: "c3",
          source_uri: "file:///onboarding-call.mp3",
          title: "onboarding-call",
          heading_path: [],
          locator: "02:15",
        },
      ]) as unknown as typeof askStream,
    );

    render(
      <Chat
        token="tok"
        onAuthError={vi.fn()}
        selection={emptySelection}
        catalog={emptyCatalog}
      />,
    );

    await ask("How hot?");

    expect(await screen.findByText("[1] kiln-basics › Firing temperatures")).toBeInTheDocument();
    expect(await screen.findByText("[3] onboarding-call · 02:15")).toBeInTheDocument();
  });

  it("renders a PDF page locator alongside the citation", async () => {
    vi.mocked(askStream).mockImplementation(
      fakeAskStream([
        {
          index: 1,
          chunk_id: "c1",
          source_uri: "file:///handbook.pdf",
          title: "handbook",
          heading_path: ["Kiln Basics"],
          locator: "p. 3",
        },
      ]) as unknown as typeof askStream,
    );

    render(
      <Chat
        token="tok"
        onAuthError={vi.fn()}
        selection={emptySelection}
        catalog={emptyCatalog}
      />,
    );

    await ask("How hot?");

    expect(
      await screen.findByText("[1] handbook › Kiln Basics · p. 3"),
    ).toBeInTheDocument();
  });

  it("drops a leading heading that just restates the document title", async () => {
    vi.mocked(askStream).mockImplementation(
      fakeAskStream([
        {
          index: 1,
          chunk_id: "c1",
          source_uri: "file:///kiln-basics.md",
          title: "kiln-basics",
          heading_path: ["Kiln Basics", "Firing temperatures"],
          locator: null,
        },
      ]) as unknown as typeof askStream,
    );

    render(
      <Chat
        token="tok"
        onAuthError={vi.fn()}
        selection={emptySelection}
        catalog={emptyCatalog}
      />,
    );

    await ask("How hot?");

    expect(
      await screen.findByText("[1] kiln-basics › Firing temperatures"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Kiln Basics › Firing temperatures/)).not.toBeInTheDocument();
  });

  it("renders a plain citation when there is no heading path or locator", async () => {
    vi.mocked(askStream).mockImplementation(
      fakeAskStream([
        {
          index: 1,
          chunk_id: "c1",
          source_uri: "file:///doc.md",
          title: "doc",
          heading_path: [],
          locator: null,
        },
      ]) as unknown as typeof askStream,
    );

    render(
      <Chat
        token="tok"
        onAuthError={vi.fn()}
        selection={emptySelection}
        catalog={emptyCatalog}
      />,
    );

    await ask("How hot?");

    expect(await screen.findByText("[1] doc")).toBeInTheDocument();
  });
});
