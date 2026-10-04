"""Gunicorn configuration for App Service (Linux).

App Service starts gunicorn by default. Two settings matter here:

* **One worker.** The data lives in a SQLite file and the refresh scheduler is
  an in-process thread. A second worker would mean two schedulers fetching the
  same feeds and two writers on one database file.
* **A long timeout.** A full refresh sweep walks every feed; the default 30s
  would shoot the worker during a slow sweep.
"""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = 1
worker_class = "uvicorn.workers.UvicornWorker"
timeout = 300
graceful_timeout = 60
keepalive = 10
accesslog = "-"
errorlog = "-"
