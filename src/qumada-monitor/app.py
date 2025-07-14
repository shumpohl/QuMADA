#!/usr/bin/env python3
"""
Live quantum‑device monitor: geometry + voltages ➜ interactive SVG plot.

Start with:
    python dash_device_monitor.py --ws ws://localhost:8765 --colorscale Cividis
"""
import argparse, json, itertools, os
from collections import defaultdict

import plotly.graph_objects as go
import plotly.express as px
from plotly.colors import sample_colorscale
from shapely.geometry import Polygon

import dash.exceptions
from dash import Dash, html, dcc, dash_table, Input, Output, State, no_update
from dash_extensions import WebSocket                           # pip install dash-extensions

# ---------------- helpers -------------------------------------------------- #
from qumada.utils.geometry import string_to_gate_list


def layer_palette(layers):
    """Return dict layer -> rgba border colour (qualitative palette, repeats if needed)."""
    palette = itertools.cycle(px.colors.qualitative.Plotly)
    return {layer: next(palette) for layer in layers}


def voltage_colour(v, vmin, vmax, colorscale):
    """Map voltage to rgba string using the chosen Plotly colourscale."""
    if vmin == vmax:
        return sample_colorscale(colorscale, [0.5])[0]            # flat value
    ratio = (v - vmin) / (vmax - vmin)
    return sample_colorscale(colorscale, [ratio])[0]              # :contentReference[oaicite:9]{index=9}


def gates_to_figure(gates, voltages, colorscale):
    """Build a Plotly figure from gate list + latest voltages dict."""
    if not gates:
        return go.Figure()

    # 1) derive colour mapping
    v_values = [voltages.get(g.label, 0.0) for g in gates]
    vmin, vmax = min(v_values), max(v_values)
    layers = sorted({g.layer for g in gates})
    border_col = layer_palette(layers)                             # :contentReference[oaicite:10]{index=10}

    fig = go.Figure()
    for gate in gates:
        poly: Polygon = gate.polygon
        x, y = poly.exterior.xy
        x = list(x)
        y = list(y)
        v = voltages.get(gate.label, 0.0)
        fill_col = voltage_colour(v, vmin, vmax, colorscale)
        line_col = border_col[gate.layer]

        fig.add_trace(
            go.Scatter(
                x=x,
                y=y,
                fill="toself",                                     # polygon fill trick :contentReference[oaicite:11]{index=11}
                fillcolor=fill_col,
                line=dict(color=line_col, width=1),
                hoverinfo="text",
                text=f"{gate.label}: {v:.3f} V",
                showlegend=False,
            )
        )
        # add static label at predefined position
        if getattr(gate, "label_position", None):
            lx, ly = gate.label_position
            fig.add_annotation(x=lx, y=ly,
                               text=f"{gate.label}: {v:.3f} V",
                               showarrow=False,
                               font=dict(size=10, color="black"))

    fig.update_layout(
        xaxis=dict(scaleanchor="y", visible=False),
        yaxis=dict(visible=False),
        margin=dict(l=0, r=0, t=0, b=0),
        plot_bgcolor="white",
    )
    return fig


# ---------------- Dash app -------------------------------------------------- #
def make_app(ws_url: str, colorscale: str) -> Dash:
    app = Dash(__name__, title="Quantum‑dot voltage monitor")

    app.layout = html.Div(
        [
            html.H3("Live device layout"),
            dcc.Dropdown(
                id="colorscale-dropdown",
                value=colorscale,
                options=[{"label": cs, "value": cs} for cs in px.colors.named_colorscales()],
                clearable=False,
                style={"width": "250px"},
            ),
            WebSocket(id="ws", url=ws_url),
            dcc.Store(id="gate-geom"),
            dcc.Store(id="voltages"),
            dcc.Graph(id="layout-graph", style={"height": "700px"}),
            html.Hr(),
            dash_table.DataTable(
                id="volt-table",
                columns=[{"name": "Parameter", "id": "param"},
                         {"name": "Voltage [V]", "id": "value"}],
                style_cell={"fontFamily": "monospace", "padding": "2px 6px"},
                style_table={"max-height": "400px", "overflowY": "auto"},
            ),
        ],
        style={"font-family": "Source Sans Pro, sans-serif", "margin": "0 20px"},
    )

    # ---------- 1) unpack every WebSocket message -------------------------- #
    @app.callback(
        Output("gate-geom", "data"),
        Output("voltages", "data"),
        Output("volt-table", "data"),
        Input("ws", "message"),
        State("gate-geom", "data"),
        prevent_initial_call=True,
    )
    def _unpack_ws(msg, stored_geom):
        if msg is None:
            return no_update, no_update, no_update

        data = json.loads(msg["data"])
        # --- parameters --------------------------------------------------- #
        params = data.get("parameters", [])
        volts = {p["name"]: p["value"] for p in params}
        table_rows = [{"param": k, "value": f"{v:.3f}"} for k, v in sorted(volts.items())]

        # --- geometry (optional) ------------------------------------------ #
        geom_str = data.get("gate_geometry", stored_geom)
        return geom_str, volts, table_rows

    # ---------- 2) (re)draw whenever geometry OR voltages OR colourscale changes ---- #
    @app.callback(
        Output("layout-graph", "figure"),
        Input("gate-geom", "data"),
        Input("voltages", "data"),
        Input("colorscale-dropdown", "value"),
        prevent_initial_call=True,
    )
    def _draw_layout(geom_str, volts, colorscale_selected):
        if geom_str is None or volts is None:
            raise dash.exceptions.PreventUpdate

        gates = string_to_gate_list(geom_str)
        fig = gates_to_figure(gates, volts, colorscale_selected)
        return fig

    return app

