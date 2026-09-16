"""Tarea 00 — Gate del motor heredado.

Cinco pruebas que fallan de verdad ante:
- inversión de máscara (polaridad)
- hueco borrado (donut)
- geometría cóncava (estrella)
- varias islas
- bounds/volumen de STL

Usan fixtures sintéticos y el motor real (prepare_mask → trace_mask → parse_vector → extrude_polygons).
"""
import io
import math

import numpy as np
import pytest
from PIL import Image, ImageDraw
from shapely.affinity import scale
from shapely.geometry import box
from shapely.ops import unary_union

from pipeline import process_image, MESH_FLATTEN_TOLERANCE_PX
from silhouettes.mask import prepare_mask
from silhouettes.trace import trace_mask, PRESETS
from silhouettes.vector import parse_vector, vector_to_polygons
from silhouettes.mesh import extrude_polygons
from silhouettes.validation import validate_mesh, top_cap_geometry


# ---------------------------------------------------------------------------
# Fixtures sintéticos (200×200 px)
# ---------------------------------------------------------------------------

def _png(shape_fn, size=200):
    """Dibuja una forma con alpha sobre fondo transparente → PNG bytes."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    shape_fn(draw, size)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def png_circle():
    def draw(d, s):
        d.ellipse([50, 50, 150, 150], fill=(0, 0, 0, 255))
    return _png(draw)


def png_donut():
    """Anillo: círculo exterior 150, interior 60 (hueco real)."""
    def draw(d, s):
        d.ellipse([25, 25, 175, 175], fill=(0, 0, 0, 255))
        d.ellipse([70, 70, 130, 130], fill=(0, 0, 0, 0))  # borrar centro
    return _png(draw)


def png_star():
    """Estrella cóncava de 5 puntas."""
    import math as m
    def draw(d, s):
        cx, cy, R, r = 100, 100, 80, 35
        pts = []
        for i in range(10):
            angle = m.pi * i / 5 - m.pi / 2
            rad = R if i % 2 == 0 else r
            pts.append((cx + rad * m.cos(angle), cy + rad * m.sin(angle)))
        d.polygon(pts, fill=(0, 0, 0, 255))
    return _png(draw)


def png_islands():
    """Tres islas separadas."""
    def draw(d, s):
        d.ellipse([20, 20, 70, 70], fill=(0, 0, 0, 255))
        d.ellipse([100, 30, 160, 90], fill=(0, 0, 0, 255))
        d.rectangle([60, 120, 150, 180], fill=(0, 0, 0, 255))
    return _png(draw)


def png_square():
    """Cuadrado simple para el gate de STL."""
    def draw(d, s):
        d.rectangle([40, 40, 160, 160], fill=(0, 0, 0, 255))
    return _png(draw)


# ---------------------------------------------------------------------------
# Gate 1: polaridad — alfa = figura (no fondo)
# ---------------------------------------------------------------------------

def test_gate1_polarity_alpha_is_figure():
    """prepare_mask debe dar 255 en la figura, 0 en el fondo.
    Si la polaridad está invertida, el área de foreground será ~0 o ~total."""
    png = png_circle()
    with Image.open(io.BytesIO(png)) as img:
        mask = prepare_mask(img)
    fg = np.count_nonzero(mask == 255)
    total = mask.size
    # Un círculo de r=50 en 200×200: área ≈ π·50² ≈ 7854 px; total = 40000
    # Si está invertido, fg ≈ 32146 (fondo) → ratio > 0.5
    ratio = fg / total
    assert 0.05 < ratio < 0.5, f"Polaridad sospechosa: fg/total={ratio:.3f}"
    # El centro debe ser foreground
    assert mask[100, 100] == 255, "Centro del círculo debe ser foreground"
    # Una esquina debe ser background
    assert mask[0, 0] == 0, "Esquina debe ser background"


# ---------------------------------------------------------------------------
# Gate 2: hueco real en donut
# ---------------------------------------------------------------------------

def test_gate2_donut_hole_preserved():
    """El donut debe producir un polígono con interior (hueco).
    Si el hueco se borra, no hay interiors."""
    png = png_donut()
    result = process_image(png, thickness=5, preset="exact", speckle_area=0)
    svg = __import__("base64").b64decode(result["svg"]).decode()
    vector = parse_vector(svg, tolerance=MESH_FLATTEN_TOLERANCE_PX)
    polygons = vector_to_polygons(vector)
    # Al menos un polígono debe tener un interior (el hueco)
    total_interiors = sum(len(p.interiors) for p in polygons)
    assert total_interiors >= 1, f"El donut perdió su hueco: {total_interiors} interiors"
    # El área del material debe ser menor que el círculo exterior completo
    material = unary_union(polygons)
    outer_area = math.pi * 75**2  # r=75 px
    assert material.area < outer_area * 0.9, "El hueco no reduce el área"


# ---------------------------------------------------------------------------
# Gate 3: estrella cóncava
# ---------------------------------------------------------------------------

def test_gate3_concave_star():
    """La estrella cóncava debe mantener su concavidad (no convex hull).
    El área debe ser menor que el círculo circunscrito."""
    png = png_star()
    result = process_image(png, thickness=5, preset="exact", speckle_area=0)
    svg = __import__("base64").b64decode(result["svg"]).decode()
    vector = parse_vector(svg, tolerance=MESH_FLATTEN_TOLERANCE_PX)
    polygons = vector_to_polygons(vector)
    material = unary_union(polygons)
    # Círculo circunscrito r=80: área ≈ 20106
    # Estrella de 5 puntas: área ≈ 0.38·R²·sin(π/5)·5 ≈ mucho menos
    circumscribed = math.pi * 80**2
    assert material.area < circumscribed * 0.7, (
        f"Área {material.area:.0f} demasiado cerca del círculo circunscrito {circumscribed:.0f}; "
        "posible convex hull"
    )
    # Debe ser una sola pieza conectada
    assert len(polygons) == 1, f"La estrella debe ser 1 polígono, hay {len(polygons)}"


# ---------------------------------------------------------------------------
# Gate 4: varias islas
# ---------------------------------------------------------------------------

def test_gate4_multiple_islands():
    """Tres islas separadas → 3 componentes en el STL."""
    png = png_islands()
    result = process_image(png, thickness=5, preset="exact", speckle_area=0)
    stl = __import__("base64").b64decode(result["stl"])
    import trimesh
    mesh = trimesh.load(io.BytesIO(stl), file_type="stl", process=True)
    components = mesh.split(only_watertight=False)
    assert len(components) >= 3, f"Se esperan ≥3 componentes, hay {len(components)}"
    # Cada componente debe ser watertight
    for i, c in enumerate(components):
        assert c.is_watertight, f"Componente {i} no es watertight"


# ---------------------------------------------------------------------------
# Gate 5: bounds y volumen de STL
# ---------------------------------------------------------------------------

def test_gate5_stl_bounds_and_volume():
    """Cuadrado 120×120 px, espesor 5 → volumen ≈ 120·120·5 = 72000.
    Bounds XY deben ser ≈ 120 en cada eje."""
    png = png_square()
    thickness = 5.0
    result = process_image(png, thickness=thickness, preset="exact", speckle_area=0)
    stl = __import__("base64").b64decode(result["stl"])
    import trimesh
    mesh = trimesh.load(io.BytesIO(stl), file_type="stl", process=True)
    assert mesh.is_watertight
    # Bounds: el cuadrado va de 40 a 160 → 120 px de lado
    extents = mesh.extents
    assert extents[0] == pytest.approx(120, rel=0.05), f"X extent {extents[0]} ≠ 120"
    assert extents[1] == pytest.approx(120, rel=0.05), f"Y extent {extents[1]} ≠ 120"
    assert extents[2] == pytest.approx(thickness, rel=0.05), f"Z extent {extents[2]} ≠ {thickness}"
    # Volumen
    expected_vol = 120 * 120 * thickness
    assert mesh.volume == pytest.approx(expected_vol, rel=0.05), (
        f"Volumen {mesh.volume:.0f} ≠ esperado {expected_vol:.0f}"
    )
