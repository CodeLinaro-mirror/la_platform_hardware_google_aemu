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

"""Unit tests for lib.stacktrace module."""

import os
import sys
import unittest

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from lib.stacktrace import StackTraceParser, extract_top_fault_frame


class StackTraceTest(unittest.TestCase):
    """Tests for StackTraceParser and fault frame extraction."""

    def test_extract_top_fault_frame_basic(self):
        """Tests extracting faulting function and file from basic crash dump."""
        sample_dump = """
Crashing Thread:
#0 0x00007f123456 in abort () from /lib64/libc.so.6
#1 0x00007f123457 in android::FrameBuffer::post() at FrameBuffer.cpp:142
#2 0x00007f123458 in RenderThread::main() at RenderThread.cpp:88
"""
        func, file_info = extract_top_fault_frame(sample_dump)
        self.assertEqual(func, "android::FrameBuffer::post")
        self.assertEqual(file_info, "FrameBuffer.cpp:142")

    def test_extract_top_fault_frame_class_method(self):
        """Tests extracting fault frame using parser instance."""
        parser = StackTraceParser()
        sample_dump = """
Thread 0 (crashed):
#0 0x12345 __kernel_vsyscall
#1 0x56789 VkDecoder::vkQueueSubmit [vulkan_decoder.cpp:250]
"""
        func, file_info = parser.extract_top_fault_frame(sample_dump)
        self.assertEqual(func, "VkDecoder::vkQueueSubmit")
        self.assertEqual(file_info, "vulkan_decoder.cpp:250")


if __name__ == "__main__":
    unittest.main()
