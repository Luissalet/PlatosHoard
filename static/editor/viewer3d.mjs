// viewer3d.mjs — 3D assembly viewer (Z-up, canvas in XY, thickness in Z)
import { store } from './store.js';
import { pathToLocalMatrix } from './affine.mjs';

function affineMatrix(p) {
  const t = p.angle_deg * Math.PI / 180;
  const c = p.scale * Math.cos(t);
  const s = p.scale * Math.sin(t);
  return [c, s, -s, c, p.tx, p.ty];
}
function matMul(A, B) {
  const [a, b, c, d, e, f] = A, [g, h, i, j, k, l] = B;
  return [a * g + c * h, b * g + d * h, a * i + c * j, b * i + d * j, a * k + c * l + e, b * k + d * l + f];
}

function _layerWorldMatrix(layerId, asset) {
  const chain = [];
  let cur = layerId;
  const seen = new Set();
  while (cur && !seen.has(cur)) {
    seen.add(cur);
    const node = store.layerById(cur);
    if (!node) break;
    chain.unshift(affineMatrix(node.pose));
    cur = node.parent_id;
  }
  let m = [1, 0, 0, 1, 0, 0];
  for (const lm of chain) m = matMul(m, lm);
  if (asset) {
    // path coords → local mm (N + viewBox origin)
    const N = pathToLocalMatrix(asset);
    m = matMul(m, N);
  }
  return m;
}

function _effectiveVisible(node) {
  let cur = node;
  const seen = new Set();
  while (cur && !seen.has(cur.id)) {
    seen.add(cur.id);
    if (!cur.visible) return false;
    cur = cur.parent_id ? store.layerById(cur.parent_id) : null;
  }
  return true;
}

function _extrusionOf(node) {
  const v = node.extrusion_mm;
  return (v === null || v === undefined) ? (store.doc?.default_extrusion_mm ?? 3) : v;
}

function _stackZ(layerId) {
  // Hierarchy stacking: each child sits on top of its parent (independent plate).
  // Sibling roots still order by stack_rank among roots only.
  const gap = store.doc?.stack_gap_mm ?? 0.4;
  const node = store.layerById(layerId);
  if (!node) return 0;

  if (node.parent_id) {
    const parent = store.layerById(node.parent_id);
    if (!parent) return 0;
    // Children of same parent: slight rank offset so they don't z-fight
    const sibs = store.childrenOf(node.parent_id);
    const idx = Math.max(0, sibs.findIndex((s) => s.id === layerId));
    return _stackZ(node.parent_id) + _extrusionOf(parent) + gap + idx * 0.05;
  }

  // Root: stack below/above other roots by stack_rank
  const roots = store.roots().slice().sort((a, b) => (a.stack_rank ?? 0) - (b.stack_rank ?? 0));
  let z = 0;
  for (const r of roots) {
    if (r.id === layerId) break;
    z += _extrusionOf(r) + gap;
  }
  return z;
}

export function mountViewer3D(container) {
  if (typeof THREE === 'undefined') {
    container.innerHTML = '<p class="empty-state">Three.js no disponible.</p>';
    return { update: () => {}, dispose: () => {} };
  }

  container.innerHTML = '';
  container.classList.add('viewer-3d-host');

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x1f2937);

  // Z-up: canvas lies on XY, thickness along +Z
  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 100000);
  camera.up.set(0, 0, 1);

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(0x1f2937, 1);
  const canvas = renderer.domElement;
  canvas.className = 'viewer-3d-canvas';
  canvas.style.display = 'block';
  canvas.style.width = '100%';
  canvas.style.height = '100%';
  container.appendChild(canvas);

  scene.add(new THREE.AmbientLight(0xffffff, 0.55));
  const dir = new THREE.DirectionalLight(0xffffff, 0.85);
  dir.position.set(1, -1.2, 2);
  scene.add(dir);
  const fill = new THREE.DirectionalLight(0xb0c4de, 0.35);
  fill.position.set(-1, 0.5, 1);
  scene.add(fill);

  // Grid in XY (rotate default XZ grid)
  let grid = new THREE.GridHelper(200, 20, 0x6b7280, 0x374151);
  grid.rotation.x = Math.PI / 2;
  scene.add(grid);

  // Canvas outline (physical sheet)
  const sheetMat = new THREE.LineBasicMaterial({ color: 0x9ca3af });
  let sheet = null;

  let controls = null;
  if (THREE.OrbitControls) {
    controls = new THREE.OrbitControls(camera, canvas);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.enablePan = true;
    controls.screenSpacePanning = true;
    controls.enableZoom = true;
    controls.enableRotate = true;
    controls.mouseButtons = {
      LEFT: THREE.MOUSE.ROTATE,
      MIDDLE: THREE.MOUSE.DOLLY,
      RIGHT: THREE.MOUSE.PAN,
    };
    canvas.addEventListener('contextmenu', (e) => e.preventDefault());
  }

  const assembly = new THREE.Group();
  scene.add(assembly);

  let _lastW = 0, _lastH = 0;
  function resize() {
    const rect = container.getBoundingClientRect();
    const w = Math.max(2, Math.floor(rect.width));
    const h = Math.max(2, Math.floor(rect.height));
    if (w === _lastW && h === _lastH) return;
    _lastW = w;
    _lastH = h;
    renderer.setSize(w, h, false);
    canvas.style.width = '100%';
    canvas.style.height = '100%';
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }

  const ro = (typeof ResizeObserver !== 'undefined')
    ? new ResizeObserver(() => { resize(); })
    : null;
  if (ro) ro.observe(container);
  window.addEventListener('resize', resize);
  // Layout may settle after first paint
  requestAnimationFrame(() => { resize(); frameStable(); });
  setTimeout(() => { resize(); frameStable(); }, 100);

  function syncSheetAndGrid() {
    const c = store.doc?.canvas;
    const W = c ? c.width_mm : 200;
    const H = c ? c.height_mm : 200;
    if (sheet) scene.remove(sheet);
    const pts = [
      new THREE.Vector3(0, 0, 0),
      new THREE.Vector3(W, 0, 0),
      new THREE.Vector3(W, H, 0),
      new THREE.Vector3(0, H, 0),
      new THREE.Vector3(0, 0, 0),
    ];
    sheet = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), sheetMat);
    scene.add(sheet);

    scene.remove(grid);
    const gSize = Math.max(W, H) * 1.15;
    const divs = 20;
    grid = new THREE.GridHelper(gSize, divs, 0x6b7280, 0x374151);
    grid.rotation.x = Math.PI / 2;
    grid.position.set(W / 2, H / 2, -0.05);
    scene.add(grid);
  }

  function frameStable() {
    resize();
    const c = store.doc?.canvas;
    const W = c ? c.width_mm : 200;
    const H = c ? c.height_mm : 200;
    let maxZ = 3;
    for (const l of Object.values(store.doc?.layers || {})) {
      if (_effectiveVisible(l)) maxZ = Math.max(maxZ, _stackZ(l.id) + _extrusionOf(l));
    }
    const cx = W / 2;
    const cy = H / 2;
    const span = Math.max(W, H, maxZ * 4);
    const dist = span * 1.6;
    camera.position.set(cx + dist * 0.55, cy - dist * 0.7, maxZ + dist * 0.65);
    camera.lookAt(cx, cy, maxZ * 0.35);
    if (controls) {
      controls.target.set(cx, cy, maxZ * 0.35);
      controls.update();
    }
  }

  function _svgPaths(asset) {
    if (!asset?.canonical_svg) return [];
    const parsed = new DOMParser().parseFromString(asset.canonical_svg, 'image/svg+xml');
    return [...parsed.querySelectorAll('path')].map(p => p.getAttribute('d')).filter(Boolean);
  }

  /**
   * Parse one SVG `d` into contour Paths (full curves — no sampling).
   * Each M/m starts a new contour (never lineTo across subpaths).
   */
  function _parseContours(d) {
    const tokens = String(d).match(/[a-df-z]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?/gi) || [];
    const contours = [];
    let i = 0;
    const num = () => parseFloat(tokens[i++]);
    let path = null;
    let cx = 0, cy = 0, sx = 0, sy = 0;

    const ensure = () => {
      if (!path) {
        path = new THREE.Path();
        contours.push(path);
      }
    };
    const startContour = (x, y) => {
      path = new THREE.Path();
      contours.push(path);
      path.moveTo(x, y);
      cx = x; cy = y; sx = x; sy = y;
    };

    try {
      while (i < tokens.length) {
        const cmd = tokens[i++];
        switch (cmd) {
          case 'M': {
            startContour(num(), num());
            // Implicit lineTos after first pair
            while (i < tokens.length && !/[a-zA-Z]/.test(tokens[i])) {
              cx = num(); cy = num();
              path.lineTo(cx, cy);
            }
            break;
          }
          case 'm': {
            startContour(cx + num(), cy + num());
            while (i < tokens.length && !/[a-zA-Z]/.test(tokens[i])) {
              cx += num(); cy += num();
              path.lineTo(cx, cy);
            }
            break;
          }
          case 'L': ensure(); cx = num(); cy = num(); path.lineTo(cx, cy); break;
          case 'l': ensure(); cx += num(); cy += num(); path.lineTo(cx, cy); break;
          case 'H': ensure(); cx = num(); path.lineTo(cx, cy); break;
          case 'h': ensure(); cx += num(); path.lineTo(cx, cy); break;
          case 'V': ensure(); cy = num(); path.lineTo(cx, cy); break;
          case 'v': ensure(); cy += num(); path.lineTo(cx, cy); break;
          case 'C': {
            ensure();
            const x1 = num(), y1 = num(), x2 = num(), y2 = num(), x = num(), y = num();
            path.bezierCurveTo(x1, y1, x2, y2, x, y); cx = x; cy = y; break;
          }
          case 'c': {
            ensure();
            const x1 = cx + num(), y1 = cy + num(), x2 = cx + num(), y2 = cy + num(), x = cx + num(), y = cy + num();
            path.bezierCurveTo(x1, y1, x2, y2, x, y); cx = x; cy = y; break;
          }
          case 'S': {
            ensure();
            const x2 = num(), y2 = num(), x = num(), y = num();
            path.bezierCurveTo(cx, cy, x2, y2, x, y); cx = x; cy = y; break;
          }
          case 's': {
            ensure();
            const x2 = cx + num(), y2 = cy + num(), x = cx + num(), y = cy + num();
            path.bezierCurveTo(cx, cy, x2, y2, x, y); cx = x; cy = y; break;
          }
          case 'Q': {
            ensure();
            const x1 = num(), y1 = num(), x = num(), y = num();
            path.quadraticCurveTo(x1, y1, x, y); cx = x; cy = y; break;
          }
          case 'q': {
            ensure();
            const x1 = cx + num(), y1 = cy + num(), x = cx + num(), y = cy + num();
            path.quadraticCurveTo(x1, y1, x, y); cx = x; cy = y; break;
          }
          case 'T': {
            ensure();
            const x = num(), y = num();
            path.quadraticCurveTo(cx, cy, x, y); cx = x; cy = y; break;
          }
          case 't': {
            ensure();
            const x = cx + num(), y = cy + num();
            path.quadraticCurveTo(cx, cy, x, y); cx = x; cy = y; break;
          }
          case 'A': {
            // Approximate arc with cubic beziers via endpoint sample — keep density high
            ensure();
            num(); num(); num(); num(); num();
            const x = num(), y = num();
            path.lineTo(x, y); cx = x; cy = y; break;
          }
          case 'a': {
            ensure();
            num(); num(); num(); num(); num();
            const x = cx + num(), y = cy + num();
            path.lineTo(x, y); cx = x; cy = y; break;
          }
          case 'Z': case 'z':
            if (path) { path.closePath(); cx = sx; cy = sy; }
            break;
          default:
            return [];
        }
      }
    } catch {
      return [];
    }
    return contours.filter((p) => p.curves && p.curves.length > 0);
  }

  function _contourArea(path) {
    // Classification only — uses a fine polyline; extrusion keeps original curves.
    const pts = path.getPoints(24);
    let a = 0;
    for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
      a += pts[j].x * pts[i].y - pts[i].x * pts[j].y;
    }
    return a * 0.5;
  }

  function _contourCentroid(path) {
    const pts = path.getPoints(24);
    let cx = 0, cy = 0;
    for (const p of pts) { cx += p.x; cy += p.y; }
    return { x: cx / pts.length, y: cy / pts.length };
  }

  function _pathContains(path, x, y) {
    const pts = path.getPoints(24);
    let inside = false;
    for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
      const xi = pts[i].x, yi = pts[i].y;
      const xj = pts[j].x, yj = pts[j].y;
      const hit = ((yi > y) !== (yj > y))
        && (x < ((xj - xi) * (y - yi)) / ((yj - yi) || 1e-12) + xi);
      if (hit) inside = !inside;
    }
    return inside;
  }

  /** Full-fidelity THREE.Shapes from SVG path `d`s (curves preserved). */
  function _shapesFromSvgPaths(pathDs) {
    const contours = [];
    for (const d of pathDs) {
      for (const path of _parseContours(d)) {
        const area = _contourArea(path);
        const c = _contourCentroid(path);
        contours.push({ path, area: Math.abs(area), signed: area, cx: c.x, cy: c.y });
      }
    }
    if (!contours.length) return [];
    contours.sort((a, b) => b.area - a.area);

    const solids = [];
    for (const c of contours) {
      let parent = null;
      for (const s of solids) {
        if (_pathContains(s.path, c.cx, c.cy) && c.area < s.area * 0.98) {
          parent = s;
          break;
        }
      }
      if (parent) {
        parent.shape.holes.push(c.path);
      } else {
        const shape = new THREE.Shape();
        // Copy curves from Path into Shape
        shape.curves = c.path.curves.slice();
        shape.currentPoint.copy(c.path.currentPoint);
        solids.push({ shape, path: c.path, area: c.area });
      }
    }
    return solids.map((s) => s.shape);
  }

  function _layerColor(layerId) {
    // Stable colour matching 2D (do not recolour selection — that looked like a blue rim).
    const ids = Object.keys(store.doc?.layers || {});
    const i = Math.max(0, ids.indexOf(layerId));
    const hue = (i * 137.508) % 360;
    const h = hue / 360, s = 0.55, l = 0.5;
    const aa = s * Math.min(l, 1 - l);
    const fn = (n) => {
      const k = (n + h * 12) % 12;
      return l - aa * Math.max(Math.min(k - 3, 9 - k, 1), -1);
    };
    const r = Math.round(fn(0) * 255), g = Math.round(fn(8) * 255), bl = Math.round(fn(4) * 255);
    return (r << 16) | (g << 8) | bl;
  }

  /** Affine-transform a Path/Shape keeping every curve control point (no resampling). */
  function transformPathAffine(src, m) {
    const [a, b, c, d, e, f] = m;
    const map = (p) => new THREE.Vector2(a * p.x + c * p.y + e, b * p.x + d * p.y + f);
    const out = src.isShape || src.type === 'Shape' ? new THREE.Shape() : new THREE.Path();

    // Replay curves with transformed control points
    let started = false;
    const moveTo = (p) => {
      const q = map(p);
      out.moveTo(q.x, q.y);
      started = true;
    };
    for (const curve of src.curves || []) {
      if (curve.type === 'LineCurve') {
        if (!started) moveTo(curve.v1);
        const q = map(curve.v2);
        out.lineTo(q.x, q.y);
      } else if (curve.type === 'CubicBezierCurve') {
        if (!started) moveTo(curve.v0);
        const c1 = map(curve.v1), c2 = map(curve.v2), c3 = map(curve.v3);
        out.bezierCurveTo(c1.x, c1.y, c2.x, c2.y, c3.x, c3.y);
      } else if (curve.type === 'QuadraticBezierCurve') {
        if (!started) moveTo(curve.v0);
        const c1 = map(curve.v1), c2 = map(curve.v2);
        out.quadraticCurveTo(c1.x, c1.y, c2.x, c2.y);
      } else {
        // Fallback: densify this curve only
        const pts = curve.getPoints(16);
        for (let i = 0; i < pts.length; i++) {
          const q = map(pts[i]);
          if (!started) { out.moveTo(q.x, q.y); started = true; }
          else out.lineTo(q.x, q.y);
        }
      }
    }
    if (!started) return null;

    // Transform holes recursively
    if (src.holes && src.holes.length) {
      out.holes = [];
      for (const hole of src.holes) {
        const th = transformPathAffine(hole, m);
        if (th) out.holes.push(th);
      }
    }
    return out;
  }

  function _worldShapes(layerId) {
    const node = store.layerById(layerId);
    const asset = store.assetById(node?.asset_id);
    if (!asset) return [];
    const m = _layerWorldMatrix(layerId, asset);
    const locals = _shapesFromSvgPaths(_svgPaths(asset));
    const out = [];
    for (const local of locals) {
      const world = transformPathAffine(local, m);
      if (world) out.push(world);
    }
    return out;
  }

  /** Document-space geometry → manufacturing mesh (Y flip). */
  function _meshFromDocShapes(shapes, depth, z0, color, name) {
    if (!shapes?.length) return null;
    const canvasH = store.doc?.canvas?.height_mm ?? 200;
    const geo = new THREE.ExtrudeGeometry(shapes, {
      depth, bevelEnabled: false, steps: 1, curveSegments: 24,
    });
    const mat4 = new THREE.Matrix4().set(
      1, 0, 0, 0,
      0, -1, 0, canvasH,
      0, 0, 1, z0,
      0, 0, 0, 1,
    );
    geo.applyMatrix4(mat4);
    const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({
      color, roughness: 0.65, metalness: 0.08, side: THREE.DoubleSide,
    }));
    mesh.name = name || '';
    return mesh;
  }

  function buildLayerMesh(layerId) {
    const node = store.layerById(layerId);
    const asset = store.assetById(node.asset_id);
    if (!asset?.canonical_svg) return null;

    const paths = _svgPaths(asset);
    if (!paths.length) return null;

    const shapes = _shapesFromSvgPaths(paths);
    if (!shapes.length) return null;

    const m = _layerWorldMatrix(layerId, asset);
    const canvasH = store.doc?.canvas?.height_mm ?? 200;
    const t = _extrusionOf(node);
    const z0 = _stackZ(layerId);

    let geo;
    try {
      geo = new THREE.ExtrudeGeometry(shapes, {
        depth: t,
        bevelEnabled: false,
        steps: 1,
        curveSegments: 24,
      });
    } catch (err) {
      console.warn('extrude failed', layerId, err);
      return null;
    }

    // SVG affine [a b c d e f]: x' = a x + c y + e, y' = b x + d y + f
    // Manufacturing: Y_mfg = H − y_doc. Thickness stays +Z.
    const [a, b, c, d, e, f] = m;
    const mat = new THREE.Matrix4().set(
      a,  c, 0, e,
      -b, -d, 0, canvasH - f,
      0,  0, 1, z0,
      0,  0, 0, 1,
    );
    geo.applyMatrix4(mat);

    const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({
      color: _layerColor(layerId), roughness: 0.65, metalness: 0.08, side: THREE.DoubleSide,
    }));
    mesh.name = layerId;
    return mesh;
  }

  /** Canvas plate with holes for layer (+ descendants). */
  function buildInverseMesh(layerId) {
    const node = store.layerById(layerId);
    if (!node || !_effectiveVisible(node)) return null;
    const c = store.doc?.canvas;
    if (!c) return null;
    const W = c.width_mm, H = c.height_mm;
    const plate = new THREE.Shape();
    plate.moveTo(0, 0);
    plate.lineTo(W, 0);
    plate.lineTo(W, H);
    plate.lineTo(0, H);
    plate.closePath();

    const subtree = store.subtreeIds(layerId);
    for (const sid of subtree) {
      const sn = store.layerById(sid);
      if (!sn || !_effectiveVisible(sn)) continue;
      for (const hole of _worldShapes(sid)) {
        plate.holes.push(hole);
      }
    }
    if (!plate.holes.length) return null;

    try {
      const t = _extrusionOf(node);
      const z0 = _stackZ(layerId);
      return _meshFromDocShapes([plate], t, z0, _layerColor(layerId), `inv-${layerId}`);
    } catch (err) {
      console.warn('inverse mesh failed', layerId, err);
      return null;
    }
  }

  /** Parent silhouette with child silhouettes cut out. */
  function buildShellMesh(layerId) {
    const node = store.layerById(layerId);
    const asset = store.assetById(node?.asset_id);
    if (!asset?.canonical_svg) return null;

    const children = store.childrenOf(layerId).filter(
      (ch) => _effectiveVisible(ch) && store.assetById(ch.asset_id)?.canonical_svg,
    );
    if (!children.length) return buildLayerMesh(layerId);

    const parentShapes = _worldShapes(layerId);
    if (!parentShapes.length) return null;

    const holeShapes = [];
    for (const ch of children) {
      for (const h of _worldShapes(ch.id)) holeShapes.push(h);
    }

    // Attach all child holes to the first (outer) parent contour; extrude remaining
    // parent contours as solids (rare multi-path assets).
    const outer = parentShapes[0];
    for (const h of holeShapes) outer.holes.push(h);
    const shapes = [outer, ...parentShapes.slice(1)];
    const t = _extrusionOf(node);
    const z0 = _stackZ(layerId);
    return _meshFromDocShapes(shapes, t, z0, _layerColor(layerId), `shell-${layerId}`);
  }

  function update() {
    resize();
    while (assembly.children.length) {
      const ch = assembly.children[0];
      assembly.remove(ch);
      ch.geometry?.dispose?.();
      ch.material?.dispose?.();
    }
    syncSheetAndGrid();
    if (!store.doc) { frameStable(); return; }

    const mode = store.viewMode || 'normal';

    if (mode === 'inverse') {
      let targets = store.selectedId ? [store.selectedId] : store.roots().map((r) => r.id);
      if (!targets.length) targets = Object.keys(store.doc.layers || {});
      for (const layerId of targets) {
        const mesh = buildInverseMesh(layerId);
        if (mesh) assembly.add(mesh);
      }
      // Nested children as solid plates stacked above
      for (const layerId of targets) {
        const addDesc = (id) => {
          for (const ch of store.childrenOf(id)) {
            if (_effectiveVisible(ch)) {
              const m = buildLayerMesh(ch.id);
              if (m) {
                if (ch.id === store.selectedId) {
                  m.material.emissive = new THREE.Color(0x1d4ed8);
                  m.material.emissiveIntensity = 0.25;
                }
                assembly.add(m);
              }
            }
            addDesc(ch.id);
          }
        };
        addDesc(layerId);
      }
    } else {
      // Matrioska / normal: every layer is an independent solid plate, stacked in Z.
      // No shell holes — those looked like a rim and hid child colour.
      const visit = (parentId) => {
        for (const child of store.childrenOf(parentId)) {
          const node = store.layerById(child.id);
          if (node && _effectiveVisible(node)) {
            const mesh = buildLayerMesh(child.id);
            if (mesh) {
              if (child.id === store.selectedId) {
                mesh.material.emissive = new THREE.Color(0x1d4ed8);
                mesh.material.emissiveIntensity = 0.25;
              }
              assembly.add(mesh);
            }
          }
          visit(child.id);
        }
      };
      visit(null);
    }
    frameStable();
  }

  window.addEventListener('editor:pose-changed', update);
  window.addEventListener('editor:selection', update);
  window.addEventListener('editor:doc-changed', update);

  let raf = null;
  function loop() {
    raf = requestAnimationFrame(loop);
    resize();
    if (controls) controls.update();
    renderer.render(scene, camera);
  }
  loop();
  update();

  return {
    update,
    resize,
    dispose() {
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', resize);
      ro?.disconnect();
      renderer.dispose();
      container.innerHTML = '';
    },
  };
}
