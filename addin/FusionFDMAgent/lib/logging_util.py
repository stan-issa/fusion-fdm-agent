"""File logging for the add-in.

Fusion swallows most exceptions raised inside event handlers and its own Text
Commands window is easy to miss, so a plain file log is the primary way to see
what the add-in did.
"""

import logging
import os

from .. import config

_logger = None


def get_logger():
    """Return the shared add-in logger, configuring it on first use."""
    global _logger
    if _logger is not None:
        return _logger

    logger = logging.getLogger("fdm_agent")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    # Re-running the add-in re-imports this module in a fresh interpreter state
    # but Fusion may keep the old logging objects, so clear stale handlers.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    try:
        os.makedirs(config.LOG_DIR, exist_ok=True)
        file_handler = logging.FileHandler(config.LOG_FILE, encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
        logger.addHandler(file_handler)
    except OSError:
        # Without a log file we still want a usable logger object.
        logger.addHandler(logging.NullHandler())

    _logger = logger
    return logger
