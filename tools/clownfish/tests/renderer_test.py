# Copyright 2026 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for Clownfish SyntheticFrameRenderer and file:// MMAP transport."""

import struct
import tempfile
import unittest
from pathlib import Path

from clownfish.renderer import (
    MmapSizeError,
    MmapUriError,
    SyntheticFrameRenderer,
    fit_within,
    write_frame_to_mmap_file,
)
from clownfish.state import EmulatorState


class SyntheticFrameRendererTest(unittest.TestCase):
    """Tests synthetic frame generation, animation changes, scaling, and file:// MMAP."""

    def test_fit_within_never_upscales_and_preserves_aspect(self) -> None:
        self.assertEqual(fit_within(1080, 2400, 0, 0), (1080, 2400))
        self.assertEqual(fit_within(1080, 2400, 2000, 4000), (1080, 2400))
        w, h = fit_within(1080, 2400, 270, 600)
        self.assertEqual((w, h), (270, 600))

    def test_render_png_and_raw_formats(self) -> None:
        state = EmulatorState(preset="phone")
        renderer = SyntheticFrameRenderer(state, fps=0.0)

        png_frame = renderer.render_frame(
            display_id=0, req_width=120, req_height=240, output_format="PNG"
        )
        self.assertTrue(png_frame.data.startswith(b"\x89PNG\r\n\x1a\n"))
        # Verify IHDR dimensions inside PNG
        ihdr_w, ihdr_h = struct.unpack(">II", png_frame.data[16:24])
        self.assertEqual((ihdr_w, ihdr_h), (png_frame.width, png_frame.height))
        self.assertLessEqual(png_frame.width, 120)
        self.assertLessEqual(png_frame.height, 240)

        rgb_frame = renderer.render_frame(
            display_id=0, req_width=120, req_height=240, output_format="RGB888"
        )
        self.assertEqual(rgb_frame.bpp, 3)
        self.assertEqual(len(rgb_frame.data), rgb_frame.width * rgb_frame.height * 3)

        rgba_frame = renderer.render_frame(
            display_id=0, req_width=120, req_height=240, output_format="RGBA8888"
        )
        self.assertEqual(rgba_frame.bpp, 4)
        self.assertEqual(len(rgba_frame.data), rgba_frame.width * rgba_frame.height * 4)

    def test_animation_changes_rendered_pixels(self) -> None:
        state = EmulatorState(preset="phone")
        renderer = SyntheticFrameRenderer(state, fps=0.0)

        f1 = renderer.render_frame(
            display_id=0, req_width=120, req_height=240, output_format="RGB888"
        )
        state.advance_frame(delta_deg=45.0)
        f2 = renderer.render_frame(
            display_id=0, req_width=120, req_height=240, output_format="RGB888"
        )
        self.assertEqual(f2.seq, f1.seq + 1)
        self.assertNotEqual(f1.data, f2.data)

    def test_write_frame_to_mmap_file_valid_and_errors(self) -> None:
        state = EmulatorState(preset="phone")
        renderer = SyntheticFrameRenderer(state, fps=0.0)
        frame = renderer.render_frame(
            display_id=0, req_width=64, req_height=64, output_format="RGBA8888"
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            mmap_path = Path(tmpdir) / "frame.mmap"
            required_bytes = frame.height * ((frame.width * frame.bpp + 3) & ~3)
            mmap_path.write_bytes(b"\x00" * required_bytes)

            written = write_frame_to_mmap_file(
                mmap_path.as_uri(),
                frame.data,
                frame.width,
                frame.height,
                frame.bpp,
            )
            self.assertEqual(written, len(frame.data))
            self.assertEqual(mmap_path.read_bytes()[:written], frame.data)

            # Too small file raises MmapSizeError
            small_path = Path(tmpdir) / "small.mmap"
            small_path.write_bytes(b"\x00" * 16)
            with self.assertRaises(MmapSizeError):
                write_frame_to_mmap_file(
                    small_path.as_uri(),
                    frame.data,
                    frame.width,
                    frame.height,
                    frame.bpp,
                )

            # Non-file:// URI raises MmapUriError
            with self.assertRaises(MmapUriError):
                write_frame_to_mmap_file(
                    "shm://unsupported",
                    frame.data,
                    frame.width,
                    frame.height,
                    frame.bpp,
                )

            # Path outside allowed_dir raises MmapUriError
            other_allowed = Path(tmpdir) / "other_allowed"
            other_allowed.mkdir()
            with self.assertRaises(MmapUriError):
                write_frame_to_mmap_file(
                    mmap_path.as_uri(),
                    frame.data,
                    frame.width,
                    frame.height,
                    frame.bpp,
                    allowed_dir=other_allowed,
                )

    def test_rotation_0_90_180_270_dimensions_top_left_green_pixel_and_top_hud(
        self,
    ) -> None:
        state = EmulatorState(preset="phone")
        renderer = SyntheticFrameRenderer(state, fps=0.0)

        expected_orientations = [
            (0, 0, (108, 240)),
            (90, 1, (240, 108)),
            (180, 2, (108, 240)),
            (270, 3, (240, 108)),
        ]

        banner_slices: dict[int, bytes] = {}
        for rot_deg, expected_skin, expected_dims in expected_orientations:
            with self.subTest(rotation_deg=rot_deg):
                state.update_display_v2(0, rotation_deg=rot_deg)
                f1 = renderer.render_frame(
                    display_id=0,
                    req_width=120,
                    req_height=240,
                    output_format="RGB888",
                )
                self.assertEqual((f1.width, f1.height), expected_dims)
                self.assertEqual(f1.rotation_deg, rot_deg)
                self.assertEqual(f1.rotation_skin, expected_skin)

                # Top-left pixel (0, 0) must always be pure green (0, 255, 0)
                top_left = f1.data[0:3]
                self.assertEqual(top_left, bytes((0, 255, 0)))

                # Other three corners must not be the top-left green marker
                w, h = f1.width, f1.height
                top_right = f1.data[(w - 1) * 3 : w * 3]
                bottom_left = f1.data[((h - 1) * w) * 3 : ((h - 1) * w + 1) * 3]
                bottom_right = f1.data[(h * w - 1) * 3 : (h * w) * 3]
                self.assertNotEqual(top_right, bytes((0, 255, 0)))
                self.assertNotEqual(bottom_left, bytes((0, 255, 0)))
                self.assertNotEqual(bottom_right, bytes((0, 255, 0)))

                # On-display rotation text banner (at ~84% height) contains yellow text
                rot_y0 = max(0, int(h * 0.84) - 2)
                rot_y1 = min(h, int(h * 0.84) + 10)
                banner_bytes = f1.data[rot_y0 * w * 3 : rot_y1 * w * 3]
                # Default brightness is 80%, so (250, 204, 21) -> (200, 163, 16)
                expected_yellow = bytes(
                    (int(250 * 0.8), int(204 * 0.8), int(21 * 0.8))
                )
                self.assertIn(expected_yellow, banner_bytes)
                banner_slices[rot_deg] = banner_bytes

                # Frame counter HUD must always be rendered at the top of the frame:
                # 1. Top HUD bar contains bright text pixels (226, 232, 240 scaled by brightness)
                bar_h = min(h // 4, max(18, h // 10))
                top_bar_bytes = f1.data[0 : bar_h * w * 3]
                # 2. Advancing frame sequence updates the frame counter in the top HUD bar
                #    even when animation_angle_deg is unchanged (delta_deg=0.0).
                state.advance_frame(delta_deg=0.0)
                # Keep animation_step identical so only the frame counter #{seq} changes
                with state._lock:
                    state.animation_step -= 1
                f2 = renderer.render_frame(
                    display_id=0,
                    req_width=120,
                    req_height=240,
                    output_format="RGB888",
                )
                top_bar_bytes_2 = f2.data[0 : bar_h * w * 3]
                self.assertNotEqual(top_bar_bytes, top_bar_bytes_2)
                # And top-left pixel in f2 is still green
                self.assertEqual(f2.data[0:3], bytes((0, 255, 0)))

                # RGBA8888 format also has (0, 255, 0, 255) in top-left (0, 0)
                f_rgba = renderer.render_frame(
                    display_id=0,
                    req_width=120,
                    req_height=240,
                    output_format="RGBA8888",
                )
                self.assertEqual((f_rgba.width, f_rgba.height), expected_dims)
                self.assertEqual(f_rgba.data[0:4], bytes((0, 255, 0, 255)))

        # Rotation text banner must differ between 0 vs 180 and 90 vs 270 degrees
        self.assertNotEqual(banner_slices[0], banner_slices[180])
        self.assertNotEqual(banner_slices[90], banner_slices[270])


if __name__ == "__main__":
    unittest.main()
