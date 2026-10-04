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
import time
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


def _port_in_use(port: int) -> bool:
    """True when something is already listening on 127.0.0.1:port."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _wait_for_port(port: int, log: logging.Logger, timeout: float = 15.0) -> bool:
    """Wait for a previous instance to release the port.

    A process manager restarting this app stops the old instance and starts the
    new one; on Windows the listening socket is released a moment later, so the
    new process can briefly find the port occupied. Waiting it out is the right
    response.

    Deliberately does *not* terminate whoever holds the port. An earlier version
    did, and it turned a restart into a loop: the new process killed the one the
    manager had just started, the manager saw its child die and restarted, and
    round it went. Killing on behalf of a supervisor is the supervisor's job.
    """
    if not _port_in_use(port):
        return True
    log.info("port %s is still held, probably by the instance being replaced "
             "- waiting up to %.0fs", port, timeout)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.5)
        if not _port_in_use(port):
            return True
    return False


if __name__ == "__main__":
    import uvicorn

    _configure_logging()
    log = logging.getLogger("nieuws")
    windowless = os.path.basename(sys.executable).lower().startswith("pythonw")
    port = int(os.environ.get("NIEUWS_PORT", "8500"))

    # A previous instance may still be releasing the port. Wait it out, and
    # report plainly rather than failing with a bare WinError 10048 buried in
    # a restart loop.
    if not _wait_for_port(port, log):
        log.error("port %s is still in use. Another instance or an unrelated "
                  "service is holding it; stop that, or set NIEUWS_PORT.", port)
        sys.exit(1)

    log.info("starting on 127.0.0.1:%s (windowless=%s)", port, windowless)
    uvicorn.run("app.api:app", host="127.0.0.1", port=port, log_level="info")
