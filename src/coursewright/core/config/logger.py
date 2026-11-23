"""Standard-library logging helpers ported from COURSE_BUILDER ``lib/logger.py``.

Library code in ``core``, ``ta``, and ``design`` is silent by default
(AGENTS.md 3.8): ``setup_logger`` attaches a ``NullHandler`` unless a
caller explicitly requests a ``log_file`` or ``console=True``.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path


def setup_logger(
    name: str = "coursewright",
    level: int | None = None,
    log_file: str | Path | None = None,
    console: bool = False,
    *,
    verbose: bool = False,
    logs_dir: Path | None = None,
) -> logging.Logger:
    """Configure and return a ``logging.Logger`` instance.

    Args:
        name: Logger name (typically ``__name__``).
        level: Logging level; defaults to ``DEBUG`` when ``verbose`` is True,
            otherwise ``INFO``.
        log_file: Optional file path for debug file logging.
        console: Attach a stdout ``StreamHandler`` when True; defaults to
            False so library callers stay silent.
        verbose: Enable ``DEBUG`` level and create a timestamped log file
            when ``logs_dir`` is provided.
        logs_dir: Optional directory for auto-named verbose log files.
    """
    logger = logging.getLogger(name)

    resolved_level = level if level is not None else (logging.DEBUG if verbose else logging.INFO)
    logger.setLevel(resolved_level)
    logger.handlers.clear()

    console_formatter = logging.Formatter("%(message)s")
    file_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(resolved_level)
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

    target_log_file: Path | None = Path(log_file) if log_file is not None else None
    if target_log_file is None and verbose and logs_dir is not None:
        logs_dir.mkdir(exist_ok=True, parents=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target_log_file = logs_dir / f"{name}_{timestamp}.log"

    if target_log_file is not None:
        target_log_file.parent.mkdir(exist_ok=True, parents=True)
        file_handler = logging.FileHandler(target_log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

    if not logger.handlers:
        logger.addHandler(logging.NullHandler())

    return logger


def log_action(logger: logging.Logger, action: str, dry_run: bool = False) -> None:
    """Log an action with a dry-run or live prefix."""
    prefix = "Would" if dry_run else "Executing:"
    logger.info("%s %s", prefix, action)


def log_success(logger: logging.Logger, message: str) -> None:
    """Log a completion message."""
    logger.info("SUCCESS: %s", message)


def log_warning(logger: logging.Logger, message: str) -> None:
    """Log a warning message."""
    logger.warning("WARNING: %s", message)


def log_error(logger: logging.Logger, message: str) -> None:
    """Log an error message."""
    logger.error("ERROR: %s", message)


def log_progress(
    logger: logging.Logger,
    current: int,
    total: int,
    item: str = "items",
) -> None:
    """Log progress through an ordered collection."""
    percentage = (current / total) * 100 if total > 0 else 0.0
    logger.info("Progress: %d/%d %s (%.1f%%)", current, total, item, percentage)


def log_divider(logger: logging.Logger, char: str = "=", length: int = 60) -> None:
    """Log a horizontal divider line."""
    logger.info(char * length)


def log_section(logger: logging.Logger, title: str) -> None:
    """Log a titled section header."""
    log_divider(logger)
    logger.info(title)
    log_divider(logger)


class DryRunContext:
    """Context manager that logs the start and completion of a dry or live run."""

    def __init__(self, logger: logging.Logger, dry_run: bool = True) -> None:
        self.logger = logger
        self.dry_run = dry_run

    def __enter__(self) -> DryRunContext:
        if self.dry_run:
            log_section(self.logger, "DRY RUN MODE - No changes will be made")
        else:
            log_section(self.logger, "LIVE MODE - Making changes")
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> bool:
        if exc_type is None:
            if self.dry_run:
                self.logger.info("Dry run complete - review changes above")
            else:
                log_success(self.logger, "Changes complete")
        return False
