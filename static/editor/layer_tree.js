// layer_tree.js — layers with preview, delete, reorder + reparent DnD
import { store } from './store.js';

/** Stable distinct color per layer id (matches viewport2d). */
export function layerColor(layerId) {
  const ids = Object.keys(store.doc?.layers || {});
  const i = Math.max(0, ids.indexOf(layerId));
  const hue = (i * 137.508) % 360;
  return `hsl(${hue.toFixed(0)}, 65%, 45%)`;
}

function displayName(layer) {
  if (layer?.name && !/^asset_/i.test(layer.name) && layer.name !== layer.id) {
    return layer.name;
  }
  const asset = store.assetById(layer?.asset_id);
  if (asset?.source_filename) {
    const f = asset.source_filename;
    return f.includes('.') ? f.slice(0, f.lastIndexOf('.')) : f;
  }
  if (asset?.name && !/^asset_/i.test(asset.name)) return asset.name;
  return layer?.name || layer?.id || '?';
}

export class LayerTree {
  /**
   * @param {HTMLElement} container
   * @param {object} viewport
   * @param {{ matrioska?: () => boolean, onNest?: (childId: string, parentId: string) => void }} [opts]
   */
  constructor(container, viewport, opts = {}) {
    this.el = container;
    this.viewport = viewport;
    this._dragId = null;
    this._opts = opts;
  }

  render() {
    this.el.innerHTML = '';
    if (!store.doc || !Object.keys(store.doc.layers).length) {
      this.el.innerHTML = '<p class="empty-state">Sin capas. Importa o suelta un PNG/SVG.</p>';
      return;
    }
    for (const root of store.roots()) {
      this._appendNode(root, 0);
    }

    // Drop on empty panel → make root (end of root list)
    this.el.addEventListener('dragover', (e) => {
      if (!this._dragId) return;
      if (e.target.closest?.('.tree-row')) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
    });
    this.el.addEventListener('drop', (e) => {
      if (e.target.closest?.('.tree-row')) return;
      e.preventDefault();
      const fromId = this._dragId || e.dataTransfer.getData('text/plain');
      if (fromId) this._dropAsRoot(fromId);
    });
  }

  _appendNode(layer, depth) {
    const row = document.createElement('div');
    row.className = 'tree-row' + (layer.id === store.selectedId ? ' selected' : '');
    row.dataset.layerId = layer.id;
    row.style.paddingLeft = `${8 + depth * 16}px`;
    row.draggable = true;

    row.addEventListener('dragstart', (e) => {
      this._dragId = layer.id;
      e.dataTransfer.setData('text/plain', layer.id);
      e.dataTransfer.effectAllowed = 'move';
      row.classList.add('dragging');
    });
    row.addEventListener('dragend', () => {
      this._dragId = null;
      row.classList.remove('dragging');
      this.el.querySelectorAll('.drop-before,.drop-after,.drop-into')
        .forEach((el) => el.classList.remove('drop-before', 'drop-after', 'drop-into'));
    });
    row.addEventListener('dragover', (e) => {
      e.preventDefault();
      e.stopPropagation();
      e.dataTransfer.dropEffect = 'move';
      const zone = this._dropZone(row, e);
      row.classList.remove('drop-before', 'drop-after', 'drop-into');
      row.classList.add(`drop-${zone}`);
    });
    row.addEventListener('dragleave', () => {
      row.classList.remove('drop-before', 'drop-after', 'drop-into');
    });
    row.addEventListener('drop', (e) => {
      e.preventDefault();
      e.stopPropagation();
      row.classList.remove('drop-before', 'drop-after', 'drop-into');
      const fromId = this._dragId || e.dataTransfer.getData('text/plain');
      if (!fromId || fromId === layer.id) return;
      const zone = this._dropZone(row, e);
      this._handleDrop(fromId, layer.id, zone);
    });

    const eye = document.createElement('button');
    eye.type = 'button';
    eye.className = 'eye-btn' + (layer.visible ? '' : ' hidden');
    eye.textContent = layer.visible ? '👁' : '–';
    eye.title = layer.visible ? 'Ocultar' : 'Mostrar';
    eye.addEventListener('click', (e) => {
      e.stopPropagation();
      this._toggleVisible(layer.id);
    });
    row.appendChild(eye);

    const swatch = document.createElement('span');
    swatch.className = 'tree-swatch';
    swatch.style.background = layerColor(layer.id);
    row.appendChild(swatch);

    const asset = store.assetById(layer.asset_id);
    const thumb = this._thumbnail(asset, layerColor(layer.id));
    if (thumb) row.appendChild(thumb);

    const name = document.createElement('span');
    name.className = 'tree-name';
    name.textContent = displayName(layer);
    name.title = displayName(layer);
    name.addEventListener('click', () => this._select(layer.id));
    row.appendChild(name);

    if (layer.locked) {
      const lock = document.createElement('span');
      lock.className = 'lock-icon';
      lock.textContent = '🔒';
      row.appendChild(lock);
    }

    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'del-btn';
    del.textContent = '✕';
    del.title = 'Eliminar capa';
    del.addEventListener('click', (e) => {
      e.stopPropagation();
      this._delete(layer.id);
    });
    row.appendChild(del);

    this.el.appendChild(row);
    for (const child of store.childrenOf(layer.id)) {
      this._appendNode(child, depth + 1);
    }
  }

  /** With Matrioska on: whole row nests (Shift = reorder by edge). */
  _dropZone(row, e) {
    const matrioska = !!this._opts.matrioska?.();
    if (matrioska && !e.shiftKey) return 'into';
    const r = row.getBoundingClientRect();
    const y = e.clientY - r.top;
    const frac = y / Math.max(1, r.height);
    const edge = 0.28;
    if (frac < edge) return 'before';
    if (frac > 1 - edge) return 'after';
    return 'into';
  }

  async _handleDrop(fromId, targetId, zone) {
    if (store.isAncestor(fromId, targetId)) {
      window.alert('No se puede anidar una capa dentro de sí misma.');
      return;
    }
    const target = store.layerById(targetId);
    if (!target) return;

    try {
      if (zone === 'into') {
        await store.commitCommand('set_parent', {
          layer_id: fromId,
          new_parent_id: targetId,
        });
        if (this._opts.matrioska?.()) {
          this._opts.onNest?.(fromId, targetId);
        }
      } else {
        // Reorder as sibling of target (same parent), before or after
        const parentId = target.parent_id ?? null;
        const from = store.layerById(fromId);
        // If different parent, reparent first then reorder
        if ((from?.parent_id ?? null) !== parentId) {
          await store.commitCommand('set_parent', {
            layer_id: fromId,
            new_parent_id: parentId,
          });
        }
        const siblings = store.childrenOf(parentId).map(s => s.id);
        const without = siblings.filter(id => id !== fromId);
        const ti = without.indexOf(targetId);
        if (ti < 0) return;
        const insertAt = zone === 'before' ? ti : ti + 1;
        without.splice(insertAt, 0, fromId);
        await store.commitCommand('reorder_siblings', {
          parent_id: parentId,
          order: without,
        });
      }
      this.render();
      this.viewport?.renderLayers();
      this.viewport?.renderSelection();
      window.dispatchEvent(new CustomEvent('editor:doc-changed'));
    } catch (err) {
      console.error('drop failed', err);
      window.alert(`No se pudo reordenar: ${err.message}`);
    }
  }

  async _dropAsRoot(fromId) {
    try {
      const from = store.layerById(fromId);
      if (!from) return;
      if (from.parent_id != null) {
        await store.commitCommand('set_parent', {
          layer_id: fromId,
          new_parent_id: null,
        });
      }
      const roots = store.roots().map(r => r.id);
      const without = roots.filter(id => id !== fromId);
      without.push(fromId);
      await store.commitCommand('reorder_siblings', {
        parent_id: null,
        order: without,
      });
      this.render();
      this.viewport?.renderLayers();
      window.dispatchEvent(new CustomEvent('editor:doc-changed'));
    } catch (err) {
      window.alert(`No se pudo mover: ${err.message}`);
    }
  }

  _thumbnail(asset, color) {
    const SVG_NS = 'http://www.w3.org/2000/svg';
    const size = 28;
    const svg = asset?.canonical_svg;
    if (!svg) return null;
    let doc;
    try {
      doc = new DOMParser().parseFromString(svg, 'image/svg+xml');
    } catch { return null; }
    const paths = [...doc.querySelectorAll('path')];
    if (!paths.length) return null;
    const bounds = this._thumbBounds(asset, doc);
    if (!bounds) return null;
    const { x0, y0, w, h } = bounds;
    const pad = 2;
    const scale = Math.min((size - 2 * pad) / w, (size - 2 * pad) / h);
    const ox = (size - w * scale) / 2, oy = (size - h * scale) / 2;
    const el = document.createElementNS(SVG_NS, 'svg');
    el.setAttribute('class', 'tree-thumb');
    el.setAttribute('width', size);
    el.setAttribute('height', size);
    el.setAttribute('viewBox', `0 0 ${size} ${size}`);
    const g = document.createElementNS(SVG_NS, 'g');
    g.setAttribute('transform', `translate(${ox} ${oy}) scale(${scale}) translate(${-x0} ${-y0})`);
    for (const p of paths) {
      const np = document.createElementNS(SVG_NS, 'path');
      np.setAttribute('d', p.getAttribute('d') || '');
      np.setAttribute('fill', color || 'currentColor');
      np.setAttribute('fill-rule', 'evenodd');
      g.appendChild(np);
    }
    el.appendChild(g);
    return el;
  }

  _thumbBounds(asset, parsed) {
    const vb = asset.source_viewbox;
    if (Array.isArray(vb) && vb.length >= 4 && vb[2] > 0 && vb[3] > 0) {
      return { x0: vb[0], y0: vb[1], w: vb[2], h: vb[3] };
    }
    const attr = parsed?.documentElement?.getAttribute?.('viewBox');
    if (attr) {
      const p = attr.trim().split(/[\s,]+/).map(Number);
      if (p.length === 4 && p.every(Number.isFinite) && p[2] > 0 && p[3] > 0) {
        return { x0: p[0], y0: p[1], w: p[2], h: p[3] };
      }
    }
    return null;
  }

  async _select(layerId) {
    store.select(layerId);
    this.render();
    this.viewport?.renderLayers();
    window.dispatchEvent(new CustomEvent('editor:selection', { detail: { layerId } }));
  }

  async _delete(layerId) {
    const node = store.layerById(layerId);
    if (!node) return;
    const subtree = store.subtreeIds(layerId);
    const descendants = subtree.length - 1;
    const name = displayName(node);
    const msg = descendants > 0
      ? `Eliminar «${name}» y sus ${descendants} descendiente(s)?`
      : `Eliminar «${name}»?`;
    if (!window.confirm(msg)) return;
    try {
      await store.commitCommand('delete_subtree', {
        layer_id: layerId,
        confirm_descendants: descendants,
      });
      if (store.selectedId && subtree.includes(store.selectedId)) {
        store.clearSelection();
      }
      this.render();
      this.viewport?.renderLayers();
      this.viewport?.renderSelection();
      window.dispatchEvent(new CustomEvent('editor:doc-changed'));
    } catch (err) {
      window.alert(`No se pudo eliminar: ${err.message}`);
    }
  }

  async _toggleVisible(layerId) {
    const node = store.layerById(layerId);
    if (!node) return;
    try {
      await store.commitCommand('set_layer_properties', {
        layer_id: layerId,
        visible: !node.visible,
      });
      this.render();
      this.viewport?.renderLayers();
      this.viewport?.renderSelection();
      window.dispatchEvent(new CustomEvent('editor:doc-changed'));
    } catch (err) {
      console.error(err);
      window.alert(`No se pudo cambiar visibilidad: ${err.message}`);
    }
  }
}
