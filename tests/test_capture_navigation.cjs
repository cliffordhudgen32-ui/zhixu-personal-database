const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Exercise the checked-in functions in memory. No browser, server, or user
// database is touched; only the page renderers and network transport are fake.
const appSource = fs.readFileSync(path.join(__dirname, '../app/static/app.js'), 'utf8');
const captureSource = fs.readFileSync(path.join(__dirname, '../app/static/capture-workspace.js'), 'utf8');
function between(source, first, next) {
  const start = source.indexOf(first);
  const end = source.indexOf(next, start + first.length);
  assert.ok(start >= 0 && end > start, `Cannot locate actual function: ${first}`);
  return source.slice(start, end);
}
const apiSource = between(appSource, 'async function api(', '\nfunction toast(');
const routeSource = between(appSource, 'async function route()', '\nasync function boot()');
const compareCallbacks = between(captureSource, '$("#capture-use-server").onclick', '  });\n  form.onsubmit');
const tick = () => new Promise(resolve => setImmediate(resolve));

function routingContext() {
  const main = { innerHTML: '' }, breadcrumb = { textContent: '' }, notices = [];
  let resolveFetch;
  const context = vm.createContext({ location: { hash: '#entries' }, routeSerial: 0,
    token: 'test-only-local-token', FormData, URLSearchParams,
    page: 1, currentFilters: {}, kinds: {}, assets: [], entityFields: {},
    $: selector => selector === '#main' ? main : breadcrumb,
    $$: () => [], heading: (title, text) => title + ':' + text,
    toast: text => notices.push(text), attachLookups() {}, captureBeforeLeave: async () => true,
    fetch: () => new Promise(resolve => { resolveFetch = resolve; }),
  });
  vm.runInContext(apiSource + '\n' + routeSource, context);
  context.listPage = async () => { await context.api('/api/entries'); main.innerHTML = 'OLD ENTRIES'; };
  context.captureWorkspacePage = async () => { main.innerHTML = 'CAPTURE INPUT PRESERVED'; };
  return { context, main, notices,
    release(data = { items: [] }) { resolveFetch({ ok: true, json: async () => data }); } };
}

test('a prior successful GET cannot replace the current capture editor or show an error page', async () => {
  const { context, main, notices, release } = routingContext();
  const previousPage = context.route();
  await tick();
  context.location.hash = '#quick';
  await context.route();
  release();
  await previousPage;
  assert.equal(main.innerHTML, 'CAPTURE INPUT PRESERVED');
  assert.equal(context.location.hash, '#quick');
  assert.deepEqual(notices, []);
});

test('an authorized POST still completes after the page hash and render serial change', async () => {
  const { context, release } = routingContext();
  context.routeSerial = 1;
  context.location.hash = '#quick';
  const saving = context.api('/api/capture-drafts/test/submit', { method: 'POST', body: { revision: 1 } });
  await tick();
  context.location.hash = '#entries';
  context.routeSerial++;
  release({ entry: { uuid: 'saved-test-entry' } });
  const result = await saving;
  assert.equal(result.entry.uuid, 'saved-test-entry');
});

test('initialization GET remains valid when navigation changes before the first route', async () => {
  const { context, release } = routingContext();
  const session = context.api('/api/session');
  await tick();
  context.location.hash = '#quick';
  release({ token: 'test-token', settings: {} });
  assert.equal((await session).token, 'test-token');
  assert.equal(context.routeSerial, 0);
});

function compareContext(active = () => false) {
  const controls = { '#capture-use-server': {}, '#capture-copy-new': {}, '#dialog': { close() { closes++; } } };
  let stops = 0, closes = 0, writes = 0;
  const currentEditor = { identity: 'current-editor-B' };
  const context = vm.createContext({ $: selector => controls[selector], busy: handler => handler,
    active, state: { files: [], busy: false, submitAttempted: false, saver: { stop() { stops++; } } },
    CaptureWorkspace: currentEditor, draft: { uuid: 'old-draft-A' }, location: { hash: '#quick/draft-B' },
    crypto: { randomUUID: () => 'test-copy-uuid' }, read: () => ({ content: 'Current prose to preserve' }),
    api: async () => { writes++; throw Error('Unexpected write from a stale dialog'); },
    captureWorkspacePage: async () => { throw Error('Unexpected stale page replacement'); }, toast() {},
  });
  vm.runInContext(compareCallbacks, context);
  return { context, currentEditor, controls, counts: () => ({ stops, closes, writes }) };
}

test('old comparison dialog cannot load draft A into the new draft B route', async () => {
  const { context, currentEditor, controls, counts } = compareContext();
  await assert.rejects(controls['#capture-use-server'].onclick(), /草稿已切换/);
  assert.equal(context.CaptureWorkspace, currentEditor);
  assert.equal(context.location.hash, '#quick/draft-B');
  assert.deepEqual(counts(), { stops: 0, closes: 0, writes: 0 });
});

test('old comparison dialog cannot copy prose or discard the current editor after navigation', async () => {
  const { context, currentEditor, controls, counts } = compareContext();
  await assert.rejects(controls['#capture-copy-new'].onclick(), /草稿已切换/);
  assert.equal(context.CaptureWorkspace, currentEditor);
  assert.equal(context.location.hash, '#quick/draft-B');
  assert.deepEqual(counts(), { stops: 0, closes: 0, writes: 0 });
});

test('copy already in flight may finish but cannot replace the later capture editor', async () => {
  let stillActive = true, releaseCopy;
  const { context, currentEditor, controls, counts } = compareContext(() => stillActive);
  let request;
  context.api = (url, options) => {
    request = { url, options };
    return new Promise(resolve => { releaseCopy = resolve; });
  };
  const copy = controls['#capture-copy-new'].onclick();
  await tick();
  assert.equal(request.options.method, 'PUT');
  assert.equal(request.options.body.content, 'Current prose to preserve');
  assert.equal(request.options.body.revision, 0);
  stillActive = false;
  releaseCopy({ uuid: 'saved-copy' });
  await copy;
  assert.equal(context.CaptureWorkspace, currentEditor);
  assert.equal(context.location.hash, '#quick/draft-B');
  assert.deepEqual(counts(), { stops: 0, closes: 0, writes: 0 });
});
