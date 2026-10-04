"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { textDifference, replaceSelection, createAutosaver } = require("../app/static/ai-review-tools.js");

const wait = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
const nextTurn = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
function harness(persist, options = {}) {
  let snapshot = { title: "审核稿", content: "原文", answers: {} };
  let active = true, buffer = null;
  const states = [];
  const saver = createAutosaver({ revision: 3, initial: snapshot, delay: 20, read: () => structuredClone(snapshot), isActive: () => active, persist,
    onBuffer: value => { buffer = structuredClone(value); }, onClear: () => { buffer = null; }, onState: (kind, value) => states.push({ kind, ...value }), ...options });
  return { saver, states, set(content) { snapshot = { ...snapshot, content }; saver.edit(); }, leave() { active = false; saver.stop(); }, get buffer() { return buffer; } };
}

test("text diff reconstructs both inputs including emoji and escaped markup as data", () => {
  const left = "今天 🍁 修理 A设备，尚未完成。<script>alert(1)</script>";
  const right = "今天 🍁 检查 A设备，尚未完成。<img onerror=alert(1)>";
  const result = textDifference(left, right);
  assert.equal(result.parts.filter(part => part.kind !== "added").map(part => part.text).join(""), left);
  assert.equal(result.parts.filter(part => part.kind !== "removed").map(part => part.text).join(""), right);
  assert.equal(result.truncated, false);
  assert(result.parts.some(part => part.kind === "added"));
});

test("long comparisons bound preview and use coarse differences", () => {
  const result = textDifference("甲".repeat(100000), "乙".repeat(100000));
  assert.equal(result.truncated, true);
  assert.equal(result.coarse, true);
  assert.equal(result.parts.reduce((sum, part) => sum + part.text.length, 0), 12000);
});

test("identical and empty text compare without false changes", () => {
  assert.deepEqual(textDifference("", "").parts, []);
  assert.deepEqual(textDifference("同一段落 🍁", "同一段落 🍁").parts, [{ kind: "same", text: "同一段落 🍁" }]);
});

test("replacement targets exact selection and rejects moved or stale text", () => {
  assert.equal(replaceSelection("abc abc", 4, 7, "abc", "新段"), "abc 新段");
  assert.equal(replaceSelection("abc changed", 4, 7, "abc", "新段"), null);
  assert.equal(replaceSelection("abc", -1, 2, "ab", "新段"), null);
});

test("flush saves edits typed during an outstanding request using the new revision", async () => {
  const first = deferred(), calls = [];
  const h = harness((snapshot, revision) => { calls.push({ snapshot, revision }); return calls.length === 1 ? first.promise : Promise.resolve({ revision: revision + 1 }); });
  h.set("第一段修改");
  const flushing = h.saver.flush();
  await nextTurn();
  h.set("保存过程中继续编辑");
  first.resolve({ revision: 4 });
  const result = await flushing;
  assert.deepEqual(calls.map(call => [call.revision, call.snapshot.content]), [[3, "第一段修改"], [4, "保存过程中继续编辑"]]);
  assert.equal(result.revision, 5);
  assert.equal(h.saver.dirty, false);
  assert.equal(h.buffer, null);
  h.leave();
});

test("failure retains the latest buffer and manual retry saves it", async () => {
  let attempts = 0;
  const h = harness(async () => { if (++attempts === 1) throw Error("服务离线"); return { revision: 4 }; });
  h.set("我补充了现场处理结果");
  await assert.rejects(h.saver.flush(), /服务离线/);
  assert.equal(h.buffer.snapshot.content, "我补充了现场处理结果");
  assert.equal(h.buffer.revision, 3);
  await wait(50);
  assert.equal(attempts, 1);
  await h.saver.flush();
  assert.equal(h.buffer, null);
  assert.equal(h.saver.revision, 4);
  h.leave();
});

test("version conflict preserves edits and stops automatic retries even after more typing", async () => {
  let attempts = 0;
  const h = harness(async () => { attempts++; throw Object.assign(Error("服务器已有新版本"), { status: 409 }); });
  h.set("未保存内容");
  await assert.rejects(h.saver.flush(), error => error.status === 409);
  h.set("继续保留我的修改");
  await wait(50);
  assert.equal(attempts, 1);
  assert.equal(h.buffer.snapshot.content, "继续保留我的修改");
  assert.equal(h.saver.revision, 3);
  h.leave();
});

test("leaving before debounce prevents a write while preserving unsaved text", async () => {
  let attempts = 0;
  const h = harness(async () => { attempts++; return { revision: 4 }; });
  h.set("尚未自动暂存");
  h.leave();
  await wait(50);
  assert.equal(attempts, 0);
  assert.equal(h.buffer.snapshot.content, "尚未自动暂存");
  await assert.rejects(h.saver.flush(), /页面已离开/);
});

test("completion after leaving updates only buffer revision and never active-page status", async () => {
  const request = deferred();
  const h = harness(() => request.promise);
  h.set("请求已发出");
  const flushing = h.saver.flush();
  await nextTurn();
  h.set("离开前最后输入");
  const previousStates = h.states.length;
  h.leave();
  request.resolve({ revision: 4 });
  await assert.rejects(flushing, /页面已离开/);
  await h.saver.settle();
  assert.equal(h.states.length, previousStates);
  assert.equal(h.buffer.revision, 4);
  assert.equal(h.buffer.snapshot.content, "离开前最后输入");
});

test("debounce coalesces quick edits and clean flush does not create extra versions", async () => {
  const calls = [];
  const h = harness(async (snapshot, revision) => { calls.push(snapshot.content); return { revision: revision + 1 }; });
  await h.saver.flush();
  assert.equal(calls.length, 0);
  h.set("1"); h.set("12"); h.set("123");
  await wait(70);
  await h.saver.settle();
  await h.saver.flush();
  assert.deepEqual(calls, ["123"]);
  assert.equal(h.saver.revision, 4);
  h.leave();
});
