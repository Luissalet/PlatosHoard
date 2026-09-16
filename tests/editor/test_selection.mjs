// test_selection.mjs — task 07 gates (viewport rendering, selection, overlays)
// Runs under jsdom: node --test tests/editor/test_selection.mjs
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
globalThis.DOMPoint = dom.window.DOMPoint;

const affine = await import('../../static/editor/affine.mjs');
const { store } = await import('../../static/editor/store.js');
const { Viewport2D } = await import('../../static/editor/viewport2d.js');

// Three-layer fixture (mirrors _ref/Silhouettes_editor_Qwen/fixtures/project/project.json)
const fixture = {
  schema_version: 2, id: 'demo_three_layers', name: 'Tres capas', revision: 0, units: 'mm',
  canvas: { width_mm: 200, height_mm: 200,
            padding_mm: { left: 10, right: 10, top: 10, bottom: 10 } },
  default_extrusion_mm: 3, stack_gap_mm: 0,
  assets: {
    a: { id: 'a', name: 'Fixture a', source_type: 'svg',
         source_uri: 'assets/a/source.svg', canonical_svg_uri: 'assets/a/canonical.svg',
         source_sha256: '26b4545ab3367bd3d0298f3cad32bcda24208d4f1552aa0779e1aaa72d0fd0ba',
         source_viewbox: [0, 0, 100, 100], mm_per_source_unit: 1.0,
         normalization_pose: { tx: -50, ty: -50, scale: 1, angle_deg: 0 },
         geometry_hash: 'e98d0a63dc37d25cd337edb1f5544a172eda42e566341824e25296070930ea25',
         trace_settings: {}, curve_tolerance_source: 0.02,
         local_bounds: [-50, -50, 50, 50],
         canonical_svg: '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0 H100 V100 H0 Z"/></svg>' },
    b: { id: 'b', name: 'Fixture b', source_type: 'svg',
         source_uri: 'assets/b/source.svg', canonical_svg_uri: 'assets/b/canonical.svg',
         source_sha256: '2d2a9918b5b43cbcdcee065c901d25c897a07b067329504bb95fb8702268df1f',
         source_viewbox: [0, 0, 60, 60], mm_per_source_unit: 1.0,
         normalization_pose: { tx: -30, ty: -30, scale: 1, angle_deg: 0 },
         geometry_hash: '0a40d28facf1cf99d610c5d6a351133a7ef32fd5254ff8b1ab68d2f5f46bfea3',
         trace_settings: {}, curve_tolerance_source: 0.02,
         local_bounds: [-30, -30, 30, 30],
         canonical_svg: '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0 H60 V60 H0 Z"/></svg>' },
    c: { id: 'c', name: 'Fixture c', source_type: 'svg',
         source_uri: 'assets/c/source.svg', canonical_svg_uri: 'assets/c/canonical.svg',
         source_sha256: 'c842acdf89c8851ce06db14fc4cd21a948b9b42bc59329048d5ef3bf2e035638',
         source_viewbox: [0, 0, 40, 40], mm_per_source_unit: 1.0,
         normalization_pose: { tx: -20, ty: -20, scale: 1, angle_deg: 0 },
         geometry_hash: '04ab8d7f0fcee6c5c0600b3e3def69197ba621d10c7f2ed2160064e16acabda8',
         trace_settings: {}, curve_tolerance_source: 0.02,
         local_bounds: [-20, -20, 20, 20],
         canonical_svg: '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0 H40 V40 H0 Z"/></svg>' },
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
    C: { id: 'C', asset_id: 'c', name: 'Interior', parent_id: 'B', order: 0, stack_rank: 2,
         pose: { tx: 0, ty: 0, scale: 1, angle_deg: 0 },
         visible: true, locked: false, export_enabled: true, extrusion_mm: null,
         fit: { target: 'parent_shape', hole_id: null, padding_mm: 5, avoid_siblings: true,
                sibling_gap_mm: 2, auto_scale_while_dragging: false, allow_rotation: false,
                rotation_half_range_deg: 20 } },
  },
};

function makeViewport() {
  store.doc = structuredClone(fixture);
  store.revision = 0;
  store.selectedId = null;
  const vp = new Viewport2D(document.getElementById('editor-svg'));
  vp.renderCanvas();
  vp.renderLayers();
  return vp;
}

test('three-layer fixture renders one group per layer with world matrix', () => {
  const vp = makeViewport();
  const groups = [...vp.content.querySelectorAll('g.layer')];
  assert.equal(groups.length, 3);
  const ids = groups.map(g => g.dataset.layerId);
  assert.deepEqual(ids, ['A', 'B', 'C']); // parent before children, siblings by order
});

test('world matrix includes normalization: A spans [-60,100] with scale 1.6', () => {
  const vp = makeViewport();
  const g = vp._layerEls.get('A');
  const m = g.getAttribute('transform').match(/matrix\(([^)]+)\)/)[1].split(' ').map(Number);
  // A: L = T(100,100)·S(1.6); N = T(-50,-50).
  // W = L·N → translation (20,20), scale 1.6.
  // Local bounds [-50,50]² map to [-60,100]² in world.
  assert.ok(Math.abs(m[4] - 20) < 1e-9 && Math.abs(m[5] - 20) < 1e-9, `A translation (${m[4]},${m[5]})`);
  assert.ok(Math.abs(m[0] - 1.6) < 1e-9 && Math.abs(m[3] - 1.6) < 1e-9);
  // Verify: local corner (-50,-50) → world (-60,-60); local corner (50,50) → world (100,100)
  const p1 = affine.point(m, {x:-50, y:-50});
  const p2 = affine.point(m, {x:50, y:50});
  assert.ok(Math.abs(p1.x + 60) < 1e-9 && Math.abs(p1.y + 60) < 1e-9);
  assert.ok(Math.abs(p2.x - 100) < 1e-9 && Math.abs(p2.y - 100) < 1e-9);
});

test('nested chain B = A·B·N_b: B spans [4,100] inside A', () => {
  const vp = makeViewport();
  const g = vp._layerEls.get('B');
  const m = g.getAttribute('transform').match(/matrix\(([^)]+)\)/)[1].split(' ').map(Number);
  // B: L_A·L_B·N_b. L_B = identity. N_b = T(-30,-30).
  // W_B = L_A·N_b → translation (52,52), scale 1.6.
  // Local bounds [-30,30]² map to [4,100]² in world.
  assert.ok(Math.abs(m[4] - 52) < 1e-9 && Math.abs(m[5] - 52) < 1e-9, `B translation (${m[4]},${m[5]})`);
  assert.ok(Math.abs(m[0] - 1.6) < 1e-9);
  const p1 = affine.point(m, {x:-30, y:-30});
  const p2 = affine.point(m, {x:30, y:30});
  assert.ok(Math.abs(p1.x - 4) < 1e-9 && Math.abs(p1.y - 4) < 1e-9);
  assert.ok(Math.abs(p2.x - 100) < 1e-9 && Math.abs(p2.y - 100) < 1e-9);
});

test('hiding the parent hides the whole subtree in the viewport', () => {
  const vp = makeViewport();
  store.doc.layers.A.visible = false;
  vp.renderLayers();
  const ids = [...vp.content.querySelectorAll('g.layer')].map(g => g.dataset.layerId);
  assert.deepEqual(ids, []); // A, B and C all hidden
  store.doc.layers.A.visible = true;
  vp.renderLayers();
  assert.equal(vp.content.querySelectorAll('g.layer').length, 3);
});

test('overlays are marked non-exportable (data-export=false)', () => {
  const vp = makeViewport();
  assert.equal(vp.guides.getAttribute('data-export'), 'false');
  store.select('A');
  vp.renderSelection();
  assert.equal(vp.handles.getAttribute('data-export'), 'false');
  // No background rect is injected into any layer group
  for (const g of vp.content.querySelectorAll('g.layer')) {
    assert.equal(g.querySelector('rect'), null);
  }
});

test('pick: point inside A but outside B/C selects A; outside selects nothing', () => {
  const vp = makeViewport();
  // A spans [-60,100]; B spans [4,100]; C spans [20,84].
  // (-40,20) is inside A only (outside B and C).
  assert.equal(vp.pick({ x: -40, y: 20 }), 'A');
  assert.equal(vp.pick({ x: 120, y: 120 }), null);
});

test('pick: topmost layer wins (C over B over A at the shared centre)', () => {
  const vp = makeViewport();
  // All three layers are centred at (100,100); C is drawn last → topmost.
  assert.equal(vp.pick({ x: 100, y: 100 }), 'C');
});

test('pick: hidden layer is not selectable', () => {
  const vp = makeViewport();
  store.doc.layers.C.visible = false;
  vp.renderLayers();
  assert.equal(vp.pick({ x: 100, y: 100 }), 'B');
});

test('pointInLayer respects scale: A spans [-60,100] in world', () => {
  const vp = makeViewport();
  const assetA = store.assetById('a');
  // A spans [-60,100] in world. Local bounds [-50,50].
  assert.equal(vp.pointInLayer('A', assetA, { x: 20, y: 20 }), true);     // centre
  assert.equal(vp.pointInLayer('A', assetA, { x: 80, y: 20 }), true);     // inside
  assert.equal(vp.pointInLayer('A', assetA, { x: 100, y: 20 }), true);    // edge
  assert.equal(vp.pointInLayer('A', assetA, { x: 101, y: 20 }), false);   // just outside
  assert.equal(vp.pointInLayer('A', assetA, { x: -61, y: 20 }), false);   // just outside
});

test('selection outline uses local bounds, not the raw source viewbox', () => {
  const vp = makeViewport();
  store.select('A');
  vp.renderSelection();
  const poly = vp.handles.querySelector('polygon');
  assert.ok(poly, 'selection polygon exists');
  const pts = poly.getAttribute('points').split(' ').map(p => p.split(',').map(Number));
  const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
  // A: local bounds [-50,50]² under W_A → [-60,100]²
  assert.ok(Math.abs(Math.min(...xs) + 60) < 1e-6, `min x ${Math.min(...xs)}`);
  assert.ok(Math.abs(Math.max(...xs) - 100) < 1e-6, `max x ${Math.max(...xs)}`);
  assert.ok(Math.abs(Math.min(...ys) + 60) < 1e-6, `min y ${Math.min(...ys)}`);
  assert.ok(Math.abs(Math.max(...ys) - 100) < 1e-6, `max y ${Math.max(...ys)}`);
});
