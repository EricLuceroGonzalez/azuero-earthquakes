"""Punto de entrada: ``uv run python -m sismos_azuero``."""

import logging

from sismos_azuero.logging_setup import setup_logging

logger = logging.getLogger(__name__)


def main() -> None:
    setup_logging(log_filename="sismos_azuero.log")
    logger.info("sismos-azuero iniciado")


if __name__ == "__main__":
    main()
