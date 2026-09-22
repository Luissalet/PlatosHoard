import test from 'node:test';
import assert from 'node:assert/strict';
import { createPlatoPreview } from '../../static/editor/plato_preview.mjs';

function harness(payload, { ok = true, status = 200 } = {}) {
  const calls = [];
  const ops = [];
  const context = new Proxy({}, {
    set(target, key, value) { ops.push(['set', key, value]); target[key] = value; return true; },
    get(target, key) {
      if (key in target) return target[key];
      return (...args) => { ops.push([key, ...args]); };
    },
  });
  const canvas = {
    width: 0,
    height: 0,
    getContext: () => context,
    toBlob: (callback, type) => { ops.push(['toBlob', type]); callback(new Blob(['png'], { type })); },
  };
  return {
    calls,
    ops,
    canvas,
    fetchImpl: async (...args) => {
      calls.push(args);
      return { ok, status, json: async () => payload };
    },
    documentApi: { createElement: tag => { assert.equal(tag, 'canvas'); return canvas; } },
  };
}

const doc = {
  id: 'doc / uno',
  canvas: { width_mm: 200, height_mm: 100 },
  layers: {
    top: { id: 'top', name: 'Interior', stack_rank: 8, visible: true },
    frame: { id: 'layer_marco_walls', name: 'Marco paredes', stack_rank: -2 },
    bottom: { id: 'bottom', name: 'Exterior', stack_rank: 2 },
    hidden: { id: 'hidden', name: 'Oculta', stack_rank: 0, visible: false },
  },
};

test('requests visible non-frame layers in stack order and preserves document aspect ratio', async () => {
  const h = harness({ layers: {
    bottom: { rings: [{ exterior: [[0, 0], [20, 0], [20, 10]], holes: [] }] },
    top: { rings: [{ exterior: [[1, 2], [3, 2], [3, 4]], holes: [] }] },
  } });
  const blob = await createPlatoPreview(doc, h);
  assert.equal(blob.type, 'image/png');
  assert.equal(h.canvas.width, 640);
  assert.equal(h.canvas.height, 320);
  assert.equal(h.calls[0][0], '/api/v2/documents/doc%20%2F%20uno/mesh-rings');
  assert.deepEqual(JSON.parse(h.calls[0][1].body), {
    mode: 'normal', layer_ids: ['bottom', 'top'], include_subtree: false,
  });
});

test('draws Y downward, colours in order, and fills holes with evenodd', async () => {
  const h = harness({ layers: {
    bottom: { rings: [{
      exterior: [[1, 2], [3, 2], [3, 4]],
      holes: [[[1.5, 2.5], [2, 2.5], [2, 3]]],
    }] },
    top: { rings: [{ exterior: [[4, 5], [6, 5], [6, 7]], holes: [] }] },
  } });
  await createPlatoPreview(doc, h);
  assert.ok(h.ops.some(op => op[0] === 'moveTo' && op[1] === 3.2 && op[2] === 6.4));
  assert.ok(h.ops.some(op => op[0] === 'moveTo'
    && Math.abs(op[1] - 4.8) < 1e-9 && Math.abs(op[2] - 8) < 1e-9));
  assert.equal(h.ops.filter(op => op[0] === 'fill' && op[1] === 'evenodd').length, 2);
  const colours = h.ops.filter(op => op[0] === 'set' && op[1] === 'fillStyle').map(op => op[2]);
  assert.deepEqual(colours, ['#f4f0e8', '#202020', '#d4553f']);
  assert.ok(h.ops.some(op => op[0] === 'set' && op[1] === 'globalAlpha' && op[2] === 0.85));
});

test('rejects HTTP errors instead of producing an empty success', async () => {
  const h = harness({ error: { message: 'geometría rota' } }, { ok: false, status: 500 });
  await assert.rejects(() => createPlatoPreview(doc, h), /geometría rota/);
  assert.equal(h.ops.some(op => op[0] === 'toBlob'), false);
});

test('rejects missing, empty, and per-layer error geometry', async () => {
  for (const layers of [
    { bottom: { rings: [] }, top: { rings: [] } },
    { bottom: { rings: [{ exterior: [[0, 0], [1, 0], [1, 1]] }] }, top: { error: { message: 'falló top' } } },
    {},
  ]) {
    const h = harness({ layers });
    await assert.rejects(() => createPlatoPreview(doc, h));
    assert.equal(h.ops.some(op => op[0] === 'toBlob'), false);
  }
});

test('rejects a null PNG blob', async () => {
  const h = harness({ layers: {
    bottom: { rings: [{ exterior: [[0, 0], [1, 0], [1, 1]] }] },
    top: { rings: [{ exterior: [[0, 0], [1, 0], [1, 1]] }] },
  } });
  h.canvas.toBlob = callback => callback(null);
  await assert.rejects(() => createPlatoPreview(doc, h), /PNG/);
});
