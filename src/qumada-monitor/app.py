import json, argparse
from dash import Dash, html, dash_table, no_update
from dash.dependencies import Input, Output
from dash_extensions import WebSocket                       # pip install dash-extensions

def make_app(ws_url: str) -> Dash:
    app = Dash(__name__, title="Voltage monitor")
    app.layout = html.Div(
        [
            WebSocket(id="ws", url=ws_url),                # ➊ opens the socket in the client
            dash_table.DataTable(                          # ➋ shows the live voltages
                id="tbl",
                columns=[
                    {"name": "Name",     "id": "name"},
                    {"name": "Value",    "id": "value"},
                    {"name": "Unit",     "id": "unit"},
                    {"name": "Label",    "id": "label"},
                    {"name": "Timestamp","id": "ts"},
                ],
                style_cell={"fontFamily": "monospace"},
                data=[],
            ),
        ]
    )

    @app.callback(Output("tbl", "data"), Input("ws", "message"))
    def _update_table(msg):
        if msg is None:                 # nothing yet
            return no_update
        payload = json.loads(msg["data"])

        # assume your message looks like {"timestamp": 1.725e9, "voltages": {"P1":0.123, "P2":…}}
        ts   = payload["timestamp"]
        parameters = payload["parameters"]
        rows = []
        for parameter in parameters:
            if "exception" in parameter:
                rows.append({
                    "name": "",
                    "value": parameter["exception"],
                    "unit": "",
                    "label": "",
                    "ts": "",
                })
            else:
                try:
                    row = {
                        "name": parameter["name"],
                        "value": parameter["value"],
                        "unit": parameter["unit"],
                        "label": parameter["label"],
                        "ts": parameter["timestamp"],
                    }
                except KeyError as e:
                    row = {
                        "name": "",
                        "value": f"{e} not in {parameter!r}",
                        "unit": "",
                        "label": "",
                        "ts": "",
                    }
                rows.append(row)
        return rows                     # entire table is replaced every update

    return app

def run_app(ws_url: str, host: str, port: int, debug: bool):
    app = make_app(ws_url)
    app.run(host=host, port=port, debug=debug)
