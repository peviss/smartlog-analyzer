import { countLines, guessFormat } from "./importFormat";

it.each([
  ["logs.csv", "", "csv"],
  ["export.TSV", "", "csv"],
  ["events.ndjson", "", "json"],
  ["dump.json", "", "json"],
  ["app.log", "[2026-11-16] ERROR x", "text"],
])("guesses %s from its extension", (name, content, expected) => {
  expect(guessFormat(name, content)).toBe(expected);
});

it.each([
  ['[{"msg": "a"}]', "json"],
  ['{"msg": "a"}', "json"],
  ['{"msg": "a"}\n{"msg": "b"}', "text"], // NDJSON: the text parser reads JSON lines
  ["2026-11-16 ERROR boom", "text"],
])("guesses pasted content %j", (content, expected) => {
  expect(guessFormat(null, content)).toBe(expected);
});

it("counts non-blank lines", () => {
  expect(countLines("a\n\n b \r\nc\n")).toBe(3);
});
