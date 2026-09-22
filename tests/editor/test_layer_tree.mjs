import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<body><div id="fit-status"></div><div id="tree"></div></body>', {
  url: 'http://localhost/editor',
});
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.DOMParser = dom.window.DOMParser;
globalThis.CustomEvent = dom.window.CustomEvent;

const { store } = await import('../../static/editor/store.js');
const { LayerTree } = await import('../../static/editor/layer_tree.js');

function fixture() {
  return {
    id: 'doc',
    canvas: { width_mm: 200, height_mm: 200, padding_mm: {} },
    assets: {
      a: { id: 'a', source_filename: 'one.svg', source_viewbox: [0, 0, 10, 10], canonical_svg: '<svg><path d="M0 0h10v10z"/></svg>' },
    },
    layers: {
      A: { id: 'A', asset_id: 'a', name: 'A', parent_id: null, order: 0, visible: true, locked: false },
      B: { id: 'B', asset_id: 'a', name: 'B', parent_id: null, order: 1, visible: true, locked: false },
      C: { id: 'C', asset_id: 'a', name: 'C', parent_id: 'A', order: 0, visible: true, locked: false },
      D: { id: 'D', asset_id: 'a', name: 'D', parent_id: 'C', order: 0, visible: true, locked: false },
      L: { id: 'L', asset_id: 'a', name: 'Locked', parent_id: null, order: 2, visible: true, locked: true },
    },
  };
}

function setup(opts = {}) {
  store.doc = structuredClone(fixture());
  store.revision = 0;
  store.selectedId = 'C';
  store.operationBusy = false;
  store.resetHistory();
  document.getElementById('tree').innerHTML = '';
  const calls = [];
  const viewport = {
    renderLayers: () => calls.push('renderLayers'),
    renderSelection: () => calls.push('renderSelection'),
  };
  return { tree: new LayerTree(document.getElementById('tree'), viewport, opts), calls };
}

test('reparent, fit hook and rendering are awaited inside one operation/history group', async () => {
  const events = [];
  const originalCommit = store.commitCommand;
  const originalGroup = store.withHistoryGroup;
  store.commitCommand = async (type, payload) => {
    events.push(type);
    if (type === 'set_parent') store.doc.layers[payload.layer_id].parent_id = payload.new_parent_id;
    return { document: store.doc };
  };
  store.withHistoryGroup = async callback => {
    events.push('group:start');
    const result = await callback();
    events.push('group:end');
    return result;
  };
  const { tree, calls } = setup({
    runOperation: async (_label, callback) => {
      events.push('operation:start');
      const result = await callback();
      events.push('operation:end');
      return result;
    },
    onNest: async () => {
      events.push('fit:start');
      await Promise.resolve();
      await store.commitCommand('fit_child', {});
      events.push('fit:end');
    },
  });
  try {
    await tree._applyMove('C', 'B', 'into');
    assert.deepEqual(events, [
      'operation:start', 'group:start', 'set_parent', 'fit:start',
      'fit_child', 'fit:end', 'group:end', 'operation:end',
    ]);
    assert.equal(store.doc.layers.C.parent_id, 'B');
    assert.deepEqual(calls, ['renderLayers', 'renderSelection']);
  } finally {
    store.commitCommand = originalCommit;
    store.withHistoryGroup = originalGroup;
  }
});

test('selected controls expose precise valid moves and thumbnail nodes are cached', () => {
  const { tree } = setup();
  tree.render();

  const controls = document.querySelector('.tree-selection-controls');
  assert.ok(controls);
  assert.ok(controls.querySelector('[aria-label="Subir una posición"]'));
  assert.ok(controls.querySelector('[aria-label="Bajar una posición"]'));
  assert.ok(controls.querySelector('[aria-label="Sacar un nivel"]'));
  const values = [...controls.querySelector('select').options].map(option => option.value);
  assert.deepEqual(values, ['', 'A', 'B']); // excludes C, descendant D and locked L
  const cacheSize = tree._thumbnailCache.size;
  assert.equal(cacheSize, 5); // one colored template per rendered layer
  const thumbs = [...document.querySelectorAll('.tree-thumb')];
  assert.ok(thumbs.length > 1);
  assert.notEqual(thumbs[0], thumbs[1]);
  tree.render();
  assert.equal(tree._thumbnailCache.size, cacheSize);
});

test('selection updates handles without rebuilding viewport layer geometry', async () => {
  const { tree, calls } = setup();
  await tree._select('B');
  assert.equal(store.selectedId, 'B');
  assert.deepEqual(calls, ['renderSelection']);
});

test('busy state rejects a second tree mutation', async () => {
  const { tree } = setup();
  store.operationBusy = true;
  const before = store.doc.layers.C.parent_id;
  await tree._applyMove('C', 'B', 'into');
  assert.equal(store.doc.layers.C.parent_id, before);
});
