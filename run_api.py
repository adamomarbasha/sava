#!/usr/bin/env python3
"""Development launcher.

Production does NOT use this file. A deployed Sava runs uvicorn directly, with
the process manager supplying host and port:

    uvicorn api.main:app --host 0.0.0.0 --port $PORT

and the worker as a second process:

    python -m api.worker

This script exists so `python run_api.py` still does the obvious thing on a
laptop. It refuses to run with reload enabled outside development, because
`--reload` in production doubles memory, watches the filesystem forever, and
silently restarts the server on any file change.
"""
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent

if __name__ == "__main__":
    import uvicorn

    environment = os.getenv("ENVIRONMENT", "development").lower()
    is_development = environment in ("development", "dev")

    # Honour the platform's port. Every managed host injects PORT and expects
    # the process to bind it; hardcoding 8000 is why a container appears to
    # start and then fails its health check.
    port = int(os.getenv("PORT", "8000"))

    if not is_development:
        print(f"refusing to start the development launcher with ENVIRONMENT={environment}.\n"
              f"Run: uvicorn api.main:app --host 0.0.0.0 --port {port}", file=sys.stderr)
        raise SystemExit(1)

    # 0.0.0.0 binds every interface, which is what lets a physical phone reach a
    # Mac on the same network — and what a container requires. It is correct in
    # both cases; the thing that must not be public is the *database*, not this.
    uvicorn.run("api.main:app", host="0.0.0.0", port=port,
                reload=True, reload_dirs=[str(REPO_ROOT / "api")])
