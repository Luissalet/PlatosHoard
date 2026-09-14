"""Independent SVG renderer; explicit platform choice, no failure fallback."""
import sys


def render_svg_bytes(svg, width=None, height=None):
    if sys.platform == "win32":
        import resvg_py
        kwargs = {"svg_string": svg}
        if width is not None:
            kwargs["width"] = width
        if height is not None:
            kwargs["height"] = height
        return resvg_py.svg_to_bytes(**kwargs)
    import cairosvg
    return cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=width, output_height=height)
