// test_copilot_context.mjs — what the Faustus copilot learns about the editor, and how it refreshes after a turn.
// Runs under jsdom: node --test tests/editor/test_copilot_context.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM(`<!DOCTYPE html><body>
  <input id="canvas-width-mm" value="200"/><input id="canvas-height-mm" value="200"/>
  <input id="canvas-padding-top" value="5"/><input id="canvas-padding-right" value="5"/>
  <input id="canvas-padding-bottom" value="5"/><input id="canvas-padding-left" value="5"/>
</body>`, { url: 'http://localhost/editor' });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.CustomEvent = dom.window.CustomEvent;
globalThis.Event = dom.window.Event;

const { buildContext, dirtyCanvasFields, documentObject, layerObject, layerTitle, MAX_LAYERS } = await import('../../static/editor/copilot_context.mjs');
const { EditorStore, store } = await import('../../static/editor/store.js');
const copilot = await import('../../static/editor/hoard-copilot.js');

function fixture(extra = {}) {
  return {
    schema_version: 2, id: 'doc_abc123', name: 'Escudo', revision: 4, units: 'mm',
    canvas: { width_mm: 200, height_mm: 150, padding_mm: { top: 5, right: 5, bottom: 5, left: 5 } },
    assets: { a: { id: 'a', name: 'Fondo.png' } },
    layers: {
      L1: { id: 'L1', asset_id: 'a', name: 'asset_a1b2', parent_id: null, order: 0, visible: true, pose: { tx: 1.23456, ty: 2, scale: 1, angle_deg: 0 } },
      L2: { id: 'L2', asset_id: 'a', name: 'Letras', parent_id: 'L1', order: 0, visible: false, locked: true },
    },
    ...extra,
  };
}

const inputsFor = (doc) => ({
  'canvas-width-mm': String(doc.canvas.width_mm), 'canvas-height-mm': String(doc.canvas.height_mm),
  'canvas-padding-top': '5', 'canvas-padding-right': '5', 'canvas-padding-bottom': '5', 'canvas-padding-left': '5',
});

test('layer titles fall back to the asset name when the layer name is only an id', () => {
  const doc = fixture();
  assert.equal(layerTitle(doc, doc.layers.L1), 'Fondo.png');
  assert.equal(layerTitle(doc, doc.layers.L2), 'Letras');
});

test('the open document becomes a project and objects with real ids', () => {
  const doc = fixture();
  const ctx = buildContext({ doc, selectedId: 'L2', inputs: inputsFor(doc) });
  assert.deepEqual(ctx.project, { external_project_id: 'doc_abc123', title: 'Escudo' });
  assert.deepEqual(ctx.active_objects.map((o) => [o.kind, o.object_id]), [['document', 'doc_abc123'], ['layer', 'L2']]);
  const [document_, layer] = ctx.active_objects;
  assert.equal(document_.title, 'Escudo');
  assert.equal(document_.revision, 4);
  assert.equal(document_.canvas.height_mm, 150);
  assert.equal(document_.layer_count, 2);
  assert.deepEqual(document_.layers.map((l) => l.id), ['L1', 'L2']);
  assert.equal(layer.title, 'Letras');
  assert.equal(layer.parent_id, 'L1');
  assert.equal(layer.visible, false);
  assert.equal(layer.locked, true);
  assert.equal(ctx.view.page, 'editor');
  assert.equal(ctx.view.editor, undefined, 'nothing unsaved: no editor block');
});

test('pose numbers are rounded and never carry geometry', () => {
  const layer = layerObject(fixture(), 'L1');
  assert.deepEqual(layer.pose, { tx: 1.235, ty: 2, scale: 1, angle_deg: 0 });
  assert.equal(JSON.stringify(layer).includes('canonical_svg'), false);
});

test('a selection that does not exist is dropped, not thrown', () => {
  const ctx = buildContext({ doc: fixture(), selectedId: 'gone' });
  assert.deepEqual(ctx.active_objects.map((o) => o.kind), ['document']);
});

test('no document (or no id) gives empty objects and no project key', () => {
  for (const doc of [null, undefined, {}, { name: 'x' }]) {
    const ctx = buildContext({ doc });
    assert.deepEqual(ctx.active_objects, []);
    assert.equal('project' in ctx, false);
    assert.equal(ctx.view.page, 'editor');
  }
});

test('canvas values typed but not applied travel in view.editor.fields', () => {
  const doc = fixture();
  const inputs = { ...inputsFor(doc), 'canvas-width-mm': '320', 'canvas-padding-left': '' };
  assert.deepEqual(dirtyCanvasFields(doc, inputs), { 'canvas-width-mm': '320', 'canvas-padding-left': '' });
  const ctx = buildContext({ doc, inputs });
  assert.deepEqual(ctx.view.editor, { kind: 'canvas', dirty: true, fields: { 'canvas-width-mm': '320', 'canvas-padding-left': '' } });
});

test('a long layer tree is capped and says how much was left out', () => {
  const layers = {};
  for (let i = 0; i < MAX_LAYERS + 7; i += 1) layers[`L${i}`] = { id: `L${i}`, name: `Capa ${i}`, parent_id: null, order: i };
  const out = documentObject(fixture({ layers }));
  assert.equal(out.layers.length, MAX_LAYERS);
  assert.equal(out.layers_truncated, 7);
  assert.equal(out.layer_count, MAX_LAYERS + 7);
});

// ---- window.hoardCopilot wiring ----

function serve(doc, status = 200) {
  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    return { ok: status === 200, status, json: async () => ({ document: doc, revision: doc?.revision }) };
  };
  return calls;
}

test('window.hoardCopilot exposes context() and onTurnEnd()', () => {
  assert.equal(typeof window.hoardCopilot.context, 'function');
  assert.equal(typeof window.hoardCopilot.onTurnEnd, 'function');
});

test('context() reads the document the server holds now (an agent may have changed it)', async () => {
  const open = fixture();
  store.doc = open; store.revision = 4; store.selectedId = 'L1';
  for (const [id, value] of Object.entries(inputsFor(open))) document.getElementById(id).value = value;
  const changed = fixture({ name: 'Escudo v2', revision: 5 });
  const calls = serve(changed);
  const ctx = await window.hoardCopilot.context({});
  assert.ok(calls[0].endsWith('/api/v2/documents/doc_abc123'));
  assert.equal(ctx.active_objects[0].title, 'Escudo v2');
  assert.equal(ctx.active_objects[0].revision, 5);
  assert.equal(ctx.active_objects[1].object_id, 'L1');
  assert.equal(ctx.view.editor, undefined, 'the inputs still show the old canvas: not "unsaved"');
});

test('context() with a document the server no longer has returns no objects', async () => {
  store.doc = fixture(); store.revision = 4;
  serve(null, 404);
  const ctx = await window.hoardCopilot.context({});
  assert.deepEqual(ctx.active_objects, []);
  assert.equal('project' in ctx, false);
});

test('context() falls back to the editor copy when the server cannot be asked, and never rejects', async () => {
  store.doc = fixture(); store.revision = 4; store.selectedId = null;
  globalThis.fetch = async () => { throw new Error('offline'); };
  const ctx = await window.hoardCopilot.context({});
  assert.equal(ctx.active_objects[0].object_id, 'doc_abc123');
  store.doc = { get id() { throw new Error('boom'); } };
  const broken = await window.hoardCopilot.context({});
  assert.deepEqual(broken.active_objects, []);
});

test('context() with no document open returns active_objects: []', async () => {
  store.doc = null;
  const ctx = await window.hoardCopilot.context({});
  assert.deepEqual(ctx.active_objects, []);
  assert.equal(ctx.view.page, 'editor');
});

test('onTurnEnd reloads a changed document once, asks for one redraw and keeps typed canvas values', async () => {
  const open = fixture();
  store.doc = open; store.revision = 4; store.selectedId = 'L2';
  document.getElementById('canvas-width-mm').value = '999';   // typed, not applied
  const changed = fixture({ revision: 5, layers: { L1: open.layers.L1 } });   // the agent deleted L2
  let loads = 0;
  store.loadDocument = async (id) => { loads += 1; store.doc = changed; store.revision = 5; return changed; };
  serve(changed);
  let redraws = 0;
  window.addEventListener('editor:refresh-request', () => { redraws += 1; });
  window.hoardCopilot.onTurnEnd({});
  window.hoardCopilot.onTurnEnd({});   // the launcher's event and the hook may both fire: one refresh
  await new Promise((resolve) => setTimeout(resolve, 400));
  assert.equal(loads, 1);
  assert.equal(redraws, 1);
  assert.equal(store.selectedId, null, 'the selection of a layer that is gone is cleared');
  assert.equal(document.getElementById('canvas-width-mm').value, '999', 'unsaved input survives');
});

test('onTurnEnd leaves the editor alone when nothing changed', async () => {
  const open = fixture();
  store.doc = open; store.revision = 4;
  let loads = 0;
  store.loadDocument = async () => { loads += 1; };
  serve(fixture());
  window.hoardCopilot.onTurnEnd({});
  await new Promise((resolve) => setTimeout(resolve, 400));
  assert.equal(loads, 0);
});

test('switching document announces a context change once', () => {
  store.doc = fixture();
  const seen = [];
  const target = new dom.window.EventTarget();
  copilot.watchDocument(target);
  target.addEventListener('hoard:copilot-context-changed', () => seen.push(store.doc.id));
  target.dispatchEvent(new dom.window.Event('editor:doc-changed'));
  assert.deepEqual(seen, []);   // same document: nothing to announce
  store.doc = fixture({ id: 'doc_other' });
  target.dispatchEvent(new dom.window.Event('editor:doc-changed'));
  target.dispatchEvent(new dom.window.Event('editor:doc-changed'));
  assert.deepEqual(seen, ['doc_other']);
});

test('the launcher button is moved left of the right panel and follows its width', () => {
  const page = new JSDOM('<body><aside class="editor-right"></aside></body>', { pretendToBeVisual: true }).window;
  let width = 340;
  page.document.querySelector('.editor-right').getBoundingClientRect = () => ({ width });
  let notify;
  page.ResizeObserver = class { constructor(callback) { notify = callback; } observe() {} };
  copilot.placeLauncher(page.document, page);
  assert.equal(page.document.documentElement.style.getPropertyValue('--hoard-copilot-right'), '356px');
  width = 420;
  notify();
  assert.equal(page.document.documentElement.style.getPropertyValue('--hoard-copilot-right'), '436px');
});

test('a page without the right panel keeps the default button position', () => {
  const page = new JSDOM('<body></body>').window;
  copilot.placeLauncher(page.document, page);
  assert.equal(page.document.documentElement.style.getPropertyValue('--hoard-copilot-right'), '');
});

test('a newer canvas revision retains typed fields in context', async () => {
  const open = fixture();
  store.doc = open; store.revision = 4;
  for (const [id, value] of Object.entries(inputsFor(open))) document.getElementById(id).value = value;
  document.getElementById('canvas-width-mm').value = '321';
  document.getElementById('canvas-padding-left').value = '';
  serve(fixture({ revision: 5, canvas: { ...open.canvas, width_mm: 222, height_mm: 444 } }));
  const ctx = await window.hoardCopilot.context();
  assert.equal(ctx.active_objects[0].canvas.width_mm, 222);
  assert.deepEqual(ctx.view.editor.fields, { 'canvas-width-mm': '321', 'canvas-padding-left': '' });
});

test('refresh rebases untouched fields while retaining typing during the load', async () => {
  const open = fixture();
  store.doc = open; store.revision = 4;
  for (const [id, value] of Object.entries(inputsFor(open))) document.getElementById(id).value = value;
  document.getElementById('canvas-width-mm').value = '321';
  const changed = fixture({ revision: 5, canvas: { ...open.canvas, width_mm: 222, height_mm: 444 } });
  let loads = 0;
  store.loadDocument = async () => {
    loads += 1;
    document.getElementById('canvas-width-mm').value = '654';
    document.getElementById('canvas-padding-left').value = '';
    await Promise.resolve();
    store.doc = changed; store.revision = 5;
  };
  const redraw = () => {
    for (const [id, value] of Object.entries(inputsFor(changed))) document.getElementById(id).value = value;
  };
  window.addEventListener('editor:refresh-request', redraw);
  serve(changed);window.hoardCopilot.onTurnEnd();
  await new Promise(resolve => setTimeout(resolve, 400));
  window.removeEventListener('editor:refresh-request', redraw);
  assert.equal(loads, 1);
  assert.equal(document.getElementById('canvas-width-mm').value, '654');
  assert.equal(document.getElementById('canvas-height-mm').value, '444');
  assert.equal(document.getElementById('canvas-padding-left').value, '');
});

test('context keeps the initial form snapshot across a delayed document fetch', async () => {
  const open = fixture();store.doc = open;store.revision = 4;store.selectedId = 'L1';
  for (const [id, value] of Object.entries(inputsFor(open))) document.getElementById(id).value = value;
  document.getElementById('canvas-width-mm').value = '321';
  let release;
  globalThis.fetch = () => new Promise(resolve => { release = resolve; });
  const pending = window.hoardCopilot.context();
  document.getElementById('canvas-width-mm').value = '654';store.selectedId = 'L2';
  release({ ok: true, status: 200, json: async () => fixture({ revision: 5 }) });
  const ctx = await pending;
  assert.equal(ctx.view.editor.fields['canvas-width-mm'], '321');
  assert.equal(ctx.active_objects[1].object_id, 'L1');
});

test('a turn refresh cannot reload an old document after a different document opens', async () => {
  store.doc = fixture(); store.revision = 4;
  let release;
  globalThis.fetch = () => new Promise(resolve => { release = resolve; });
  let loads = 0;store.loadDocument = async () => { loads += 1; };
  window.hoardCopilot.onTurnEnd();
  await new Promise(resolve => setTimeout(resolve, 200));
  store.doc = fixture({ id: 'doc_other', revision: 7 });store.revision = 7;
  document.getElementById('canvas-width-mm').value = '888';
  release({ ok: true, status: 200, json: async () => fixture({ revision: 5 }) });
  await new Promise(resolve => setTimeout(resolve, 50));
  assert.equal(loads, 0);
  assert.equal(store.doc.id, 'doc_other');
  assert.equal(document.getElementById('canvas-width-mm').value, '888');
});

test('a conditional load preserves a different document and its undo history after the response', async () => {
  const state = new EditorStore();state.doc = fixture();state.revision = 4;
  let release;
  globalThis.fetch = () => new Promise(resolve => { release = resolve; });
  const pending = state.loadDocument('doc_abc123', { expectedCurrentId: 'doc_abc123' });
  state.doc = fixture({ id: 'doc_other', revision: 7 });state.revision = 7;state.history = ['keep'];
  release({ ok: true, json: async () => fixture({ revision: 5 }) });
  await pending;
  assert.equal(state.doc.id, 'doc_other');assert.equal(state.revision, 7);assert.deepEqual(state.history, ['keep']);
});

test('a conditional load also guards an asynchronously decoded payload', async () => {
  const state = new EditorStore();state.doc = fixture();state.revision = 4;
  let release;
  globalThis.fetch = async () => ({ ok: true, json: () => new Promise(resolve => { release = resolve; }) });
  const pending = state.loadDocument('doc_abc123', { expectedCurrentId: 'doc_abc123' });
  await Promise.resolve();
  state.doc = fixture({ id: 'doc_other', revision: 7 });state.revision = 7;state.history = ['keep'];
  release(fixture({ revision: 5 }));await pending;
  assert.equal(state.doc.id, 'doc_other');assert.equal(state.revision, 7);assert.deepEqual(state.history, ['keep']);
});
