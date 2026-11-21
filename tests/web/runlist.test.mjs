// Issue 05: merging the run list (GET /v1/runs) with live `run` events, in either order.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { mergeRun } = require("../../web/runlist.js");

test("a stale 'running' from a slow list reply does not undo a final status", () => {
  const live = { runId: "r", status: "finished", steps: 60, reason: null };
  const stale = { runId: "r", status: "running", steps: 12, modelId: "sa_dqn/1" };
  const merged = mergeRun(live, stale);
  assert.equal(merged.status, "finished");
  assert.equal(merged.steps, 60);
  assert.equal(merged.modelId, "sa_dqn/1"); // fields the event lacked are still filled in
});

test("a final status replaces 'running'", () => {
  const merged = mergeRun({ runId: "r", status: "running", steps: 3 }, { runId: "r", status: "aborted", steps: 5 });
  assert.equal(merged.status, "aborted");
  assert.equal(merged.steps, 5);
});

test("a new run is taken as it is", () => {
  assert.deepEqual(mergeRun(undefined, { runId: "r", status: "running" }), { runId: "r", status: "running" });
});
