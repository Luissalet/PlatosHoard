"""
Silhouette 3D — Flask web UI.

Upload a PNG, watch it become a black silhouette, a vector SVG, and an
extruded 3D mesh (STL). The 3D preview uses Three.js in the browser.

Run:
    python app.py
Then open http://localhost:5000
"""

import os
import io
import base64
from flask import Flask, request, jsonify, render_template_string

from pipeline import process_image

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB upload limit


HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Silhouette 3D</title>
<style>
  :root {
    --bg: #0f1115;
    --panel: #1a1d24;
    --border: #2a2e38;
    --text: #e4e6eb;
    --muted: #8b8f9a;
    --accent: #4f8cff;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    padding: 24px;
  }
  h1 { font-size: 1.4rem; font-weight: 600; margin-bottom: 4px; }
  .subtitle { color: var(--muted); font-size: 0.85rem; margin-bottom: 24px; }

  /* Upload area */
  .upload-area {
    border: 2px dashed var(--border);
    border-radius: 12px;
    padding: 40px;
    text-align: center;
    cursor: pointer;
    transition: border-color 0.2s, background 0.2s;
    margin-bottom: 24px;
  }
  .upload-area:hover, .upload-area.dragover {
    border-color: var(--accent);
    background: rgba(79, 140, 255, 0.05);
  }
  .upload-area p { color: var(--muted); font-size: 0.95rem; }
  .upload-area .icon { font-size: 2.5rem; margin-bottom: 12px; }
  #file-input { display: none; }

  /* Controls */
  .controls {
    display: flex;
    gap: 16px;
    align-items: center;
    margin-bottom: 16px;
    flex-wrap: wrap;
  }
  .control-group { display: flex; align-items: center; gap: 8px; }
  .control-group label { font-size: 0.85rem; color: var(--muted); }
  .control-group input[type="range"] { width: 120px; accent-color: var(--accent); }
  .control-group .value {
    font-size: 0.85rem;
    min-width: 32px;
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .control-group select {
    background: var(--panel);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 0.85rem;
  }
  button {
    background: var(--accent);
    color: white;
    border: none;
    border-radius: 8px;
    padding: 10px 20px;
    font-size: 0.9rem;
    font-weight: 500;
    cursor: pointer;
    transition: opacity 0.15s;
  }
  button:hover { opacity: 0.85; }
  button:disabled { opacity: 0.4; cursor: not-allowed; }
  button.secondary {
    background: var(--border);
    color: var(--text);
    padding: 6px 12px;
    font-size: 0.8rem;
  }
  button.secondary.active { background: var(--accent); color: white; }

  .advanced-toggle {
    font-size: 0.8rem;
    color: var(--muted);
    cursor: pointer;
    user-select: none;
    margin-bottom: 16px;
  }
  .advanced-toggle:hover { color: var(--text); }
  .advanced { display: none; margin-bottom: 16px; }
  .advanced.open { display: flex; gap: 16px; flex-wrap: wrap; align-items: center; }

  /* View buttons */
  .view-buttons { display: flex; gap: 6px; }

  /* Results grid */
  .results {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 16px;
  }
  .card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
  }
  .card-header {
    padding: 12px 16px;
    font-size: 0.8rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .card-header .badge {
    background: var(--accent);
    color: white;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 0.7rem;
  }
  .card-body {
    padding: 16px;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 200px;
    background: #ffffff; /* white preview background (CSS, not part of SVG) */
  }
  .card-body img { max-width: 100%; max-height: 300px; object-fit: contain; }
  .card-body svg { max-width: 100%; max-height: 300px; }
  .card-body canvas { width: 100%; height: 300px; border-radius: 8px; }
  .card-body .placeholder { color: var(--muted); font-size: 0.85rem; }

  /* 3D card spans full width */
  .card-3d { grid-column: 1 / -1; }
  .card-3d .card-body { min-height: 400px; padding: 0; background: var(--panel); }
  .card-3d canvas { height: 400px; border-radius: 0; }

  /* Metadata bar */
  .meta-bar {
    display: flex;
    gap: 16px;
    flex-wrap: wrap;
    padding: 8px 16px;
    font-size: 0.75rem;
    color: var(--muted);
    border-top: 1px solid var(--border);
  }
  .meta-bar .ok { color: #4caf50; }
  .meta-bar .bad { color: #ff6b6b; }

  /* Download buttons */
  .downloads { display: flex; gap: 8px; }
  .downloads a {
    font-size: 0.75rem;
    padding: 4px 10px;
    border-radius: 6px;
    background: var(--border);
    color: var(--text);
    text-decoration: none;
    transition: background 0.15s;
  }
  .downloads a:hover { background: var(--accent); color: white; }

  /* Spinner */
  .spinner {
    display: inline-block;
    width: 20px;
    height: 20px;
    border: 2px solid var(--border);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .status { text-align: center; padding: 20px; color: var(--muted); font-size: 0.9rem; }
  .status.error { color: #ff6b6b; }
</style>
</head>
<body>
  <h1>Silhouette 3D</h1>
  <p class="subtitle">PNG &rarr; black silhouette &rarr; SVG &rarr; extruded 3D mesh (single source of truth)</p>

  <div class="upload-area" id="upload-area">
    <div class="icon">&#128193;</div>
    <p>Drop a PNG here or click to browse</p>
    <input type="file" id="file-input" accept="image/png"/>
  </div>

  <div class="controls">
    <div class="control-group">
      <label for="thickness">Thickness</label>
      <input type="range" id="thickness" min="1" max="50" value="10"/>
      <span class="value" id="thickness-val">10</span>
    </div>
    <div class="control-group">
      <label for="detail">Detail</label>
      <input type="range" id="detail" min="0.1" max="3.0" step="0.1" value="0.5"/>
      <span class="value" id="detail-val">0.5</span>
    </div>
    <div class="control-group">
      <label for="speckle">Speck removal</label>
      <input type="range" id="speckle" min="0" max="200" step="5" value="10"/>
      <span class="value" id="speckle-val">10</span>
    </div>
    <div class="control-group">
      <label for="preset">Preset</label>
      <select id="preset">
        <option value="exact">Exact</option>
        <option value="clean" selected>Clean</option>
        <option value="smooth">Smooth</option>
      </select>
    </div>
    <button id="process-btn" disabled>Process</button>
  </div>

  <div class="advanced-toggle" id="adv-toggle">&#9656; Advanced</div>
  <div class="advanced" id="advanced">
    <div class="control-group">
      <label for="alpha-threshold">Alpha threshold</label>
      <input type="range" id="alpha-threshold" min="1" max="254" value="128"/>
      <span class="value" id="alpha-threshold-val">128</span>
    </div>
  </div>

  <div class="status" id="status" style="display:none"></div>

  <div class="results" id="results" style="display:none">
    <div class="card">
      <div class="card-header">
        Original
        <div class="downloads" id="dl-original"></div>
      </div>
      <div class="card-body" id="original-body">
        <span class="placeholder">No image loaded</span>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        Silhouette
        <div class="downloads" id="dl-silhouette"></div>
      </div>
      <div class="card-body" id="silhouette-body">
        <span class="placeholder">Waiting for processing</span>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        SVG Vector
        <div class="downloads" id="dl-svg"></div>
      </div>
      <div class="card-body" id="svg-body">
        <span class="placeholder">Waiting for processing</span>
      </div>
    </div>

    <div class="card card-3d">
      <div class="card-header">
        3D Extrusion
        <div style="display:flex;gap:8px;align-items:center">
          <div class="view-buttons">
            <button class="secondary" data-view="front">Front</button>
            <button class="secondary" data-view="iso">Iso</button>
            <button class="secondary" data-view="side">Side</button>
            <button class="secondary" data-view="top">Top</button>
            <button class="secondary" id="overlay-btn" title="Overlay SVG contour on STL front view">Overlay SVG</button>
          </div>
          <div class="downloads" id="dl-stl"></div>
        </div>
      </div>
      <div class="card-body" id="stl-body">
        <span class="placeholder">Waiting for processing</span>
      </div>
      <div class="meta-bar" id="meta-bar" style="display:none"></div>
    </div>
  </div>

  <script src="https://cdn.jsdelivr.net/npm/three@0.128.0/build/three.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/loaders/STLLoader.js"></script>
  <script>
    const uploadArea = document.getElementById('upload-area');
    const fileInput = document.getElementById('file-input');
    const processBtn = document.getElementById('process-btn');
    const statusEl = document.getElementById('status');
    const resultsEl = document.getElementById('results');
    const thicknessSlider = document.getElementById('thickness');
    const detailSlider = document.getElementById('detail');
    const speckleSlider = document.getElementById('speckle');
    const presetSelect = document.getElementById('preset');
    const alphaSlider = document.getElementById('alpha-threshold');
    const thicknessVal = document.getElementById('thickness-val');
    const detailVal = document.getElementById('detail-val');
    const speckleVal = document.getElementById('speckle-val');
    const alphaVal = document.getElementById('alpha-threshold-val');
    const advToggle = document.getElementById('adv-toggle');
    const advancedEl = document.getElementById('advanced');

    let currentFile = null;
    let currentSvgB64 = null;
    let threeScene = null;
    let threeRenderer = null;
    let threeCamera = null;
    let threeMesh = null;
    let threeAnimId = null;
    let overlayLine = null;

    // Slider value display
    thicknessSlider.addEventListener('input', () => { thicknessVal.textContent = thicknessSlider.value; });
    detailSlider.addEventListener('input', () => { detailVal.textContent = detailSlider.value; });
    speckleSlider.addEventListener('input', () => { speckleVal.textContent = speckleSlider.value; });
    alphaSlider.addEventListener('input', () => { alphaVal.textContent = alphaSlider.value; });

    // Advanced toggle
    advToggle.addEventListener('click', () => {
      advancedEl.classList.toggle('open');
      advToggle.innerHTML = advancedEl.classList.contains('open')
        ? '&#9662; Advanced' : '&#9656; Advanced';
    });

    // Upload handling
    uploadArea.addEventListener('click', () => fileInput.click());
    uploadArea.addEventListener('dragover', (e) => { e.preventDefault(); uploadArea.classList.add('dragover'); });
    uploadArea.addEventListener('dragleave', () => { uploadArea.classList.remove('dragover'); });
    uploadArea.addEventListener('drop', (e) => {
      e.preventDefault();
      uploadArea.classList.remove('dragover');
      const files = e.dataTransfer.files;
      if (files.length > 0) handleFile(files[0]);
    });
    fileInput.addEventListener('change', () => {
      if (fileInput.files.length > 0) handleFile(fileInput.files[0]);
    });

    function handleFile(file) {
      if (!file.type.includes('png') && !file.name.toLowerCase().endsWith('.png')) {
        showStatus('Please upload a PNG file.', true);
        return;
      }
      currentFile = file;
      processBtn.disabled = false;

      const reader = new FileReader();
      reader.onload = (e) => {
        document.getElementById('original-body').innerHTML =
          `<img src="${e.target.result}" alt="Original"/>`;
        const dl = document.getElementById('dl-original');
        dl.innerHTML = `<a href="${e.target.result}" download="${file.name}">Download</a>`;
      };
      reader.readAsDataURL(file);

      resultsEl.style.display = 'grid';
      showStatus('');
    }

    function showStatus(msg, isError = false) {
      if (!msg) { statusEl.style.display = 'none'; return; }
      statusEl.style.display = 'block';
      statusEl.className = 'status' + (isError ? ' error' : '');
      statusEl.innerHTML = msg;
    }

    // Process button
    processBtn.addEventListener('click', async () => {
      if (!currentFile) return;

      processBtn.disabled = true;
      processBtn.textContent = 'Processing...';
      showStatus('<span class="spinner"></span> Processing silhouette...');

      const formData = new FormData();
      formData.append('file', currentFile);
      formData.append('thickness', thicknessSlider.value);
      formData.append('detail', detailSlider.value);
      formData.append('speckle_area', speckleSlider.value);
      formData.append('preset', presetSelect.value);
      formData.append('alpha_threshold', alphaSlider.value);

      try {
        const resp = await fetch('/api/process', { method: 'POST', body: formData });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          throw new Error(err.error || `HTTP ${resp.status}`);
        }
        const data = await resp.json();
        displayResults(data);
        showStatus('');
      } catch (err) {
        showStatus(`Error: ${err.message}`, true);
      } finally {
        processBtn.disabled = false;
        processBtn.textContent = 'Process';
      }
    });

    function displayResults(data) {
      // Silhouette
      const silB64 = data.silhouette_png;
      document.getElementById('silhouette-body').innerHTML =
        `<img src="data:image/png;base64,${silB64}" alt="Silhouette"/>`;
      document.getElementById('dl-silhouette').innerHTML =
        `<a href="data:image/png;base64,${silB64}" download="silhouette.png">Download</a>`;

      // SVG
      const svgB64 = data.svg;
      currentSvgB64 = svgB64;
      const svgDataUrl = `data:image/svg+xml;base64,${svgB64}`;
      document.getElementById('svg-body').innerHTML =
        `<img src="${svgDataUrl}" alt="SVG"/>`;
      document.getElementById('dl-svg').innerHTML =
        `<a href="${svgDataUrl}" download="silhouette.svg">Download</a>`;

      // STL 3D preview
      const stlB64 = data.stl;
      document.getElementById('dl-stl').innerHTML =
        `<a href="data:application/octet-stream;base64,${stlB64}" download="silhouette.stl">Download STL</a>`;

      // Metadata bar
      const meta = data.metadata || {};
      const metaBar = document.getElementById('meta-bar');
      metaBar.style.display = 'flex';
      metaBar.innerHTML = `
        <span class="${meta.watertight ? 'ok' : 'bad'}">watertight: ${meta.watertight}</span>
        <span>vertices: ${meta.vertices}</span>
        <span>triangles: ${meta.triangle_count}</span>
        <span>components: ${meta.components}</span>
        <span>holes: ${meta.holes}</span>
        <span>volume: ${meta.volume ? meta.volume.toFixed(1) : '?'}</span>
      `;

      // Load STL into Three.js
      const stlBody = document.getElementById('stl-body');
      stlBody.innerHTML = '';
      loadSTL(stlB64, stlBody);
    }

    function loadSTL(b64, container) {
      const binaryStr = atob(b64);
      const bytes = new Uint8Array(binaryStr.length);
      for (let i = 0; i < binaryStr.length; i++) {
        bytes[i] = binaryStr.charCodeAt(i);
      }

      // Clean up previous scene
      if (threeAnimId) cancelAnimationFrame(threeAnimId);
      if (threeRenderer) { threeRenderer.dispose(); threeRenderer = null; }
      if (overlayLine) { threeScene.remove(overlayLine); overlayLine = null; }

      const width = container.clientWidth || 600;
      const height = 400;

      threeScene = new THREE.Scene();
      threeScene.background = new THREE.Color(0x1a1d24);

      threeCamera = new THREE.PerspectiveCamera(45, width / height, 0.1, 10000);
      threeCamera.position.set(0, 0, 500);

      threeRenderer = new THREE.WebGLRenderer({ antialias: true });
      threeRenderer.setSize(width, height);
      threeRenderer.setPixelRatio(window.devicePixelRatio);
      container.appendChild(threeRenderer.domElement);

      const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
      threeScene.add(ambientLight);
      const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
      dirLight.position.set(1, 1, 1);
      threeScene.add(dirLight);
      const dirLight2 = new THREE.DirectionalLight(0xffffff, 0.4);
      dirLight2.position.set(-1, -0.5, -1);
      threeScene.add(dirLight2);

      const loader = new THREE.STLLoader();
      const geometry = loader.parse(bytes.buffer);

      geometry.center();
      const bbox = new THREE.Box3().setFromBufferAttribute(geometry.attributes.position);
      const size = bbox.getSize(new THREE.Vector3());
      const maxDim = Math.max(size.x, size.y, size.z);
      const scale = 300 / maxDim;
      geometry.scale(scale, scale, scale);

      const material = new THREE.MeshPhongMaterial({
        color: 0x4f8cff,
        shininess: 80,
        side: THREE.DoubleSide,
      });

      threeMesh = new THREE.Mesh(geometry, material);
      threeScene.add(threeMesh);

      // Default view: FRONT (rotX = 0, rotY = 0)
      let rotX = 0, rotY = 0;
      let isDragging = false;
      let prevX = 0, prevY = 0;

      threeRenderer.domElement.addEventListener('mousedown', (e) => {
        isDragging = true; prevX = e.clientX; prevY = e.clientY;
      });
      threeRenderer.domElement.addEventListener('mousemove', (e) => {
        if (!isDragging) return;
        const dx = e.clientX - prevX;
        const dy = e.clientY - prevY;
        rotY += dx * 0.01;
        rotX += dy * 0.01;
        prevX = e.clientX; prevY = e.clientY;
      });
      threeRenderer.domElement.addEventListener('mouseup', () => { isDragging = false; });
      threeRenderer.domElement.addEventListener('mouseleave', () => { isDragging = false; });

      threeRenderer.domElement.addEventListener('wheel', (e) => {
        e.preventDefault();
        threeCamera.position.z += e.deltaY * 0.5;
        threeCamera.position.z = Math.max(50, Math.min(2000, threeCamera.position.z));
      }, { passive: false });

      // View presets
      const views = {
        front: { x: 0, y: 0 },
        iso:   { x: Math.PI / 6, y: Math.PI / 4 },
        side:  { x: 0, y: Math.PI / 2 },
        top:   { x: Math.PI / 2, y: 0 },
      };
      document.querySelectorAll('[data-view]').forEach(btn => {
        btn.addEventListener('click', () => {
          const v = views[btn.dataset.view];
          if (v) { rotX = v.x; rotY = v.y; }
        });
      });

      // Overlay SVG contour on front view
      document.getElementById('overlay-btn').addEventListener('click', () => {
        if (overlayLine) {
          threeScene.remove(overlayLine);
          overlayLine = null;
          document.getElementById('overlay-btn').classList.remove('active');
          return;
        }
        if (!currentSvgB64) return;
        const svgText = new TextDecoder().decode(Uint8Array.from(atob(currentSvgB64), c => c.charCodeAt(0)));
        const pts = extractSvgOutline(svgText);
        if (!pts || pts.length < 2) return;

        // Project SVG outline (image coords) onto the front face of the mesh.
        // The mesh is centered and scaled; map image coords to mesh local coords.
        const svgW = parseFloat((svgText.match(/width="([0-9.]+)"/) || [])[1] || 1);
        const svgH = parseFloat((svgText.match(/height="([0-9.]+)"/) || [])[1] || 1);
        const bbox2 = new THREE.Box3().setFromObject(threeMesh);
        const sz = bbox2.getSize(new THREE.Vector3());
        const frontZ = bbox2.max.z;

        const positions = [];
        for (let i = 0; i < pts.length; i++) {
          const px = (pts[i][0] / svgW - 0.5) * sz.x;
          const py = -(pts[i][1] / svgH - 0.5) * sz.y; // flip Y (image vs 3D)
          positions.push(px, py, frontZ + 0.5);
        }
        const lineGeo = new THREE.BufferGeometry();
        lineGeo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
        const lineMat = new THREE.LineBasicMaterial({ color: 0xff4444, linewidth: 2 });
        overlayLine = new THREE.Line(lineGeo, lineMat);
        threeScene.add(overlayLine);
        document.getElementById('overlay-btn').classList.add('active');
      });

      function animate() {
        threeAnimId = requestAnimationFrame(animate);
        threeMesh.rotation.x = rotX;
        threeMesh.rotation.y = rotY;
        threeRenderer.render(threeScene, threeCamera);
      }
      animate();

      window.addEventListener('resize', () => {
        const w = container.clientWidth || 600;
        threeCamera.aspect = w / height;
        threeCamera.updateProjectionMatrix();
        threeRenderer.setSize(w, height);
      });
    }

    // Extract the first outline ring from SVG path data (M/L/C/Z commands).
    // Returns a flat list of [x, y] points (curves approximated by endpoints
    // for the overlay; good enough for visual comparison).
    function extractSvgOutline(svgText) {
      const m = svgText.match(/d="([^"]+)"/);
      if (!m) return null;
      const d = m[1];
      const pts = [];
      const re = /([MLCQZ])([^MLCQZ]*)/gi;
      let match;
      while ((match = re.exec(d)) !== null) {
        const cmd = match[1];
        const nums = (match[2].match(/-?[\d.]+(?:e-?\d+)?/g) || []).map(Number);
        if (cmd === 'M' || cmd === 'L') {
          for (let i = 0; i + 1 < nums.length; i += 2) pts.push([nums[i], nums[i + 1]]);
        } else if (cmd === 'C') {
          for (let i = 0; i + 5 < nums.length; i += 6) pts.push([nums[i + 4], nums[i + 5]]);
        } else if (cmd === 'Q') {
          for (let i = 0; i + 3 < nums.length; i += 4) pts.push([nums[i + 2], nums[i + 3]]);
        }
      }
      return pts;
    }
  </script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route("/api/process", methods=["POST"])
def api_process():
    """Process an uploaded PNG through the full pipeline."""
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    if not file.filename.lower().endswith(".png"):
        return jsonify({"error": "Only PNG files are supported"}), 400

    png_bytes = file.read()
    if not png_bytes:
        return jsonify({"error": "Empty file"}), 400

    thickness = float(request.form.get("thickness", 10))
    detail = float(request.form.get("detail", 0.5))
    speckle_area = float(request.form.get("speckle_area", 10))
    preset = request.form.get("preset", "clean")
    alpha_threshold = int(request.form.get("alpha_threshold", 128))

    try:
        result = process_image(
            png_bytes,
            thickness=thickness,
            preset=preset,
            detail=detail,
            speckle_area=speckle_area,
            alpha_threshold=alpha_threshold,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
