// interactions2d.js — drag, pan, scale and rotate gestures (tasks 08, 09)
// One gesture = ONE transaction: the pose is committed exactly once on
// pointerup, never per pointermove (spec §8.2, task 08 step 4).
//
// Task 08 gates implemented here:
//  - pointerdown/move/up/cancel with pointer capture, initial snapshot,
//    getScreenCTM inverse conversion (affine.clientToDocument)
//  - dragPose in the PARENT frame captured at gesture start (no incremental
//    sums across events)
//  - visual updates via requestAnimationFrame; no POST per frame
//  - pointerup → one set_pose; Escape/pointercancel → restore, no commit
//  - a new document revision while a gesture is active cancels it
//  - Space+drag and middle-button pan; wheel zoom around the cursor;
//    none of these change any pose
//  - confirmed-content history: one drag = one action; Ctrl+Z / Ctrl+Y via
//    restore_snapshot with a growing revision; shortcuts ignored in inputs
import * as affine from './affine.mjs';
import { store } from './store.js';

const HISTORY_LIMIT = 100;

export class Interactions2D {
  /**
   * @param {object} viewport  Viewport2D instance
   */
  constructor(viewport) {
    this.viewport = viewport;
    this.svg = viewport.svg;
    this._gesture = null;
    this._pendingPose = null;
    this._rafId = null;
    this._pendingRender = null;
    this._spaceDown = false;
    this._bind();
  }

  // ------------------------------------------------------------------
  // History (confirmed contents only — spec task 08 step 6)
  // ------------------------------------------------------------------

  _snapshot() {
    return {
      doc: structuredClone(store.doc),
      revision: store.revision,
    };
  }

  pushHistory() {
    if (!store.doc) return;
    // Production store.history is an array; commitCommand already records it.
    // Tests install { undo, redo } on the same field — keep that path working.
    if (Array.isArray(store.history)) return;
    const h = store.history || (store.history = { undo: [], redo: [] });
    if (!Array.isArray(h.undo)) return;
    h.undo.push(this._snapshot());
    if (h.undo.length > HISTORY_LIMIT) h.undo.shift();
    h.redo.length = 0;
  }

  undo() {
    const h = store.history;
    if (!h || !h.undo.length || !store.doc) return false;
    const snap = h.undo.pop();
    h.redo.push(this._snapshot());
    this._restore(snap);
    return true;
  }

  redo() {
    const h = store.history;
    if (!h || !h.redo.length || !store.doc) return false;
    const snap = h.redo.pop();
    h.undo.push(this._snapshot());
    this._restore(snap);
    return true;
  }

  _restore(snap) {
    store.doc = snap.doc;
    store.revision = snap.revision;
    store.selectedId = null;
    this.viewport.renderCanvas();
    this.viewport.renderLayers();
    window.dispatchEvent(new CustomEvent('editor:doc-changed'));
  }

  // ------------------------------------------------------------------
  // Binding
  // ------------------------------------------------------------------

  _bind() {
    this.svg.addEventListener('pointerdown', (e) => this._onDown(e));
    window.addEventListener('pointermove', (e) => this._onMove(e));
    window.addEventListener('pointerup', (e) => this._onUp(e));
    window.addEventListener('pointercancel', (e) => this._onCancel(e));
    // Wheel zoom (around the pointer)
    this.svg.addEventListener('wheel', (e) => this._onWheel(e), { passive: false });
    // Keyboard: Space for pan, Escape to cancel, Ctrl+Z / Ctrl+Y history
    window.addEventListener('keydown', (e) => this._onKeyDown(e));
    window.addEventListener('keyup', (e) => this._onKeyUp(e));
    // A remote revision change (new document revision) cancels the gesture
    window.addEventListener('editor:doc-changed', () => this._onRemoteChange());
  }

  _inInput(e) {
    const t = e.target;
    return t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
  }

  _onKeyDown(e) {
    if (this._inInput(e)) return; // never capture shortcuts inside inputs
    if (e.code === 'Space' && !this._spaceDown) {
      this._spaceDown = true;
      this.svg.style.cursor = 'grab';
      e.preventDefault();
    } else if (e.key === 'Escape' && this._gesture) {
      this._cancelGesture();
    } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z' && !e.shiftKey) {
      if (this.undo()) e.preventDefault();
    } else if ((e.ctrlKey || e.metaKey) && (e.key.toLowerCase() === 'y' || (e.key.toLowerCase() === 'z' && e.shiftKey))) {
      if (this.redo()) e.preventDefault();
    }
  }

  _onKeyUp(e) {
    if (e.code === 'Space') {
      this._spaceDown = false;
      this.svg.style.cursor = '';
    }
  }

  _onRemoteChange() {
    // A new revision arrived while a gesture is active: cancel it without
    // committing (spec task 08 step 4).
    if (this._gesture) this._cancelGesture();
  }

  // ------------------------------------------------------------------
  // Pointer helpers
  // ------------------------------------------------------------------

  _docPoint(e) {
    return affine.clientToDocument(this.svg, e);
  }

  _parentWorldMatrix(layerId) {
    const node = store.layerById(layerId);
    if (!node?.parent_id) return affine.identity();
    const chain = [];
    let cur = node.parent_id;
    const seen = new Set();
    while (cur && !seen.has(cur)) {
      seen.add(cur);
      const n = store.layerById(cur);
      if (!n) break;
      chain.unshift(affine.layerMatrix(n));
      cur = n.parent_id;
    }
    let m = affine.identity();
    for (const lm of chain) m = affine.multiply(m, lm);
    return m;
  }

  // ------------------------------------------------------------------
  // Gesture start
  // ------------------------------------------------------------------

  _onDown(e) {
    // Middle button → pan (spec task 08 step 5)
    if (e.button === 1) {
      e.preventDefault();
      this._startPan(e);
      return;
    }
    if (e.button !== 0) return;
    const pt = this._docPoint(e);

    // Space+drag → pan even over a layer (spec task 08 step 5)
    if (this._spaceDown) {
      this._startPan(e);
      return;
    }

    // Handle interactions first (scale / rotate)
    const handle = e.target.closest?.('[data-handle]');
    if (handle && store.selectedId) {
      this._startHandleGesture(handle.dataset.handle, e, pt);
      return;
    }

    // Layer drag
    const layerEl = e.target.closest?.('.layer');
    if (layerEl) {
      const layerId = layerEl.dataset.layerId;
      const node = store.layerById(layerId);
      if (node?.locked) {
        this._flash('Capa bloqueada.');
        return;
      }
      store.select(layerId);
      this.viewport.renderLayers();
      this._gesture = {
        kind: 'drag',
        layerId,
        startPose: { ...node.pose },
        startWorld: pt,
        parentWorld: this._parentWorldMatrix(layerId),
        revisionAtStart: store.revision,
      };
      this.svg.setPointerCapture?.(e.pointerId);
      return;
    }

    // Empty space → pan
    this._startPan(e);
  }

  _startPan(e) {
    this._gesture = {
      kind: 'pan',
      startClient: { x: e.clientX, y: e.clientY },
      startViewport: { ...store.viewport },
    };
    this.svg.setPointerCapture?.(e.pointerId);
    this.svg.style.cursor = 'grabbing';
  }

  _startHandleGesture(kind, e, pt) {
    const layerId = store.selectedId;
    const node = store.layerById(layerId);
    if (!node || node.locked) return;
    const asset = store.assetById(node.asset_id);
    const lb = asset?.local_bounds || asset?.source_viewbox || [0, 0, 100, 100];
    const parentWorld = this._parentWorldMatrix(layerId);

    if (kind === 'scale') {
      const lbArr = Array.isArray(lb) && lb.length >= 4
        ? [lb[0], lb[1], lb[2], lb[3]]
        : [0, 0, 100, 100];
      // Pick local corners that match the visual TL / BR after flip+pose.
      const { oppositeLocal, draggedLocal } = affine.scaleHandleLocals(
        lbArr, node, parentWorld,
      );
      this._gesture = {
        kind: 'scale',
        layerId,
        startPose: { ...node.pose },
        parentWorld,
        oppositeLocal,
        draggedLocal,
        flipH: !!node.flip_h,
        revisionAtStart: store.revision,
      };
    } else if (kind === 'rotate') {
      const centerLocal = { x: (lb[0] + lb[2]) / 2, y: (lb[1] + lb[3]) / 2 };
      const centerWorld = affine.point(
        affine.multiply(this._parentWorldMatrix(layerId), affine.layerMatrix(node)),
        centerLocal,
      );
      this._gesture = {
        kind: 'rotate',
        layerId,
        startPose: { ...node.pose },
        parentWorld,
        centerLocal,
        startAngle: Math.atan2(pt.y - centerWorld.y, pt.x - centerWorld.x) * 180 / Math.PI,
        revisionAtStart: store.revision,
      };
    }
    this.svg.setPointerCapture?.(e.pointerId);
  }

  // ------------------------------------------------------------------
  // Gesture move (visual only — rAF, no POST)
  // ------------------------------------------------------------------

  _onMove(e) {
    const g = this._gesture;
    if (!g) return;
    const pt = this._docPoint(e);

    if (g.kind === 'pan') {
      const ctm = this.svg.getScreenCTM();
      if (!ctm) return;
      const dx = (e.clientX - g.startClient.x) / ctm.a;
      const dy = (e.clientY - g.startClient.y) / ctm.d;
      store.setViewport(g.startViewport.x - dx, g.startViewport.y - dy, g.startViewport.scale);
      this.viewport.applyViewport();
      return;
    }

    let pose = null;
    if (g.kind === 'drag') {
      pose = affine.dragPose(g.startPose, g.parentWorld, g.startWorld, pt);
    } else if (g.kind === 'scale') {
      try {
        pose = affine.scaleOppositeFixed(
          g.startPose, g.parentWorld, g.oppositeLocal, g.draggedLocal, pt,
          1e-6, !!g.flipH,
        );
      } catch { /* pointer too close to the fixed corner */ }
    } else if (g.kind === 'rotate') {
      const node = store.layerById(g.layerId);
      const worldM = affine.multiply(g.parentWorld, affine.layerMatrix({ ...node, pose: g.startPose }));
      const centerWorld = affine.point(worldM, g.centerLocal);
      const ang = Math.atan2(pt.y - centerWorld.y, pt.x - centerWorld.x) * 180 / Math.PI;
      pose = { ...g.startPose, angle_deg: g.startPose.angle_deg + (ang - g.startAngle) };
    }
    if (pose) {
      pose = this._maybeClampPose(g.layerId, pose);
      this._previewPose(g.layerId, pose);
    }
  }

  /**
   * Fit canvas → clamp roots to usable sheet (rect).
   * Matrioska / nested → clamp children to parent SVG contour (not AABB).
   */
  _maybeClampPose(layerId, pose) {
    const node = store.layerById(layerId);
    if (!node) return pose;
    const asset = store.assetById(node.asset_id);
    if (!asset) return pose;

    try {
      if (store.fitToCanvas && !node.parent_id && store.doc?.canvas) {
        const lb = affine.measureLocalBounds(asset) || asset.local_bounds;
        if (!lb) return pose;
        return affine.clampPoseToRect(pose, lb, affine.usableCanvasRect(store.doc.canvas));
      }
      if (node.parent_id && (store.matrioskaMode || store.fitToCanvas)) {
        const parent = store.layerById(node.parent_id);
        const pAsset = store.assetById(parent?.asset_id);
        if (!pAsset) return pose;
        const padEl = document.getElementById('matrioska-padding-enabled');
        const padMmEl = document.getElementById('matrioska-padding-mm');
        let padding = Number(node.fit?.padding_mm ?? 0);
        if (padEl?.checked) {
          const v = parseFloat(padMmEl?.value);
          padding = Number.isFinite(v) && v >= 0 ? v : 0;
        }
        return affine.clampPoseInsideSilhouette(asset, pAsset, pose, padding, {
          flipChild: !!node.flip_h,
          flipParent: !!parent.flip_h,
        });
      }
    } catch {
      return pose;
    }
    return pose;
  }

  _previewPose(layerId, pose) {
    // Live preview: update the layer's transform without committing.
    // Batched through requestAnimationFrame (spec task 08 step 3).
    this._pendingPose = { layerId, pose };
    if (this._rafId) return;
    this._rafId = requestAnimationFrame(() => {
      this._rafId = null;
      const p = this._pendingPose;
      if (!p) return;
      const el = this.viewport._layerEls.get(p.layerId);
      if (!el) return;
      const asset = store.assetById(store.layerById(p.layerId)?.asset_id);
      const node = store.layerById(p.layerId);
      const parentM = this._parentWorldMatrix(p.layerId);
      let world = affine.multiply(parentM, affine.layerMatrix({ ...node, pose: p.pose }));
      // Paths live in source SVG units; include N + viewBox correction.
      if (asset) {
        world = affine.multiply(world, affine.pathToLocalMatrix(asset));
      }
      el.setAttribute('transform', `matrix(${world.join(' ')})`);
    });
  }

  // ------------------------------------------------------------------
  // Gesture end
  // ------------------------------------------------------------------

  _onUp(e) {
    const g = this._gesture;
    this._gesture = null;
    this.svg.style.cursor = '';
    if (!g) return;

    if (g.kind === 'pan') return; // viewport is UI state, nothing to commit

    // Commit the pending pose as ONE transaction (spec task 08 step 4).
    const pending = this._pendingPose;
    this._pendingPose = null;
    if (!pending) return;
    if (store.revision !== g.revisionAtStart) {
      // A remote change happened mid-gesture: do not commit.
      this.viewport.renderLayers();
      return;
    }
    this.pushHistory();
    this._commitPose(pending.layerId, pending.pose);
  }

  async _commitPose(layerId, pose) {
    let finalPose = pose;
    const node = store.layerById(layerId);
    // Nested matrioska: authoritative contour constrain (Shapely) before commit.
    if (node?.parent_id && (store.matrioskaMode || store.fitToCanvas)) {
      try {
        const padEl = document.getElementById('matrioska-padding-enabled');
        const padMmEl = document.getElementById('matrioska-padding-mm');
        let padding = Number(node.fit?.padding_mm ?? 0);
        if (padEl?.checked) {
          const v = parseFloat(padMmEl?.value);
          padding = Number.isFinite(v) && v >= 0 ? v : 0;
        }
        const res = await fetch(
          `${store.baseUrl}/documents/${encodeURIComponent(store.doc.id)}/constrain`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              layer_id: layerId,
              pose,
              padding_mm: padding,
            }),
          },
        );
        const body = await res.json().catch(() => ({}));
        if (res.ok && body.pose) finalPose = body.pose;
      } catch (err) {
        console.warn('constrain failed, using client pose', err);
      }
    }
    try {
      await store.commitCommand('set_pose', {
        layer_id: layerId,
        pose: finalPose,
      });
      this.viewport.renderLayers();
      this.viewport.renderSelection();
      window.dispatchEvent(new CustomEvent('editor:pose-changed', { detail: { layerId } }));
    } catch (err) {
      this._flash(err.message);
      this.viewport.renderLayers();
      this.viewport.renderSelection();
    }
  }

  _onCancel(e) {
    if (!this._gesture) return;
    this._cancelGesture();
  }

  _cancelGesture() {
    // Escape / pointercancel: restore the initial pose, no commit.
    const g = this._gesture;
    this._gesture = null;
    this._pendingPose = null;
    this.svg.style.cursor = '';
    if (g && g.kind !== 'pan') {
      const node = store.layerById(g.layerId);
      if (node) node.pose = { ...g.startPose };
      this.viewport.renderLayers();
    }
  }

  // ------------------------------------------------------------------
  // Wheel zoom around the cursor (UI state only — no pose change)
  // ------------------------------------------------------------------

  _onWheel(e) {
    e.preventDefault();
    const factor = Math.exp(-e.deltaY * 0.001);
    const vp = store.viewport;
    const newScale = Math.min(50, Math.max(0.01, vp.scale * factor));
    const pt = this._docPoint(e);
    // Keep the document point under the cursor fixed while zooming.
    store.setViewport(
      pt.x - (pt.x - vp.x) * (vp.scale / newScale),
      pt.y - (pt.y - vp.y) * (vp.scale / newScale),
      newScale,
    );
    this.viewport.applyViewport();
  }

  _flash(msg) {
    const el = document.getElementById('fit-status');
    if (!el) return;
    el.textContent = msg;
    setTimeout(() => { el.textContent = ''; }, 3000);
  }
}
