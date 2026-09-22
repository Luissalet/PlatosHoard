import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<body><button id="one"></button><button id="all"></button></body>', {
  url: 'http://localhost/editor',
});
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.localStorage = dom.window.localStorage;

const { store } = await import('../../static/editor/store.js');
const { ExportDialog } = await import('../../static/editor/export_dialog.js');

const zipBlob = () => new Blob([Uint8Array.from([0x50, 0x4b, 0x03, 0x04, 0x00])]);

function setup(status = { textContent: '' }) {
  localStorage.clear();
  store.doc = {
    id: 'doc-export',
    layers: {
      A: { id: 'A', parent_id: null, visible: true, export_enabled: true },
      B: { id: 'B', parent_id: 'A', visible: true, export_enabled: false },
      C: { id: 'C', parent_id: null, visible: false, export_enabled: true },
      D: { id: 'D', parent_id: 'C', visible: true, export_enabled: true },
    },
  };
  store.selectedId = 'A';
  store.operationBusy = false;
  const dialog = new ExportDialog({
    exportSelected: document.getElementById('one'),
    exportBatch: document.getElementById('all'),
    fitStatus: status,
    formats: () => ['stl'],
  });
  return dialog;
}

test('client selection and counter match server export eligibility', () => {
  const dialog = setup();
  assert.deepEqual(dialog._selectedLayerIds(true), ['A']);
  dialog.updateCounter();
  assert.match(dialog.els.fitStatus.textContent, /^4 piezas · 4 archivos/);

  store.selectedId = 'B';
  dialog._setBusy(false);
  assert.equal(dialog.els.exportSelected.disabled, true);
});

test('queued jobs do not display opaque zero progress', async () => {
  const messages = [];
  const status = {
    get textContent() { return messages.at(-1) || ''; },
    set textContent(value) { messages.push(value); },
  };
  const dialog = setup(status);
  const originalFetch = globalThis.fetch;
  const originalTimeout = globalThis.setTimeout;
  let calls = 0;
  globalThis.setTimeout = callback => { callback(); return 0; };
  globalThis.fetch = async () => {
    calls += 1;
    return {
      ok: true,
      json: async () => calls === 1
        ? { state: 'queued', phase: 'queued', progress: 0 }
        : { state: 'failed', error: { message: 'test failure' } },
    };
  };
  try {
    dialog._rememberJob('job_test');
    await dialog._pollJob('job_test');
    assert.ok(messages.includes('En cola'));
    assert.ok(messages.every(message => !message.includes('0 %')));
    assert.equal(localStorage.getItem('platos.exportJob.doc-export'), null);
  } finally {
    globalThis.fetch = originalFetch;
    globalThis.setTimeout = originalTimeout;
  }
});

test('unfinished job id survives and can be resumed after reload', async () => {
  const dialog = setup();
  localStorage.setItem('platos.exportJob.doc-export', 'job_saved');
  let resumed = null;
  dialog._pollJob = async id => { resumed = id; dialog._forgetJob(id); };
  await dialog._resumePending();
  assert.equal(resumed, 'job_saved');
  assert.equal(dialog._busy, false);
});

test('export validates the completed ZIP before choosing a destination', async () => {
  const dialog = setup();
  const calls = [];
  const target = { handle: { name: 'linea.zip' } };
  const blob = zipBlob();
  dialog.els.projectFiles = {
    prepareExport: async doc => { calls.push(['picker', doc.id]); return target; },
    writeExport: async (destination, content) => {
      calls.push(['write', destination]);
      assert.equal(content, blob);
      return { downloaded: false, name: 'linea.zip' };
    },
  };
  const originalFetch = globalThis.fetch, originalTimeout = globalThis.setTimeout;
  globalThis.setTimeout = callback => { callback(); return 0; };
  globalThis.fetch = async (url, options) => {
    calls.push([options?.method === 'POST' ? 'submit' : url.endsWith('/download') ? 'download' : 'poll']);
    return { ok: true, json: async () => options?.method === 'POST' ? { id: 'job_direct' } : { state: 'completed' }, blob: async () => blob };
  };
  try {
    await dialog._export(true);
    assert.deepEqual(calls.map(x => x[0]), ['submit', 'poll', 'download']);
    assert.equal(dialog.els.exportBatch.textContent, 'Guardar ZIP');
    await dialog._export(true);
    assert.deepEqual(calls.map(x => x[0]), ['submit', 'poll', 'download', 'picker', 'write']);
    assert.equal(calls.at(-1)[1], target);
    assert.equal(localStorage.getItem('platos.exportJob.doc-export'), null);
    assert.match(dialog.els.fitStatus.textContent, /ZIP guardado: linea.zip/);
    assert.equal(dialog._busy, false);
  } finally { globalThis.fetch = originalFetch; globalThis.setTimeout = originalTimeout; }
});

test('cancelling the destination picker retains the completed export', async () => {
  const dialog = setup();
  dialog.els.projectFiles = { prepareExport: async () => { throw new DOMException('cancel', 'AbortError'); } };
  const originalFetch = globalThis.fetch, originalTimeout = globalThis.setTimeout;
  globalThis.setTimeout = callback => { callback(); return 0; };
  globalThis.fetch = async (_url, options) => ({
    ok: true, json: async () => options?.method === 'POST' ? { id: 'job_cancel' } : { state: 'completed' },
    blob: async () => zipBlob(),
  });
  try {
    await dialog._export(true);
    await dialog._export(true);
    assert.equal(dialog._busy, false);
    assert.match(dialog.els.fitStatus.textContent, /ZIP sigue listo/);
    assert.equal(localStorage.getItem('platos.exportJob.doc-export'), 'job_cancel');
  } finally { globalThis.fetch = originalFetch; globalThis.setTimeout = originalTimeout; }
});

test('completed export after reload waits for a click and saves without regenerating', async () => {
  const dialog = setup();
  const calls = [];
  const target = { handle: { name: 'recovered.zip' } };
  dialog.els.projectFiles = {
    prepareExport: async () => { calls.push('picker'); return target; },
    writeExport: async destination => { assert.equal(destination, target); calls.push('write'); return { name: 'recovered.zip', downloaded: false }; },
  };
  const originalFetch = globalThis.fetch, originalTimeout = globalThis.setTimeout;
  globalThis.setTimeout = callback => { callback(); return 0; };
  globalThis.fetch = async (url, options) => {
    assert.notEqual(options?.method, 'POST');
    calls.push(url.endsWith('/download') ? 'download' : 'poll');
    return { ok: true, json: async () => ({ state: 'completed' }), blob: async () => zipBlob() };
  };
  try {
    dialog._rememberJob('job_recovered');
    await dialog._resumePending();
    assert.deepEqual(calls, ['poll', 'download']);
    assert.equal(dialog.els.exportBatch.textContent, 'Guardar ZIP');
    dialog.updateCounter();
    assert.equal(dialog._readyJobId, 'job_recovered');
    await dialog._export(true);
    assert.deepEqual(calls, ['poll', 'download', 'picker', 'write']);
    assert.equal(dialog._readyJobId, null);
    assert.equal(dialog.els.exportBatch.textContent, 'Exportar STL');
  } finally { globalThis.fetch = originalFetch; globalThis.setTimeout = originalTimeout; }
});

test('failed ZIP write retains completed work for retry', async () => {
  const dialog = setup();
  dialog.els.projectFiles = {
    prepareExport: async () => ({ handle: {} }),
    writeExport: async () => { throw new Error('disk full'); },
  };
  const originalFetch = globalThis.fetch, originalTimeout = globalThis.setTimeout;
  globalThis.setTimeout = callback => { callback(); return 0; };
  globalThis.fetch = async (_url, options) => ({
    ok: true, json: async () => options?.method === 'POST' ? { id: 'job_retry' } : { state: 'completed' }, blob: async () => zipBlob(),
  });
  try {
    await dialog._export(true);
    await dialog._export(true);
    assert.equal(localStorage.getItem('platos.exportJob.doc-export'), 'job_retry');
    assert.equal(dialog._readyJobId, 'job_retry');
    assert.equal(dialog.els.exportBatch.textContent, 'Guardar ZIP');
    assert.match(dialog.els.fitStatus.textContent, /disk full/);
  } finally { globalThis.fetch = originalFetch; globalThis.setTimeout = originalTimeout; }
});

test('empty HTTP 200 download is rejected before choosing or writing a destination', async () => {
  const dialog = setup();
  let picks = 0, writes = 0;
  dialog.els.projectFiles = {
    prepareExport: async () => { picks += 1; return { handle: {} }; },
    writeExport: async () => { writes += 1; },
  };
  const originalFetch = globalThis.fetch, originalTimeout = globalThis.setTimeout;
  globalThis.setTimeout = callback => { callback(); return 0; };
  globalThis.fetch = async (_url, options) => ({
    ok: true,
    json: async () => options?.method === 'POST' ? { id: 'job_empty' } : { state: 'completed' },
    blob: async () => new Blob([]),
  });
  try {
    await dialog._export(true);
    assert.equal(picks, 0);
    assert.equal(writes, 0);
    assert.match(dialog.els.fitStatus.textContent, /ZIP vacío/);
    assert.equal(localStorage.getItem('platos.exportJob.doc-export'), 'job_empty');
    assert.equal(dialog._readyJobId, 'job_empty');
  } finally { globalThis.fetch = originalFetch; globalThis.setTimeout = originalTimeout; }
});
