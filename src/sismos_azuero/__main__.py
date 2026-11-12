"""Punto de entrada: ``uv run python -m sismos_azuero {fetch,sounds,render,all}``."""

import logging

from sismos_azuero import main as pipeline
from sismos_azuero.logging_setup import setup_logging

logger = logging.getLogger(__name__)


def main() -> None:
    setup_logging(log_filename="sismos_azuero.log")
    logger.info("sismos-azuero iniciado")
    pipeline.main()


if __name__ == "__main__":
    main()
