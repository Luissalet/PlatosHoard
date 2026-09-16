// export_dialog.js — recipe preview + batch export (tasks 18, 20)
// The counter shows N layers × active families. With 3 layers and 3
// families it reads 9 pieces; with SVG+PNG+STL it reads 27 files + manifest.
import { store } from './store.js';

const FAMILIES = [
  { id: 'normal_registered', label: 'Sólidas apiladas (normal)' },
  { id: 'inverse_registered', label: 'Planchas con huecos (inverse)' },
  { id: 'normal_fullframe', label: 'Independientes a recuadro (fullframe)' },
];
const FORMATS = [
  { id: 'svg', label: 'SVG' },
  { id: 'png', label: 'PNG' },
  { id: 'stl', label: 'STL' },
];

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
    // "batch" option exports all three families (normal + inverse + fullframe)
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
    const n = this._selectedLayerIds(batchish).length;
    const fams = this._activeFamilies().length;
    const fmts = this._activeFormats().length;
    const pieces = n * fams;
    const files = n * fams * fmts;
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
          set('El trabajo terminó pero no hay archivo.');
        }
        return;
      }
      if (job.state === 'failed') {
        set(`Fallo: ${job.error || 'desconocido'}`);
        return;
      }
      if (job.state === 'cancelled') { set('Trabajo cancelado.'); return; }
      set(`Trabajando… (${job.progress ?? ''})`);
    }
    set('Tiempo de espera agotado; consulta el trabajo más tarde.');
  }
}
