// Merging run summaries from GET /v1/runs and from `run` events, which can arrive in either order.
// Loaded by the page as a script (window.RunList) and by Node's test runner (module.exports).
(function (root) {
  "use strict";
  const isFinal = (status) => status && status !== "running";

  // A run only moves from `running` to a final status, so an older `running` never wins over a final one.
  function mergeRun(old, update) {
    if (!old) return { ...update };
    const stale = isFinal(old.status) && !isFinal(update.status);
    const merged = { ...old };
    for (const [k, v] of Object.entries(update)) {
      if (v === null || v === undefined) continue;
      if (stale && k in old && old[k] !== null && old[k] !== undefined) continue;
      merged[k] = v;
    }
    return merged;
  }

  const api = { mergeRun };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.RunList = api;
})(typeof window !== "undefined" ? window : globalThis);
