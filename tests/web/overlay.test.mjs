// Issue 06: the perturbation overlay's maths. Run with `node --test "tests/web/*.test.mjs"`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const Overlay = require("../../web/overlay.js");
const Replay = require("../../web/replay.js");

const meta = (attack = "pgd", epsilon = 0.01) => ({
  settings: { attack, epsilon },
  obsSpec: { ranges: { x: [-200, 200], y: [-16, 16], vx: [-80, 80], vy: [-80, 80] } },
});

function step({ clean, perturbed, action = 1, cleanAction = 1, ego = [0, 100, 4, 0, 25] }) {
  return { obs: clean, obsPerturbed: perturbed, action, cleanAction, poses: [ego] };
}

function rows(list) {
  const out = new Array(50).fill(0);
  list.forEach((r, i) => r.forEach((v, j) => (out[i * 5 + j] = v)));
  return out;
}

test("denormalise maps [-1, 1] back to the feature range", () => {
  assert.equal(Overlay.denorm(-1, [-200, 200]), -200);
  assert.equal(Overlay.denorm(0, [-200, 200]), 0);
  assert.equal(Overlay.denorm(1, [-16, 16]), 16);
  assert.equal(Overlay.denorm(0.5, [0, 10]), 7.5);
});

test("one ghost per observed row 1-9, at the ego's true position plus the perturbed offset", () => {
  const clean = rows([[1, 0.5, 0.25, 0, 0], [1, 0.1, 0.25, 0, 0], [0, 0, 0, 0, 0], [1, -0.05, -0.25, 0, 0]]);
  const perturbed = rows([[1, 0.5, 0.25, 0, 0], [1, 0.11, 0.25, 0, 0], [0, 0.2, 0.2, 0, 0], [1, -0.05, -0.26, 0, 0]]);
  const g = Overlay.ghosts(step({ clean, perturbed }), meta());
  assert.equal(g.length, 2); // row 2 has clean presence 0; the ego (row 0) gets none
  assert.equal(g[0].row, 1);
  assert.ok(Math.abs(g[0].x - (100 + 0.11 * 200)) < 1e-9);
  assert.ok(Math.abs(g[0].y - (4 + 0.25 * 16)) < 1e-9);
  assert.equal(g[1].row, 3);
  assert.ok(Math.abs(g[1].y - (4 - 0.26 * 16)) < 1e-9);
});

test("runs without an attack get no ghosts and no marks", () => {
  const clean = rows([[1, 0.5, 0.25, 0, 0], [1, 0.1, 0.25, 0, 0]]);
  for (const m of [meta("none", 0.01), meta("pgd", 0)]) {
    assert.deepEqual(Overlay.ghosts(step({ clean, perturbed: clean }), m), []);
    assert.deepEqual(Overlay.flips([step({ clean, perturbed: clean, action: 0, cleanAction: 1 })], m), []);
  }
});

test("flips are the steps where the attack changed the action", () => {
  const s = (t, a, c) => ({ t, action: a, cleanAction: c });
  assert.deepEqual(Overlay.flips([s(0, 1, 1), s(1, 0, 3), s(2, 4, 4), s(3, 2, 1)], meta()), [1, 3]);
});

test("largest change is the max of the per-step linf", () => {
  assert.equal(Overlay.maxLinf([{ linf: 0.002 }, { linf: 0.0099 }, { linf: 0 }]), 0.0099);
  assert.equal(Overlay.maxLinf([]), 0);
});

// ---- continuous playback: ghosts that follow their car ----------------------------------------
// Two steps. The ego (id 0) is at x=100 then x=125. Car 3 is 20 m ahead then 10 m ahead, car 4 is
// 40 m ahead then 5 m ahead: the distance order swaps, so the observation rows swap too.
const X = (m) => m / 200 - 0; // metres along the road -> normalised value (range [-200, 200])
const Y = (m) => m / 16;      // metres across the road -> normalised value (range [-16, 16])

function obsRows(rows) {
  const out = new Array(50).fill(0);
  out[0] = 1;
  rows.forEach((r, i) => [1, X(r[0]), Y(r[1]), 0, 0].forEach((v, j) => (out[(i + 1) * 5 + j] = v)));
  return out;
}

// clean offsets match the cars; the attack moves car 3's ghost +1 m and car 4's -2 m along the road
const stepA = {
  t: 0, action: 1, cleanAction: 1,
  poses: [[0, 100, 4, 0, 25], [3, 120, 4, 0, 20], [4, 140, 8, 0, 20]],
  obs: obsRows([[20, 0], [40, 4]]),
  obsPerturbed: obsRows([[21, 0], [38, 4]]),
};
const stepB = {
  t: 1, action: 1, cleanAction: 1,
  poses: [[0, 125, 4, 0, 25], [3, 135, 4, 0, 20], [4, 130, 8, 0, 20]],
  obs: obsRows([[5, 4], [10, 0]]),          // car 4 is now nearer: row 1 is car 4, row 2 is car 3
  obsPerturbed: obsRows([[5, 4], [13, 0]]), // car 3's ghost is now +3 m off
};
const near = (a, b) => Math.abs(a - b) < 1e-6;

test("each observed row is matched to its car by the clean offset", () => {
  const e = Overlay.errors(stepA, meta());
  assert.deepEqual(e.map((x) => x.id).sort(), [3, 4]);
  const car3 = e.find((x) => x.id === 3);
  assert.ok(near(car3.dx, 1) && near(car3.dy, 0)); // the ghost sits 1 m ahead of car 3
  assert.ok(near(e.find((x) => x.id === 4).dx, -2));
});

test("at a stored step the ghosts are exactly where the agent saw the cars", () => {
  const at0 = Overlay.ghostsAt(stepA, stepB, 0, meta(), stepA.poses);
  const exact = Overlay.ghosts(stepA, meta());
  for (const g of exact) assert.ok(at0.some((h) => near(h.x, g.x) && near(h.y, g.y)));
  assert.equal(at0.length, exact.length);
});

test("between steps a ghost follows its own car, even when the rows swap", () => {
  const poses = Replay.between(stepA.poses, stepB.poses, 0.5);
  const g = Overlay.ghostsAt(stepA, stepB, 0.5, meta(), poses);
  const car3 = g.find((x) => x.id === 3);
  // car 3 is halfway (x=127.5) and its attack error is halfway between +1 and +3 m
  assert.ok(near(car3.x, 127.5 + 2), car3.x);
  assert.ok(near(car3.y, 4));
  const car4 = g.find((x) => x.id === 4);
  assert.ok(near(car4.x, 135 + (-2 + 0) / 2), car4.x); // error -2 m then 0 m
});

test("no ghosts between steps without an attack", () => {
  assert.deepEqual(Overlay.ghostsAt(stepA, stepB, 0.5, meta("none", 0), stepA.poses), []);
});
