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

"""Unit tests for AEMU developer and agent gadgets."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import install
from pane_title import zellij_pane_title


class GadgetsTest(unittest.TestCase):

    def test_extract_fallback_title_with_bug_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "transcript.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "type": "USER_INPUT",
                        "content": "<USER_REQUEST>Investigate https://buganizer.corp.google.com/issues/555286414 keyboard DPAD</USER_REQUEST>",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            title = zellij_pane_title.extract_fallback_title(str(transcript))
            self.assertTrue(title.startswith("b/555286414:"))

    def test_format_osc_sequence_strips_control_chars(self):
        seq = zellij_pane_title.format_osc_sequence("⏳ b/123456\n\007test")
        self.assertEqual(
            seq, "\033]0;⏳ b/123456 test\007\033]2;⏳ b/123456 test\007"
        )

    def test_save_and_load_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = zellij_pane_title.get_state_path(
                "/dev/pts/4", state_dir=Path(tmp)
            )
            zellij_pane_title.save_state(state_path, "win-aarch64", "conv-123")
            loaded = zellij_pane_title.load_state(state_path)
            self.assertEqual(loaded["title"], "win-aarch64")
            self.assertEqual(loaded["conversationId"], "conv-123")

    def test_install_preserves_existing_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            hooks_json = config_dir / "hooks.json"
            hooks_json.write_text(
                json.dumps({"existing-hook": {"enabled": True}}),
                encoding="utf-8",
            )
            installed = install.install_pane_title_gadget(config_dir)
            self.assertEqual(len(installed), 3)
            merged = json.loads(hooks_json.read_text(encoding="utf-8"))
            self.assertIn("existing-hook", merged)
            self.assertIn("zellij-pane-title", merged)
            self.assertTrue((config_dir / "hooks" / "zellij_pane_title.py").is_file())
            self.assertTrue((config_dir / "rules" / "zellij_pane_title.md").is_file())


if __name__ == "__main__":
    unittest.main()
