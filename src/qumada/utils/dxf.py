import re
import sys
from pathlib import Path
from typing import Tuple
import math
import random

import numpy as np
import shapely.geometry as sg
import ezdxf.document
import ezdxf.entities
from ezdxf.addons.drawing import RenderContext, Frontend
from ezdxf.addons.drawing.svg import SVGBackend
from shapely.geometry import (
    box, Polygon, LineString, Point, MultiLineString, MultiPolygon, GeometryCollection
)
import svgwrite

import ezdxf
from ezdxf.addons.drawing import RenderContext, Frontend, layout, config
from ezdxf.addons.drawing.svg import SVGBackend
from ezdxf.addons.importer import Importer
from shapely.geometry import box, Polygon, LineString
import svgwrite


DEFAULT_WINDOW = (-3., -1.5, 3., 1.5)




def entity_to_geom(e):
    """
    Best-effort conversion of an ezdxf entity to a Shapely geometry.
    Extend this to cover more exotic entities as needed.
    """
    if e.dxftype() == "LINE":
        return LineString([e.dxf.start, e.dxf.end])

    if e.dxftype() in {"LWPOLYLINE", "POLYLINE"}:
        if hasattr(e, "get_points"):
            points = e.get_points()
        else:
            points = e.points()
        pts = [tuple(p)[:2] for p in points]  # ignore bulge for now
        closed = bool(e.closed) if hasattr(e, "closed") else pts[0] == pts[-1]
        return Polygon(pts) if closed else LineString(pts)

    raise NotImplementedError(e.dxftype())



def iterate_all_entities(e, path = None):
    if path is None:
        path = []
    if e.dxftype() == "INSERT":
        path.append(e.dxf.name)
        for sub in e.virtual_entities():
            yield from iterate_all_entities(sub, path)
        path.pop()
    else:
        yield e, list(path)


def crop_dxf_to_svg(
    doc: ezdxf.document.Drawing,
    x_rng: Tuple[float, float] = (-3., 3.),
    y_rng: Tuple[float, float] = (-1.5, 1.5),
    unit: str = "mm",
    scale: float = 1e-3,
    layer_regex: str = r".*BEAM\_L.",
) -> str:
    regex = re.compile(layer_regex)

    keep_layer = {
        layer.dxf.name: bool(regex.search(layer.dxf.name))
        for layer in doc.layers
    }

    xmin, xmax =  x_rng
    ymin, ymax =  y_rng
    roi_poly = box(xmin, ymin, xmax, ymax)

    chosen = []
    labels = {}
    for model in doc.modelspace():
        for e, path in iterate_all_entities(model):
            if keep_layer[e.dxf.layer] and (geom := entity_to_geom(e)) is not None and geom.intersects(roi_poly):


                chosen.append(e)

                label_position = geom.intersection(roi_poly.boundary).centroid
                labels[(label_position.x, label_position.y)] = (e, path)

    tgt = ezdxf.new(doc.dxfversion, setup=True)
    importer = Importer(doc, tgt)
    importer.import_entities(chosen)
    importer.finalize()

    cfg = config.Configuration(
        background_policy=config.BackgroundPolicy.OFF
    )

    backend = SVGBackend()
    frontend = Frontend(RenderContext(tgt), backend, config=cfg)
    frontend.draw_layout(tgt.modelspace())
    try:
        page_unit = getattr(layout.Units, unit)
    except AttributeError:
        raise ValueError(f"Invalid unit: {unit}. Must be one of: {list(map(lambda x: x.name, layout.Units))}")

    page = layout.Page(0, 0, page_unit,
                       margins=layout.Margins.all(2))
    settings = layout.Settings(scale=scale)
    svg = backend.get_string(page, settings=settings)
    Path("test.svg").write_text(svg)
    return svg
