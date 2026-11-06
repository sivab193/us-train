"""Run the API with ``python -m app``."""

from __future__ import annotations

import uvicorn

from .config import Settings


if __name__ == "__main__":
    settings = Settings.from_env()
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port)
