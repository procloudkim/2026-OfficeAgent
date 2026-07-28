"""FastAPI application entrypoint."""

from __future__ import annotations

import uvicorn

from officeagent.api.app import create_app

app = create_app()

def run() -> None:
    """Run the development server through the project console script."""

    uvicorn.run("officeagent.main:app", host="0.0.0.0", port=8000)
