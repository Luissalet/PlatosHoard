// export_dialog.js — recipe preview + batch export (tasks 18, 20)
// Batch = 4 silhouette families + marco pieces once.
import { store } from './store.js';

const FAMILIES = [
  { id: 'normal_registered', label: 'Sólidas anidadas (matrioska)' },
  { id: 'inverse_registered', label: 'Inversa anidada (huecos)' },
  { id: 'normal_fullframe', label: 'Sólida fit canvas (centrada)' },
  { id: 'inverse_fullframe', label: 'Inversa fit canvas (centrada)' },
];
const FORMATS = [
  { id: 'svg', label: 'SVG' },
  { id: 'png', label: 'PNG' },
  { id: 'stl', label: 'STL' },
];

function isMarcoLayer(layer) {
  if (!layer) return false;
  const name = layer.name || '';
  return name === 'Marco' || name === 'Marco fondo' || name === 'Marco paredes'
    || String(layer.id || '').startsWith('layer_marco_');
}

export class ExportDialog {
  /**
   * @param {object} els  { recipeSelect, exportSelected, exportBatch, fitStatus }
   */
  constructor(els) {
    this.els = els;
    this._bind();
  }

  _bind() {
    this.els.exportSelected?.addEventListener('click', () => this._export(false));
    this.els.exportBatch?.addEventListener('click', () => this._export(true));
  }

  _activeFamilies() {
    const sel = this.els.recipeSelect;
    if (!sel) return FAMILIES.map(f => f.id);
    if (sel.value === 'batch_all') return FAMILIES.map(f => f.id);
    return [sel.value];
  }

  _activeFormats() {
    return FORMATS.map(f => f.id);
  }

  _selectedLayerIds(batch) {
    if (batch) return Object.keys(store.doc?.layers || {});
    if (store.selectedId) return [store.selectedId];
    return Object.keys(store.doc?.layers || {});
  }

  updateCounter() {
    const batchish = this.els.recipeSelect?.value === 'batch_all';
    const ids = this._selectedLayerIds(batchish);
    const layers = store.doc?.layers || {};
    let sil = 0;
    let marco = 0;
    for (const id of ids) {
      if (isMarcoLayer(layers[id])) marco += 1;
      else sil += 1;
    }
    const fams = this._activeFamilies().length;
    const fmts = this._activeFormats().length;
    // Marco always exports once (normal), independent of family count.
    const pieces = sil * fams + marco;
    const files = pieces * fmts;
    const el = this.els.fitStatus;
    if (el) {
      el.textContent = `${pieces} piezas · ${files} archivos + manifest.json`;
    }
  }

  async _export(batch) {
    const status = this.els.fitStatus;
    const set = (msg) => { if (status) status.textContent = msg; };
    const layerIds = this._selectedLayerIds(batch);
    if (!layerIds.length) { set('Nada seleccionado.'); return; }

    set('Enviando exportación…');
    try {
      const res = await fetch(`${store.baseUrl}/documents/${encodeURIComponent(store.doc.id)}/exports`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          layer_ids: layerIds,
          families: this._activeFamilies(),
          formats: this._activeFormats(),
          png_width_px: 1000,
          request_seq: Date.now(),
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.error?.message || `HTTP ${res.status}`);
      }
      const job = await res.json();
      set(`Trabajo ${job.id} en cola…`);
      this._pollJob(job.id);
    } catch (err) {
      set(`Error: ${err.message}`);
    }
  }

  async _pollJob(jobId) {
    const status = this.els.fitStatus;
    const set = (msg) => { if (status) status.textContent = msg; };
    for (let i = 0; i < 120; i++) {
      await new Promise(r => setTimeout(r, 1000));
      const res = await fetch(`${store.baseUrl}/jobs/${encodeURIComponent(jobId)}`);
      if (!res.ok) { set('Error consultando el trabajo.'); return; }
      const job = await res.json();
      if (job.state === 'completed') {
        set('Exportación lista. Descargando…');
        const dl = await fetch(`${store.baseUrl}/jobs/${encodeURIComponent(jobId)}/download`);
        if (dl.ok) {
          const blob = await dl.blob();
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = `silhouettes_export_${jobId}.zip`;
          document.body.appendChild(a);
          a.click();
          a.remove();
          URL.revokeObjectURL(url);
          set('Exportación descargada.');
        } else {
          const body = await dl.json().catch(() => ({}));
          set(`El trabajo terminó pero no hay archivo${body?.error?.message ? `: ${body.error.message}` : ''}.`);
        }
        return;
      }
      if (job.state === 'failed') {
        const msg = job.error?.message || job.error?.code || 'desconocido';
        set(`Fallo: ${msg}`);
        return;
      }
      if (job.state === 'cancelled') { set('Trabajo cancelado.'); return; }
      set(`Trabajando… (${job.progress ?? ''})`);
    }
    set('Tiempo de espera agotado; consulta el trabajo más tarde.');
  }
}
