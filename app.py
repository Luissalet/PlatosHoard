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
  h1 {
    font-size: 1.4rem;
    font-weight: 600;
    margin-bottom: 4px;
  }
  .subtitle {
    color: var(--muted);
    font-size: 0.85rem;
    margin-bottom: 24px;
  }

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
  .upload-area p {
    color: var(--muted);
    font-size: 0.95rem;
  }
  .upload-area .icon {
    font-size: 2.5rem;
    margin-bottom: 12px;
  }
  #file-input { display: none; }

  /* Controls */
  .controls {
    display: flex;
    gap: 16px;
    align-items: center;
    margin-bottom: 24px;
    flex-wrap: wrap;
  }
  .control-group {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .control-group label {
    font-size: 0.85rem;
    color: var(--muted);
  }
  .control-group input[type="range"] {
    width: 120px;
    accent-color: var(--accent);
  }
  .control-group .value {
    font-size: 0.85rem;
    min-width: 32px;
    text-align: right;
    font-variant-numeric: tabular-nums;
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
  }
  .card-body img {
    max-width: 100%;
    max-height: 300px;
    object-fit: contain;
  }
  .card-body svg {
    max-width: 100%;
    max-height: 300px;
  }
  .card-body canvas {
    width: 100%;
    height: 300px;
    border-radius: 8px;
  }
  .card-body .placeholder {
    color: var(--muted);
    font-size: 0.85rem;
  }

  /* 3D card spans full width */
  .card-3d {
    grid-column: 1 / -1;
  }
  .card-3d .card-body {
    min-height: 400px;
    padding: 0;
  }
  .card-3d canvas {
    height: 400px;
    border-radius: 0;
  }

  /* Download buttons */
  .downloads {
    display: flex;
    gap: 8px;
  }
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

  .status {
    text-align: center;
    padding: 20px;
    color: var(--muted);
    font-size: 0.9rem;
  }
  .status.error { color: #ff6b6b; }
</style>
</head>
<body>
  <h1>Silhouette 3D</h1>
  <p class="subtitle">PNG &rarr; black silhouette &rarr; SVG &rarr; extruded 3D mesh</p>

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
      <label for="simplify">Simplify</label>
      <input type="range" id="simplify" min="1" max="100" value="10"/>
      <span class="value" id="simplify-val">10</span>
    </div>
    <button id="process-btn" disabled>Process</button>
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
        <div class="downloads" id="dl-stl"></div>
      </div>
      <div class="card-body" id="stl-body">
        <span class="placeholder">Waiting for processing</span>
      </div>
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
    const simplifySlider = document.getElementById('simplify');
    const thicknessVal = document.getElementById('thickness-val');
    const simplifyVal = document.getElementById('simplify-val');

    let currentFile = null;
    let threeScene = null;
    let threeRenderer = null;
    let threeCamera = null;
    let threeMesh = null;
    let threeAnimId = null;

    // Slider value display
    thicknessSlider.addEventListener('input', () => {
      thicknessVal.textContent = thicknessSlider.value;
    });
    simplifySlider.addEventListener('input', () => {
      simplifyVal.textContent = simplifySlider.value;
    });

    // Upload handling
    uploadArea.addEventListener('click', () => fileInput.click());
    uploadArea.addEventListener('dragover', (e) => {
      e.preventDefault();
      uploadArea.classList.add('dragover');
    });
    uploadArea.addEventListener('dragleave', () => {
      uploadArea.classList.remove('dragover');
    });
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

      // Show original preview
      const reader = new FileReader();
      reader.onload = (e) => {
        document.getElementById('original-body').innerHTML =
          `<img src="${e.target.result}" alt="Original"/>`;
        // Add download link
        const dl = document.getElementById('dl-original');
        dl.innerHTML = `<a href="${e.target.result}" download="${file.name}">Download</a>`;
      };
      reader.readAsDataURL(file);

      resultsEl.style.display = 'grid';
      showStatus('');
    }

    function showStatus(msg, isError = false) {
      if (!msg) {
        statusEl.style.display = 'none';
        return;
      }
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
      formData.append('simplify', simplifySlider.value);

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
      const svgDataUrl = `data:image/svg+xml;base64,${svgB64}`;
      document.getElementById('svg-body').innerHTML =
        `<img src="${svgDataUrl}" alt="SVG"/>`;
      document.getElementById('dl-svg').innerHTML =
        `<a href="${svgDataUrl}" download="silhouette.svg">Download</a>`;

      // STL 3D preview
      const stlB64 = data.stl;
      document.getElementById('dl-stl').innerHTML =
        `<a href="data:application/octet-stream;base64,${stlB64}" download="silhouette.stl">Download STL</a>`;

      // Show metadata
      const stlBody = document.getElementById('stl-body');
      stlBody.innerHTML = '';

      // Load STL into Three.js
      loadSTL(stlB64, stlBody);
    }

    function loadSTL(b64, container) {
      // Decode base64 to binary
      const binaryStr = atob(b64);
      const bytes = new Uint8Array(binaryStr.length);
      for (let i = 0; i < binaryStr.length; i++) {
        bytes[i] = binaryStr.charCodeAt(i);
      }

      // Clean up previous scene
      if (threeAnimId) cancelAnimationFrame(threeAnimId);
      if (threeRenderer) {
        threeRenderer.dispose();
        threeRenderer = null;
      }

      const width = container.clientWidth || 600;
      const height = 400;

      // Scene
      threeScene = new THREE.Scene();
      threeScene.background = new THREE.Color(0x1a1d24);

      // Camera
      threeCamera = new THREE.PerspectiveCamera(45, width / height, 0.1, 10000);
      threeCamera.position.set(0, 0, 500);

      // Renderer
      threeRenderer = new THREE.WebGLRenderer({ antialias: true });
      threeRenderer.setSize(width, height);
      threeRenderer.setPixelRatio(window.devicePixelRatio);
      container.appendChild(threeRenderer.domElement);

      // Lights
      const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
      threeScene.add(ambientLight);
      const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
      dirLight.position.set(1, 1, 1);
      threeScene.add(dirLight);
      const dirLight2 = new THREE.DirectionalLight(0xffffff, 0.4);
      dirLight2.position.set(-1, -0.5, -1);
      threeScene.add(dirLight2);

      // Load STL
      const loader = new THREE.STLLoader();
      const geometry = loader.parse(bytes.buffer);

      // Center and scale
      geometry.center();
      const bbox = new THREE.Box3().setFromBufferAttribute(geometry.attributes.position);
      const size = bbox.getSize(new THREE.Vector3());
      const maxDim = Math.max(size.x, size.y, size.z);
      const scale = 300 / maxDim;
      geometry.scale(scale, scale, scale);

      // Material
      const material = new THREE.MeshPhongMaterial({
        color: 0x4f8cff,
        shininess: 80,
        side: THREE.DoubleSide,
      });

      threeMesh = new THREE.Mesh(geometry, material);
      threeScene.add(threeMesh);

      // Orbit controls (simple mouse drag)
      let isDragging = false;
      let prevX = 0, prevY = 0;
      let rotX = 0.3, rotY = 0.5;

      threeRenderer.domElement.addEventListener('mousedown', (e) => {
        isDragging = true;
        prevX = e.clientX;
        prevY = e.clientY;
      });
      threeRenderer.domElement.addEventListener('mousemove', (e) => {
        if (!isDragging) return;
        const dx = e.clientX - prevX;
        const dy = e.clientY - prevY;
        rotY += dx * 0.01;
        rotX += dy * 0.01;
        prevX = e.clientX;
        prevY = e.clientY;
      });
      threeRenderer.domElement.addEventListener('mouseup', () => { isDragging = false; });
      threeRenderer.domElement.addEventListener('mouseleave', () => { isDragging = false; });

      // Zoom
      threeRenderer.domElement.addEventListener('wheel', (e) => {
        e.preventDefault();
        threeCamera.position.z += e.deltaY * 0.5;
        threeCamera.position.z = Math.max(50, Math.min(2000, threeCamera.position.z));
      }, { passive: false });

      // Animation loop (no auto-rotate; only re-renders on interaction)
      function animate() {
        threeAnimId = requestAnimationFrame(animate);
        threeMesh.rotation.x = rotX;
        threeMesh.rotation.y = rotY;
        threeRenderer.render(threeScene, threeCamera);
      }
      animate();

      // Handle resize
      window.addEventListener('resize', () => {
        const w = container.clientWidth || 600;
        threeCamera.aspect = w / height;
        threeCamera.updateProjectionMatrix();
        threeRenderer.setSize(w, height);
      });
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
    simplify = float(request.form.get("simplify", 10))

    try:
        result = process_image(png_bytes, thickness=thickness, simplify=simplify)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
