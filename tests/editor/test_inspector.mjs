import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><body><section id="inspector"></section></body>', { url: 'http://localhost' });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.CustomEvent = dom.window.CustomEvent;

const { store } = await import('../../static/editor/store.js');
const { Inspector } = await import('../../static/editor/inspector.js');

const base = {
  revision: 0,
  assets: {
    outer: { id: 'outer', local_bounds: [-50, -50, 50, 50] },
    inner: { id: 'inner', local_bounds: [-10, -20, 10, 20] },
  },
  layers: {
    A: { id: 'A', asset_id: 'outer', name: 'Exterior', parent_id: null,
      pose: { tx: 0, ty: 0, scale: 2, angle_deg: 0 }, locked: false, export_enabled: true, fit: {} },
    B: { id: 'B', asset_id: 'inner', name: 'Interior', parent_id: 'A',
      pose: { tx: 0, ty: 0, scale: 0.5, angle_deg: 0 }, locked: false, export_enabled: true, fit: {} },
  },
};

function setup(opts = {}) {
  store.doc = structuredClone(base);
  store.selectedId = 'B';
  store.operationBusy = false;
  const commands = [];
  store.commitCommand = async (type, payload) => {
    commands.push({ type, payload });
    if (type === 'set_pose') store.doc.layers[payload.layer_id].pose = structuredClone(payload.pose);
    if (type === 'set_layer_properties') Object.assign(store.doc.layers[payload.layer_id], payload);
    if (type === 'set_parent') store.doc.layers[payload.layer_id].parent_id = payload.new_parent_id;
    return {};
  };
  const viewport = { renderLayers() {}, renderSelection() {}, _isFrameLayer() { return false; } };
  const inspector = new Inspector(document.getElementById('inspector'), viewport, opts);
  inspector.render('B');
  return { inspector, commands };
}

const inputFor = (label) => [...document.querySelectorAll('.insp-row')]
  .find((row) => row.querySelector('span')?.textContent === label)?.querySelector('input,select');
const change = async (input, value) => {
  input.value = String(value);
  input.dispatchEvent(new window.Event('change', { bubbles: true }));
  await new Promise((resolve) => setTimeout(resolve, 0));
};

test('stale selected layer renders safely', () => {
  const { inspector } = setup();
  assert.doesNotThrow(() => inspector.render('deleted-layer'));
  assert.match(inspector.el.textContent, /Capa no válida/);
});

test('nested dimensions display world millimetres and height independently drives scale', async () => {
  const { commands } = setup();
  assert.equal(Number(inputFor('Ancho').value), 20);
  assert.equal(Number(inputFor('Alto').value), 40);

  await change(inputFor('Alto'), 80);

  assert.equal(commands.at(-1).type, 'set_pose');
  assert.equal(commands.at(-1).payload.pose.scale, 1);
});

test('property changes notify dependent views', async () => {
  setup();
  let changes = 0;
  window.addEventListener('editor:doc-changed', () => changes++, { once: true });
  await change(inputFor('Nombre'), 'Nuevo nombre');
  assert.equal(changes, 1);
});

test('parent selector delegates without nesting the callback operation', async () => {
  let request = null;
  let operations = 0;
  setup({
    runOperation: async (_label, fn) => { operations++; return fn(); },
    onReparent: async (layerId, parentId) => { request = [layerId, parentId]; },
  });
  await change(inputFor('Padre'), '');
  assert.deepEqual(request, ['B', null]);
  assert.equal(operations, 0);
});

test('flip awaits descendant refit inside one operation', async () => {
  const order = [];
  const { commands } = setup({
    runOperation: async (_label, fn) => { order.push('operation:start'); await fn(); order.push('operation:end'); },
    onRefit: async (layerId) => { order.push(`refit:${layerId}`); },
  });
  const flip = [...document.querySelectorAll('input[type="checkbox"]')][1];
  flip.checked = true;
  flip.dispatchEvent(new window.Event('change', { bubbles: true }));
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(commands.at(-1).payload.flip_h, true);
  assert.deepEqual(order, ['operation:start', 'refit:B', 'operation:end']);
});

test('locked layers disable geometry and hierarchy edits', () => {
  const { inspector } = setup();
  store.doc.layers.B.locked = true;
  inspector.render('B');
  for (const label of ['X (mm)', 'Y (mm)', 'Escala', 'Ángulo (°)', 'Ancho', 'Alto', 'Padre']) {
    assert.equal(inputFor(label).disabled, true, label);
  }
});

test('busy guard rejects edits without committing', async () => {
  const { commands } = setup();
  store.operationBusy = true;
  await change(inputFor('Nombre'), 'No debe guardarse');
  assert.equal(commands.length, 0);
  store.operationBusy = false;
});
