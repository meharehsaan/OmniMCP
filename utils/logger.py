import sys
import logging
import colorlog
from logging.handlers import RotatingFileHandler

# custom logger with colors
def setup_logger(
    name: str = __name__,
    log_file: str = "app.log",
    level: int = logging.INFO,
) -> logging.Logger:
    # Define log colors
    log_colors = {
        "DEBUG": "cyan",
        "INFO": "green",
        "WARNING": "yellow",
        "ERROR": "red",
        "CRITICAL": "bold_red",
    }

    # Console (colored) formatter
    console_formatter = colorlog.ColoredFormatter(
        "%(log_color)s%(asctime)s - %(levelname)s - %(filename)s - %(funcName)s - %(message)s",
        log_colors=log_colors,
    )

    # File (plain) formatter
    file_formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(filename)s - %(funcName)s - %(message)s"
    )

    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False  # avoid duplicate logs

    # Prevent adding handlers multiple times
    if logger.handlers:
        return logger

    # Rotating file handler
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=50 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(file_formatter)

    # Console handler (Use stderr for MCP compatibility) donot use stdout
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(console_formatter)

    # Add handlers
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger