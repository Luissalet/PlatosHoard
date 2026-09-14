"""Test VTracer and Earcut integration."""

from PIL import Image, ImageDraw

# Import the modules we're working with
from silhouettes.mask import prepare_mask
from silhouettes.trace import trace_mask, PRESETS
from silhouettes.vector import parse_vector, vector_to_polygons
from silhouettes.mesh import extrude_polygons

def test_vtracer_integration():
    """Test that VTracer integration works correctly."""
    
    # Create a simple test image (a circle)
    img = Image.new('RGBA', (100, 100), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.ellipse([25, 25, 75, 75], fill=(0, 0, 0, 255))
    
    # Prepare mask
    mask = prepare_mask(img)
    
    # Trace with VTracer
    settings = PRESETS["clean"]
    svg_str = trace_mask(mask, settings)
    
    # Parse vector
    rings = parse_vector(svg_str)
    
    # Convert to polygons
    polygons = vector_to_polygons(rings)
    
    # Extrude
    try:
        extrude_polygons(polygons, thickness=10.0)
        print("VTracer and Earcut integration test PASSED")
        return True
    except Exception as e:
        print(f"VTracer and Earcut integration test FAILED: {e}")
        return False

if __name__ == "__main__":
    test_vtracer_integration()