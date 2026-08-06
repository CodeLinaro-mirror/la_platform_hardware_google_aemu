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

"""Unit tests for lib.logging_config module."""

import logging
import os
import sys
import tempfile
import unittest

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from lib.logging_config import get_logger, setup_logging


class LoggingConfigTest(unittest.TestCase):
    """Tests for setup_logging and get_logger functions."""

    def tearDown(self):
        root_logger = logging.getLogger()
        for h in list(root_logger.handlers):
            root_logger.removeHandler(h)
            h.close()

    def test_setup_logging_standard(self):
        """Tests standard non-verbose logging initialization."""
        logger = setup_logging(verbose=False)
        self.assertEqual(logger.level, logging.INFO)
        root = logging.getLogger()
        self.assertEqual(root.level, logging.INFO)
        self.assertTrue(len(root.handlers) >= 1)

    def test_setup_logging_verbose(self):
        """Tests verbose logging initialization enabling DEBUG level."""
        logger = setup_logging(verbose=True)
        self.assertEqual(logger.level, logging.DEBUG)
        root = logging.getLogger()
        self.assertEqual(root.level, logging.DEBUG)

    def test_setup_logging_file(self):
        """Tests file handler logging creation."""
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            setup_logging(verbose=True, log_file=tmp_path)
            test_logger = get_logger("emu_dev_cli.test")
            test_logger.debug("Test log entry to file")

            with open(tmp_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("Test log entry to file", content)
            self.assertIn("DEBUG", content)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_get_logger(self):
        """Tests get_logger returning named Logger instance."""
        lg = get_logger("test_module")
        self.assertIsInstance(lg, logging.Logger)
        self.assertEqual(lg.name, "test_module")


if __name__ == "__main__":
    unittest.main()
