// Replace the entire old loadSTL function with this one.
// Call it with loadSTL(stlB64, stlBody, data.outline_rings || []).
// Delete extractSvgOutline: a regex must not parse SVG a second time.
function loadSTL(b64, container, outlineRings = []) {
  if (loadSTL.dispose) loadSTL.dispose();
  const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
  const geometry = new THREE.STLLoader().parse(bytes.buffer);
  geometry.computeBoundingBox();
  const sourceCenter = geometry.boundingBox.getCenter(new THREE.Vector3());
  const sourceSize = geometry.boundingBox.getSize(new THREE.Vector3());
  const sourceTop = geometry.boundingBox.max.z;
  const maxDim = Math.max(sourceSize.x, sourceSize.y, sourceSize.z);
  if (!(maxDim > 0)) throw new Error('Empty STL bounds');
  const displayScale = 300 / maxDim;
  geometry.translate(-sourceCenter.x, -sourceCenter.y, -sourceCenter.z);
  geometry.scale(displayScale, displayScale, displayScale);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x1a1d24);
  const camera = new THREE.OrthographicCamera(-200, 200, 200, -200, 0.1, 10000);
  camera.position.set(0, 0, 500);
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  container.replaceChildren(renderer.domElement);
  const material = new THREE.MeshPhongMaterial({ color: 0x4f8cff, shininess: 80 });
  const mesh = new THREE.Mesh(geometry, material);
  scene.add(mesh);
  scene.add(new THREE.AmbientLight(0xffffff, 0.7));
  const light = new THREE.DirectionalLight(0xffffff, 0.8);
  light.position.set(1, 1, 2);
  scene.add(light);

  const overlay = new THREE.Group();
  for (const ring of outlineRings) {
    if (ring.length < 3) continue;
    const points = ring.slice(0, -1).map(([x, y]) => new THREE.Vector3(
      (x - sourceCenter.x) * displayScale,
      (y - sourceCenter.y) * displayScale,
      (sourceTop - sourceCenter.z) * displayScale + 0.2
    ));
    const g = new THREE.BufferGeometry().setFromPoints(points);
    const m = new THREE.LineBasicMaterial({ color: 0xff4444 });
    overlay.add(new THREE.LineLoop(g, m));
  }
  overlay.visible = false;
  mesh.add(overlay); // Same coordinates, same rotation; holes stay separate.
  const render = () => renderer.render(scene, camera);
  const resize = () => {
    const w = container.clientWidth || 600, h = 400, aspect = w / h;
    const halfH = Math.max(180, 180 / aspect);
    camera.left = -halfH * aspect; camera.right = halfH * aspect;
    camera.top = halfH; camera.bottom = -halfH;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
    render();
  };
  const observer = new ResizeObserver(resize);
  observer.observe(container);
  const listeners = [];
  const on = (target, event, handler, options) => {
    target.addEventListener(event, handler, options);
    listeners.push(() => target.removeEventListener(event, handler, options));
  };
  let dragging = false, prevX = 0, prevY = 0;
  const canvas = renderer.domElement;
  on(canvas, 'pointerdown', e => {
    dragging = true; prevX = e.clientX; prevY = e.clientY;
    canvas.setPointerCapture(e.pointerId);
  });
  on(canvas, 'pointermove', e => {
    if (!dragging) return;
    mesh.rotation.y += (e.clientX - prevX) * 0.01;
    mesh.rotation.x += (e.clientY - prevY) * 0.01;
    prevX = e.clientX; prevY = e.clientY; render();
  });
  on(canvas, 'pointerup', () => { dragging = false; });
  on(canvas, 'pointercancel', () => { dragging = false; });
  on(canvas, 'wheel', e => {
    e.preventDefault();
    camera.zoom = Math.max(0.1, Math.min(20, camera.zoom * Math.exp(-e.deltaY * 0.001)));
    camera.updateProjectionMatrix(); render();
  }, { passive: false });
  const views = { front: [0, 0], iso: [Math.PI / 6, Math.PI / 4],
                  side: [0, Math.PI / 2], top: [Math.PI / 2, 0] };
  for (const button of document.querySelectorAll('[data-view]')) {
    on(button, 'click', () => {
      const value = views[button.dataset.view];
      if (value) { mesh.rotation.set(value[0], value[1], 0); render(); }
    });
  }
  const overlayButton = document.getElementById('overlay-btn');
  overlayButton.classList.remove('active');
  overlayButton.disabled = !outlineRings.length;
  on(overlayButton, 'click', () => {
    overlay.visible = !overlay.visible;
    overlayButton.classList.toggle('active', overlay.visible);
    render();
  });
  loadSTL.dispose = () => {
    observer.disconnect();
    listeners.forEach(remove => remove());
    overlay.children.forEach(line => { line.geometry.dispose(); line.material.dispose(); });
    geometry.dispose(); material.dispose(); renderer.dispose();
  };
  resize();
}
