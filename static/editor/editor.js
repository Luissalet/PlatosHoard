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
                    'matrioska-padding-enabled', 'matrioska-padding-mm', 'auto-scale-drag',
                    'marco-wall-w', 'marco-wall-h', 'marco-padding', 'marco-generate']) {
    const el = $(id);
    if (el) el.disabled = !hasDoc;
  }
  if (hasDoc && $('matrioska-mode') && !$('matrioska-mode').dataset.userTouched) {
    $('matrioska-mode').checked = true;
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
  const padding = matrioskaPaddingMm(node);
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
    if ($('view-inverse')) $('view-inverse').checked = false;
    store.matrioskaMode = true;
    refreshAll();
    flash('Matrioska: encajado en la silueta padre.');
  } catch (err) {
    flash(`Matrioska: ${err.message}`);
    refreshAll();
  }
}

/**
 * Optional clearance between child contour and parent contour (mm).
 * Global UI wins when the padding checkbox is on; else per-layer fit.padding_mm.
 */
function matrioskaPaddingMm(layer = null) {
  const enabled = !!$('matrioska-padding-enabled')?.checked;
  if (enabled) {
    const v = parseFloat($('matrioska-padding-mm')?.value);
    return Number.isFinite(v) && v >= 0 ? v : 0;
  }
  const fromLayer = Number(layer?.fit?.padding_mm);
  return Number.isFinite(fromLayer) && fromLayer >= 0 ? fromLayer : 0;
}

/**
 * Fit child inside parent using exact SVG contours (server Shapely covers).
 * Trust the server pose — client AABB/Path2D clamp can corrupt a valid fit.
 */
async function fitChildIntoParentPose(childId, _parentId) {
  const child = store.layerById(childId);
  if (!child?.parent_id) return null;

  const wanted = matrioskaPaddingMm(child);
  const attempts = [{ padding_mm: wanted, max_evaluations: 12000 }];
  if (wanted > 0.5) attempts.push({ padding_mm: wanted * 0.5, max_evaluations: 14000 });
  if (wanted > 0) attempts.push({ padding_mm: 0, max_evaluations: 18000 });

  let lastErr = 'no feasible';
  for (const opts of attempts) {
    const res = await fetch(`${store.baseUrl}/documents/${encodeURIComponent(store.doc.id)}/fit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        layer_id: childId,
        target: 'parent_shape',
        mode: 'best',
        padding_mm: opts.padding_mm,
        angles_deg: [Number(child.pose?.angle_deg) || 0],
        max_evaluations: opts.max_evaluations,
        seed: 42,
        request_seq: Date.now(),
      }),
    });
    const body = await res.json().catch(() => ({}));
    if (res.ok) {
      const pose = body.pose_local || body.pose;
      if (pose && Number.isFinite(pose.scale) && pose.scale > 0) {
        pose._used_padding_mm = opts.padding_mm;
        return pose;
      }
      lastErr = 'respuesta sin pose';
    } else {
      lastErr = body?.error?.message || `HTTP ${res.status}`;
    }
  }
  throw new Error(lastErr);
}

function layerArea(layer) {
  const asset = store.assetById(layer.asset_id);
  const lb = affine.measureLocalBounds(asset) || asset?.local_bounds;
  if (!lb) return 0;
  return Math.max(0, lb[2] - lb[0]) * Math.max(0, lb[3] - lb[1]);
}

function layerDepth(layerId) {
  let d = 0;
  let cur = store.layerById(layerId)?.parent_id;
  const seen = new Set();
  while (cur && !seen.has(cur)) {
    seen.add(cur);
    d += 1;
    cur = store.layerById(cur)?.parent_id;
  }
  return d;
}

/** Nested children in top-down order (parent before child). */
function nestedLayersTopDown() {
  return Object.values(store.doc?.layers || {})
    .filter((l) => !l.locked && l.parent_id)
    .sort((a, b) => layerDepth(a.id) - layerDepth(b.id) || (a.order ?? 0) - (b.order ?? 0));
}

/**
 * Force a single matrioska chain: largest → … → smallest.
 * Detach first so a wrong existing tree (siblings, inverted) is rebuilt.
 */
async function remountMatrioskaChain(layers) {
  const ordered = [...layers].sort((a, b) => layerArea(b) - layerArea(a));
  if (ordered.length < 2) return ordered;

  // Leaves → roots: clear parents without creating cycles mid-way.
  for (const layer of [...ordered].reverse()) {
    if (layer.parent_id) {
      await store.commitCommand('set_parent', {
        layer_id: layer.id,
        new_parent_id: null,
      });
    }
  }
  for (let i = 1; i < ordered.length; i++) {
    await store.commitCommand('set_parent', {
      layer_id: ordered[i].id,
      new_parent_id: ordered[i - 1].id,
    });
  }
  return ordered;
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
 * Matrioska = fit every nested child into its parent contour, top-down.
 */
async function applyMatrioskaFitAll() {
  if (!store.doc?.layers) return;
  let n = 0;
  for (const layer of nestedLayersTopDown()) {
    try {
      const pose = await fitChildIntoParentPose(layer.id, layer.parent_id);
      if (!pose) continue;
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
    ? `Matrioska: ${n} hijo(s) encajado(s) por contorno.`
    : 'Matrioska: no hay hijos anidados. Pulsa Apilar o suelta una capa sobre otra.');
}

/**
 * Nest all layers into a size chain (largest parent ← smaller …) and fit
 * each child into its parent contour top-down. Depth-2+ must be fitted
 * after the intermediate parent, or the grandchild stays parent-sized.
 */
async function stackMatrioska() {
  const layers = Object.values(store.doc?.layers || {}).filter((l) => !l.locked);
  if (layers.length < 2) {
    flash('Matrioska: importa al menos 2 capas.');
    return;
  }

  flash(`Apilando matrioska (${layers.length} capas)…`);
  try {
    await remountMatrioskaChain(layers);

    // Persist optional padding onto each nested child so inspector / drag match Apilar.
    const pad = matrioskaPaddingMm();
    if ($('matrioska-padding-enabled')?.checked) {
      for (const layer of nestedLayersTopDown()) {
        const fit = { ...(layer.fit || {}), padding_mm: pad, target: 'parent_shape' };
        try {
          await store.commitCommand('set_layer_properties', {
            layer_id: layer.id,
            fit,
          });
        } catch (err) {
          console.warn('padding persist', err);
        }
      }
    }

    let n = 0;
    let lastScale = null;
    const errors = [];
    // Top-down: fit mid into outer, then inner into mid, …
    for (const layer of nestedLayersTopDown()) {
      try {
        const pose = await fitChildIntoParentPose(layer.id, layer.parent_id);
        if (!pose) continue;
        await store.commitCommand('apply_fit_result', {
          layer_id: layer.id,
          pose,
          base_revision: store.revision,
        });
        n += 1;
        lastScale = pose.scale;
      } catch (err) {
        errors.push(`${layer.name || layer.id}: ${err.message}`);
      }
    }

    if ($('matrioska-mode')) $('matrioska-mode').checked = true;
    if ($('view-inverse')) $('view-inverse').checked = false;
    store.matrioskaMode = true;
    applyViewMode();
    refreshAll();
    if (n) {
      flash(`Matrioska OK: cadena de ${n + 1} — último hijo escala ${lastScale?.toFixed?.(2) ?? '?'}.`);
    } else {
      flash(`Matrioska: no cupo en el contorno${errors[0] ? ` — ${errors[0]}` : ''}.`);
    }
  } catch (err) {
    flash(`Matrioska: ${err.message}`);
    refreshAll();
  }
}

async function fitBest() { return runFit('best'); }
async function fitAtPosition() { return runFit('at_position'); }

async function generateFrame() {
  if (!store.doc) { flash('Abre o crea un documento primero.'); return; }
  const wallW = parseFloat($('marco-wall-w')?.value);
  const wallH = parseFloat($('marco-wall-h')?.value);
  const padding = parseFloat($('marco-padding')?.value);
  if (!(wallW > 0) || !(wallH > 0)) {
    flash('Ancho y altura de pared deben ser > 0 mm.');
    return;
  }
  if (!(padding >= 0) || !Number.isFinite(padding)) {
    flash('Padding del marco debe ser ≥ 0 mm.');
    return;
  }
  try {
    try {
      await store.commitCommand('generate_frame', {
        wall_w_mm: wallW,
        wall_h_mm: wallH,
        padding_mm: padding,
      });
    } catch (err) {
      // Server without generate_frame yet → build via add_layers.
      if (!/UNKNOWN_COMMAND/i.test(err.message || '')) throw err;
      await generateFrameViaAddLayers(wallW, wallH, padding);
    }
    refreshAll();
    requestAnimationFrame(() => viewport.fitToCanvas());
    flash(`Caja: ancho pared ${wallW} mm, altura ${wallH} mm, padding ${padding} mm.`);
  } catch (err) {
    flash(`Marco: ${err.message}`);
  }
}

/** Client-side caja (fondo + paredes) when server lacks `generate_frame`. */
async function generateFrameViaAddLayers(wallW, wallH, padding) {
  const c = store.doc.canvas;
  const W = Number(c.width_mm), H = Number(c.height_mm);
  // Plan: wallW equal on all 4 sides. wallH = Z extrusion of paredes only.
  const innerW = W + 2 * padding, innerH = H + 2 * padding;
  const outerW = innerW + 2 * wallW, outerH = innerH + 2 * wallW;
  const x0 = wallW, y0 = wallW, x1 = wallW + innerW, y1 = wallW + innerH;
  const dFloor = `M0,0 H${outerW} V${outerH} H0 Z`;
  const dWalls = `M0,0 H${outerW} V${outerH} H0 Z M${x0},${y0} H${x1} V${y1} H${x0} Z`;
  const svgFloor = `<svg xmlns="http://www.w3.org/2000/svg" width="${outerW}mm" height="${outerH}mm" viewBox="0 0 ${outerW} ${outerH}"><path d="${dFloor}"/></svg>`;
  const svgWalls = `<svg xmlns="http://www.w3.org/2000/svg" width="${outerW}mm" height="${outerH}mm" viewBox="0 0 ${outerW} ${outerH}"><path fill-rule="evenodd" d="${dWalls}"/></svg>`;
  const shaFloor = await sha256Hex(svgFloor);
  const shaWalls = await sha256Hex(svgWalls);
  const uid = Date.now().toString(36);
  const aidFloor = `asset_marco_fondo_${shaFloor.slice(0, 10)}`;
  const aidWalls = `asset_marco_paredes_${shaWalls.slice(0, 10)}`;
  const lidFloor = `layer_marco_fondo_${uid}`;
  const lidWalls = `layer_marco_paredes_${uid}`;
  const norm = { tx: -outerW / 2, ty: -outerH / 2, scale: 1, angle_deg: 0 };
  const lb = [-outerW / 2, -outerH / 2, outerW / 2, outerH / 2];
  const pose = { tx: W / 2, ty: H / 2, scale: 1, angle_deg: 0 };

  const old = Object.values(store.doc.layers || {}).filter(
    (n) => ['Marco', 'Marco fondo', 'Marco paredes'].includes(n.name)
      || String(n.id || '').startsWith('layer_marco_'),
  );
  for (const n of old) {
    await store.commitCommand('delete_subtree', {
      layer_id: n.id,
      confirm_descendants: 0,
    });
  }

  const mkAsset = (id, name, file, sha, svg) => ({
    id,
    name,
    source_filename: file,
    source_type: 'svg',
    source_uri: `assets/${id}/source.svg`,
    canonical_svg_uri: `assets/${id}/canonical.svg`,
    source_sha256: sha,
    source_viewbox: [0, 0, outerW, outerH],
    mm_per_source_unit: 1,
    normalization_pose: { ...norm },
    geometry_hash: sha,
    trace_settings: { kind: name === 'Marco fondo' ? 'procedural_frame_floor' : 'procedural_frame_walls' },
    curve_tolerance_source: 0.02,
    local_bounds: [...lb],
    canonical_svg: svg,
  });

  await store.commitCommand('add_layers', {
    assets: [
      mkAsset(aidFloor, 'Marco fondo', 'marco_fondo.svg', shaFloor, svgFloor),
      mkAsset(aidWalls, 'Marco paredes', 'marco_paredes.svg', shaWalls, svgWalls),
    ],
    layers: [
      { id: lidFloor, asset_id: aidFloor, name: 'Marco fondo', pose: { ...pose } },
      { id: lidWalls, asset_id: aidWalls, name: 'Marco paredes', pose: { ...pose } },
    ],
  });

  await store.commitCommand('set_layer_properties', {
    layer_id: lidWalls,
    extrusion_mm: wallH,
  });

  const allIds = Object.keys(store.doc.layers || {});
  const rest = allIds
    .filter((id) => id !== lidFloor && id !== lidWalls)
    .sort((a, b) => (store.layerById(a)?.stack_rank ?? 0) - (store.layerById(b)?.stack_rank ?? 0));
  await store.commitCommand('set_stack_order', { order: [lidFloor, lidWalls, ...rest] });
  const rootRest = store.roots().map((r) => r.id).filter((id) => id !== lidFloor && id !== lidWalls);
  await store.commitCommand('reorder_siblings', {
    parent_id: null,
    order: [lidFloor, lidWalls, ...rootRest],
  });
}

async function sha256Hex(text) {
  if (globalThis.crypto?.subtle) {
    const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
    return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
  }
  // Fallback: non-crypto hash sufficient for dedup id.
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return `fb${(h >>> 0).toString(16).padStart(8, '0')}${'0'.repeat(54)}`;
}

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
    ? 'Modo: INVERSA (1 plancha por capa)'
    : mode === 'shell'
      ? 'Modo: MATRIOSKA (solo encaje 2D; 3D independiente)'
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

/** Wire Vista panel checkboxes (Matrioska / Inversa / Fit canvas). */
function bindViewToggles() {
  const matrioska = $('matrioska-mode');
  const inverse = $('view-inverse');
  const fitCanvas = $('fit-to-canvas');

  matrioska?.addEventListener('change', async () => {
    matrioska.dataset.userTouched = '1';
    store.matrioskaMode = !!matrioska.checked;
    if (matrioska.checked) {
      if (inverse) inverse.checked = false;
      applyViewMode();
      const layers = Object.values(store.doc?.layers || {});
      if (layers.filter((l) => !l.locked).length >= 2) {
        await stackMatrioska();
      } else {
        flash('Matrioska: importa al menos 2 capas.');
        refreshAll();
      }
      return;
    }
    applyViewMode();
  });

  fitCanvas?.addEventListener('change', async () => {
    store.fitToCanvas = !!fitCanvas.checked;
    if (fitCanvas.checked) {
      await applyFitToCanvasAll();
      refreshAll();
      return;
    }
    applyViewMode();
  });

  inverse?.addEventListener('change', () => {
    if (inverse.checked) {
      if (matrioska) matrioska.checked = false;
      store.matrioskaMode = false;
    }
    applyViewMode();
  });

  store.fitToCanvas = !!fitCanvas?.checked;
  store.matrioskaMode = !!matrioska?.checked;
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
  $('marco-generate')?.addEventListener('click', generateFrame);
  for (const id of ['canvas-width-mm', 'canvas-height-mm', 'canvas-padding-top',
                    'canvas-padding-right', 'canvas-padding-bottom', 'canvas-padding-left']) {
    $(id).addEventListener('change', applyCanvas);
  }
  $('recipe-select')?.addEventListener('change', () => {
    const v = $('recipe-select').value;
    // Selecting "Inversa" in export auto-applies the live inverse view.
    if (v === 'inverse_registered') {
      if ($('view-inverse')) $('view-inverse').checked = true;
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
