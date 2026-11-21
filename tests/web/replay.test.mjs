// Continuous replay: car positions between two stored steps.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { between, advance } = require("../../web/replay.js");

// poses are [id, x, y, heading, speed]
const a = [[0, 100, 4, 0, 25], [3, 140, 8, 0, 20], [5, 60, 0, 0, 22]];
const b = [[0, 125, 8, 0.2, 26], [3, 160, 8, 0, 20], [7, 300, 12, 0, 30]];

test("halfway, each car is halfway between its two positions, matched by id", () => {
  const m = between(a, b, 0.5);
  assert.deepEqual(m[0], [0, 112.5, 6, 0.1, 25.5]); // the ego stays first
  assert.deepEqual(m.find((p) => p[0] === 3), [3, 150, 8, 0, 20]);
});

test("a car in only one of the two steps is shown as it is", () => {
  const m = between(a, b, 0.5);
  assert.deepEqual(m.find((p) => p[0] === 5), [5, 60, 0, 0, 22]);
  assert.deepEqual(m.find((p) => p[0] === 7), [7, 300, 12, 0, 30]);
});

test("at 0 and 1 the stored steps come back exactly", () => {
  const byId = (list) => Object.fromEntries(list.map((p) => [p[0], p]));
  const start = byId(between(a, b, 0));
  const end = byId(between(a, b, 1));
  for (const p of a) assert.deepEqual(start[p[0]], p);
  for (const p of b) assert.deepEqual(end[p[0]], p);
});

test("playback advances by speed x elapsed seconds (one step is one simulated second) and stops at the end", () => {
  assert.deepEqual(advance(3.25, 0.5, 2, 10), { pos: 4.25, done: false });
  assert.deepEqual(advance(8.5, 1, 4, 10), { pos: 9, done: true }); // last step index is 9
});
