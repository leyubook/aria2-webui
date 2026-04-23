from __future__ import annotations

import uvicorn

from server.main import load_settings


def main() -> None:
    settings = load_settings()
    uvicorn.run(
        "server.main:app",
        host=str(settings.get("api_host", "127.0.0.1")),
        port=int(settings.get("api_port", 8080)),
        reload=True,
    )


if __name__ == "__main__":
    main()
