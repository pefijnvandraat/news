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

# Hosted platforms mount persistent storage outside the deployment directory,
# and often make the code directory read-only. NIEUWS_DATA keeps the database
# and the log together wherever that happens to be.
LOG_DIR = os.environ.get("NIEUWS_DATA") or os.path.join(HERE, "data")
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

    # A hosted platform tells us where to listen: App Service and most PaaS
    # set PORT and expect 0.0.0.0. Locally we stay on the loopback, because a
    # personal news reader has no business being reachable from the network.
    hosted = bool(os.environ.get("WEBSITE_SITE_NAME") or os.environ.get("PORT"))
    port = int(os.environ.get("NIEUWS_PORT") or os.environ.get("PORT") or "8500")
    host = os.environ.get("NIEUWS_HOST") or ("0.0.0.0" if hosted else "127.0.0.1")

    # The port dance below only makes sense for a local restart; a hosted
    # platform hands us a fresh container with the port already free.
    if not hosted and not _wait_for_port(port, log):
        log.error("port %s is still in use. Another instance or an unrelated "
                  "service is holding it; stop that, or set NIEUWS_PORT.", port)
        sys.exit(1)

    log.info("starting on %s:%s (windowless=%s, hosted=%s)",
             host, port, windowless, hosted)
    uvicorn.run("app.api:app", host=host, port=port, log_level="info")
