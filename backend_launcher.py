from __future__ import annotations

import os

import uvicorn

from backend.amadeus_app.main import app


def main() -> None:
    host = os.getenv("AMADEUS_DESKTOP_HOST", "0.0.0.0")
    port = int(os.getenv("AMADEUS_DESKTOP_PORT", "8765"))
    uvicorn.run(
        app,
        host=host,
        port=port,
        reload=False,
        access_log=False,
    )


if __name__ == "__main__":
    main()
