import assert from 'node:assert/strict';
import test from 'node:test';

import { EditorStore } from '../../static/editor/store.js';

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function makeStore() {
  const store = new EditorStore('/api/v2');
  store.doc = {
    id: 'doc-1',
    canvas: { width_mm: 200, height_mm: 200 },
    assets: {},
    layers: { a: { id: 'a', pose: { tx: 0 } } },
  };
  store.revision = 0;
  store._resetHistory();
  return store;
}

function installServer(store, { failTypes = new Set(), delayFirst = false } = {}) {
  const requests = [];
  let active = 0;
  let maxActive = 0;
  globalThis.fetch = async (_url, options) => {
    active += 1;
    maxActive = Math.max(maxActive, active);
    const envelope = JSON.parse(options.body);
    requests.push(envelope);
    if (delayFirst && requests.length === 1) {
      await new Promise(resolve => setTimeout(resolve, 15));
    }
    if (failTypes.has(envelope.type)) {
      active -= 1;
      return { ok: false, status: 409, json: async () => ({ error: { message: 'conflict' } }) };
    }
    const doc = clone(store.doc);
    if (envelope.type === 'restore_snapshot') {
      doc.assets = clone(envelope.payload.snapshot.assets);
      doc.layers = clone(envelope.payload.snapshot.layers);
    } else if (envelope.type === 'move') {
      doc.layers.a.pose.tx = envelope.payload.tx;
    }
    const revision = store.revision + 1;
    active -= 1;
    return { ok: true, json: async () => ({ document: doc, revision }) };
  };
  return { requests, get maxActive() { return maxActive; } };
}

test('successful commands create a state timeline that undo and redo traverse', async () => {
  const store = makeStore();
  installServer(store);

  await store.commitCommand('move', { tx: 10 });
  assert.equal(store.doc.layers.a.pose.tx, 10);
  assert.equal(store.canUndo(), true);
  assert.equal(store.canRedo(), false);

  await store.undo();
  assert.equal(store.doc.layers.a.pose.tx, 0);
  assert.equal(store.canUndo(), false);
  assert.equal(store.canRedo(), true);

  await store.redo();
  assert.equal(store.doc.layers.a.pose.tx, 10);
  assert.equal(store.canRedo(), false);
});

test('commands are serialized and take their base revision when they start', async () => {
  const store = makeStore();
  const server = installServer(store, { delayFirst: true });

  const pending = Promise.all([
    store.commitCommand('move', { tx: 1 }),
    store.commitCommand('move', { tx: 2 }),
  ]);
  assert.equal(store.commandBusy, true);
  await pending;

  assert.equal(server.maxActive, 1);
  assert.deepEqual(server.requests.map(r => r.base_revision), [0, 1]);
  assert.equal(store.doc.layers.a.pose.tx, 2);
  assert.equal(store.commandBusy, false);
});

test('failed commands do not add history and do not poison the queue', async () => {
  const store = makeStore();
  installServer(store, { failTypes: new Set(['fail']) });

  await assert.rejects(store.commitCommand('fail', {}), /conflict/);
  assert.equal(store.history.length, 1);
  assert.equal(store.canUndo(), false);
  await store.commitCommand('move', { tx: 4 });
  assert.equal(store.doc.layers.a.pose.tx, 4);
  assert.equal(store.canUndo(), true);
});

test('a rejected optimistic gesture restores the confirmed document', async () => {
  const store = makeStore();
  installServer(store, { failTypes: new Set(['fail']) });
  store._pendingHistorySnapshot = store._snapshot();
  store.doc.layers.a.pose.tx = 99;

  await assert.rejects(store.commitCommand('fail', {}), /conflict/);
  assert.equal(store.doc.layers.a.pose.tx, 0);
  assert.equal(store.history.length, 1);
});

test('network and response parse failures restore the full optimistic snapshot', async () => {
  const store = makeStore();
  const before = store._snapshot();
  store._pendingHistorySnapshot = before;
  store.doc.canvas.width_mm = 999;
  store.doc.layers.a.pose.tx = 99;
  globalThis.fetch = async () => { throw new Error('network down'); };

  await assert.rejects(store.commitCommand('fail', {}), /network down/);
  assert.deepEqual(store.doc.canvas, before.canvas);
  assert.deepEqual(store.doc.layers, before.layers);

  store._pendingHistorySnapshot = store._snapshot();
  store.doc.canvas.height_mm = 777;
  globalThis.fetch = async () => ({ ok: true, json: async () => { throw new Error('invalid json'); } });
  await assert.rejects(store.commitCommand('fail', {}), /invalid json/);
  assert.deepEqual(store.doc.canvas, before.canvas);
  assert.deepEqual(store.doc.layers, before.layers);
});

test('gesture snapshots preserve the true state before optimistic mutation', async () => {
  const store = makeStore();
  installServer(store);
  store._pendingHistorySnapshot = store._snapshot();
  store.doc.layers.a.pose.tx = 7;

  await store.commitCommand('move', { tx: 7 });
  await store.undo();
  assert.equal(store.doc.layers.a.pose.tx, 0);
});

test('history groups collapse multiple commands into one undo step', async () => {
  const store = makeStore();
  installServer(store);

  await store.withHistoryGroup(async () => {
    await store.commitCommand('move', { tx: 3 });
    await store.commitCommand('move', { tx: 9 });
  });

  assert.equal(store.history.length, 2);
  await store.undo();
  assert.equal(store.doc.layers.a.pose.tx, 0);
});

test('a new command after undo discards the redo branch', async () => {
  const store = makeStore();
  installServer(store);
  await store.commitCommand('move', { tx: 1 });
  await store.commitCommand('move', { tx: 2 });
  await store.undo();
  await store.commitCommand('move', { tx: 8 });

  assert.equal(store.canRedo(), false);
  await store.undo();
  assert.equal(store.doc.layers.a.pose.tx, 1);
});

test('successful mutations from upload APIs can join the undo timeline', async () => {
  const store = makeStore();
  const before = store._snapshot();
  const document = clone(store.doc);
  document.assets.uploaded = { id: 'uploaded' };
  store.applyExternalMutationPayload({ document, revision: 1 }, before);

  assert.equal(store.history.length, 2);
  assert.equal(store.doc.assets.uploaded.id, 'uploaded');
  installServer(store);
  await store.undo();
  assert.deepEqual(store.doc.assets, {});
});
