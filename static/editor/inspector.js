// inspector.js — dimension/property panel wired to task-06 setters (tasks 07, 09)
// Numeric changes go through the same pose math as the 2D handles:
// width/height inputs recompute ONE uniform scale (aspect preserved).
import { store } from './store.js';
import * as affine from './affine.mjs';

export class Inspector {
  /**
   * @param {HTMLElement} container  the #inspector element
   * @param {object} viewport  Viewport2D instance
   */
  constructor(container, viewport) {
    this.el = container;
    this.viewport = viewport;
    window.addEventListener('editor:selection', (e) => this.render(e.detail.layerId));
  }

  render(layerId = store.selectedId) {
    this.el.innerHTML = '';
    if (!layerId) {
      this.el.innerHTML = '<p class="empty-state">Sin selección.</p>';
      return;
    }
    const node = store.layerById(layerId);
    const asset = store.assetById(node.asset_id);
    if (!node || !asset) {
      this.el.innerHTML = '<p class="empty-state">Capa no válida.</p>';
      return;
    }

    // Local bounds in mm: prefer the asset's local_bounds (filled geometry,
    // centred at origin after normalization); fall back to the source viewbox
    // scaled by mm_per_source_unit.
    const lb = asset.local_bounds; // [x0, y0, x1, y1] in local mm
    const localW = lb ? (lb[2] - lb[0]) : ((asset.source_viewbox || [0, 0, 100, 100])[2] * (asset.mm_per_source_unit || 1));
    const localH = lb ? (lb[3] - lb[1]) : ((asset.source_viewbox || [0, 0, 100, 100])[3] * (asset.mm_per_source_unit || 1));
    const pose = node.pose;

    const h = (title) => {
      const el = document.createElement('h3');
      el.textContent = title;
      this.el.appendChild(el);
    };
    const row = (label, input) => {
      const r = document.createElement('label');
      r.className = 'insp-row';
      const s = document.createElement('span');
      s.textContent = label;
      r.appendChild(s);
      r.appendChild(input);
      this.el.appendChild(r);
    };
    const num = (value, step = 0.1, min = null) => {
      const i = document.createElement('input');
      i.type = 'number';
      i.step = String(step);
      if (min !== null) i.min = String(min);
      i.value = Number.isFinite(value) ? String(Math.round(value * 1000) / 1000) : '';
      return i;
    };

    h('Transformación');
    const tx = num(pose.tx);
    const ty = num(pose.ty);
    const scale = num(pose.scale, 0.01, 0.0001);
    const angle = num(pose.angle_deg, 1);
    row('X (mm)', tx);
    row('Y (mm)', ty);
    row('Escala', scale);
    row('Ángulo (°)', angle);

    const applyPose = async () => {
      const p = {
        tx: parseFloat(tx.value),
        ty: parseFloat(ty.value),
        scale: parseFloat(scale.value),
        angle_deg: parseFloat(angle.value),
      };
      if (![p.tx, p.ty, p.scale, p.angle_deg].every(Number.isFinite) || p.scale <= 0) {
        this._flash('Valores no válidos (escala > 0).');
        return;
      }
      try {
        await store.commitCommand('set_pose', { layer_id: layerId, pose: p });
        this.viewport.renderLayers();
        this.render(layerId);
      } catch (err) {
        this._flash(err.message);
      }
    };
    for (const i of [tx, ty, scale, angle]) i.addEventListener('change', applyPose);

    // Width/height: uniform scale derived from the local bounds (task 09)
    h('Tamaño (mm, escala uniforme)');
    const width = num(localW * pose.scale, 0.1, 0.001);
    const height = num(localH * pose.scale, 0.1, 0.001);
    row('Ancho', width);
    row('Alto', height);
    const applySize = async () => {
      const w = parseFloat(width.value), hgt = parseFloat(height.value);
      if (!(w > 0) || !(hgt > 0)) { this._flash('Tamaño debe ser positivo.'); return; }
      // Uniform scale from displayed width: width = localW * scale.
      const newScale = w / localW;
      try {
        await store.commitCommand('set_pose', {
          layer_id: layerId,
          pose: { ...pose, scale: newScale },
        });
        this.viewport.renderLayers();
        this.render(layerId);
      } catch (err) {
        this._flash(err.message);
      }
    };
    width.addEventListener('change', applySize);
    height.addEventListener('change', applySize);

    h('Propiedades');
    const name = document.createElement('input');
    name.type = 'text';
    name.value = node.name || '';
    name.addEventListener('change', async () => {
      try {
        await store.commitCommand('set_layer_properties', { layer_id: layerId, name: name.value });
        this.render(layerId);
      } catch (err) { this._flash(err.message); }
    });
    row('Nombre', name);

    const extrusion = num(node.extrusion_mm ?? '', 0.1, 0);
    extrusion.placeholder = 'heredada';
    extrusion.addEventListener('change', async () => {
      const v = extrusion.value === '' ? null : parseFloat(extrusion.value);
      try {
        await store.commitCommand('set_layer_properties', { layer_id: layerId, extrusion_mm: v });
        this.render(layerId);
      } catch (err) { this._flash(err.message); }
    });
    row('Extrusión (mm)', extrusion);

    const locked = document.createElement('input');
    locked.type = 'checkbox';
    locked.checked = !!node.locked;
    locked.addEventListener('change', async () => {
      try {
        await store.commitCommand('set_layer_properties', { layer_id: layerId, locked: locked.checked });
        this.render(layerId);
      } catch (err) { this._flash(err.message); }
    });
    const lockRow = document.createElement('label');
    lockRow.className = 'insp-row checkbox-row';
    lockRow.textContent = 'Bloqueada ';
    lockRow.appendChild(locked);
    this.el.appendChild(lockRow);

    const exportEnabled = document.createElement('input');
    exportEnabled.type = 'checkbox';
    exportEnabled.checked = node.export_enabled !== false;
    exportEnabled.addEventListener('change', async () => {
      try {
        await store.commitCommand('set_layer_properties', { layer_id: layerId, export_enabled: exportEnabled.checked });
        this.render(layerId);
      } catch (err) { this._flash(err.message); }
    });
    const expRow = document.createElement('label');
    expRow.className = 'insp-row checkbox-row';
    expRow.textContent = 'Incluida en exportación ';
    expRow.appendChild(exportEnabled);
    this.el.appendChild(expRow);

    h('Ajuste / padding interior');
    const fit = node.fit || {};
    const padIn = num(fit.padding_mm ?? 0, 0.1, 0);
    row('Padding padre (mm)', padIn);
    padIn.addEventListener('change', async () => {
      const v = parseFloat(padIn.value);
      if (!(v >= 0)) { this._flash('Padding ≥ 0'); return; }
      try {
        await store.commitCommand('set_layer_properties', {
          layer_id: layerId,
          fit: { ...fit, padding_mm: v },
        });
        this.render(layerId);
      } catch (err) { this._flash(err.message); }
    });

    const parentSelect = document.createElement('select');
    const noneOpt = document.createElement('option');
    noneOpt.value = '';
    noneOpt.textContent = '(raíz del documento)';
    parentSelect.appendChild(noneOpt);
    for (const other of Object.values(store.doc.layers)) {
      if (other.id === layerId) continue;
      if (store.isAncestor(layerId, other.id)) continue; // avoid cycles
      const opt = document.createElement('option');
      opt.value = other.id;
      opt.textContent = other.name || other.id;
      if (other.id === node.parent_id) opt.selected = true;
      parentSelect.appendChild(opt);
    }
    row('Padre', parentSelect);
    parentSelect.addEventListener('change', async () => {
      const newParent = parentSelect.value || null;
      try {
        await store.commitCommand('set_parent', {
          layer_id: layerId,
          new_parent_id: newParent,
        });
        this.viewport.renderLayers();
        this.viewport.renderSelection();
        this.render(layerId);
        window.dispatchEvent(new CustomEvent('editor:doc-changed'));
      } catch (err) { this._flash(err.message); }
    });
  }

  _flash(msg) {
    const p = document.createElement('p');
    p.className = 'insp-error';
    p.textContent = msg;
    this.el.appendChild(p);
    setTimeout(() => p.remove(), 4000);
  }
}
