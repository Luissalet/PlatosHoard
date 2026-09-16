// SVG six-value affine convention: [a,b,c,d,e,f]. Y points down in 2D.
export const identity = () => [1,0,0,1,0,0];
export function matrix(p) {
  if (![p.tx,p.ty,p.scale,p.angle_deg].every(Number.isFinite) || p.scale<=0) throw new Error('INVALID_POSE');
  const t=p.angle_deg*Math.PI/180, c=p.scale*Math.cos(t), s=p.scale*Math.sin(t);
  return [c,s,-s,c,p.tx,p.ty];
}
export function multiply(A,B) {
  const [a,b,c,d,e,f]=A, [g,h,i,j,k,l]=B;
  return [a*g+c*h,b*g+d*h,a*i+c*j,b*i+d*j,a*k+c*l+e,b*k+d*l+f];
}
export function inverse(M) {
  const [a,b,c,d,e,f]=M, det=a*d-b*c;
  if (!Number.isFinite(det)||Math.abs(det)<1e-18) throw new Error('SINGULAR_MATRIX');
  return [d/det,-b/det,-c/det,a/det,(c*f-d*e)/det,(b*e-a*f)/det];
}
export function point(M,p) {
  const [a,b,c,d,e,f]=M;
  return {x:a*p.x+c*p.y+e,y:b*p.x+d*p.y+f};
}
export function dragPose(startPose,parentWorld,startWorld,nowWorld) {
  // Convert both pointer positions into the SAME parent frame captured on pointerdown.
  const inv=inverse(parentWorld), p0=point(inv,startWorld), p1=point(inv,nowWorld);
  return {...startPose,tx:startPose.tx+p1.x-p0.x,ty:startPose.ty+p1.y-p0.y};
}
export function scaleOppositeFixed(startPose,parentWorld,oppositeLocal,draggedLocal,pointerWorld,minScale=1e-6) {
  const P=point(inverse(parentWorld),pointerWorld);
  const A=point(matrix(startPose),oppositeLocal);
  const R=matrix({tx:0,ty:0,scale:1,angle_deg:startPose.angle_deg});
  const v=point(R,{x:draggedLocal.x-oppositeLocal.x,y:draggedLocal.y-oppositeLocal.y});
  const n=v.x*v.x+v.y*v.y;
  if(n<=0) throw new Error('ZERO_SIZED_HANDLE');
  const scale=Math.max(minScale,((P.x-A.x)*v.x+(P.y-A.y)*v.y)/n);
  const anchor=point(R,oppositeLocal);
  return {...startPose,scale,tx:A.x-scale*anchor.x,ty:A.y-scale*anchor.y};
}
export function clientToDocument(svgRoot,event) {
  const m=svgRoot.getScreenCTM();
  if(!m) throw new Error('SVG_NOT_MOUNTED');
  const p=new DOMPoint(event.clientX,event.clientY).matrixTransform(m.inverse());
  return {x:p.x,y:p.y};
}
export function acceptPreview(response,current) {
  return response.project_revision===current.project_revision &&
         response.request_seq===current.request_seq &&
         response.layer_id===current.layer_id;
}
export function frustum(worldWidth,worldHeight,viewportWidth,viewportHeight,margin=1.1) {
  if(![worldWidth,worldHeight,viewportWidth,viewportHeight,margin].every(x=>Number.isFinite(x)&&x>0)) throw new Error('INVALID_VIEWPORT');
  const aspect=viewportWidth/viewportHeight;
  const h=Math.max(worldHeight,worldWidth/aspect)*margin, w=h*aspect;
  return {left:-w/2,right:w/2,top:h/2,bottom:-h/2};
}

/**
 * Max uniform scale + centre so local_bounds fill the usable canvas (with padding).
 * local_bounds: [x0,y0,x1,y1] in local mm (centred at origin).
 * Ignores rotation for the closed-form size (uses AABB of rotated box if angle≠0).
 */
export function fitPoseToRect(localBounds, rect, angleDeg = 0) {
  const [x0, y0, x1, y1] = localBounds;
  const lw = x1 - x0, lh = y1 - y0;
  if (!(lw > 0) || !(lh > 0)) throw new Error('EMPTY_BOUNDS');
  const { left, top, right, bottom } = rect;
  const rw = right - left, rh = bottom - top;
  if (!(rw > 0) || !(rh > 0)) throw new Error('EMPTY_RECT');

  const a = (angleDeg || 0) * Math.PI / 180;
  const c = Math.cos(a), s = Math.sin(a);
  // AABB of the unit local box after rotation (scale=1, centred)
  const corners = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const [x, y] of corners) {
    const rx = c * x - s * y;
    const ry = s * x + c * y;
    if (rx < minX) minX = rx; if (rx > maxX) maxX = rx;
    if (ry < minY) minY = ry; if (ry > maxY) maxY = ry;
  }
  const bw = maxX - minX, bh = maxY - minY;
  const scale = Math.min(rw / bw, rh / bh);
  const cx = (left + right) / 2;
  const cy = (top + bottom) / 2;
  // After scale+rotate, local origin maps to (tx,ty). Centre of AABB in local
  // rotated frame is ((minX+maxX)/2, (minY+maxY)/2) * scale from origin.
  const ox = ((minX + maxX) / 2) * scale;
  const oy = ((minY + maxY) / 2) * scale;
  return {
    tx: cx - ox,
    ty: cy - oy,
    scale,
    angle_deg: angleDeg || 0,
  };
}

/**
 * Clamp a root pose so the rotated local AABB stays inside rect.
 * If the shape is larger than the rect, shrink scale to the max that fits.
 */
export function clampPoseToRect(pose, localBounds, rect) {
  let p = { ...pose };
  const [x0, y0, x1, y1] = localBounds;
  const a = (p.angle_deg || 0) * Math.PI / 180;
  const c = Math.cos(a), s = Math.sin(a);
  const corners = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];

  const aabbAt = (scale, tx, ty) => {
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const [x, y] of corners) {
      const rx = scale * (c * x - s * y) + tx;
      const ry = scale * (s * x + c * y) + ty;
      if (rx < minX) minX = rx; if (rx > maxX) maxX = rx;
      if (ry < minY) minY = ry; if (ry > maxY) maxY = ry;
    }
    return { minX, maxX, minY, maxY, w: maxX - minX, h: maxY - minY };
  };

  const { left, top, right, bottom } = rect;
  const rw = right - left, rh = bottom - top;
  let box = aabbAt(p.scale, p.tx, p.ty);
  if (box.w > rw + 1e-9 || box.h > rh + 1e-9) {
    const maxScale = p.scale * Math.min(rw / box.w, rh / box.h);
    p = { ...p, scale: Math.max(1e-6, maxScale) };
    box = aabbAt(p.scale, p.tx, p.ty);
  }
  let dx = 0, dy = 0;
  if (box.minX < left) dx = left - box.minX;
  else if (box.maxX > right) dx = right - box.maxX;
  if (box.minY < top) dy = top - box.minY;
  else if (box.maxY > bottom) dy = bottom - box.maxY;
  // Re-check after shift (corner case: both sides oversized already handled by scale)
  if (dx || dy) {
    p = { ...p, tx: p.tx + dx, ty: p.ty + dy };
    box = aabbAt(p.scale, p.tx, p.ty);
    dx = 0; dy = 0;
    if (box.minX < left) dx = left - box.minX;
    else if (box.maxX > right) dx = right - box.maxX;
    if (box.minY < top) dy = top - box.minY;
    else if (box.maxY > bottom) dy = bottom - box.maxY;
    p = { ...p, tx: p.tx + dx, ty: p.ty + dy };
  }
  return p;
}

/** Usable rect inside a parent silhouette (local mm), with padding inset. */
export function parentFitRect(parentLocalBounds, paddingMm = 2) {
  const [x0, y0, x1, y1] = parentLocalBounds;
  const pad = Math.max(0, Number(paddingMm) || 0);
  return {
    left: x0 + pad,
    top: y0 + pad,
    right: x1 - pad,
    bottom: y1 - pad,
  };
}

/** Usable canvas rect from document canvas + padding_mm. */
export function usableCanvasRect(canvas) {
  const p = canvas.padding_mm || {};
  return {
    left: Number(p.left) || 0,
    top: Number(p.top) || 0,
    right: canvas.width_mm - (Number(p.right) || 0),
    bottom: canvas.height_mm - (Number(p.bottom) || 0),
  };
}

/**
 * VTracer (and some SVGs) use a padded viewBox origin. Server polygon bounds
 * subtract that origin once; raw path `d` coords do not. Compose the missing
 * translate so path → local mm matches local_bounds / selection.
 */
export function viewBoxOrigin(svgText) {
  if (!svgText) return { x: 0, y: 0 };
  const m = String(svgText).match(/viewBox\s*=\s*["']?\s*([-\d.eE+]+)\s+([-\d.eE+]+)/);
  if (!m) return { x: 0, y: 0 };
  const x = parseFloat(m[1]), y = parseFloat(m[2]);
  return {
    x: Number.isFinite(x) ? x : 0,
    y: Number.isFinite(y) ? y : 0,
  };
}

/** Source SVG path coords → local mm (centred), including viewBox correction. */
export function pathToLocalMatrix(asset) {
  if (!asset?.normalization_pose) return identity();
  let N = matrix(asset.normalization_pose);
  const o = viewBoxOrigin(asset.canonical_svg);
  if (o.x !== 0 || o.y !== 0) {
    // poly = path - (vx,vy); N was built for poly-space.
    N = multiply(N, matrix({ tx: -o.x, ty: -o.y, scale: 1, angle_deg: 0 }));
  }
  return N;
}

/** Transform axis-aligned [x0,y0,x1,y1] by SVG affine matrix → new AABB. */
export function transformBounds(bounds, M) {
  const [x0, y0, x1, y1] = bounds;
  const pts = [
    point(M, { x: x0, y: y0 }),
    point(M, { x: x1, y: y0 }),
    point(M, { x: x1, y: y1 }),
    point(M, { x: x0, y: y1 }),
  ];
  const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

/**
 * Local-mm AABB of the *rendered* silhouette (path coords × pathToLocalMatrix).
 * Falls back to asset.local_bounds if measurement fails.
 */
export function measureLocalBounds(asset) {
  if (!asset) return null;
  const fallback = asset.local_bounds || null;
  if (!asset.canonical_svg || typeof document === 'undefined') return fallback;
  try {
    const parsed = new DOMParser().parseFromString(asset.canonical_svg, 'image/svg+xml');
    const paths = [...parsed.querySelectorAll('path')];
    if (!paths.length) return fallback;

    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('width', '0');
    svg.setAttribute('height', '0');
    svg.style.cssText = 'position:absolute;left:-9999px;visibility:hidden';
    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    for (const p of paths) {
      const np = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      np.setAttribute('d', p.getAttribute('d') || '');
      g.appendChild(np);
    }
    svg.appendChild(g);
    document.body.appendChild(svg);
    let bb;
    try {
      bb = g.getBBox();
    } finally {
      svg.remove();
    }
    if (!(bb.width > 0) || !(bb.height > 0)) return fallback;

    const N = pathToLocalMatrix(asset);
    return transformBounds([bb.x, bb.y, bb.x + bb.width, bb.y + bb.height], N);
  } catch {
    return fallback;
  }
}

/** `d` attributes from canonical SVG paths. */
export function extractPathDs(svgText) {
  if (!svgText || typeof DOMParser === 'undefined') return [];
  try {
    const doc = new DOMParser().parseFromString(svgText, 'image/svg+xml');
    return [...doc.querySelectorAll('path')]
      .map((p) => p.getAttribute('d') || '')
      .filter(Boolean);
  } catch {
    return [];
  }
}

/**
 * Dense outline samples in local mm (path → N). Used for silhouette containment.
 */
export function sampleLocalOutline(asset, samplesPerPath = 80) {
  if (!asset?.canonical_svg || typeof document === 'undefined') return [];
  const ds = extractPathDs(asset.canonical_svg);
  if (!ds.length) return [];
  const N = pathToLocalMatrix(asset);
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('width', '0');
  svg.setAttribute('height', '0');
  svg.style.cssText = 'position:absolute;left:-9999px;visibility:hidden';
  document.body.appendChild(svg);
  const pts = [];
  try {
    for (const d of ds) {
      const el = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      el.setAttribute('d', d);
      svg.appendChild(el);
      const len = el.getTotalLength();
      if (!(len > 0)) continue;
      const n = Math.max(24, Math.min(samplesPerPath, Math.ceil(len / 1.5)));
      for (let i = 0; i < n; i++) {
        const sp = el.getPointAtLength((i / n) * len);
        pts.push(point(N, { x: sp.x, y: sp.y }));
      }
    }
  } finally {
    svg.remove();
  }
  return pts;
}

/** Parent silhouette Path2D in *source* SVG units + inverse of N (local→source). */
export function localPath2D(asset) {
  if (!asset?.canonical_svg || typeof Path2D === 'undefined') return null;
  const ds = extractPathDs(asset.canonical_svg);
  if (!ds.length) return null;
  const out = new Path2D();
  for (const dAttr of ds) out.addPath(new Path2D(dAttr));
  return { path: out, localToSource: inverse(pathToLocalMatrix(asset)) };
}

let _hitCtx = null;
function hitCtx() {
  if (_hitCtx) return _hitCtx;
  if (typeof document === 'undefined') return null;
  const c = document.createElement('canvas');
  c.width = 1;
  c.height = 1;
  _hitCtx = c.getContext('2d');
  return _hitCtx;
}

/**
 * True iff every (optionally radially padded) child outline sample lies inside
 * the parent SVG contour. Child samples are local-mm; pose maps them to parent-local.
 */
export function poseInsideParentSilhouette(childLocalPts, pose, parentHit, paddingMm = 0) {
  const ctx = hitCtx();
  if (!ctx || !parentHit?.path || !childLocalPts?.length) return true;
  const M = matrix(pose);
  const pad = Math.max(0, Number(paddingMm) || 0);
  const cx = pose.tx, cy = pose.ty;
  const toSrc = parentHit.localToSource;
  for (const lp of childLocalPts) {
    let pp = point(M, lp);
    if (pad > 0) {
      const dx = pp.x - cx, dy = pp.y - cy;
      const r = Math.hypot(dx, dy);
      if (r > 1e-9) {
        pp = { x: pp.x + (dx / r) * pad, y: pp.y + (dy / r) * pad };
      }
    }
    const sp = point(toSrc, pp);
    if (!ctx.isPointInPath(parentHit.path, sp.x, sp.y)) return false;
  }
  return true;
}

/**
 * Clamp pose so the child SVG contour stays inside the parent SVG contour.
 * Shrinks about (tx,ty), then walks toward the parent centroid if needed.
 * This is contour containment — not AABB.
 */
export function clampPoseInsideSilhouette(childAsset, parentAsset, pose, paddingMm = 1) {
  const childPts = sampleLocalOutline(childAsset, 96);
  const parentPath = localPath2D(parentAsset);
  if (!childPts.length || !parentPath) return { ...pose };

  let p = {
    tx: Number(pose.tx) || 0,
    ty: Number(pose.ty) || 0,
    scale: Math.max(1e-6, Number(pose.scale) || 1),
    angle_deg: Number(pose.angle_deg) || 0,
  };
  if (poseInsideParentSilhouette(childPts, p, parentPath, paddingMm)) return p;

  const parentPts = sampleLocalOutline(parentAsset, 48);
  let gx = 0, gy = 0;
  if (parentPts.length) {
    for (const q of parentPts) { gx += q.x; gy += q.y; }
    gx /= parentPts.length;
    gy /= parentPts.length;
  }

  const searchAt = (tx, ty, maxScale) => {
    let lo = 1e-6, hi = Math.max(1e-6, maxScale), best = null;
    for (let i = 0; i < 28; i++) {
      const mid = (lo + hi) / 2;
      const cand = { tx, ty, scale: mid, angle_deg: p.angle_deg };
      if (poseInsideParentSilhouette(childPts, cand, parentPath, paddingMm)) {
        best = cand;
        lo = mid;
      } else {
        hi = mid;
      }
    }
    return best;
  };

  let best = searchAt(p.tx, p.ty, p.scale);
  if (best) return best;

  // Walk center toward parent centroid while shrinking.
  for (let step = 1; step <= 12; step++) {
    const t = step / 12;
    const tx = p.tx + (gx - p.tx) * t;
    const ty = p.ty + (gy - p.ty) * t;
    best = searchAt(tx, ty, p.scale * (1 - 0.5 * t));
    if (best) return best;
  }

  // Last resort: tiny at centroid.
  best = searchAt(gx, gy, p.scale);
  return best || { tx: gx, ty: gy, scale: Math.max(1e-6, p.scale * 0.05), angle_deg: p.angle_deg };
}
