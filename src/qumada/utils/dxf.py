import dataclasses
import pathlib
import re
import sys
from pathlib import Path
from typing import Tuple
import math
import random
import json

from matplotlib import pyplot as plt
import matplotlib.widgets

import numpy as np
import shapely.geometry as sg
import ezdxf.document
import ezdxf.entities
from ezdxf.addons.drawing import RenderContext, Frontend
from ezdxf.addons.drawing.svg import SVGBackend
from shapely.geometry import (
    box, Polygon, LineString, Point, MultiLineString, MultiPolygon, GeometryCollection
)

import ezdxf
from ezdxf.addons.drawing import RenderContext, Frontend, layout, config
from ezdxf.addons.drawing.svg import SVGBackend
from ezdxf.addons.importer import Importer
from shapely.geometry import box, Polygon, LineString
from shapely.plotting import plot_polygon
from shapely import wkt



DEFAULT_WINDOW = (-3., -1.5, 3., 1.5)
SELECT_ALPHA = 0.3


@dataclasses.dataclass
class Gate:
    polygon: Polygon
    path: list[str]
    layer: str
    label: str | None
    label_position: tuple[float, float]


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
            points = e.points_in_wcs()
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



def get_gates_from_cropped_region(
    doc: ezdxf.document.Drawing,
    x_rng: Tuple[float, float] = (-3., 3.),
    y_rng: Tuple[float, float] = (-1.5, 1.5),
    layer_regex: str = r".*BEAM\_L.",
) -> list[Gate]:
    regex = re.compile(layer_regex)

    keep_layer = {
        layer.dxf.name: bool(regex.search(layer.dxf.name))
        for layer in doc.layers
    }

    xmin, xmax =  x_rng
    ymin, ymax =  y_rng
    roi_poly = Polygon(box(xmin, ymin, xmax, ymax))

    chosen = []
    for model in doc.modelspace():
        for e, path in iterate_all_entities(model):
            layer = e.dxf.layer
            if not keep_layer[layer]:
                continue

            raw_geom = entity_to_geom(e)
            if raw_geom is None:
                continue

            geom = Polygon(raw_geom).simplify(1e-3)
            geom_roi = geom.intersection(roi_poly)
            if geom_roi == roi_poly:
                continue

            if geom_roi.is_empty:
                continue

            boundary = geom.intersection(roi_poly.boundary)

            if not boundary.is_empty and isinstance(boundary, (LineString, MultiLineString)):
                label_position_point = boundary.line_interpolate_point(0.5, normalized=True)
                label_position = label_position_point.x, label_position_point.y
            else:
                label_position = None
            
            label = f"G{len(chosen)}"
            if label == "G36":
                pass
                
            gate = Gate(
                polygon=geom_roi,
                label_position=label_position,
                label=label,
                path=path,
                layer=layer,
            )
            chosen.append(gate)
    
    return chosen


def auto_merge(gates: list[Gate]):
    # merge touching gates that do not touch the boundary
    connected = []
    unconnected = []
    for gate in gates:
        if gate.label_position:
            connected.append(gate)
        else:
            unconnected.append(gate)

    print("Connected:", len(connected))
    print("Unconnected:", len(unconnected))

    while unconnected:
        temp = []
        for gate in unconnected:
            for con_gate in connected:
                if gate.layer != con_gate.layer:
                    continue
                boundary = gate.polygon.intersection(con_gate.polygon, grid_size=1e-3)
                if boundary.is_empty:
                    continue

                new_geom = gate.polygon.union(con_gate.polygon)
                con_gate.polygon = new_geom
                break
            else:
                temp.append(gate)
        print(len(temp))
        if len(temp) == len(unconnected):
            break
        unconnected = temp

    print("Unconnected after auto-merge:", len(unconnected))
    for u in unconnected:
        print("Unconnected ",u.label ,"in layer", u.layer, u.polygon)
        u.label_position = u.polygon.centroid.x, u.polygon.centroid.y

    return connected + unconnected


def label_gates(gates: list[Gate]) -> list[Gate]:
    gates = list(gates)

    axd = plt.figure(layout="constrained").subplot_mosaic(
        """
        AAAA
        BCDE
        """,
        height_ratios=[1, 0.1]
    )
    ax = axd["A"]
    fig = ax.get_figure()
    prv = matplotlib.widgets.Button(ax=axd["B"], label="Previous")
    txt = matplotlib.widgets.TextBox(ax=axd["C"], label="Name")
    apply = matplotlib.widgets.Button(ax=axd["D"], label="Apply")
    nxt = matplotlib.widgets.Button(ax=axd["E"], label="Next")
    
    layers = {gate.layer for gate in gates}
    color_iter = iter(plt.rcParams['axes.prop_cycle'].by_key()['color'])
    layer_colors = dict(zip(layers, color_iter))
    

    plots = []
    for idx, gate in enumerate(gates):
        color = layer_colors[gate.layer]
        poly_patch = plot_polygon(
            gate.polygon,
            facecolor="none",
            color=color,
            add_points=False,
            ax=ax)
        
        poly_patch.gate_index = idx
        poly_patch.set_picker(True)
        
        label_plot = ax.annotate(str(gate.label),
                                 gate.label_position,
                                 bbox=dict(boxstyle="round", fc="0.8"),
                                 ha="center", va="center")
        plots.append((poly_patch, label_plot))

    def deselect_gate(idx):
        poly_plot, label_plot = plots[idx]
        poly_plot.set_facecolor("none")
        txt.set_val("")
        plt.draw()

    def select_gate(idx):
        poly_plot, label_plot = plots[idx]
        gate = gates[idx]
        color = layer_colors[gate.layer]
        poly_plot.set_facecolor((color, SELECT_ALPHA))
        if str(gate.label) != str(None):
            txt.set_val(str(gate.label))
        plt.draw()


    current_gate = 0
    select_gate(current_gate)
    def apply_action(*_):
        print("apply", txt.text)
        gate = gates[current_gate]
        _, label_plot = plots[current_gate]
        gate.label = txt.text
        label_plot.set_text(txt.text)
        plt.draw()

    def prev_action(*_):
        print("prev_action")
        nonlocal current_gate
        deselect_gate(current_gate)
        if current_gate == 0:
            current_gate += len(gates)
        current_gate -= 1
        select_gate(current_gate)

    def next_action(*_):
        print("next_action")
        nonlocal current_gate
        deselect_gate(current_gate)
        current_gate += 1
        if current_gate == len(gates):
            current_gate -= len(gates)
        select_gate(current_gate)
    
    def pick_handler(event):
        nonlocal current_gate
        artist = event.artist
        if hasattr(artist, "gate_index"):
            gate_index = artist.gate_index
            if gate_index != current_gate:
                deselect_gate(current_gate)
                current_gate = gate_index
                select_gate(current_gate)
        else:
            raise NotImplementedError(event)
    
    prv.on_clicked(prev_action)
    apply.on_clicked(apply_action)
    nxt.on_clicked(next_action)
    fig.canvas.mpl_connect('pick_event', pick_handler)
    
    widgets = [prv, apply, nxt, txt]
    fig.widgets = widgets


def gate_list_to_string(gates: list[Gate]) -> str:
    def to_json(o):
        if isinstance(o, Gate):
            return dataclasses.asdict(o)
        elif isinstance(o, Polygon):
            return o.wkt
        else:
            return o
    txt = json.dumps(gates, indent=2, default=to_json)
    return txt

def string_to_gate_list(txt: str) -> list[Gate]:
    data = json.loads(txt)
    assert isinstance(data, list)

    gates = []
    for d in data:
        d["polygon"] = wkt.loads(d["polygon"])
        d["label_position"] = tuple(d["label_position"])
        gates.append(Gate(**d))
    return gates



def store_to_file(gates: list[Gate], path: pathlib.Path):
    path = pathlib.Path(path)
    txt = gate_list_to_string(gates)
    path.write_text(txt)


    

def load_from_file(path) -> list[Gate]:
    path = pathlib.Path(path)
    
    