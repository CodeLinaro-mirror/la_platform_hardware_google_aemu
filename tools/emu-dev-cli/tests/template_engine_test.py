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

"""Unit tests for TemplateEngine."""

from pathlib import Path
import tempfile
import unittest
from lib.template_engine import TemplateEngine, get_template_engine


class TemplateEngineTest(unittest.TestCase):

    def test_render_template(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tpl_file = Path(tmpdir) / "test_prompt.md"
            tpl_file.write_text("Hello $name! Target: $target.", encoding="utf-8")

            engine = TemplateEngine(Path(tmpdir))
            out = engine.render("test_prompt.md", {"name": "Antigravity", "target": "emulator_linux_x64"})
            self.assertEqual(out, "Hello Antigravity! Target: emulator_linux_x64.")

    def test_get_template_engine(self):
        engine = get_template_engine()
        self.assertIsNotNone(engine)
        self.assertTrue(engine.template_dir.exists())
        self.assertTrue((engine.template_dir / "jetski_investigation_prompt.md").exists())


if __name__ == "__main__":
    unittest.main()
