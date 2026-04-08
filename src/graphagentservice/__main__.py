import uvicorn

from .common.logging import build_log_config, configure_logging
from .config import get_settings


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    uvicorn.run(
        "graphagentservice.main:app",
        host=settings.app.host,
        port=settings.app.port,
        reload=settings.app.reload,
        log_level=settings.app.log_level,
        log_config=build_log_config(settings),
        access_log=bool(settings.get("observability.logging.access_log", True)),
    )


if __name__ == "__main__":
    main()
