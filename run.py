"""Entry point.

    python run.py                 # console
    pythonw.exe run.py            # windowless (how pm2 runs it on Windows)

Under pythonw.exe there is no console and stdout/stderr are discarded, so the
server always writes a rotating log to data/nieuws.log as well. That keeps the
app diagnosable when it runs hidden as a background service.
"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

LOG_DIR = os.path.join(HERE, "data")
LOG_FILE = os.path.join(LOG_DIR, "nieuws.log")


def _configure_logging() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    handler = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3,
                                  encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # uvicorn installs its own handlers; make sure they also reach the file.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).addHandler(handler)
    # One INFO line per feed request would bury the refresh summaries, and we
    # already record per-source health in the sources table.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


if __name__ == "__main__":
    import uvicorn

    _configure_logging()
    windowless = os.path.basename(sys.executable).lower().startswith("pythonw")
    port = int(os.environ.get("NIEUWS_PORT", "8500"))
    logging.getLogger("nieuws").info(
        "starting on 127.0.0.1:%s (windowless=%s)", port, windowless)
    uvicorn.run("app.api:app", host="127.0.0.1", port=port, log_level="info")
