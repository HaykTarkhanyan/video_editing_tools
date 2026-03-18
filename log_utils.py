"""Shared logging setup for video editing tools.

Usage in any script:
    from log_utils import setup_logger
    log = setup_logger("detect_silence")   # creates wip/detect_silence_20260318_211500.log
    log.info("Starting analysis...")
"""

import logging
import os
import sys
from datetime import datetime


def setup_logger(name, log_dir="wip"):
    """Create a logger that writes to both console and a timestamped log file.

    Returns a standard logging.Logger. Every message goes to:
      - stdout (concise format)
      - wip/<name>_<timestamp>.log (detailed format with timestamps)
    """
    os.makedirs(log_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(log_dir, f"{name}_{timestamp}.log")

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # Avoid duplicate handlers if called multiple times
    if logger.handlers:
        return logger

    # File handler — detailed, DEBUG level
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))

    # Console handler — concise, INFO level
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(message)s"))

    logger.addHandler(fh)
    logger.addHandler(ch)

    logger.info(f"Log file: {log_path}")
    logger.debug(f"Logger initialized: {name}")
    return logger
