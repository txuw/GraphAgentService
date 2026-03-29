import uvicorn

from .common import build_log_config, get_settings


def main() -> None:
    settings = get_settings()
    log_level: str = str(settings.app.log_level).lower()
    uvicorn.run(
        "graphagentservice.main:app",
        host=settings.app.host,
        port=settings.app.port,
        reload=settings.app.reload,
        log_level=log_level,
        log_config=build_log_config(log_level),
    )


if __name__ == "__main__":
    main()
