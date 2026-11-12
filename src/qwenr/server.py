"""Run qwenr from a config file."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import uvicorn

from qwenr.app import build_app
from qwenr.config import ConfigError, load_settings
from qwenr.dotenv import load_dotenv_near


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="qwenr", description="Token Plan role router for OpenBot")
    parser.add_argument(
        "--config",
        default=os.environ.get("QWENR_CONFIG", "config.yaml"),
        help="Path to config.yaml (or set QWENR_CONFIG)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=os.environ.get("QWENR_LOG", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    path = Path(args.config)
    for dotenv_path in load_dotenv_near(path):
        logging.getLogger("qwenr").info("loaded %s", dotenv_path)
    try:
        settings = load_settings(path)
    except ConfigError as exc:
        print(f"qwenr: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    app = build_app(settings)
    uvicorn.run(app, host=settings.listen_host, port=settings.listen_port, log_level="info")


if __name__ == "__main__":
    main()
