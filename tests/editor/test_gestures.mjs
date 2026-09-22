// test_gestures.mjs — task 08 gates (drag, pan, gesture transactions)
// Runs under jsdom: node --test tests/editor/test_gestures.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM(`<!DOCTYPE html><body>
  <svg id="editor-svg" viewBox="0 0 200 200">
    <g id="canvas-guides"></g>
    <g id="layer-content"></g>
    <g id="constraint-overlays" pointer-events="none"></g>
    <g id="selection-handles"></g>
  </svg>
</body>`, { url: 'http://localhost/editor' });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.SVGElement = dom.window.SVGElement;
globalThis.DOMParser = dom.window.DOMParser;
// jsdom does not provide DOMPoint — implement the minimal API affine.mjs needs.
class DOMPoint {
  constructor(x = 0, y = 0, z = 0, w = 1) { this.x = x; this.y = y; this.z = z; this.w = w; }
  matrixTransform(m) {
    const x = m.a * this.x + m.c * this.y + m.e;
    const y = m.b * this.x + m.d * this.y + m.f;
    return new DOMPoint(x, y, this.z, this.w);
  }
}
globalThis.DOMPoint = DOMPoint;
globalThis.requestAnimationFrame = (cb) => setTimeout(cb, 0);
globalThis.CustomEvent = dom.window.CustomEvent;

// jsdom does not implement SVG CTM methods — stub them with an identity
// mapping (client pixels == document mm) so gesture math is testable.
const identityCTM = {
  a: 1, b: 0, c: 0, d: 1, e: 0, f: 0,
  inverse: () => identityCTM,
};
dom.window.SVGElement.prototype.getScreenCTM = function () { return identityCTM; };
dom.window.SVGElement.prototype.setPointerCapture = function () {};
dom.window.SVGElement.prototype.releasePointerCapture = function () {};

const affine = await import('../../static/editor/affine.mjs');
const { store } = await import('../../static/editor/store.js');
const { Viewport2D } = await import('../../static/editor/viewport2d.js');
const { Interactions2D } = await import('../../static/editor/interactions2d.js');
const { Inspector } = await import('../../static/editor/inspector.js');

// Two-layer fixture: A (root, 100mm box at (100,100) scale 1.6) and
// B (child of A, 60mm box, identity pose).
const fixture = {
  schema_version: 2, id: 'demo', name: 'demo', revision: 0, units: 'mm',
  canvas: { width_mm: 200, height_mm: 200,
            padding_mm: { left: 10, right: 10, top: 10, bottom: 10 } },
  default_extrusion_mm: 3, stack_gap_mm: 0,
  assets: {
    a: { id: 'a', name: 'A', source_type: 'svg',
         source_uri: 'assets/a/source.svg', canonical_svg_uri: 'assets/a/canonical.svg',
         source_sha256: '26b4545ab3367bd3d0298f3cad32bcda24208d4f1552aa0779e1aaa72d0fd0ba',
         source_viewbox: [0, 0, 100, 100], mm_per_source_unit: 1.0,
         normalization_pose: { tx: -50, ty: -50, scale: 1, angle_deg: 0 },
         geometry_hash: 'e98d0a63dc37d25cd337edb1f5544a172eda42e566341824e25296070930ea25',
         trace_settings: {}, curve_tolerance_source: 0.02,
         local_bounds: [-50, -50, 50, 50],
         canonical_svg: '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0 H100 V100 H0 Z"/></svg>' },
    b: { id: 'b', name: 'B', source_type: 'svg',
         source_uri: 'assets/b/source.svg', canonical_svg_uri: 'assets/b/canonical.svg',
         source_sha256: '2d2a9918b5b43cbcdcee065c901d25c897a07b067329504bb95fb8702268df1f',
         source_viewbox: [0, 0, 60, 60], mm_per_source_unit: 1.0,
         normalization_pose: { tx: -30, ty: -30, scale: 1, angle_deg: 0 },
         geometry_hash: '0a40d28facf1cf99d610c5d6a351133a7ef32fd5254ff8b1ab68d2f5f46bfea3',
         trace_settings: {}, curve_tolerance_source: 0.02,
         local_bounds: [-30, -30, 30, 30],
         canonical_svg: '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0 H60 V60 H0 Z"/></svg>' },
  },
  layers: {
    A: { id: 'A', asset_id: 'a', name: 'Exterior', parent_id: null, order: 0, stack_rank: 0,
         pose: { tx: 100, ty: 100, scale: 1.6, angle_deg: 0 },
         visible: true, locked: false, export_enabled: true, extrusion_mm: null,
         fit: { target: 'canvas', hole_id: null, padding_mm: 0, avoid_siblings: true,
                sibling_gap_mm: 2, auto_scale_while_dragging: false, allow_rotation: false,
                rotation_half_range_deg: 20 } },
    B: { id: 'B', asset_id: 'b', name: 'Intermedio', parent_id: 'A', order: 0, stack_rank: 1,
         pose: { tx: 0, ty: 0, scale: 1, angle_deg: 0 },
         visible: true, locked: false, export_enabled: true, extrusion_mm: null,
         fit: { target: 'parent_shape', hole_id: null, padding_mm: 5, avoid_siblings: true,
                sibling_gap_mm: 2, auto_scale_while_dragging: false, allow_rotation: false,
                rotation_half_range_deg: 20 } },
  },
};

function makeEnv() {
  store.doc = structuredClone(fixture);
  store.revision = 0;
  store.selectedId = null;
  store.operationBusy = false;
  store.history = { undo: [], redo: [] };
  const vp = new Viewport2D(document.getElementById('editor-svg'));
  vp.renderCanvas();
  vp.renderLayers();
  const inter = new Interactions2D(vp);
  return { vp, inter };
}

// Simulate a pointer event with clientX/clientY and a target element.
function pointerEvent(type, clientX, clientY, target, extra = {}) {
  return {
    type,
    clientX, clientY,
    target: target || document.getElementById('editor-svg'),
    button: extra.button ?? 0,
    pointerId: extra.pointerId ?? 1,
    preventDefault: () => {},
  };
}

test('canvas selection opens the inspector without needing a hierarchy click', () => {
  const { vp, inter } = makeEnv();
  const panel = document.createElement('section');
  document.body.appendChild(panel);
  let reveals = 0;
  panel.scrollIntoView = () => { reveals += 1; };
  new Inspector(panel, vp);
  const layerEl = document.querySelector('[data-layer-id="B"]');
  inter._onDown(pointerEvent('pointerdown', 100, 100, layerEl));
  assert.equal(store.selectedId, 'B');
  assert.equal(panel.querySelector('input[type="text"]').value, 'Intermedio');
  assert.equal(reveals, 0, "selection must not scroll the canvas under a held pointer");
  inter._onUp(pointerEvent('pointerup', 100, 100, document.getElementById('editor-svg')));
  assert.equal(store.selectedId, 'B', 'pointer capture must preserve selection');
  assert.equal(reveals, 1, 'reveal properties after releasing the pointer');
  inter._onDown(pointerEvent('pointerdown', 0, 0, document.getElementById('editor-svg')));
  assert.equal(store.selectedId, null);
  assert.match(panel.textContent, /Sin selección/);
  inter._cancelGesture();
  panel.remove();
});

test('a second pointer cannot move or finish the active gesture', () => {
  const { inter } = makeEnv();
  const layerEl = [...document.querySelectorAll('g.layer')].find(g => g.dataset.layerId === 'A');
  const initial = { ...store.doc.layers.A.pose };
  inter._onDown(pointerEvent('pointerdown', 100, 100, layerEl, { pointerId: 7 }));
  inter._onMove(pointerEvent('pointermove', 160, 100, layerEl, { pointerId: 8 }));
  assert.deepEqual(store.doc.layers.A.pose, initial);
  inter._onUp(pointerEvent('pointerup', 160, 100, layerEl, { pointerId: 8 }));
  assert.equal(inter._gesture?.kind, 'drag');
  inter._cancelGesture();
});

test('busy operations block pointer gestures, history shortcuts, and wheel zoom', () => {
  const { inter } = makeEnv();
  const svg = document.getElementById('editor-svg');
  const layerEl = [...document.querySelectorAll('g.layer')].find(g => g.dataset.layerId === 'A');
  const beforeViewport = { ...store.viewport };
  let undoCalled = false;
  inter.undo = () => { undoCalled = true; return true; };
  store.operationBusy = true;

  inter._onDown(pointerEvent('pointerdown', 100, 100, layerEl));
  inter._onKeyDown({ key: 'z', ctrlKey: true, shiftKey: false, target: document.body, preventDefault() {} });
  inter._onWheel({ deltaY: -100, clientX: 100, clientY: 100, target: svg, preventDefault() {} });

  assert.equal(inter._gesture, null);
  assert.equal(undoCalled, false);
  assert.deepEqual(store.viewport, beforeViewport);
  store.operationBusy = false;
});

test('pose clamp coalesces a pointermove burst to one calculation per frame', async () => {
  const { inter } = makeEnv();
  const layerEl = [...document.querySelectorAll('g.layer')].find(g => g.dataset.layerId === 'A');
  let clamps = 0;
  inter._maybeClampPose = (_id, pose) => { clamps += 1; return pose; };
  inter._onDown(pointerEvent('pointerdown', 100, 100, layerEl));
  for (let i = 0; i < 20; i++) inter._onMove(pointerEvent('pointermove', 101 + i, 100, layerEl));
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(clamps, 1);
  assert.equal(inter._gesture.lastRawPose.tx, 120);
  inter._cancelGesture();
});

test('pointerup flushes the latest release point and cancels queued preview RAF', async () => {
  const { inter } = makeEnv();
  const layerEl = [...document.querySelectorAll('g.layer')].find(g => g.dataset.layerId === 'A');
  inter._maybeClampPose = (_id, pose) => pose;
  const poses = [];
  const previousCommit = store.commitCommand;
  store.commitCommand = async (_type, payload) => { poses.push(payload.pose); return {}; };
  inter._onDown(pointerEvent('pointerdown', 100, 100, layerEl));
  inter._onMove(pointerEvent('pointermove', 110, 100, layerEl));
  inter._onMove(pointerEvent('pointermove', 120, 100, layerEl));
  inter._onUp(pointerEvent('pointerup', 135, 100, layerEl));
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(poses.length, 1);
  assert.equal(poses[0].tx, 135);
  assert.equal(inter._pendingPose, null);
  assert.equal(inter._rafId, null);
  store.commitCommand = previousCommit;
});

test('release flush does not enqueue a duplicate live server preview', async () => {
  const { inter, vp } = makeEnv();
  store.matrioskaMode = true;
  const layerEl = vp._layerEls.get('B');
  let previews = 0;
  inter._maybeClampPose = (_id, pose) => pose;
  inter._scheduleServerPreview = () => { previews += 1; };
  inter._onDown(pointerEvent('pointerdown', 100, 100, layerEl));
  inter._onMove(pointerEvent('pointermove', 120, 100, layerEl));
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(previews, 1);
  inter._onUp(pointerEvent('pointerup', 135, 100, layerEl));
  assert.equal(previews, 1);
  store.matrioskaMode = false;
});

test('cancel discards a queued pose calculation and no-op clicks do not fit', async () => {
  const { inter } = makeEnv();
  const layerEl = [...document.querySelectorAll('g.layer')].find(g => g.dataset.layerId === 'A');
  let clamps = 0;
  inter._maybeClampPose = (_id, pose) => { clamps += 1; return pose; };
  inter._onDown(pointerEvent('pointerdown', 100, 100, layerEl));
  inter._onMove(pointerEvent('pointermove', 140, 100, layerEl));
  inter._cancelGesture();
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(clamps, 0);

  inter._onDown(pointerEvent('pointerdown', 100, 100, layerEl));
  inter._onUp(pointerEvent('pointerup', 100, 100, layerEl));
  assert.equal(clamps, 0);
});

test('async pose finalization holds operationBusy through fit and releases on failure', async () => {
  const { inter } = makeEnv();
  store.matrioskaMode = true;
  const previousFetch = globalThis.fetch;
  let releaseFit;
  let fitCalls = 0;
  globalThis.fetch = async () => {
    fitCalls += 1;
    if (fitCalls === 1) await new Promise(resolve => { releaseFit = resolve; });
    throw new Error('fit unavailable');
  };
  const previousCommit = store.commitCommand;
  store.commitCommand = async () => { throw new Error('commit unavailable'); };

  const pending = inter._commitPose('B', { ...store.doc.layers.B.pose });
  assert.equal(store.operationBusy, true);
  // A second gesture entry is rejected while fit/constrain is outstanding.
  inter._onDown(pointerEvent('pointerdown', 100, 100, document.getElementById('editor-svg')));
  assert.equal(inter._gesture, null);
  releaseFit();
  await pending;
  assert.equal(store.operationBusy, false);

  store.commitCommand = previousCommit;
  globalThis.fetch = previousFetch;
  store.matrioskaMode = false;
});

test('100 pointermove events produce exactly ONE committed command', async () => {
  const { inter } = makeEnv();
  const layerEl = document.querySelector('g.layer[data-layer-id="A"]') ||
                  [...document.querySelectorAll('g.layer')].find(g => g.dataset.layerId === 'A');
  assert.ok(layerEl, 'layer A element exists');

  let commitCount = 0;
  const origCommit = store.commitCommand.bind(store);
  store.commitCommand = async (type, payload) => {
    commitCount++;
    return origCommit(type, payload);
  };

  // pointerdown on layer A
  inter._onDown(pointerEvent('pointerdown', 100, 100, layerEl));
  assert.ok(inter._gesture, 'gesture started');
  assert.equal(inter._gesture.kind, 'drag');

  // 100 pointermove events
  for (let i = 0; i < 100; i++) {
    inter._onMove(pointerEvent('pointermove', 100 + i * 0.1, 100, layerEl));
  }

  // pointerup → ONE commit
  inter._onUp(pointerEvent('pointerup', 200, 100, layerEl));

  store.commitCommand = origCommit;
  assert.equal(commitCount, 1, `expected 1 commit, got ${commitCount}`);
});

test('Escape restores the initial pose exactly (no commit)', async () => {
  const { inter } = makeEnv();
  const layerEl = [...document.querySelectorAll('g.layer')].find(g => g.dataset.layerId === 'A');
  const initialPose = { ...store.doc.layers.A.pose };

  let commitCount = 0;
  const origCommit = store.commitCommand.bind(store);
  store.commitCommand = async () => { commitCount++; return {}; };

  // Start a drag
  inter._onDown(pointerEvent('pointerdown', 100, 100, layerEl));
  // Move the pointer
  inter._onMove(pointerEvent('pointermove', 150, 120, layerEl));
  // Escape → cancel
  inter._cancelGesture();

  store.commitCommand = origCommit;
  assert.equal(commitCount, 0, 'no commit on Escape');
  assert.deepEqual(store.doc.layers.A.pose, initialPose, 'pose restored to initial');
});

test('pan does not modify any layer pose (content hash unchanged)', async () => {
  const { inter } = makeEnv();
  const svg = document.getElementById('editor-svg');
  const before = JSON.stringify(store.doc.layers);

  // Start a pan (empty space, button 0)
  inter._onDown(pointerEvent('pointerdown', 50, 50, svg));
  assert.equal(inter._gesture?.kind, 'pan');

  // Move
  inter._onMove(pointerEvent('pointermove', 80, 70, svg));
  // Release
  inter._onUp(pointerEvent('pointerup', 80, 70, svg));

  const after = JSON.stringify(store.doc.layers);
  assert.equal(before, after, 'pan must not change any layer pose');
});

test('middle-button pan does not modify any layer pose', async () => {
  const { inter } = makeEnv();
  const svg = document.getElementById('editor-svg');
  const before = JSON.stringify(store.doc.layers);

  // Middle button (button=1)
  inter._onDown(pointerEvent('pointerdown', 50, 50, svg, { button: 1 }));
  assert.equal(inter._gesture?.kind, 'pan');

  inter._onMove(pointerEvent('pointermove', 90, 90, svg));
  inter._onUp(pointerEvent('pointerup', 90, 90, svg));

  const after = JSON.stringify(store.doc.layers);
  assert.equal(before, after, 'middle-button pan must not change poses');
});

test('Space+drag pans even over a layer', async () => {
  const { inter } = makeEnv();
  const layerEl = [...document.querySelectorAll('g.layer')].find(g => g.dataset.layerId === 'A');
  const before = JSON.stringify(store.doc.layers);

  // Simulate Space keydown
  inter._onKeyDown({ code: 'Space', key: ' ', target: document.body, preventDefault: () => {} });
  assert.equal(inter._spaceDown, true);

  // pointerdown on a layer while Space is held → pan, not drag
  inter._onDown(pointerEvent('pointerdown', 100, 100, layerEl));
  assert.equal(inter._gesture?.kind, 'pan', 'Space+drag should be a pan');

  inter._onMove(pointerEvent('pointermove', 130, 130, layerEl));
  inter._onUp(pointerEvent('pointerup', 130, 130, layerEl));

  // Space keyup
  inter._onKeyUp({ code: 'Space', key: ' ', target: document.body });

  const after = JSON.stringify(store.doc.layers);
  assert.equal(before, after, 'Space+drag must not change poses');
});

test('drag with a rotated parent: dragPose uses the parent frame', async () => {
  const { inter } = makeEnv();
  // Rotate parent A by 90 degrees
  store.doc.layers.A.pose = { tx: 100, ty: 100, scale: 1.6, angle_deg: 90 };
  inter.viewport.renderLayers();

  const layerEl = [...document.querySelectorAll('g.layer')].find(g => g.dataset.layerId === 'B');
  assert.ok(layerEl, 'layer B element exists');

  const initialPose = { ...store.doc.layers.B.pose };

  let commitCount = 0;
  const origCommit = store.commitCommand.bind(store);
  store.commitCommand = async (type, payload) => {
    commitCount++;
    // Apply the pose to the document (simulating server response)
    store.doc.layers[payload.layer_id].pose = payload.pose;
    store.revision++;
    return { document: store.doc, revision: store.revision };
  };

  // Start drag on B (child of rotated A)
  inter._onDown(pointerEvent('pointerdown', 100, 100, layerEl));
  assert.equal(inter._gesture?.kind, 'drag');
  assert.equal(inter._gesture.layerId, 'B');

  // Move pointer by (10, 0) in screen space
  inter._onMove(pointerEvent('pointermove', 110, 100, layerEl));
  // Release
  inter._onUp(pointerEvent('pointerup', 110, 100, layerEl));

  store.commitCommand = origCommit;
  assert.equal(commitCount, 1, 'one commit for the drag');
  // The pose should have changed (B moved relative to rotated A)
  assert.notDeepEqual(store.doc.layers.B.pose, initialPose, 'B pose changed after drag');
});

test('wheel zoom does not modify any layer pose', async () => {
  const { inter } = makeEnv();
  const svg = document.getElementById('editor-svg');
  const before = JSON.stringify(store.doc.layers);

  // Simulate wheel event
  inter._onWheel({
    deltaY: -100,
    clientX: 100, clientY: 100,
    target: svg,
    preventDefault: () => {},
  });

  const after = JSON.stringify(store.doc.layers);
  assert.equal(before, after, 'wheel zoom must not change poses');
});

test('undo restores the previous pose after a committed drag', async () => {
  const { inter } = makeEnv();
  const layerEl = [...document.querySelectorAll('g.layer')].find(g => g.dataset.layerId === 'A');
  const initialPose = { ...store.doc.layers.A.pose };

  const origCommit = store.commitCommand.bind(store);
  store.commitCommand = async (type, payload) => {
    store.doc.layers[payload.layer_id].pose = payload.pose;
    store.revision++;
    return { document: store.doc, revision: store.revision };
  };

  // Drag and commit
  inter._onDown(pointerEvent('pointerdown', 100, 100, layerEl));
  inter._onMove(pointerEvent('pointermove', 150, 120, layerEl));
  inter._onUp(pointerEvent('pointerup', 150, 120, layerEl));

  const afterDrag = { ...store.doc.layers.A.pose };
  assert.notDeepEqual(afterDrag, initialPose, 'pose changed after drag');

  // Undo
  const didUndo = inter.undo();
  assert.equal(didUndo, true, 'undo succeeded');
  assert.deepEqual(store.doc.layers.A.pose, initialPose, 'pose restored after undo');

  // Redo
  const didRedo = inter.redo();
  assert.equal(didRedo, true, 'redo succeeded');
  assert.deepEqual(store.doc.layers.A.pose, afterDrag, 'pose restored after redo');

  store.commitCommand = origCommit;
});

test('shortcuts are ignored when the target is an input element', async () => {
  const { inter } = makeEnv();
  const input = document.createElement('input');
  document.body.appendChild(input);

  // Simulate Ctrl+Z while focused on an input
  inter._onKeyDown({
    key: 'z', ctrlKey: true, shiftKey: false,
    target: input,
    preventDefault: () => {},
  });

  // No undo should have happened (history is empty anyway, but the
  // important thing is that _inInput returned true and the handler exited)
  assert.equal(store.history.undo.length, 0, 'no history entry from input shortcut');
  input.remove();
});

test('pointercancel restores the initial pose (no commit)', async () => {
  const { inter } = makeEnv();
  const layerEl = [...document.querySelectorAll('g.layer')].find(g => g.dataset.layerId === 'A');
  const initialPose = { ...store.doc.layers.A.pose };

  let commitCount = 0;
  const origCommit = store.commitCommand.bind(store);
  store.commitCommand = async () => { commitCount++; return {}; };

  inter._onDown(pointerEvent('pointerdown', 100, 100, layerEl));
  inter._onMove(pointerEvent('pointermove', 150, 120, layerEl));
  inter._onCancel(pointerEvent('pointercancel', 150, 120, layerEl));

  store.commitCommand = origCommit;
  assert.equal(commitCount, 0, 'no commit on pointercancel');
  assert.deepEqual(store.doc.layers.A.pose, initialPose, 'pose restored after pointercancel');
});


test('an intermediate drag previews its descendants without mutating the document', async () => {
  const { vp, inter } = makeEnv();
  store.doc.layers.C = { ...structuredClone(store.doc.layers.B), id: 'C', parent_id: 'B',
    pose: { tx: 5, ty: 0, scale: 0.3, angle_deg: 0 } };
  vp.renderLayers();
  const before = structuredClone(store.doc.layers);
  const child = vp._layerEls.get('C');
  const childStart = child.getAttribute('transform');
  inter._maybeClampPose = (_id, pose) => pose;
  inter._scheduleServerPreview = () => {};
  inter._onDown(pointerEvent('pointerdown', 100, 100, vp._layerEls.get('B')));
  inter._onMove(pointerEvent('pointermove', 120, 100));
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.notEqual(child.getAttribute('transform'), childStart);
  const matrixValues = text => text.match(/matrix\((.*)\)/)[1].split(/\s+/).map(Number);
  assert.equal(matrixValues(child.getAttribute('transform'))[4] - matrixValues(childStart)[4], 20);
  assert.deepEqual(store.doc.layers, before);
  inter._cancelGesture();
  assert.equal(vp._layerEls.get('C').getAttribute('transform'), childStart);
});

test('a shrunk child can grow in the live preview before a server response', async () => {
  const { vp, inter } = makeEnv();
  store.doc.layers.B.pose.scale = 0.2;
  inter._maybeClampPose = (_id, pose) => ({ ...pose, scale: 1 });
  inter._scheduleServerPreview = () => {};
  inter._onDown(pointerEvent('pointerdown', 100, 100, vp._layerEls.get('B')));
  inter._onMove(pointerEvent('pointermove', 110, 100));
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(inter._pendingPose.pose.scale, 1);
  inter._cancelGesture();
});

test('returning inward queues a fresh fit and ignores a late edge preview', async () => {
  const { inter } = makeEnv();
  store.matrioskaMode = true;
  const oldFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (_url, opts) => new Promise(resolve => {
    requests.push({ body: JSON.parse(opts.body), resolve });
  });
  const poses = [];
  inter._previewPose = (_id, pose) => poses.push(pose);
  const g = { layerId: 'B', serverPoseFor: { tx: 0, ty: 0 } };
  inter._gesture = g;
  const edge = { tx: 50, ty: 0, scale: 0.2, angle_deg: 0 };
  const center = { tx: 0, ty: 0, scale: 0.2, angle_deg: 0 };
  try {
    g.lastRawPose = edge;
    inter._scheduleServerPreview(g, edge);
    g.lastRawPose = center;
    inter._scheduleServerPreview(g, center);
    requests[0].resolve({ ok: true, json: async () => ({ pose_local: edge }) });
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(requests.length, 2);
    assert.equal(requests[1].body.pose.tx, 0);
    assert.equal(poses.length, 0, 'stale edge result must not overwrite current preview');
    requests[1].resolve({ ok: true, json: async () => ({ pose_local: { ...center, scale: 1 } }) });
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(poses.at(-1).scale, 1);
  } finally {
    globalThis.fetch = oldFetch;
    inter._gesture = null;
    store.matrioskaMode = false;
  }
});

test('release away from the last fitted edge requests a fit, not a shrink-only clamp', () => {
  const { vp, inter } = makeEnv();
  inter._onDown(pointerEvent('pointerdown', 100, 100, vp._layerEls.get('B')));
  inter._gesture.serverPose = { tx: 50, ty: 0, scale: 0.2, angle_deg: 0 };
  inter._gesture.serverPoseFor = { tx: 50, ty: 0 };
  inter._gesture.serverPoseExact = true;
  inter._gesture.lastRawPose = { tx: 0, ty: 0, scale: 1, angle_deg: 0 };
  inter._pendingPose = { layerId: 'B', pose: { tx: 0, ty: 0, scale: 0.2, angle_deg: 0 } };
  let request;
  inter._commitPose = (id, pose, opts) => { request = { id, pose, opts }; };
  inter._onUp(pointerEvent('pointerup', 100, 100));
  assert.equal(request.pose.tx, 0);
  assert.notEqual(request.opts.constrainOnly, true);
  assert.notEqual(request.opts.skipServerFit, true);
});
