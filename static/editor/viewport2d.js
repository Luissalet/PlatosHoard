// viewport2d.js — 2D canvas rendering + selection (task 07)
// Renders one flat <g> per layer with its world matrix and local paths.
// Canvas, margins and overlays live in non-exportable groups.
// Selection is by fill hit-test (DOM), not bbox.
import * as affine from './affine.mjs';
import { store } from './store.js';

const SVG_NS = 'http://www.w3.org/2000/svg';

export class Viewport2D {
  /**
   * @param {SVGSVGElement} svgRoot  the #editor-svg element
   */
  constructor(svgRoot) {
    this.svg = svgRoot;
    this.defs = svgRoot.querySelector('#editor-defs') || this._ensureDefs(svgRoot);
    this.guides = svgRoot.querySelector('#canvas-guides');
    this.content = svgRoot.querySelector('#layer-content');
    this.overlays = svgRoot.querySelector('#constraint-overlays');
    this.handles = svgRoot.querySelector('#selection-handles');
    this._layerEls = new Map(); // layer_id -> <g>
  }

  _ensureDefs(svgRoot) {
    let defs = svgRoot.querySelector('defs');
    if (!defs) {
      defs = document.createElementNS(SVG_NS, 'defs');
      svgRoot.insertBefore(defs, svgRoot.firstChild);
    }
    defs.id = 'editor-defs';
    return defs;
  }

  // ---- canvas + margins (non-exportable) ----------------------------------

  renderCanvas() {
    const c = store.doc?.canvas;
    if (!c) return;
    this.guides.innerHTML = '';
    this.guides.setAttribute('data-export', 'false');
    const w = c.width_mm, h = c.height_mm;
    const p = c.padding_mm || { left: 0, right: 0, top: 0, bottom: 0 };

    // Physical sheet only — sized exactly to width×height mm. Outside is the
    // editor chrome (gray), not a larger white plane.
    this.guides.appendChild(this._rect(0, 0, w, h, 'canvas-outer'));
    // Padding inset guide (non-exportable).
    const iw = w - p.left - p.right;
    const ih = h - p.top - p.bottom;
    if (iw > 0 && ih > 0 && (p.left || p.right || p.top || p.bottom)) {
      this.guides.appendChild(this._rect(p.left, p.top, iw, ih, 'canvas-inner'));
    }
  }

  // ---- layers -------------------------------------------------------------

  renderLayers() {
    this.content.innerHTML = '';
    this.defs.innerHTML = '';
    this._layerEls.clear();
    if (!store.doc) return;

    const mode = store.viewMode || 'normal';
    if (mode === 'inverse') {
      this._renderInverseMode();
      this.renderSelection();
      return;
    }

    // Matrioska and normal: solid stack, parents under children (children on top).
    // Do NOT punch holes here — holes are inversa/export only; masking hid children.
    const ordered = this._drawOrder();
    for (const layerId of ordered) {
      const node = store.layerById(layerId);
      if (!node) continue;
      if (!this._effectiveVisible(node)) continue;
      const g = this._renderLayer(layerId, node);
      if (g) {
        this.content.appendChild(g);
        this._layerEls.set(layerId, g);
      }
    }
    this.renderSelection();
  }

  /** Plancha del canvas con un hueco por capa (máscara SVG). */
  _renderInverseMode() {
    const c = store.doc.canvas;
    const W = c.width_mm, H = c.height_mm;
    // Prefer the selection; otherwise all roots (one plate each).
    let targets = store.selectedId ? [store.selectedId] : store.roots().map((r) => r.id);
    if (!targets.length) targets = this._drawOrder();

    for (const layerId of targets) {
      const node = store.layerById(layerId);
      if (!node || !this._effectiveVisible(node)) continue;
      const asset = store.assetById(node.asset_id);
      if (!asset?.canonical_svg) continue;

      const maskId = `inv-mask-${layerId}`;
      const mask = document.createElementNS(SVG_NS, 'mask');
      mask.setAttribute('id', maskId);
      mask.setAttribute('maskUnits', 'userSpaceOnUse');
      const keep = this._rect(0, 0, W, H, '');
      keep.setAttribute('fill', '#ffffff');
      mask.appendChild(keep);
      // Cut this layer AND its descendants (matrioska stack hole)
      const subtree = store.subtreeIds(layerId);
      for (const sid of subtree) {
        const sn = store.layerById(sid);
        if (!sn || !this._effectiveVisible(sn)) continue;
        const sa = store.assetById(sn.asset_id);
        const hole = this._pathsGroup(sid, sa, '#000000');
        if (hole) mask.appendChild(hole);
      }
      this.defs.appendChild(mask);

      const plate = this._rect(0, 0, W, H, 'layer inverse-plate');
      plate.dataset.layerId = layerId;
      plate.setAttribute('fill', this._layerColor(layerId));
      plate.setAttribute('mask', `url(#${maskId})`);
      plate.style.opacity = targets.length > 1 ? '0.55' : '0.92';
      this.content.appendChild(plate);
      this._layerEls.set(layerId, plate);
    }
  }

  /** Group of layer paths with world transform (incl. normalization). */
  _pathsGroup(layerId, asset, fill) {
    if (!asset?.canonical_svg) return null;
    const g = document.createElementNS(SVG_NS, 'g');
    const m = this._worldMatrix(layerId, asset);
    g.setAttribute('transform', `matrix(${m.join(' ')})`);
    for (const d of this._extractPaths(asset.canonical_svg)) {
      const p = document.createElementNS(SVG_NS, 'path');
      p.setAttribute('d', d);
      p.setAttribute('fill', fill);
      p.setAttribute('fill-rule', 'evenodd');
      p.setAttribute('stroke', 'none');
      g.appendChild(p);
    }
    return g.childNodes.length ? g : null;
  }

  _drawOrder() {
    // Depth-first: parent before children, siblings by order
    const out = [];
    const visit = (parentId) => {
      for (const child of store.childrenOf(parentId)) {
        out.push(child.id);
        visit(child.id);
      }
    };
    visit(null);
    return out;
  }

  _effectiveVisible(node) {
    let cur = node;
    const seen = new Set();
    while (cur && !seen.has(cur.id)) {
      seen.add(cur.id);
      if (!cur.visible) return false;
      cur = cur.parent_id ? store.layerById(cur.parent_id) : null;
    }
    return true;
  }

  _renderLayer(layerId, node) {
    const asset = store.assetById(node.asset_id);
    if (!asset) return null;
    const svg = asset.canonical_svg;
    if (!svg) return null;

    const g = document.createElementNS(SVG_NS, 'g');
    g.setAttribute('class', 'layer');
    g.dataset.layerId = layerId;

    // The group carries the FULL world matrix (spec §5.2/§5.3):
    //   W = L_root · ... · L_layer · N
    // where N is the asset's normalization_pose (source → local mm, centred
    // at the origin).  The canonical SVG paths are therefore re-expressed in
    // LOCAL mm coordinates, and local bounds / selection / hit-tests all use
    // the same frame.
    const m = this._worldMatrix(layerId, asset);
    g.setAttribute('transform', `matrix(${m.join(' ')})`);

    // Local paths: parse the canonical SVG's <path> elements
    const paths = this._extractPaths(svg);
    for (const d of paths) {
      const p = document.createElementNS(SVG_NS, 'path');
      p.setAttribute('d', d);
      p.setAttribute('fill', 'currentColor');
      p.setAttribute('fill-rule', 'evenodd');
      p.setAttribute('stroke', 'none');
      g.appendChild(p);
    }

    // Hit-test: the group itself catches pointer events on filled areas.
    // Holes (evenodd) are transparent to hits → click selects what's behind.
    g.style.color = this._layerColor(layerId);
    return g;
  }

  /** Layer poses only (parent chain). Use for local_bounds already in mm. */
  _layerMatrix(layerId) {
    const chain = [];
    let cur = layerId;
    const seen = new Set();
    while (cur && !seen.has(cur)) {
      seen.add(cur);
      const node = store.layerById(cur);
      if (!node) break;
      chain.unshift(affine.matrix(node.pose));
      cur = node.parent_id;
    }
    let m = affine.identity();
    for (const lm of chain) m = affine.multiply(m, lm);
    return m;
  }

  _worldMatrix(layerId, asset) {
    // Walk the parent chain: W = L_root · ... · L_layer · N_path
    // N_path maps raw canonical SVG path coords → local mm (incl. viewBox).
    let m = this._layerMatrix(layerId);
    if (asset) {
      m = affine.multiply(m, affine.pathToLocalMatrix(asset));
    }
    return m;
  }

  _extractPaths(svgText) {
    // Parse SVG text and return the `d` attribute of every <path>.
    // Uses DOMParser (browser) — no regex on geometry.
    const doc = new DOMParser().parseFromString(svgText, 'image/svg+xml');
    const out = [];
    for (const p of doc.querySelectorAll('path')) {
      const d = p.getAttribute('d');
      if (d) out.push(d);
    }
    return out;
  }

  _layerColor(layerId) {
    // Stable per-layer colour. Selection uses the dashed overlay only —
    // filling selected layers blue made nested pieces look like a rim.
    const node = store.layerById(layerId);
    if (node?.locked) return '#9ca3af';
    const idx = this._layerIndex(layerId);
    const hue = (idx * 137.508) % 360;
    return `hsl(${hue.toFixed(0)}, 65%, 45%)`;
  }

  _layerIndex(layerId) {
    const ids = Object.keys(store.doc?.layers || {});
    const i = ids.indexOf(layerId);
    return i >= 0 ? i : 0;
  }

  // ---- selection overlay --------------------------------------------------

  renderSelection() {
    this.handles.innerHTML = '';
    this.handles.setAttribute('data-export', 'false');
    if (!store.selectedId) return;
    const node = store.layerById(store.selectedId);
    if (!node) return;

    const asset = store.assetById(node.asset_id);
    if (!asset) return;

    // local_bounds are already in local mm (after N). Use pose chain ONLY —
    // multiplying by pathToLocalMatrix again put the blue box in empty space.
    const m = this._layerMatrix(store.selectedId);
    const lb = asset.local_bounds || [0, 0, 100, 100];
    let corners = [
      affine.point(m, { x: lb[0], y: lb[1] }),
      affine.point(m, { x: lb[2], y: lb[1] }),
      affine.point(m, { x: lb[2], y: lb[3] }),
      affine.point(m, { x: lb[0], y: lb[3] }),
    ];

    // Prefer the live rendered path bbox when available (exact visual match).
    const el = this._layerEls.get(store.selectedId);
    if (el) {
      try {
        const bb = el.getBBox();
        if (bb.width > 0 && bb.height > 0) {
          const ctm = el.getCTM();
          const root = this.svg.getScreenCTM();
          if (ctm && root) {
            const inv = root.inverse();
            const toDoc = (x, y) => {
              const s = new DOMPoint(x, y).matrixTransform(ctm);
              const d = s.matrixTransform(inv);
              return { x: d.x, y: d.y };
            };
            corners = [
              toDoc(bb.x, bb.y),
              toDoc(bb.x + bb.width, bb.y),
              toDoc(bb.x + bb.width, bb.y + bb.height),
              toDoc(bb.x, bb.y + bb.height),
            ];
          }
        }
      } catch { /* keep local_bounds corners */ }
    }

    const pts = corners.map(c => `${c.x},${c.y}`).join(' ');
    const poly = document.createElementNS(SVG_NS, 'polygon');
    poly.setAttribute('points', pts);
    poly.setAttribute('fill', 'none');
    poly.setAttribute('stroke', '#2563eb');
    poly.setAttribute('stroke-width', '1.5');
    poly.setAttribute('stroke-dasharray', '4 2');
    poly.setAttribute('pointer-events', 'none');
    this.handles.appendChild(poly);

    const br = corners[2];
    const handle = document.createElementNS(SVG_NS, 'rect');
    handle.setAttribute('x', br.x - 4);
    handle.setAttribute('y', br.y - 4);
    handle.setAttribute('width', 8);
    handle.setAttribute('height', 8);
    handle.setAttribute('fill', '#2563eb');
    handle.setAttribute('stroke', '#fff');
    handle.setAttribute('stroke-width', '1');
    handle.setAttribute('vector-effect', 'non-scaling-stroke');
    handle.dataset.handle = 'scale';
    handle.style.cursor = 'nwse-resize';
    this.handles.appendChild(handle);
  }

  // ---- hit-test selection -------------------------------------------------

  /**
   * Select the topmost layer whose filled area contains the document point.
   * Holes are transparent (evenodd), so a click in a hole selects the layer
   * behind it. Returns the selected layer id or null.
   */
  pick(docPoint) {
    const ordered = this._drawOrder().reverse();
    for (const layerId of ordered) {
      const node = store.layerById(layerId);
      if (!node || !this._effectiveVisible(node)) continue;
      const el = this._layerEls.get(layerId);
      if (!el) continue;
      const asset = store.assetById(node.asset_id);
      for (const p of el.querySelectorAll('path')) {
        try {
          if (p.isPointInFill && p.isPointInFill(new DOMPoint(docPoint.x, docPoint.y))) {
            return layerId;
          }
        } catch { /* isPointInFill unsupported — skip */ }
      }
      // Fallback (and testable path): filled-bounds hit-test in local mm.
      // Holes are not resolved here; the DOM fill test above is authoritative
      // in a real browser.
      if (asset && this.pointInLayer(layerId, asset, docPoint)) {
        return layerId;
      }
    }
    return null;
  }

  /**
   * True if the document point lies inside the layer's filled local bounds
   * (asset.local_bounds in mm, centred at the origin after normalization).
   */
  pointInLayer(layerId, asset, docPoint) {
    const lb = asset.local_bounds;
    if (!lb) return false;
    // local_bounds are already mm — invert pose chain only (not N again).
    const m = this._layerMatrix(layerId);
    const local = affine.point(affine.inverse(m), docPoint);
    return local.x >= lb[0] && local.x <= lb[2] &&
           local.y >= lb[1] && local.y <= lb[3];
  }

  // ---- helpers ------------------------------------------------------------

  _rect(x, y, w, h, cls) {
    const r = document.createElementNS(SVG_NS, 'rect');
    r.setAttribute('x', x); r.setAttribute('y', y);
    r.setAttribute('width', w); r.setAttribute('height', h);
    r.setAttribute('class', cls);
    r.setAttribute('pointer-events', 'none');
    return r;
  }

  /** Update the SVG viewBox to the current viewport (pan/zoom). */
  applyViewport() {
    const c = store.doc?.canvas;
    if (!c) return;
    const { visW, visH, x, y } = this._viewBoxRect();
    this.svg.setAttribute('viewBox', `${x} ${y} ${visW} ${visH}`);
  }

  /** Fit the whole canvas into the viewport with 10% margin. */
  fitToCanvas() {
    const c = store.doc?.canvas;
    if (!c) return;
    const vw = Math.max(1, this.svg.clientWidth || 800);
    const vh = Math.max(1, this.svg.clientHeight || 600);
    const margin = 1.1;
    const aspect = vw / vh;
    const neededW = c.width_mm * margin;
    const neededH = c.height_mm * margin;
    let visW, visH;
    if (neededW / neededH < aspect) {
      visH = neededH;
      visW = visH * aspect;
    } else {
      visW = neededW;
      visH = visW / aspect;
    }
    store.setViewport(
      (c.width_mm - visW) / 2,
      (c.height_mm - visH) / 2,
      c.height_mm / visH,
    );
    this.applyViewport();
  }

  _viewBoxRect() {
    const c = store.doc.canvas;
    const vw = Math.max(1, this.svg.clientWidth || 800);
    const vh = Math.max(1, this.svg.clientHeight || 600);
    const vp = store.viewport || { x: 0, y: 0, scale: 1 };
    const scale = (Number.isFinite(vp.scale) && vp.scale > 0) ? vp.scale : 1;
    const visH = c.height_mm / scale;
    const visW = visH * (vw / vh);
    return { visW, visH, x: vp.x, y: vp.y };
  }
}
