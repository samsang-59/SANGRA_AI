"""Start the backend: `python -m app` (from the backend folder, next to the .env file)."""

import sys

import uvicorn

from app.core.config import SettingsError, load_settings
from app.core.logging import setup_logging
from app.main import create_app


def main() -> None:
    try:
        settings = load_settings()
    except SettingsError as exc:
        print(f"SANGRA_AI cannot start. {exc}", file=sys.stderr)
        sys.exit(1)
    setup_logging(settings.log_level, settings.secret_values())
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_config=None)


if __name__ == "__main__":
    main()
