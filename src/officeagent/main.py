"""FastAPI application entrypoint."""

from __future__ import annotations

import uvicorn

from officeagent.api.app import create_app
from officeagent.infrastructure.settings import get_settings

app = create_app()


def run() -> None:
    """Run the development server through the project console script."""

    settings = get_settings()
    uvicorn.run(
        "officeagent.main:app",
        host=settings.api_host,
        port=settings.api_port,
    )
