// Continuous replay: where the cars are between two stored steps, and how far playback has got.
// Loaded by the page as a script (window.Replay) and by Node's test runner (module.exports).
(function (root) {
  "use strict";

  // Poses are [id, x, y, heading, speed], ego first. Cars are matched by id; a car present in only one
  // of the two steps (it entered or left the scene) is shown where that step has it.
  function between(from, to, f) {
    const next = new Map(to.map((p) => [p[0], p]));
    const out = from.map((p) => {
      const q = next.get(p[0]);
      if (!q) return p.slice();
      return [p[0], ...[1, 2, 3, 4].map((i) => p[i] + (q[i] - p[i]) * f)];
    });
    const seen = new Set(from.map((p) => p[0]));
    for (const q of to) if (!seen.has(q[0])) out.push(q.slice());
    return out;
  }

  // One step is one simulated second, so speed 1 is real time. `pos` is a fractional step index.
  function advance(pos, seconds, speed, steps) {
    const last = Math.max(0, steps - 1);
    const p = Math.min(last, pos + seconds * speed);
    return { pos: p, done: p >= last };
  }

  const api = { between, advance };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Replay = api;
})(typeof window !== "undefined" ? window : globalThis);
