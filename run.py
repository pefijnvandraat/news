"""Entry point: python run.py  (or: uvicorn app.api:app --port 8500)."""
from __future__ import annotations

import os
import sys

import uvicorn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    port = int(os.environ.get("NIEUWS_PORT", "8500"))
    uvicorn.run("app.api:app", host="127.0.0.1", port=port, log_level="info")
