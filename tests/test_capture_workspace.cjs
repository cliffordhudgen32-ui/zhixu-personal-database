const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createSaver, insertTemplate } = require('../app/static/capture-workspace.js');
const tick = () => new Promise(resolve => setImmediate(resolve));

test('input arriving during save is persisted before submission uses the latest revision', async () => {
  let value = { content: '第一次输入' }, release;
  const sent = [];
  const saver = createSaver({ initial: { content: '' }, read: () => ({ ...value }), onState() {}, delay: 999999,
    persist: async (body, revision) => {
      sent.push({ body, revision });
      if (!revision) await new Promise(resolve => release = resolve);
      return { revision: revision + 1 };
    } });
  saver.edit(); const flushed = saver.flush(); await tick();
  value.content = '第二次输入，数值为 12'; saver.edit(); release();
  assert.equal(await flushed, 2);
  assert.deepEqual(sent, [{ body: { content: '第一次输入' }, revision: 0 }, { body: { content: '第二次输入，数值为 12' }, revision: 1 }]);
  assert.equal(saver.dirty, false); saver.stop();
});

test('a stale revision stops writes and keeps local text for explicit comparison', async () => {
  let calls = 0; const states = [], value = { content: '本地尚未合并的文字' };
  const failure = Object.assign(new Error('版本冲突'), { status: 409 });
  const saver = createSaver({ initial: { content: '打开时的文字' }, revision: 3, read: () => ({ ...value }),
    onState: state => states.push(state), delay: 999999, persist: async () => { calls++; throw failure; } });
  saver.edit(); await assert.rejects(saver.flush(), /版本冲突/);
  assert.equal(calls, 1); assert.equal(saver.revision, 3); assert.equal(saver.dirty, true);
  assert.equal(value.content, '本地尚未合并的文字'); assert.equal(states.at(-1).error, failure); saver.stop();
});

test('network retry retains identity revision and flushes the same snapshot once', async () => {
  const sent = []; const value = { content: '断网时输入的记录' };
  const saver = createSaver({ initial: { content: '' }, read: () => ({ ...value }), onState() {}, delay: 999999,
    persist: async (body, revision) => { sent.push({ body, revision }); if (sent.length === 1) throw Error('网络断开'); return { revision: 1 }; } });
  saver.edit(); await assert.rejects(saver.flush(), /网络断开/); assert.equal(await saver.flush(), 1);
  assert.deepEqual(sent[0], sent[1]); assert.equal(saver.dirty, false); saver.stop();
});

test('explicit blank draft save is durable but opening a blank editor creates no request', async () => {
  let calls = 0; const saver = createSaver({ initial: { content: '' }, read: () => ({ content: '' }), onState() {},
    persist: async () => { calls++; return { revision: 1 }; } });
  assert.equal(await saver.flush(), 0); assert.equal(calls, 0);
  assert.equal(await saver.flush(true), 1); assert.equal(calls, 1); saver.stop();
});

test('scene outlines append to existing observations without adding facts', () => {
  const original = '设备甲运行十分钟，未观察到异响。';
  assert.equal(insertTemplate(original, 'equipment').split('\n\n')[0], original);
  assert.equal(insertTemplate(original, 'quick'), original);
  assert.equal(insertTemplate('', 'work'), '事情：\n进展：\n遇到的问题：\n下一步：');
});
