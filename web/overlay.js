// The perturbation overlay's maths (docs/ARCHITECTURE.md, section 11). Plain JavaScript, no build step.
// Loaded by the page as a script (window.Overlay) and by Node's test runner (module.exports).
(function (root) {
  "use strict";
  const ROW = 5; // presence, x, y, vx, vy
  const ROWS = 10;

  function attacked(meta) {
    const s = (meta && meta.settings) || {};
    return s.attack && s.attack !== "none" && Number(s.epsilon) > 0;
  }

  // A normalised value back to its unit: low + (v + 1) / 2 * (high - low)
  function denorm(v, range) {
    const [low, high] = range;
    return low + ((v + 1) / 2) * (high - low);
  }

  // Ghost cars: where the agent believes each observed car is. Rows 1 to 9 with clean presence 1.
  function ghosts(step, meta) {
    if (!attacked(meta) || !step || !step.obsPerturbed || !step.poses || !step.poses.length) return [];
    const ranges = meta.obsSpec.ranges;
    const ego = step.poses[0]; // [id, x, y, heading, speed]
    const out = [];
    for (let r = 1; r < ROWS; r++) {
      if (step.obs[r * ROW] !== 1) continue;
      const dx = denorm(step.obsPerturbed[r * ROW + 1], ranges.x);
      const dy = denorm(step.obsPerturbed[r * ROW + 2], ranges.y);
      out.push({ row: r, x: ego[1] + dx, y: ego[2] + dy });
    }
    return out;
  }

  // For playback: tie each observed row to the car it describes, and give the attack's error relative to
  // that car. A row's clean offset from the ego points at its car; rows are sorted by distance, so the
  // same row can be a different car at the next step. The error is measured from the car's stored pose,
  // so car pose + error is exactly the ghost the agent saw at this step.
  const MATCH_M = 5; // a clean offset further than this from every car is left unmatched
  function errors(step, meta) {
    if (!attacked(meta) || !step || !step.obsPerturbed || !step.poses || !step.poses.length) return [];
    const ranges = meta.obsSpec.ranges;
    const ego = step.poses[0];
    const out = [];
    for (let r = 1; r < ROWS; r++) {
      if (step.obs[r * ROW] !== 1) continue;
      const cx = ego[1] + denorm(step.obs[r * ROW + 1], ranges.x);
      const cy = ego[2] + denorm(step.obs[r * ROW + 2], ranges.y);
      const gx = ego[1] + denorm(step.obsPerturbed[r * ROW + 1], ranges.x);
      const gy = ego[2] + denorm(step.obsPerturbed[r * ROW + 2], ranges.y);
      let best = null, bestD = MATCH_M;
      for (let i = 1; i < step.poses.length; i++) {
        const p = step.poses[i];
        const d = Math.hypot(p[1] - cx, p[2] - cy);
        if (d <= bestD) { best = p; bestD = d; }
      }
      if (best) out.push({ id: best[0], row: r, dx: gx - best[1], dy: gy - best[2] });
      else out.push({ id: null, row: r, ox: gx - ego[1], oy: gy - ego[2] }); // offset from the ego instead
    }
    return out;
  }

  // Ghosts at a fractional position f between two stored steps: each follows its own car (whose position
  // `poses` gives, already interpolated), and its error is blended between the two steps. At f = 0 the
  // ghosts are exactly those of `from`. Between steps this is a display blend: the agent acts once a step.
  function ghostsAt(from, to, f, meta, poses) {
    const a = errors(from, meta);
    if (!a.length) return [];
    const b = to ? new Map(errors(to, meta).filter((e) => e.id !== null).map((e) => [e.id, e])) : new Map();
    const where = new Map(poses.map((p) => [p[0], p]));
    const ego = poses[0];
    const out = [];
    for (const e of a) {
      if (e.id === null) {
        out.push({ row: e.row, id: null, x: ego[1] + e.ox, y: ego[2] + e.oy });
        continue;
      }
      const car = where.get(e.id);
      if (!car) continue;
      const n = b.get(e.id);
      const dx = n ? e.dx + (n.dx - e.dx) * f : e.dx;
      const dy = n ? e.dy + (n.dy - e.dy) * f : e.dy;
      out.push({ row: e.row, id: e.id, x: car[1] + dx, y: car[2] + dy });
    }
    return out;
  }

  // Steps where the attack changed the action.
  function flips(steps, meta) {
    if (!attacked(meta)) return [];
    return steps.filter((s) => s && s.action !== s.cleanAction).map((s) => s.t);
  }

  function maxLinf(steps) {
    return steps.reduce((m, s) => (s && s.linf > m ? s.linf : m), 0);
  }

  const api = { attacked, denorm, ghosts, errors, ghostsAt, flips, maxLinf };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Overlay = api;
})(typeof window !== "undefined" ? window : globalThis);
