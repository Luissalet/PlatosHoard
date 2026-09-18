// store.js — canonical document store + UI state (tasks 07, 08)
import * as affine from './affine.mjs';
// The document is the single source of truth. UI state (selection, zoom,
// pan) is kept SEPARATE and never written back into the document.

export class EditorStore {
  constructor(baseUrl = '/api/v2') {
    this.baseUrl = baseUrl;
    this.doc = null;            // canonical document (server state)
    this.revision = 0;
    this.selectedId = null;     // UI state — not part of the document
    this.viewport = { x: 0, y: 0, scale: 1 }; // UI state
    this.viewMode = 'normal';   // 'normal' | 'inverse' | 'shell'
    this._recipePreview = null; // {layerId: {svg, summary}} from /preview
    this.fitToCanvas = false;   // constrain + max-fit roots to usable canvas
    this.matrioskaMode = false; // constrain + max-fit children inside parent silhouette
    this._commandSeq = 0;
    this._pendingHistorySnapshot = null; // pre-action state handed over by a gesture
    // Undo/redo history (spec §13.4): confirmed contents only, max 100 actions.
    this.history = [];           // snapshots: {assets, layers}
    this.historyIndex = -1;      // index of the current state in history
  }

  // ---- document lifecycle -------------------------------------------------

  async createDocument(name = 'Nuevo documento', canvas = null) {
    const body = { name };
    if (canvas) body.canvas = canvas;
    const res = await fetch(`${this.baseUrl}/documents`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw await this._error(res);
    this._applyDocumentPayload(await res.json());
    return this.doc;
  }

  async loadDocument(docId) {
    const res = await fetch(`${this.baseUrl}/documents/${encodeURIComponent(docId)}`);
    if (!res.ok) throw await this._error(res);
    this._applyDocumentPayload(await res.json());
    return this.doc;
  }

  async commitCommand(type, payload, commandId = null) {
    this._commandSeq += 1;
    const body = { ...(payload || {}) };
    // Only apply_fit_result reads base_revision from the payload body.
    // Do NOT inject it into other commands — set_layer_properties treats
    // unknown keys as unauthorized fields (broke the eye / visibility toggle).
    if (type === 'apply_fit_result' && body.base_revision === undefined) {
      body.base_revision = this.revision;
    }
    const envelope = {
      command_id: commandId || `cmd_${Date.now()}_${this._commandSeq}`,
      base_revision: this.revision,
      type,
      payload: body,
    };
    const res = await fetch(`${this.baseUrl}/documents/${encodeURIComponent(this.doc.id)}/commands`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(envelope),
    });
    if (!res.ok) throw await this._error(res);
    const result = await res.json();
    if (type !== 'restore_snapshot') {
      // Record the pre-action state so undo can restore it (one gesture = one action).
      this._pushHistory();
    }
    this._applyDocumentPayload(result);
    this._syncHistoryPointer();
    return result;
  }

  /**
   * Normalize server payloads that may be either a bare document or
   * {document, revision} (and historically a double-wrapped import).
   */
  _applyDocumentPayload(payload) {
    if (!payload || typeof payload !== 'object') return;
    let doc = payload;
    if (payload.document && typeof payload.document === 'object') {
      doc = payload.document;
      // Unwrap accidental double-nesting from older import responses.
      if (doc.document && doc.document.layers && !doc.layers) {
        doc = doc.document;
      }
    }
    if (doc.layers !== undefined || doc.assets !== undefined || doc.canvas) {
      this.doc = doc;
      this.revision = payload.revision ?? doc.revision ?? this.revision;
    }
  }

  // ---- undo / redo (spec §13.4) -------------------------------------------

  _snapshot() {
    return {
      assets: JSON.parse(JSON.stringify(this.doc?.assets ?? {})),
      layers: JSON.parse(JSON.stringify(this.doc?.layers ?? {})),
    };
  }

  _pushHistory() {
    if (!this.doc) return;
    // A new action after undo empties redo.
    if (this.historyIndex < this.history.length - 1) {
      this.history.length = this.historyIndex + 1;
    }
    // A gesture may have written an optimistic pose into the doc before the
    // commit; it hands us the true pre-action snapshot through this field.
    const snap = this._pendingHistorySnapshot || this._snapshot();
    this._pendingHistorySnapshot = null;
    this.history.push(snap);
    if (this.history.length > 100) this.history.shift();
    this.historyIndex = this.history.length - 1;
  }

  _syncHistoryPointer() {
    // After a restore_snapshot the current state equals the snapshot we
    // just restored; move the pointer there so redo/undo stay consistent.
    if (this.historyIndex >= 0 && this.history[this.historyIndex]) {
      const cur = this._snapshot();
      const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
      if (same(this.history[this.historyIndex], cur)) return;
    }
    // Fallback: treat the current state as the newest entry.
    if (this.history.length === 0) {
      this.history.push(this._snapshot());
      this.historyIndex = 0;
    }
  }

  canUndo() {
    return this.historyIndex >= 0;
  }

  canRedo() {
    return this.historyIndex < this.history.length - 1;
  }

  async undo() {
    if (!this.canUndo()) return null;
    const snapshot = this.history[this.historyIndex];
    const result = await this.commitCommand('restore_snapshot', { snapshot });
    this.historyIndex -= 1;
    return result;
  }

  async redo() {
    if (!this.canRedo()) return null;
    const snapshot = this.history[this.historyIndex + 1];
    const result = await this.commitCommand('restore_snapshot', { snapshot });
    this.historyIndex += 1;
    return result;
  }

  // ---- selection (UI state, not persisted) --------------------------------

  select(layerId) {
    this.selectedId = layerId;
  }

  clearSelection() {
    this.selectedId = null;
  }

  // ---- viewport (UI state, not persisted) ---------------------------------

  setViewport(x, y, scale) {
    this.viewport = { x, y, scale };
  }

  // ---- helpers ------------------------------------------------------------

  layerById(id) {
    return this.doc?.layers?.[id] || null;
  }

  assetById(id) {
    return this.doc?.assets?.[id] || null;
  }

  childrenOf(parentId) {
    if (!this.doc?.layers) return [];
    // A root's parent_id may be null OR missing: normalise both to null so
    // childrenOf(null) really returns every root (reordering depended on it).
    const want = parentId ?? null;
    return Object.values(this.doc.layers)
      .filter(l => (l.parent_id ?? null) === want)
      .sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  }

  roots() {
    return this.childrenOf(null);
  }

  subtreeIds(rootId) {
    const out = [rootId];
    const walk = (id) => {
      for (const child of this.childrenOf(id)) {
        out.push(child.id);
        walk(child.id);
      }
    };
    walk(rootId);
    return out;
  }

  isAncestor(ancestorId, descendantId) {
    let cur = descendantId;
    const seen = new Set();
    while (cur != null && !seen.has(cur)) {
      if (cur === ancestorId) return true;
      seen.add(cur);
      cur = this.layerById(cur)?.parent_id ?? null;
    }
    return false;
  }

  async _error(res) {
    let body = {};
    try { body = await res.json(); } catch { /* ignore */ }
    const err = new Error(body?.error?.message || `HTTP ${res.status}`);
    err.status = res.status;
    err.code = body?.error?.code || 'HTTP_ERROR';
    err.details = body?.error?.details || {};
    err.layerId = body?.error?.layer_id || null;
    return err;
  }
}

// Singleton for the page
export const store = new EditorStore();

// ---------------------------------------------------------------------------
// Turning / moving a layer WITHOUT dragging its subtree along
// ---------------------------------------------------------------------------

/** World matrix of a layer from the pose chain (flip is geometry, not pose). */
export function worldMatrixOf(layerId) {
  const chain = [];
  let cur = layerId;
  const seen = new Set();
  while (cur && !seen.has(cur)) {
    seen.add(cur);
    const n = store.layerById(cur);
    if (!n) break;
    chain.unshift(affine.matrix(n.pose));
    cur = n.parent_id;
  }
  let M = affine.identity();
  for (const lm of chain) M = affine.multiply(M, lm);
  return M;
}

/** Where each direct child sits in the world right now. */
export function childWorldSnapshots(layerId) {
  try {
    const world = worldMatrixOf(layerId);
    return store.childrenOf(layerId).map((c) => ({
      id: c.id,
      world: affine.multiply(world, affine.matrix(c.pose)),
    }));
  } catch {
    return [];
  }
}

/**
 * Put every child back where it was: L_new = inverse(W_parent_new) · W_child_old.
 * Call it after the parent's new pose has been committed.
 */
export async function restoreChildWorlds(layerId, snaps) {
  if (!snaps?.length || !store.layerById(layerId)) return 0;
  let inv;
  try {
    inv = affine.inverse(worldMatrixOf(layerId));
  } catch {
    return 0;
  }
  let n = 0;
  for (const snap of snaps) {
    if (!store.layerById(snap.id)) continue;
    const local = affine.poseFromMatrix(affine.multiply(inv, snap.world));
    if (!Number.isFinite(local.scale) || local.scale <= 0) continue;
    try {
      await store.commitCommand('set_pose', {
        layer_id: snap.id,
        pose: {
          tx: Number(local.tx), ty: Number(local.ty),
          scale: Number(local.scale), angle_deg: Number(local.angle_deg),
        },
      });
      n += 1;
    } catch (err) {
      console.warn('keep child in place failed', snap.id, err);
    }
  }
  return n;
}

/** The panel switch: transform this layer only, leaving its children put. */
export function childrenAreDetached() {
  return !!(typeof document !== 'undefined' && document.getElementById('rotate-solo')?.checked);
}
