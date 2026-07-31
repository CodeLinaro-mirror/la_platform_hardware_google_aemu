# Copyright 2026 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Standardized Python logging configuration module for emu-dev-cli."""

import logging
import os
import sys
from typing import Optional


LOG_FORMAT_VERBOSE = (
    "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s"
)
LOG_FORMAT_STANDARD = "%(asctime)s [%(levelname)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
    verbose: bool = False,
    log_file: Optional[str] = None,
) -> logging.Logger:
    """Configures root and emu_dev_cli loggers with console and file handlers.

    Args:
        verbose: If True, sets log level to DEBUG and uses detailed format.
        log_file: Optional log file path. If None, checks EMU_DEV_CLI_LOG_FILE env var.

    Returns:
        Configured logger for emu_dev_cli.
    """
    log_level = logging.DEBUG if verbose else logging.INFO
    fmt = LOG_FORMAT_VERBOSE if verbose else LOG_FORMAT_STANDARD

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Clear existing handlers to prevent duplicate logs
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)

    # Console StreamHandler sending logs to sys.stderr
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(logging.Formatter(fmt, datefmt=DATE_FORMAT))
    root_logger.addHandler(console_handler)

    # Optional FileHandler logging
    target_log_file = log_file or os.environ.get("EMU_DEV_CLI_LOG_FILE")
    if target_log_file:
        try:
            log_dir = os.path.dirname(os.path.abspath(target_log_file))
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)
            file_handler = logging.FileHandler(
                target_log_file, mode="a", encoding="utf-8"
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(
                logging.Formatter(LOG_FORMAT_VERBOSE, datefmt=DATE_FORMAT)
            )
            root_logger.addHandler(file_handler)
        except Exception as e:
            sys.stderr.write(
                f"⚠️ Warning: Could not initialize log file {target_log_file}: {e}\n"
            )

    logger = logging.getLogger("emu_dev_cli")
    logger.setLevel(log_level)
    return logger


def get_logger(name: str) -> logging.Logger:
    """Returns a logger instance for the given module name.

    Args:
        name: Module name string.

    Returns:
        logging.Logger instance.
    """
    return logging.getLogger(name)
