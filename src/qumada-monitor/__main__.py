import argparse

from .app import run_app

p = argparse.ArgumentParser(description="Dash front-end for your measurement WebSocket")
p.add_argument("--ws", required=True, help="WebSocket URL, e.g. ws://localhost:8765")
p.add_argument("--host", default="127.0.0.1", help="Dash host to bind")
p.add_argument("--port", default=8050, type=int, help="Dash port (default: 8050)")
args = p.parse_args()

run_app(args.ws, args.host, args.port, debug=True)
