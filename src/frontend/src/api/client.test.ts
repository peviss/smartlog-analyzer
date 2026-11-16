import { toApiError } from "./client";

describe("toApiError", () => {
  it("reads the API error envelope, keeping extra fields", () => {
    const error = toApiError(502, {
      error: { code: "LLM_TIMEOUT", message: "late", analysis_id: "abc" },
      request_id: "req-1",
    });
    expect(error).toMatchObject({ status: 502, code: "LLM_TIMEOUT", message: "late", requestId: "req-1" });
    expect(error.extra).toEqual({ analysis_id: "abc" });
  });

  it("keeps validation details", () => {
    const details = [{ field: "message", message: "required" }];
    expect(toApiError(422, { error: { code: "VALIDATION_ERROR", message: "bad", details } }).details).toEqual(
      details,
    );
  });

  it("handles responses that are not the API envelope (proxy pages, rate limit)", () => {
    expect(toApiError(429, "<html>").code).toBe("RATE_LIMITED");
    expect(toApiError(504, undefined)).toMatchObject({ code: "HTTP_ERROR", status: 504 });
    expect(toApiError(413, "<html>").code).toBe("PAYLOAD_TOO_LARGE");
  });
});
