import { describe, expect, it } from "vitest";

import { parseSseBlock, readSseStream } from "./sse";

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>) {
  const events = [];
  for await (const event of readSseStream(stream)) events.push(event);
  return events;
}

describe("parseSseBlock", () => {
  it("parses event and json data", () => {
    expect(parseSseBlock('event: delta\ndata: {"text": "hi"}')).toEqual({
      event: "delta",
      data: { text: "hi" },
    });
  });

  it("returns null for blocks without data or with bad json", () => {
    expect(parseSseBlock("event: done")).toBeNull();
    expect(parseSseBlock("event: x\ndata: not-json")).toBeNull();
  });
});

describe("readSseStream", () => {
  it("yields events split across chunk boundaries", async () => {
    const events = await collect(
      streamOf(['event: delta\ndata: {"te', 'xt": "a"}\n\nevent: done\ndata: {}\n\n']),
    );
    expect(events).toEqual([
      { event: "delta", data: { text: "a" } },
      { event: "done", data: {} },
    ]);
  });

  it("handles the real endpoint sequence", async () => {
    const raw =
      'event: delta\ndata: {"text": "Very "}\n\n' +
      'event: delta\ndata: {"text": "hot"}\n\n' +
      'event: answer\ndata: {"text": "Very hot", "citations": [], "model": "fake"}\n\n' +
      "event: done\ndata: {}\n\n";
    const events = await collect(streamOf([raw]));
    expect(events.map((e) => e.event)).toEqual(["delta", "delta", "answer", "done"]);
  });
});
