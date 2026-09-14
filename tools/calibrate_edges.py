"""Run bounded VTracer A/B tests on the real input. Does not select silently.

Run from the project root after copying this package's silhouettes/ and tools/:
  python tools/calibrate_edges.py input.png --render
Uses the repaired SVG parser/extruder supplied in the PREVIOUS repair package.
Does NOT work with the old corrupt-path implementation still in GitHub main.
"""
from __future__ import annotations
import argparse
from dataclasses import replace
from datetime import datetime
import importlib.metadata
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("png", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--render", action="store_true", help="Also render SVG independently at 1x/8x")
    parser.add_argument("--speckle-area", type=float, default=10.0)
    parser.add_argument("--alpha-threshold", type=int, default=128)
    args = parser.parse_args()

    from PIL import Image
    import shapely
    from shapely.affinity import scale
    import trimesh
    from silhouettes.mask import prepare_mask, remove_small_components
    from silhouettes.trace import trace_mask, PRESETS
    from silhouettes.vector import parse_vector, vector_to_polygons
    from silhouettes.mesh import extrude_polygons
    from silhouettes.validation import validate_mesh
    from silhouettes.edge_quality import mask_region, compare_regions

    out = args.out or ROOT / "edge-diagnostics" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    out.mkdir(parents=True, exist_ok=False)  # Never overwrite a previous report.
    with Image.open(args.png) as image:
        if image.format != "PNG":
            raise ValueError("Expected a PNG")
        image.load()
        mask = prepare_mask(image, args.alpha_threshold)
    mask = remove_small_components(mask, args.speckle_area)
    reference = mask_region(mask)
    Image.fromarray(mask).save(out / "mask-255-is-foreground.png")
    height, width = mask.shape
    if args.render and width * height * 64 > 64_000_000:
        raise ValueError("8x render exceeds 64 megapixels; use an ROI or omit --render")

    candidates = [
        ("baseline-0.4-c90", 0.4, 90),
        ("clean-0.8-c90", 0.8, 90),
        ("clean-0.8-c100", 0.8, 100),
        ("clean-1.0-c100", 1.0, 100),
        ("clean-1.25-c100", 1.25, 100),
    ]
    rows = []
    for name, tolerance, corner in candidates:
        row = {"name": name, "trace_tolerance_px": tolerance, "corner_threshold": corner}
        try:
            settings = replace(PRESETS["clean"], mode="spline", simplify=tolerance,
                corner_threshold=corner, splice_threshold=45,
                length_threshold=4.0, max_iterations=10,
                filter_speckle=0, path_precision=5)
            svg = trace_mask(mask, settings)
            (out / f"{name}.svg").write_text(svg, encoding="utf-8")
            vector = parse_vector(svg, tolerance=0.025)
            polygons = vector_to_polygons(vector)
            candidate = shapely.union_all(polygons)
            row.update(compare_regions(reference, candidate, curve_flatten_error_px=0.025))
            if args.render:
                for factor in (1, 8):
                    if sys.platform == "win32":
                        import resvg_py
                        rendered = resvg_py.svg_to_bytes(svg_string=svg,
                            width=width * factor, height=height * factor)
                    else:
                        import cairosvg
                        rendered = cairosvg.svg2png(bytestring=svg.encode(),
                            output_width=width * factor, output_height=height * factor)
                    (out / f"{name}-{factor}x.png").write_bytes(rendered)
            if row["passes_guard"]:
                model = [scale(p, xfact=1, yfact=-1, origin=(0, 0)) for p in polygons]
                mesh = extrude_polygons(model, thickness=10.0)
                row["mesh"] = validate_mesh(mesh, model, 10.0)
                stl = mesh.export(file_type="stl")
                # Validate the actual serialized triangles, not only the pre-export mesh.
                reloaded = trimesh.load_mesh(io.BytesIO(stl), file_type="stl", process=True)
                validate_mesh(reloaded, model, 10.0)
                (out / f"{name}.stl").write_bytes(stl)
                row["export_verified"] = True
            else:
                row["export_verified"] = False
        except Exception as exc:
            row.update(passes_guard=False, export_verified=False,
                       error=f"{type(exc).__name__}: {exc}")
        rows.append(row)
        print(name, "PASS" if row.get("export_verified") else "REJECT", flush=True)

    versions = {}
    for package in ("vtracer", "trimesh", "mapbox-earcut", "svg.path", "shapely", "numpy"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not installed"
    payload = {"input": args.png.name, "source_size": [width, height],
               "python": sys.version, "versions": versions, "candidates": rows,
               "note": "PASS means fidelity and mesh checks passed, NOT a visual smoothness guarantee."}
    (out / "report.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(out)
    return 0 if any(r.get("export_verified") for r in rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
