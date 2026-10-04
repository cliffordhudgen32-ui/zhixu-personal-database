"use strict";

// Pure helpers are shared by the browser and the small Node regression suite.
(function (root) {
  function equalSnapshot(left, right) {
    return JSON.stringify(left) === JSON.stringify(right);
  }

  function textDifference(original, revised, limits = {}) {
    const maximum = Math.max(1, Math.min(6000, Number.isInteger(limits.maxChars) ? limits.maxChars : 6000));
    const originalText = String(original || "");
    const revisedText = String(revised || "");
    const a = Array.from(originalText.slice(0, maximum * 2)).slice(0, maximum);
    const b = Array.from(revisedText.slice(0, maximum * 2)).slice(0, maximum);
    let prefix = 0, suffix = 0;
    while (prefix < a.length && prefix < b.length && a[prefix] === b[prefix]) prefix++;
    while (suffix < a.length - prefix && suffix < b.length - prefix && a[a.length - suffix - 1] === b[b.length - suffix - 1]) suffix++;
    const oldMiddle = a.slice(prefix, a.length - suffix);
    const newMiddle = b.slice(prefix, b.length - suffix);
    const maxCells = Math.max(1, Math.min(250000, Number.isInteger(limits.maxCells) ? limits.maxCells : 250000));
    const coarse = (oldMiddle.length + 1) * (newMiddle.length + 1) > maxCells;
    const parts = [];
    function push(kind, text) {
      if (!text) return;
      const last = parts[parts.length - 1];
      if (last?.kind === kind) last.text += text;
      else parts.push({ kind, text });
    }
    push("same", a.slice(0, prefix).join(""));
    if (coarse) {
      push("removed", oldMiddle.join(""));
      push("added", newMiddle.join(""));
    } else {
      const width = newMiddle.length + 1;
      const matrix = new Uint16Array((oldMiddle.length + 1) * width);
      for (let i = oldMiddle.length - 1; i >= 0; i--) {
        for (let j = newMiddle.length - 1; j >= 0; j--) {
          matrix[i * width + j] = oldMiddle[i] === newMiddle[j] ? matrix[(i + 1) * width + j + 1] + 1 : Math.max(matrix[(i + 1) * width + j], matrix[i * width + j + 1]);
        }
      }
      let i = 0, j = 0;
      while (i < oldMiddle.length || j < newMiddle.length) {
        if (i < oldMiddle.length && j < newMiddle.length && oldMiddle[i] === newMiddle[j]) {
          push("same", oldMiddle[i]); i++; j++;
        } else if (j < newMiddle.length && (i >= oldMiddle.length || matrix[i * width + j + 1] > matrix[(i + 1) * width + j])) {
          push("added", newMiddle[j++]);
        } else push("removed", oldMiddle[i++]);
      }
    }
    push("same", suffix ? a.slice(a.length - suffix).join("") : "");
    return { parts, coarse, truncated: originalText.length > a.join("").length || revisedText.length > b.join("").length };
  }

  function replaceSelection(text, start, end, expected, proposal) {
    if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start || end > text.length || text.slice(start, end) !== expected) return null;
    return text.slice(0, start) + proposal + text.slice(end);
  }

  function createAutosaver(options) {
    let revision = options.revision;
    let dirty = false, stopped = false, timer = null, pending = null, failure = null;
    let saved = options.initial;
    const active = () => !stopped && options.isActive();
    const status = (kind, error = null) => { if (active()) options.onState(kind, { revision, error }); };
    const buffer = snapshot => options.onBuffer({ revision, snapshot });
    function cancelTimer() { if (timer !== null) clearTimeout(timer); timer = null; }
    function schedule() {
      cancelTimer();
      if (active() && dirty && !failure) timer = setTimeout(() => { timer = null; saveOnce().catch(() => {}); }, options.delay ?? 2000);
    }
    function edit() {
      if (!active()) return;
      const snapshot = options.read();
      dirty = !equalSnapshot(snapshot, saved) || Boolean(pending);
      if (dirty) buffer(snapshot);
      else options.onClear();
      status(dirty ? failure ? "failed" : "waiting" : "saved", failure);
      schedule();
    }
    async function saveOnce() {
      if (pending) return pending;
      if (!active() || !dirty) return saved;
      const snapshot = options.read();
      const sentRevision = revision;
      status("saving");
      // Deferring persist also makes the promise visible before callbacks run.
      pending = Promise.resolve().then(() => options.persist(snapshot, sentRevision)).then(updated => {
        revision = updated.revision ?? revision;
        saved = snapshot;
        failure = null;
        const latest = options.read();
        dirty = !equalSnapshot(latest, snapshot);
        if (dirty) buffer(latest);
        else options.onClear();
        status(dirty ? "waiting" : "saved");
        return updated;
      }).catch(error => {
        failure = error;
        dirty = true;
        buffer(options.read());
        status("failed", error);
        throw error;
      }).finally(() => { pending = null; schedule(); });
      return pending;
    }
    async function flush() {
      cancelTimer();
      failure = null;
      while (active() && (pending || dirty)) {
        await (pending || saveOnce());
        cancelTimer();
      }
      if (!active()) throw Error("页面已离开，审核修改保留在浏览器缓冲中。请重新打开这份草稿。");
      return { revision, saved };
    }
    return {
      edit, flush,
      stop() { stopped = true; cancelTimer(); },
      async settle() { if (pending) await pending.catch(() => {}); },
      get revision() { return revision; },
      get dirty() { return dirty; },
    };
  }
  const helpers = { equalSnapshot, textDifference, replaceSelection, createAutosaver };
  if (typeof module !== "undefined" && module.exports) module.exports = helpers;
  root.AIReviewTools = helpers;
})(typeof globalThis !== "undefined" ? globalThis : window);
