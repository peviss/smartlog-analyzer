import { levelFromSeverity } from "./format";

it.each([
  [0, "critical"],
  [2, "critical"],
  [3, "error"],
  [4, "warn"],
  [5, "info"],
  [6, "info"],
  [7, "debug"],
])("severity %i is %s", (severity, level) => {
  expect(levelFromSeverity(severity)).toBe(level);
});
