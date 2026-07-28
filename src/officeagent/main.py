"""FastAPI application entrypoint."""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI

app = FastAPI(
    title="OfficeAgent Document Q&A API",
    version="0.1.0",
    description="Document-grounded question answering with verifiable citations.",
)


@app.get("/health/live", tags=["health"])
async def liveness() -> dict[str, str]:
    """Return process liveness without touching external dependencies."""

    return {"status": "ok"}


def run() -> None:
    """Run the development server through the project console script."""

    uvicorn.run("officeagent.main:app", host="0.0.0.0", port=8000)
