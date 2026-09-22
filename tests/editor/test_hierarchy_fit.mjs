import test from 'node:test';
import assert from 'node:assert/strict';
import { constrainDescendants, fitNestedPose } from '../../static/editor/hierarchy_fit.mjs';

function fixture() {
  const pose = { tx: 0, ty: 0, scale: 1, angle_deg: 0 };
  const layers = {
    moved: { id: 'moved', parent_id: 'outer', pose: { ...pose } },
    child: { id: 'child', parent_id: 'moved', pose: { ...pose } },
    grandchild: { id: 'grandchild', parent_id: 'child', pose: { ...pose } },
    unrelated: { id: 'unrelated', parent_id: 'outer', pose: { ...pose } },
  };
  const commands = [];
  return {
    doc: { id: 'test' }, baseUrl: '/api/v2', commands,
    subtreeIds: () => ['moved', 'child', 'grandchild'],
    layerById: id => layers[id],
    commitCommand: async (type, payload) => {
      commands.push({ type, ...payload });
      layers[payload.layer_id].pose = payload.pose;
    },
  };
}

test('hierarchy changes preserve valid descendants without fit searches or document writes', async () => {
  const store = fixture();
  const requests = [];
  const saved = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    const request = JSON.parse(options.body);
    requests.push({ url, ...request });
    return { ok: true, json: async () => ({ pose_local: request.pose }) };
  };
  try {
    assert.equal(await constrainDescendants(store, 'moved', 2), 0);
    assert.deepEqual(requests.map(r => r.layer_id), ['child', 'grandchild']);
    assert.ok(requests.every(r => r.url.endsWith('/constrain') && r.padding_mm === 2 && r.quality === 'preview'));
    assert.equal(store.commands.length, 0);
  } finally { globalThis.fetch = saved; }
});

test('a reduced parent is committed before checking its descendants clearance', async () => {
  const store = fixture();
  const saved = globalThis.fetch;
  globalThis.fetch = async (_url, options) => {
    const request = JSON.parse(options.body);
    if (request.layer_id === 'grandchild') assert.equal(store.layerById('child').pose.scale, 0.5);
    return { ok: true, json: async () => ({ pose_local: { ...request.pose, scale: 0.5 } }) };
  };
  try {
    assert.equal(await constrainDescendants(store, 'moved', 2), 2);
    assert.deepEqual(store.commands.map(c => c.layer_id), ['child', 'grandchild']);
    assert.equal(store.layerById('unrelated').pose.scale, 1);
  } finally { globalThis.fetch = saved; }
});

test('failed clearance check stops the chain instead of committing an invalid pose', async () => {
  const store = fixture();
  const saved = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: false, json: async () => ({ error: { message: 'No cabe' } }) });
  try {
    await assert.rejects(constrainDescendants(store, 'moved', 2), /No cabe/);
    assert.equal(store.commands.length, 0);
  } finally { globalThis.fetch = saved; }
});

test('clearance edits request a fixed-centre fit and allow a shrunken layer to grow again', async () => {
  const store = fixture();
  store.layerById('child').pose = { tx: 12, ty: -7, scale: 0.2, angle_deg: 35 };
  const saved = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (_url, options) => {
    const request = JSON.parse(options.body);
    requests.push(request);
    return { ok: true, json: async () => ({ pose_local: { ...request.pose, scale: 0.8 } }) };
  };
  try {
    const result = await fitNestedPose(store, 'child', 1, { mode: 'at_position', quality: 'preview' });
    assert.equal(requests.length, 1);
    assert.equal(requests[0].mode, 'at_position');
    assert.equal(requests[0].quality, 'preview');
    assert.equal(requests[0].padding_mm, 1);
    assert.deepEqual(requests[0].angles_deg, [35]);
    assert.deepEqual(result, { tx: 12, ty: -7, scale: 0.8, angle_deg: 35 });
  } finally { globalThis.fetch = saved; }
});

test('clearance only searches a new centre when no fit exists at the chosen position', async () => {
  const store = fixture();
  const saved = globalThis.fetch;
  const modes = [];
  globalThis.fetch = async (_url, options) => {
    const request = JSON.parse(options.body);
    modes.push(request.mode);
    return request.mode === 'at_position'
      ? { ok: false, status: 422, json: async () => ({ error: { message: 'No feasible pose' } }) }
      : { ok: true, json: async () => ({ pose_local: request.pose }) };
  };
  try {
    await fitNestedPose(store, 'child', 20, { mode: 'at_position', quality: 'preview' });
    assert.deepEqual(modes, ['at_position', 'best']);
    modes.length = 0;
    await fitNestedPose(store, 'child', 2);
    assert.deepEqual(modes, ['best']); // Manual best-fit keeps its full search.
  } finally { globalThis.fetch = saved; }
});

test('network/server errors are surfaced without launching another expensive fit', async () => {
  const saved = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return { ok: false, status: 500, json: async () => ({ error: { message: 'Server error' } }) };
  };
  try {
    await assert.rejects(fitNestedPose(fixture(), 'child', 2, { mode: 'at_position' }), /Server error/);
    assert.equal(calls, 1);
  } finally { globalThis.fetch = saved; }
});
