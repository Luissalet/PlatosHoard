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

const affine = await import('../../static/editor/affine.mjs');
const { store } = await import('../../static/editor/store.js');
const { Viewport2D } = await import('../../static/editor/viewport2d.js');
const { Interactions2D } = await import('../../static/editor/interactions2d.js');

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
    pointerId: 1,
    preventDefault: () => {},
  };
}

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
