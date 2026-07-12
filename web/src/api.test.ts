import { describe, expect, it } from "vitest";

import { errorDetailToMessage, parseAuthFragment } from "./api";

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
