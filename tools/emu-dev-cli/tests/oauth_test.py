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

"""Unit tests for lib.oauth module."""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from lib.oauth import OAuthTokenManager


class OAuthTokenManagerTest(unittest.TestCase):
    """Tests for OAuthTokenManager class."""

    def test_clean_token(self):
        """Tests token cleaning logic (stripping Bearer prefix, newlines, and errors)."""
        self.assertEqual(
            OAuthTokenManager.clean_token("Bearer my_token_123\n"), "my_token_123"
        )
        self.assertEqual(OAuthTokenManager.clean_token("my_token_123"), "my_token_123")
        self.assertIsNone(OAuthTokenManager.clean_token(None))
        self.assertIsNone(OAuthTokenManager.clean_token("Error: Failed to fetch token"))
        self.assertIsNone(OAuthTokenManager.clean_token("invalid token with spaces"))

    @patch.dict(os.environ, {"BUGANIZER_TOKEN": "Bearer env_token_999"})
    def test_get_token_from_env(self):
        """Tests reading token from environment variables."""
        manager = OAuthTokenManager()
        self.assertEqual(manager.get_token_from_env(), "env_token_999")

    def test_acquire_token_explicit(self):
        """Tests acquire_token with explicit user token."""
        manager = OAuthTokenManager()
        token = manager.acquire_token("Bearer explicit_user_tok")
        self.assertEqual(token, "explicit_user_tok")

    @patch("subprocess.run")
    def test_fetch_oauth2l_token(self, mock_run):
        """Tests fetching token via oauth2l subprocess command."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="fetched_oauth2l_token\n"
        )
        manager = OAuthTokenManager()
        token = manager.fetch_oauth2l_token()
        self.assertEqual(token, "fetched_oauth2l_token")


if __name__ == "__main__":
    unittest.main()
