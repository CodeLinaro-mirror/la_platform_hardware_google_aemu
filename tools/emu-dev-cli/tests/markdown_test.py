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

"""Unit tests for lib.markdown module."""

import os
import sys
import unittest

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from lib.markdown import extract_yaml_block, extract_fenced_code_blocks


class MarkdownTest(unittest.TestCase):
    """Tests for markdown parsing and block extraction utilities."""

    def test_extract_yaml_block(self):
        """Tests extracting key-value pairs from actionability block in Markdown."""
        sample_md = """
# Root Cause Analysis

actionability:
  fixable: true
  target_file: "android/FrameBuffer.cpp" # Target source file
  target_function: 'android::FrameBuffer::post'
  remediation_summary: Check buffer pointer
```
"""
        block = extract_yaml_block(sample_md, "actionability:")
        self.assertEqual(block.get("fixable"), "true")
        self.assertEqual(block.get("target_file"), "android/FrameBuffer.cpp")
        self.assertEqual(block.get("target_function"), "android::FrameBuffer::post")
        self.assertEqual(block.get("remediation_summary"), "Check buffer pointer")

    def test_extract_fenced_code_blocks(self):
        """Tests extracting code from markdown fenced blocks."""
        sample_md = """
```python
print("Hello")
```
Some other text
```bash
echo "World"
```
"""
        py_blocks = extract_fenced_code_blocks(sample_md, lang="python")
        self.assertEqual(len(py_blocks), 1)
        self.assertEqual(py_blocks[0].strip(), 'print("Hello")')


if __name__ == "__main__":
    unittest.main()
