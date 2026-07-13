import { afterEach, describe, expect, it, vi } from "vitest";

import {
  askStream,
  deleteDocument,
  errorDetailToMessage,
  listConnectors,
  parseAuthFragment,
} from "./api";

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
    ...init,
  });
}

describe("errorDetailToMessage", () => {
  it("passes through a string detail", () => {
    expect(errorDetailToMessage("not found", 404)).toBe("not found");
  });

  it("joins FastAPI-shaped validation errors by msg", () => {
    const detail = [{ loc: ["body", "question"], msg: "field required", type: "missing" }];
    expect(errorDetailToMessage(detail, 422)).toBe("field required");
  });

  it("joins multiple validation errors", () => {
    const detail = [
      { loc: ["body", "a"], msg: "field required", type: "missing" },
      { loc: ["body", "b"], msg: "must be a string", type: "type_error" },
    ];
    expect(errorDetailToMessage(detail, 422)).toBe("field required; must be a string");
  });

  it("falls back to request failed for null/undefined", () => {
    expect(errorDetailToMessage(null, 500)).toBe("request failed: 500");
    expect(errorDetailToMessage(undefined, 503)).toBe("request failed: 503");
  });

  it("stringifies array items without a msg field", () => {
    expect(errorDetailToMessage([1, "x", { foo: "bar" }], 400)).toBe(
      '1; "x"; {"foo":"bar"}',
    );
  });

  it("falls back to request failed for an empty array", () => {
    expect(errorDetailToMessage([], 400)).toBe("request failed: 400");
  });
});

describe("parseAuthFragment", () => {
  it("parses a token fragment", () => {
    expect(parseAuthFragment("#token=abc")).toEqual({ token: "abc" });
  });

  it("parses an sso_error fragment, decoding it", () => {
    expect(parseAuthFragment("#sso_error=Bad%20thing")).toEqual({ error: "Bad thing" });
  });

  it("returns empty object for an empty string", () => {
    expect(parseAuthFragment("")).toEqual({});
  });

  it("returns empty object for a bare hash", () => {
    expect(parseAuthFragment("#")).toEqual({});
  });

  it("returns empty object for an unrelated fragment", () => {
    expect(parseAuthFragment("#other=1")).toEqual({});
  });

  it("decodes URL-encoded characters in the token", () => {
    expect(parseAuthFragment("#token=abc%2Bdef%3D")).toEqual({ token: "abc+def=" });
  });
});

describe("deleteDocument", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends DELETE to /documents/:id with auth headers", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await deleteDocument("tok", "abc-123");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/documents/abc-123");
    expect(init).toMatchObject({ method: "DELETE" });
    expect(init.headers).toMatchObject({ authorization: "Bearer tok" });
  });

  it("throws ApiError with the server detail on failure", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ detail: "document not found" }, { status: 404 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(deleteDocument("tok", "missing")).rejects.toMatchObject({
      status: 404,
      message: "document not found",
    });
  });
});

describe("listConnectors", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fetches GET /connectors with auth headers and returns parsed connectors", async () => {
    const connectors = [
      { name: "slack", status: "ok", needs_login: false },
      { name: "gdrive", status: "down", needs_login: true },
    ];
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(connectors));
    vi.stubGlobal("fetch", fetchMock);

    const result = await listConnectors("tok");

    expect(result).toEqual(connectors);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/connectors");
    expect(init.headers).toMatchObject({ authorization: "Bearer tok" });
  });
});

describe("askStream request body", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function emptyStream(): ReadableStream<Uint8Array> {
    return new ReadableStream({
      start(controller) {
        controller.close();
      },
    });
  }

  async function drain(gen: AsyncGenerator<unknown>) {
    for await (const _event of gen) {
      // exhaust the generator so fetch actually gets called
    }
  }

  it("omits source_ids and connectors when not provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(emptyStream(), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await drain(askStream("tok", "hi"));

    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(init.body as string)).toEqual({ question: "hi" });
  });

  it("includes source_ids and connectors when provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(emptyStream(), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await drain(askStream("tok", "hi", undefined, ["doc-1", "doc-2"], ["slack"]));

    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(init.body as string)).toEqual({
      question: "hi",
      source_ids: ["doc-1", "doc-2"],
      connectors: ["slack"],
    });
  });
});
