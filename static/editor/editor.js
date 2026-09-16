// editor.js — mounts the editor (tasks 07, 08, 09, 16, 21)
// Wires: store, viewport2d, layer_tree, inspector, interactions2d,
// export_dialog, viewer3d, and the toolbar (new/save/open/import/fit).
import { store } from './store.js';
import { Viewport2D } from './viewport2d.js';
import { LayerTree } from './layer_tree.js';
import { Inspector } from './inspector.js';
import { Interactions2D } from './interactions2d.js';
import { ExportDialog } from './export_dialog.js';
import { mountViewer3D } from './viewer3d.mjs';
import * as affine from './affine.mjs';

function $(id) { return document.getElementById(id); }

function enableToolbar(hasDoc) {
  for (const id of ['editor-new', 'editor-save', 'editor-open', 'editor-undo', 'editor-redo', 'import-files']) {
    const el = $(id);
    if (el) el.disabled = !hasDoc;
  }
  for (const id of ['canvas-width-mm', 'canvas-height-mm', 'canvas-padding-top',
                    'canvas-padding-right', 'canvas-padding-bottom', 'canvas-padding-left']) {
    const el = $(id);
    if (el) el.disabled = !hasDoc;
  }
  for (const id of ['recipe-select', 'export-selected', 'export-batch', 'fit-best', 'fit-at-position',
                    'matrioska-mode', 'view-inverse', 'fit-to-canvas', 'matrioska-stack',
                    'matrioska-mode-side', 'view-inverse-side', 'fit-to-canvas-side']) {
    const el = $(id);
    if (el) el.disabled = !hasDoc;
  }
  if (hasDoc && $('matrioska-mode') && !$('matrioska-mode').dataset.userTouched) {
    $('matrioska-mode').checked = true;
    if ($('matrioska-mode-side')) $('matrioska-mode-side').checked = true;
  }
}

function syncCanvasInputs() {
  const c = store.doc?.canvas;
  if (!c) return;
  $('canvas-width-mm').value = c.width_mm;
  $('canvas-height-mm').value = c.height_mm;
  const p = c.padding_mm || {};
  $('canvas-padding-top').value = p.top ?? 0;
  $('canvas-padding-right').value = p.right ?? 0;
  $('canvas-padding-bottom').value = p.bottom ?? 0;
  $('canvas-padding-left').value = p.left ?? 0;
}

async function applyCanvas() {
  const w = parseFloat($('canvas-width-mm').value);
  const h = parseFloat($('canvas-height-mm').value);
  const p = {
    top: parseFloat($('canvas-padding-top').value) || 0,
    right: parseFloat($('canvas-padding-right').value) || 0,
    bottom: parseFloat($('canvas-padding-bottom').value) || 0,
    left: parseFloat($('canvas-padding-left').value) || 0,
  };
  if (!(w > 0) || !(h > 0)) { flash('Canvas inválido.'); return; }
  try {
    await store.commitCommand('set_canvas', {
      width_mm: w, height_mm: h, padding_mm: p, resize_policy: 'keep_layers',
    });
    if (store.fitToCanvas) await applyFitToCanvasAll();
    viewport.renderCanvas();
    viewport.renderLayers();
    viewport.fitToCanvas();
    window.dispatchEvent(new CustomEvent('editor:doc-changed'));
  } catch (err) { flash(err.message); }
}

function flash(msg) {
  const el = $('fit-status');
  if (el) { el.textContent = msg; setTimeout(() => { el.textContent = ''; }, 3500); }
}

async function importFiles(input) {
  const files = [...(input.files || [])];
  if (!files.length || !store.doc) return;
  const fd = new FormData();
  for (const f of files) fd.append('files[]', f, f.name);
  fd.append('base_revision', String(store.revision));
  flash(`Importando ${files.length} archivo(s)…`);
  try {
    const res = await fetch(`${store.baseUrl}/documents/${encodeURIComponent(store.doc.id)}/assets`, {
      method: 'POST',
      body: fd,
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body?.error?.message || `HTTP ${res.status}`);
    }
    const out = await res.json();
    store._applyDocumentPayload(out);
    // If payload didn't stick (stale client), reload from server.
    if (!Object.keys(store.doc?.layers || {}).length && store.doc?.id) {
      await store.loadDocument(store.doc.id);
    }
    input.value = '';
    refreshAll();
    viewport.applyViewport();
    viewport.fitToCanvas();
    const n = Object.keys(store.doc?.layers || {}).length;
    flash(n ? `Importados ${out.imported ?? n} asset(s).` : 'Importó assets pero no hay capas.');
  } catch (err) {
    flash(`Importación fallida: ${err.message}`);
  }
}

async function saveProject() {
  if (!store.doc) return;
  flash('Descargando proyecto…');
  const res = await fetch(`${store.baseUrl}/documents/${encodeURIComponent(store.doc.id)}/package`);
  if (!res.ok) { flash('No se pudo guardar.'); return; }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${store.doc.name || store.doc.id}.silhouettes`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  flash('Proyecto guardado.');
}

async function openProject(input) {
  const f = input.files?.[0];
  if (!f) return;
  const fd = new FormData();
  fd.append('file', f, f.name);
  input.value = '';
  flash('Abriendo proyecto…');
  try {
    const res = await fetch(`${store.baseUrl}/documents/import`, { method: 'POST', body: fd });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body?.error?.message || `HTTP ${res.status}`);
    }
    store._applyDocumentPayload(await res.json());
    store.selectedId = null;
    refreshAll();
    requestAnimationFrame(() => viewport.fitToCanvas());
    flash('Proyecto abierto.');
  } catch (err) {
    flash(`No se pudo abrir: ${err.message}`);
  }
}

async function runFit(mode = 'best') {
  if (!store.selectedId) { flash('Selecciona una capa primero.'); return; }
  const node = store.layerById(store.selectedId);
  if (!node) return;
  const matrioska = !!$('matrioska-mode')?.checked;
  let target = 'canvas';
  if (node.parent_id) {
    target = 'parent_shape';
  } else if (matrioska) {
    flash('Modo matrioska: anida la capa dentro de otra (arrastra al centro de la fila) antes de encajar.');
    return;
  }
  const padding = Number(node.fit?.padding_mm ?? (matrioska ? 2.0 : 0));
  flash(mode === 'at_position' ? 'Ajustando escala en posición…' : 'Buscando mejor posición…');
  try {
    const res = await fetch(`${store.baseUrl}/documents/${encodeURIComponent(store.doc.id)}/fit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        layer_id: store.selectedId,
        target,
        mode,
        padding_mm: Number.isFinite(padding) ? padding : 2.0,
        angles_deg: [0],
        max_evaluations: matrioska ? 6000 : 2000,
        seed: 42,
        request_seq: Date.now(),
      }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(body?.error?.message || `HTTP ${res.status}`);
    }
    // Sync response (200) or legacy job (202)
    if (res.status === 202 || body.state === 'queued' || body.id?.startsWith?.('job_')) {
      flash('Trabajo en cola…');
      for (let i = 0; i < 60; i++) {
        await new Promise(r => setTimeout(r, 400));
        const jr = await fetch(`${store.baseUrl}/jobs/${encodeURIComponent(body.id)}`);
        const j = await jr.json();
        if (j.state === 'completed') {
          const pose = j.result?.pose_local || j.result?.pose || j.result?.pose_world;
          if (!pose) { flash('Sin propuesta.'); return; }
          await applyFitPose(pose);
          return;
        }
        if (j.state === 'failed') { flash(`Fallo: ${j.error?.message || j.error || '?'}`); return; }
      }
      flash('Tiempo agotado.');
      return;
    }
    const pose = body.pose_local || body.pose;
    if (!pose) { flash('Sin propuesta de pose.'); return; }
    await applyFitPose(pose);
  } catch (err) {
    flash(`Fit fallido: ${err.message}`);
  }
}

async function applyFitPose(pose) {
  await store.commitCommand('apply_fit_result', {
    layer_id: store.selectedId,
    pose,
    base_revision: store.revision,
  });
  refreshAll();
  flash('Posición aplicada.');
}

async function nestAndFit(childId, parentId) {
  store.select(childId);
  flash('Encaje matrioska…');
  try {
    const pose = await fitChildIntoParentPose(childId, parentId);
    if (!pose) throw new Error('sin pose');
    await store.commitCommand('apply_fit_result', {
      layer_id: childId,
      pose,
      base_revision: store.revision,
    });
    if ($('matrioska-mode')) $('matrioska-mode').checked = true;
    if ($('matrioska-mode-side')) $('matrioska-mode-side').checked = true;
    if ($('view-inverse')) $('view-inverse').checked = false;
    if ($('view-inverse-side')) $('view-inverse-side').checked = false;
    store.matrioskaMode = true;
    refreshAll();
    flash('Matrioska: encajado en la silueta padre.');
  } catch (err) {
    flash(`Matrioska: ${err.message}`);
    refreshAll();
  }
}

/**
 * Fit child inside parent silhouette (same idea as Fit canvas → usable rect).
 * Prefers server parent_shape (real contour); falls back to inset AABB.
 */
async function fitChildIntoParentPose(childId, parentId) {
  const child = store.layerById(childId);
  const parent = store.layerById(parentId);
  const cAsset = store.assetById(child?.asset_id);
  const pAsset = store.assetById(parent?.asset_id);
  if (!cAsset?.local_bounds || !pAsset?.local_bounds) return null;
  const padding = Number(child?.fit?.padding_mm ?? 2.0);

  // Server: fit inside the actual parent polygon (not the AABB).
  try {
    const res = await fetch(`${store.baseUrl}/documents/${encodeURIComponent(store.doc.id)}/fit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        layer_id: childId,
        target: 'parent_shape',
        mode: 'best',
        padding_mm: Number.isFinite(padding) ? padding : 2.0,
        angles_deg: [0],
        max_evaluations: 5000,
        seed: 42,
        request_seq: Date.now(),
      }),
    });
    const body = await res.json().catch(() => ({}));
    if (res.ok) {
      const pose = body.pose_local || body.pose;
      if (pose && Number.isFinite(pose.scale) && pose.scale > 0) return pose;
    }
  } catch (err) {
    console.warn('parent_shape fit failed, using AABB', err);
  }

  // Fallback: AABB inset — use generous padding so we stay inside the silhouette.
  const pb = pAsset.local_bounds;
  const minSide = Math.min(pb[2] - pb[0], pb[3] - pb[1]);
  const inset = Math.max(padding, minSide * 0.12);
  const rect = affine.parentFitRect(pb, inset);
  if (!(rect.right > rect.left) || !(rect.bottom > rect.top)) return null;
  return affine.fitPoseToRect(cAsset.local_bounds, rect, child.pose?.angle_deg || 0);
}

/** Fit canvas: max-scale every root into the usable sheet. */
async function applyFitToCanvasAll() {
  if (!store.doc?.canvas) return;
  const rect = affine.usableCanvasRect(store.doc.canvas);
  const roots = store.roots();
  let n = 0;
  for (const layer of roots) {
    if (layer.locked) continue;
    const asset = store.assetById(layer.asset_id);
    if (!asset?.local_bounds) continue;
    try {
      const pose = affine.fitPoseToRect(asset.local_bounds, rect, layer.pose?.angle_deg || 0);
      await store.commitCommand('apply_fit_result', {
        layer_id: layer.id,
        pose,
        base_revision: store.revision,
      });
      n += 1;
    } catch (err) {
      console.warn('fit canvas failed', layer.id, err);
    }
  }
  flash(n ? `Fit canvas: ${n} capa(s) ajustada(s).` : 'Fit canvas: nada que ajustar.');
}

/**
 * Matrioska = Fit canvas but target = parent silhouette.
 * For every child with a parent: max-scale inside the parent's shape.
 */
async function applyMatrioskaFitAll() {
  if (!store.doc?.layers) return;
  const layers = Object.values(store.doc.layers);
  let n = 0;
  for (const layer of layers) {
    if (layer.locked || !layer.parent_id) continue;
    const pose = await fitChildIntoParentPose(layer.id, layer.parent_id);
    if (!pose) continue;
    try {
      await store.commitCommand('apply_fit_result', {
        layer_id: layer.id,
        pose,
        base_revision: store.revision,
      });
      n += 1;
    } catch (err) {
      console.warn('matrioska fit failed', layer.id, err);
    }
  }
  flash(n
    ? `Matrioska: ${n} capa(s) encajada(s) en su silueta padre.`
    : 'Matrioska: anida capas (suelta una sobre otra) y vuelve a activar.');
}

/**
 * Nest root layers largest→smallest, then fit each child into its parent
 * (same as Fit canvas → parent silhouette).
 */
async function stackMatrioska() {
  const roots = store.roots().filter((l) => !l.locked);
  if (roots.length < 2) {
    flash('Matrioska: hace falta al menos 2 capas raíz.');
    return;
  }
  const area = (layer) => {
    const lb = store.assetById(layer.asset_id)?.local_bounds;
    if (!lb) return 0;
    return Math.max(0, lb[2] - lb[0]) * Math.max(0, lb[3] - lb[1]);
  };
  const ordered = [...roots].sort((a, b) => area(b) - area(a));
  flash('Apilando matrioska…');
  try {
    for (let i = 1; i < ordered.length; i++) {
      const childId = ordered[i].id;
      const parentId = ordered[i - 1].id;
      await store.commitCommand('set_parent', {
        layer_id: childId,
        new_parent_id: parentId,
      });
      const pose = await fitChildIntoParentPose(childId, parentId);
      if (pose) {
        await store.commitCommand('apply_fit_result', {
          layer_id: childId,
          pose,
          base_revision: store.revision,
        });
      }
    }
    if ($('matrioska-mode')) $('matrioska-mode').checked = true;
    if ($('matrioska-mode-side')) $('matrioska-mode-side').checked = true;
    if ($('view-inverse')) $('view-inverse').checked = false;
    if ($('view-inverse-side')) $('view-inverse-side').checked = false;
    store.matrioskaMode = true;
    applyViewMode();
    refreshAll();
    flash('Matrioska: apilada y encajada en cada silueta padre.');
  } catch (err) {
    flash(`Matrioska: ${err.message}`);
    refreshAll();
  }
}

async function fitBest() { return runFit('best'); }
async function fitAtPosition() { return runFit('at_position'); }

let viewport, tree, inspector, interactions, exportDialog, viewer3d;

function refreshAll() {
  enableToolbar(!!store.doc);
  try {
    syncCanvasInputs();
    viewport.renderCanvas();
    applyViewMode();
    viewport.applyViewport();
  } catch (err) {
    console.error('refreshAll view', err);
  }
  try {
    tree.render();
    inspector.render(store.selectedId);
    exportDialog.updateCounter();
    viewer3d?.update();
  } catch (err) {
    console.error('refreshAll ui', err);
  }
  window.dispatchEvent(new CustomEvent('editor:doc-changed'));
}

/** Resolve live canvas mode from the Vista checkboxes. */
function currentViewMode() {
  if ($('view-inverse')?.checked) return 'inverse';
  if ($('matrioska-mode')?.checked) return 'shell';
  return 'normal';
}

function setViewHint(mode) {
  const el = $('view-mode-hint');
  if (!el) return;
  el.textContent = mode === 'inverse'
    ? 'Modo: INVERSA (plancha con huecos)'
    : mode === 'shell'
      ? 'Modo: MATRIOSKA (hijo encima del padre, fit a silueta)'
      : 'Modo: normal';
}

/** Apply view instantly (client-side masks — no server round-trip). */
function applyViewMode() {
  if (!store.doc) {
    store.viewMode = 'normal';
    viewport.renderLayers();
    setViewHint('normal');
    return;
  }
  const mode = currentViewMode();
  store.viewMode = mode;
  viewport.renderLayers();
  setViewHint(mode);
  viewer3d?.update?.();
  if (mode === 'inverse') flash('Vista inversa aplicada.');
  else if (mode === 'shell') flash('Vista matrioska aplicada.');
}

/** Keep toolbar + sidebar checkboxes in sync. */
function bindViewToggles() {
  const pairs = [
    ['matrioska-mode', 'matrioska-mode-side'],
    ['view-inverse', 'view-inverse-side'],
    ['fit-to-canvas', 'fit-to-canvas-side'],
  ];
  for (const [a, b] of pairs) {
    const elA = $(a), elB = $(b);
    if (!elA || !elB) continue;
    const sync = async (from, to) => {
      to.checked = from.checked;
      if (from.id.includes('matrioska')) {
        from.dataset.userTouched = '1';
        to.dataset.userTouched = '1';
        store.matrioskaMode = !!from.checked;
        if (from.checked) {
          if ($('view-inverse')) $('view-inverse').checked = false;
          if ($('view-inverse-side')) $('view-inverse-side').checked = false;
          applyViewMode();
          await applyMatrioskaFitAll();
          refreshAll();
          return;
        }
      }
      if (from.id.includes('fit-to-canvas')) {
        store.fitToCanvas = !!from.checked;
        if (from.checked) {
          await applyFitToCanvasAll();
          refreshAll();
          return;
        }
      }
      // Inverse and matrioska are mutually exclusive for live view
      if (from.id.includes('view-inverse') && from.checked) {
        if ($('matrioska-mode')) $('matrioska-mode').checked = false;
        if ($('matrioska-mode-side')) $('matrioska-mode-side').checked = false;
        store.matrioskaMode = false;
      }
      applyViewMode();
    };
    elA.addEventListener('change', () => sync(elA, elB));
    elB.addEventListener('change', () => sync(elB, elA));
  }
  store.fitToCanvas = !!$('fit-to-canvas')?.checked;
  store.matrioskaMode = !!$('matrioska-mode')?.checked;
}

async function boot() {
  const svg = $('editor-svg');
  viewport = new Viewport2D(svg);
  tree = new LayerTree($('layer-tree'), viewport, {
    matrioska: () => !!$('matrioska-mode')?.checked,
    onNest: (childId, parentId) => nestAndFit(childId, parentId),
  });
  inspector = new Inspector($('inspector'), viewport);
  interactions = new Interactions2D(viewport);
  exportDialog = new ExportDialog({
    recipeSelect: $('recipe-select'),
    exportSelected: $('export-selected'),
    exportBatch: $('export-batch'),
    fitStatus: $('fit-status'),
  });
  viewer3d = mountViewer3D($('viewer-3d'));

  // Toolbar
  $('editor-new').addEventListener('click', async () => {
    try {
      await store.createDocument('Nuevo documento');
      enableToolbar(true);
      refreshAll();
      requestAnimationFrame(() => viewport.fitToCanvas());
      flash('Documento creado.');
    } catch (err) { flash(err.message); }
  });
  $('editor-save').addEventListener('click', saveProject);
  $('editor-open').addEventListener('click', () => {
    const inp = document.createElement('input');
    inp.type = 'file';
    inp.accept = '.silhouettes';
    inp.addEventListener('change', () => openProject(inp));
    inp.click();
  });
  $('import-files').addEventListener('change', (e) => importFiles(e.target));

  // Drag-and-drop import: drop PNG/SVG anywhere on the editor (spec §3.2).
  const dropTarget = document.querySelector('.editor-main') || document.body;
  let dragDepth = 0;
  dropTarget.addEventListener('dragenter', (e) => {
    if (!store.doc) return;
    e.preventDefault();
    dragDepth += 1;
    dropTarget.classList.add('drop-active');
  });
  dropTarget.addEventListener('dragover', (e) => {
    if (!store.doc) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  });
  dropTarget.addEventListener('dragleave', (e) => {
    e.preventDefault();
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) dropTarget.classList.remove('drop-active');
  });
  dropTarget.addEventListener('drop', async (e) => {
    e.preventDefault();
    dragDepth = 0;
    dropTarget.classList.remove('drop-active');
    if (!store.doc) return;
    const files = [...(e.dataTransfer?.files || [])].filter((f) =>
      /\.(png|svg)$/i.test(f.name) || f.type === 'image/png' || f.type === 'image/svg+xml');
    if (!files.length) { flash('Suelta archivos PNG o SVG.'); return; }
    const fd = new FormData();
    for (const f of files) fd.append('files[]', f, f.name);
    fd.append('base_revision', String(store.revision));
    flash(`Importando ${files.length} archivo(s)…`);
    try {
      const res = await fetch(`${store.baseUrl}/documents/${encodeURIComponent(store.doc.id)}/assets`, {
        method: 'POST',
        body: fd,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.error?.message || `HTTP ${res.status}`);
      }
      const out = await res.json();
      store._applyDocumentPayload(out);
      if (!Object.keys(store.doc?.layers || {}).length && store.doc?.id) {
        await store.loadDocument(store.doc.id);
      }
      refreshAll();
      viewport.applyViewport();
      viewport.fitToCanvas();
      const n = Object.keys(store.doc?.layers || {}).length;
      flash(n ? `Importados ${out.imported ?? n} asset(s).` : 'Importó assets pero no hay capas.');
    } catch (err) {
      flash(`Importación fallida: ${err.message}`);
    }
  });
  $('fit-best').addEventListener('click', fitBest);
  $('fit-at-position').addEventListener('click', fitAtPosition);
  $('matrioska-stack')?.addEventListener('click', stackMatrioska);
  for (const id of ['canvas-width-mm', 'canvas-height-mm', 'canvas-padding-top',
                    'canvas-padding-right', 'canvas-padding-bottom', 'canvas-padding-left']) {
    $(id).addEventListener('change', applyCanvas);
  }
  $('recipe-select')?.addEventListener('change', () => {
    const v = $('recipe-select').value;
    // Selecting "Inversa" in export auto-applies the live inverse view.
    if (v === 'inverse_registered') {
      if ($('view-inverse')) $('view-inverse').checked = true;
      if ($('view-inverse-side')) $('view-inverse-side').checked = true;
    }
    exportDialog.updateCounter();
    applyViewMode();
  });
  bindViewToggles();

  const doUndo = () => store.undo().then(() => refreshAll()).catch((err) => flash(err.message));
  const doRedo = () => store.redo().then(() => refreshAll()).catch((err) => flash(err.message));
  $('editor-undo')?.addEventListener('click', doUndo);
  $('editor-redo')?.addEventListener('click', doRedo);

  // Undo / redo (spec §13.4): Ctrl+Z / Ctrl+Y (or Ctrl+Shift+Z).
  // Never intercept while the user is editing a text field.
  window.addEventListener('keydown', (e) => {
    const key0 = e.key.toLowerCase();
    if (!(e.ctrlKey || e.metaKey) || (key0 !== 'z' && key0 !== 'y')) return;
    const t = e.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
    if (key0 === 'z' && !e.shiftKey) {
      e.preventDefault();
      doUndo();
    } else if ((key0 === 'z' && e.shiftKey) || key0 === 'y') {
      e.preventDefault();
      doRedo();
    }
  });

  // Delete selected layer
  window.addEventListener('keydown', (e) => {
    if (e.key !== 'Delete' && e.key !== 'Backspace') return;
    const t = e.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
    if (!store.selectedId) return;
    e.preventDefault();
    tree._delete(store.selectedId);
  });

  $('auto-scale-drag')?.addEventListener('change', async (e) => {
    if (!store.selectedId) return;
    const node = store.layerById(store.selectedId);
    if (!node) return;
    try {
      await store.commitCommand('set_layer_properties', {
        layer_id: store.selectedId,
        fit: { ...node.fit, auto_scale_while_dragging: !!e.target.checked },
      });
    } catch (err) { flash(err.message); }
  });
  const main = document.querySelector('.editor-main');
  const persist = (key, val) => { try { localStorage.setItem(key, String(val)); } catch { /* ignore */ } };
  const restore = (key, fallback) => {
    let v = null;
    try { v = parseFloat(localStorage.getItem(key)); } catch { /* ignore */ }
    return Number.isFinite(v) && v >= 160 ? v : fallback;
  };
  let leftW = restore('editor.panelLeft', 300);
  let rightW = restore('editor.panelRight', 340);
  main.style.setProperty('--panel-left', `${leftW}px`);
  main.style.setProperty('--panel-right', `${rightW}px`);

  const bindResizer = (handle, side) => {
    if (!handle) return;
    handle.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      handle.setPointerCapture?.(e.pointerId);
      const startX = e.clientX;
      const startW = side === 'left' ? leftW : rightW;
      const onMove = (ev) => {
        const dx = ev.clientX - startX;
        let w = side === 'left' ? startW + dx : startW - dx;
        w = Math.min(600, Math.max(160, w));
        if (side === 'left') { leftW = w; main.style.setProperty('--panel-left', `${w}px`); }
        else { rightW = w; main.style.setProperty('--panel-right', `${w}px`); }
      };
      const onUp = () => {
        handle.removeEventListener('pointermove', onMove);
        handle.removeEventListener('pointerup', onUp);
        handle.removeEventListener('pointercancel', onUp);
        persist(side === 'left' ? 'editor.panelLeft' : 'editor.panelRight', side === 'left' ? leftW : rightW);
        // Refit canvas / 3D after column width change
        requestAnimationFrame(() => {
          viewport.fitToCanvas();
          viewer3d?.resize?.();
          viewer3d?.update?.();
        });
      };
      handle.addEventListener('pointermove', onMove);
      handle.addEventListener('pointerup', onUp);
      handle.addEventListener('pointercancel', onUp);
    });
  };
  bindResizer(document.querySelector('.panel-resizer-left'), 'left');
  bindResizer(document.querySelector('.panel-resizer-right'), 'right');

  // Selection from the viewport (click on a layer)
  svg.addEventListener('click', (e) => {
    const layerEl = e.target.closest?.('.layer');
    if (layerEl) {
      store.select(layerEl.dataset.layerId);
      tree.render();
      inspector.render(store.selectedId);
      viewport.renderSelection();
      viewer3d?.update();
      return;
    }
    // Click on empty canvas space clears selection
    if (!e.target.closest?.('[data-handle]')) {
      store.clearSelection();
      tree.render();
      inspector.render(null);
      viewport.renderSelection();
      viewer3d?.update();
    }
  });

  // Try to restore the most recent document (reopen after close)
  try {
    const res = await fetch(`${store.baseUrl}/documents`);
    if (res.ok) {
      const list = await res.json();
      const docs = Array.isArray(list) ? list : list.documents || [];
      if (docs.length) {
        await store.loadDocument(docs[docs.length - 1].id);
        enableToolbar(true);
        refreshAll();
        requestAnimationFrame(() => viewport.fitToCanvas());
        flash(`Documento restaurado: ${store.doc.name}`);
        return;
      }
    }
  } catch { /* no documents yet */ }
  enableToolbar(false);
  flash('Crea un documento nuevo o abre un proyecto.');
}

boot();
