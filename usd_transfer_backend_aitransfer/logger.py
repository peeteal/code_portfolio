import logging
import sys

# Configure root logger
logger = logging.getLogger("Exporter")
logger.setLevel(logging.DEBUG)  # Accept everything

# Formatter with explicit prefixes
formatter = logging.Formatter(
    fmt="%(asctime)s %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# Single handler to stdout (shows everything)
stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setLevel(logging.DEBUG)  # Make sure DEBUG comes through
stdout_handler.setFormatter(formatter)

# Attach only once
if not logger.handlers:
    logger.addHandler(stdout_handler)
# Convenience wrapper functions
def log_stdout(msg: str):
    """General info messages to stdout."""
    logger.info(msg)

def log_debug(msg: str):
    """Debug messages (stdout)."""
    logger.debug(msg)

def log_warning(msg: str):
    """Warnings to stderr."""
    logger.warning(msg)

def log_error(msg: str):
    """Errors to stderr."""
    logger.error(msg)
