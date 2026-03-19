import uvicorn

from .config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "graphagentservice.main:app",
        host=settings.app.host,
        port=settings.app.port,
        reload=settings.app.reload,
        log_level=settings.app.log_level,
    )


if __name__ == "__main__":
    main()
