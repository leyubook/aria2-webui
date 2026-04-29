from __future__ import annotations

import os
import uvicorn

from server.main import load_settings


def main() -> None:
    settings = load_settings()
    reload = os.environ.get("ARIA2_PLUS_RELOAD", "0") == "1"
    uvicorn.run(
        "server.main:app",
        host=str(settings.get("api_host", "127.0.0.1")),
        port=int(settings.get("api_port", 8080)),
        reload=reload,
    )


if __name__ == "__main__":
    main()
