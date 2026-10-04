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


def _reclaim_port(port: int, log: logging.Logger, timeout: float = 12.0) -> bool:
    """Terminate a stale copy of *this* app that is still holding the port.

    Process managers on Windows can lose track of a windowless child. The
    orphan keeps serving, every new instance fails to bind, and the manager
    sits in a restart loop while the site still appears to work - which is a
    genuinely confusing state to debug.

    Only processes whose command line references this exact script are ever
    touched, so this can never kill an unrelated service that happens to use
    the same port; in that case we leave it alone and report the conflict.
    """
    try:
        import psutil
    except ImportError:
        log.warning("psutil not installed - cannot reclaim port %s automatically", port)
        return False

    me = os.getpid()
    script = os.path.abspath(__file__).lower()
    stale = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        if proc.info["pid"] == me:
            continue
        cmdline = " ".join(proc.info["cmdline"] or []).lower()
        if script in cmdline:
            stale.append(proc)

    if not stale:
        return False

    log.warning("reclaiming port %s from %d stale instance(s): %s",
                port, len(stale), [p.pid for p in stale])
    for proc in stale:
        try:
            proc.terminate()
        except psutil.Error:
            pass
    gone, alive = psutil.wait_procs(stale, timeout=timeout * 0.6)
    for proc in alive:
        try:
            proc.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(alive, timeout=timeout * 0.4)

    # Windows frees the listening socket a moment after the owner exits.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _port_in_use(port):
            return True
        time.sleep(0.4)
    return not _port_in_use(port)


if __name__ == "__main__":
    import uvicorn

    _configure_logging()
    log = logging.getLogger("nieuws")
    windowless = os.path.basename(sys.executable).lower().startswith("pythonw")
    port = int(os.environ.get("NIEUWS_PORT", "8500"))

    # A previous instance may still be holding the port. Under a process
    # manager the raw bind failure is a cryptic WinError 10048 buried in a
    # restart loop, so reclaim our own stale copy and report anything else
    # plainly instead of looping.
    if _port_in_use(port) and not _reclaim_port(port, log):
        log.error("port %s is in use by something that is not this app. "
                  "Free it, or set NIEUWS_PORT to another port.", port)
        sys.exit(1)

    log.info("starting on 127.0.0.1:%s (windowless=%s)", port, windowless)
    uvicorn.run("app.api:app", host="127.0.0.1", port=port, log_level="info")
