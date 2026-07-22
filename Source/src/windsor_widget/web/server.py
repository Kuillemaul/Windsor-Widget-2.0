"""Command-line entry point for the Windsor Widget web server."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from windsor_widget.web.app import create_app


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Windsor Widget 2.0 on the local network.")
    parser.add_argument("--config", type=Path, default=Path("config/development.local.json"))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    app = create_app(args.config)
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload, access_log=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
