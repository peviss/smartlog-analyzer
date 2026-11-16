import { parseIngestInput } from "./ingest";

describe("parseIngestInput", () => {
  it("sends an object as one log", () => {
    expect(parseIngestInput('{"message": "x"}')).toEqual({ kind: "single", doc: { message: "x" } });
  });

  it("sends an array or a {logs} body as a batch", () => {
    expect(parseIngestInput("[{}, {}]")).toEqual({ kind: "batch", logs: [{}, {}] });
    expect(parseIngestInput('{"logs": [{}]}')).toEqual({ kind: "batch", logs: [{}] });
  });

  it("treats an object with a logs field and other fields as a single document", () => {
    expect(parseIngestInput('{"logs": [], "message": "x"}').kind).toBe("single");
  });

  it.each([
    ["", "Pegá un documento JSON."],
    ["{nope", "JSON inválido"],
    ["[]", "La lista está vacía."],
    ["42", "Se espera un objeto"],
  ])("rejects %j", (text, message) => {
    const result = parseIngestInput(text);
    expect(result.kind).toBe("invalid");
    expect(result.kind === "invalid" && result.message).toContain(message);
  });

  it("enforces the batch limit before calling the API", () => {
    const text = JSON.stringify(Array.from({ length: 1001 }, () => ({})));
    expect(parseIngestInput(text)).toMatchObject({ kind: "invalid" });
  });
});
