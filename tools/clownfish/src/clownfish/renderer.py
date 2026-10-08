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

"""Synthetic display frame renderer with rotating triangle animation, HUD, and file:// MMAP."""

import math
import mmap
import os
import struct
import tempfile
import threading
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass
from pathlib import Path

from clownfish.state import EmulatorState

MAX_DISPLAY_DIMENSION = 4096
MAX_DISPLAY_DPI = 1200

# Compact 5x7 ASCII bitmap font (each row is a 5-bit integer from MSB to LSB).
_FONT_5X7: dict[str, tuple[int, ...]] = {
    " ": (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00),
    "#": (0x0A, 0x1F, 0x0A, 0x0A, 0x1F, 0x0A, 0x00),
    "%": (0x18, 0x19, 0x02, 0x04, 0x08, 0x13, 0x03),
    "(": (0x02, 0x04, 0x08, 0x08, 0x08, 0x04, 0x02),
    ")": (0x08, 0x04, 0x02, 0x02, 0x02, 0x04, 0x08),
    "+": (0x00, 0x04, 0x04, 0x1F, 0x04, 0x04, 0x00),
    ",": (0x00, 0x00, 0x00, 0x00, 0x06, 0x04, 0x08),
    "-": (0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00),
    ".": (0x00, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x0C),
    "/": (0x01, 0x02, 0x02, 0x04, 0x08, 0x08, 0x10),
    "0": (0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E),
    "1": (0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E),
    "2": (0x0E, 0x11, 0x01, 0x06, 0x08, 0x10, 0x1F),
    "3": (0x1F, 0x02, 0x04, 0x02, 0x01, 0x11, 0x0E),
    "4": (0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02),
    "5": (0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E),
    "6": (0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E),
    "7": (0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08),
    "8": (0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E),
    "9": (0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C),
    ":": (0x00, 0x0C, 0x0C, 0x00, 0x0C, 0x0C, 0x00),
    "A": (0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11),
    "B": (0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E),
    "C": (0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E),
    "D": (0x1C, 0x12, 0x11, 0x11, 0x11, 0x12, 0x1C),
    "E": (0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F),
    "F": (0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10),
    "G": (0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F),
    "H": (0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11),
    "I": (0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E),
    "J": (0x07, 0x02, 0x02, 0x02, 0x02, 0x12, 0x0C),
    "K": (0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11),
    "L": (0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F),
    "M": (0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11),
    "N": (0x11, 0x19, 0x15, 0x13, 0x11, 0x11, 0x11),
    "O": (0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E),
    "P": (0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10),
    "Q": (0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D),
    "R": (0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11),
    "S": (0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E),
    "T": (0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04),
    "U": (0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E),
    "V": (0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04),
    "W": (0x11, 0x11, 0x11, 0x15, 0x15, 0x1B, 0x11),
    "X": (0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11),
    "Y": (0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04),
    "Z": (0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F),
    "_": (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x1F),
    "|": (0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04),
}


@dataclass(frozen=True)
class RenderedFrame:
    """Rendered frame buffer and associated display metadata."""

    data: bytes
    width: int
    height: int
    row_stride_bytes: int
    bpp: int
    seq: int
    timestamp_ns: int
    rotation_deg: int
    rotation_skin: int
    folded: bool
    folded_x_offset: int
    folded_y_offset: int
    folded_width: int
    folded_height: int
    display_mode: int


def fit_within(src_w: int, src_h: int, req_w: int, req_h: int) -> tuple[int, int]:
    """Scales `(src_w, src_h)` down to fit within `(req_w, req_h)` preserving aspect ratio.

    Matches C++ `FitWithin` in `display_service.cc`: never upscales beyond `(src_w, src_h)`.
    """
    if req_w <= 0 and req_h <= 0:
        return (src_w, src_h)
    target_w = req_w if 0 < req_w < src_w else src_w
    target_h = req_h if 0 < req_h < src_h else src_h
    scale = min(target_w / max(1, src_w), target_h / max(1, src_h), 1.0)
    out_w = max(1, math.floor(src_w * scale))
    out_h = max(1, math.floor(src_h * scale))
    return (out_w, out_h)


def encode_png_rgba(raw_bytes: bytes, width: int, height: int, bpp: int = 4) -> bytes:
    """Encodes a raw RGB888 (`bpp=3`) or RGBA8888 (`bpp=4`) buffer into a valid RFC 2083 PNG."""
    color_type = 6 if bpp == 4 else 2
    row_bytes = width * bpp

    # Prepend filter byte 0x00 (None) to each scanline
    scanlines = bytearray(height * (row_bytes + 1))
    for y in range(height):
        src_start = y * row_bytes
        dst_start = y * (row_bytes + 1)
        scanlines[dst_start] = 0
        scanlines[dst_start + 1 : dst_start + 1 + row_bytes] = raw_bytes[
            src_start : src_start + row_bytes
        ]

    def _chunk(tag: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(tag)
        crc = zlib.crc32(payload, crc) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    compressed = zlib.compress(bytes(scanlines), level=1)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", compressed)
        + _chunk(b"IEND", b"")
    )


class MmapSizeError(Exception):
    """Raised when a `file://` MMAP backing file is smaller than required stride * height."""


class MmapUriError(Exception):
    """Raised when an `ImageTransport.MMAP` handle is not a valid `file://` URI."""


def write_frame_to_mmap_file(
    uri: str,
    payload: bytes,
    width: int,
    height: int,
    bpp: int,
    allowed_dir: str | os.PathLike[str] | None = None,
) -> int:
    """Writes `payload` into a `file://` shared-memory mapped file matching `display_service.cc`.

    Args:
        uri: A `file://` URI pointing to an existing memory-mapped file on disk.
        payload: Encoded PNG or raw RGB/RGBA pixel bytes to write at offset 0.
        width: Rendered frame width in pixels.
        height: Rendered frame height in pixels.
        bpp: Bytes per pixel (`3` for RGB888, `4` for RGBA8888/PNG).
        allowed_dir: Base directory within which the MMAP file must reside
            (defaults to `tempfile.gettempdir()`).

    Returns:
        Number of bytes written.

    Raises:
        MmapUriError: If `uri` does not use the `file://` scheme or resolves outside `allowed_dir`.
        FileNotFoundError: If the target file does not exist.
        MmapSizeError: If the target file is smaller than `height * ((width * bpp + 3) & ~3)`.
    """
    if not uri or not uri.startswith("file://"):
        raise MmapUriError(
            f"Unsupported MMAP transport URI '{uri}'. Only file:// URIs are supported."
        )

    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme != "file":
        raise MmapUriError(
            f"Unsupported MMAP transport scheme '{parsed.scheme}'. Only file:// is supported."
        )

    raw_path = urllib.request.url2pathname(parsed.path)
    if parsed.netloc and parsed.netloc != "localhost":
        raw_path = f"//{parsed.netloc}{raw_path}"

    if ".." in Path(raw_path).parts or os.path.islink(raw_path):
        raise MmapUriError(
            f"MMAP path '{raw_path}' must not contain parent traversal or symlinks."
        )

    base_dir = os.path.realpath(
        os.fspath(allowed_dir) if allowed_dir is not None else tempfile.gettempdir()
    )
    real_path = os.path.realpath(raw_path)
    try:
        in_allowed_dir = (
            os.path.commonpath([real_path, base_dir]) == base_dir
            and real_path != base_dir
        )
    except ValueError:
        in_allowed_dir = False
    if not in_allowed_dir:
        raise MmapUriError(
            f"MMAP path '{raw_path}' must reside within allowed directory '{base_dir}'."
        )

    if not os.path.isfile(real_path):
        raise FileNotFoundError(f"MMAP backing file does not exist: {real_path}")

    stride = (width * bpp + 3) & ~3
    required_size = max(height * stride, len(payload))
    actual_size = os.path.getsize(real_path)
    if actual_size < required_size:
        raise MmapSizeError(
            f"MMAP file '{real_path}' size ({actual_size} bytes) is smaller than "
            f"required buffer size ({required_size} bytes)."
        )

    with (
        open(real_path, "r+b") as f,
        mmap.mmap(f.fileno(), required_size, access=mmap.ACCESS_WRITE) as mm,
    ):
        mm.seek(0)
        mm.write(payload)
        mm.flush()

    return len(payload)


class SyntheticFrameRenderer:
    """Renders animated synthetic display frames (rotating triangle + moving block + HUD)."""

    def __init__(self, state: EmulatorState, fps: float = 15.0) -> None:
        self.state = state
        self.fps = fps
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Starts the background animation loop if `self.fps > 0`."""
        if self.fps <= 0 or (self._thread is not None and self._thread.is_alive()):
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._animation_loop,
            name="clownfish-frame-ticker",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stops the background animation loop thread."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    def _animation_loop(self) -> None:
        interval = 1.0 / max(1.0, self.fps)
        while not self._stop_event.wait(interval):
            self.state.advance_frame(delta_deg=12.0)

    @staticmethod
    def _fill_rect(
        buf: bytearray,
        width: int,
        height: int,
        bpp: int,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        color: bytes,
    ) -> None:
        """Fast C-speed bytearray slice fill for an axis-aligned rectangle."""
        cx0 = max(0, min(width, x0))
        cx1 = max(0, min(width, x1))
        cy0 = max(0, min(height, y0))
        cy1 = max(0, min(height, y1))
        if cx0 >= cx1 or cy0 >= cy1:
            return
        span_len = cx1 - cx0
        row_span = color * span_len
        row_stride = width * bpp
        for y in range(cy0, cy1):
            start = y * row_stride + cx0 * bpp
            buf[start : start + span_len * bpp] = row_span

    @staticmethod
    def _fill_triangle(
        buf: bytearray,
        width: int,
        height: int,
        bpp: int,
        v0: tuple[float, float],
        v1: tuple[float, float],
        v2: tuple[float, float],
        color: bytes,
    ) -> None:
        """Scanline triangle rasterizer using C-speed row slice assignments."""
        pts = sorted([v0, v1, v2], key=lambda p: p[1])
        (x0, y0), (x1, y1), (x2, y2) = pts
        if y2 - y0 < 0.5:
            return

        y_start = max(0, math.ceil(y0))
        y_end = min(height - 1, math.floor(y2))
        row_stride = width * bpp

        for y in range(y_start, y_end + 1):
            yf = float(y)
            # Long edge v0 -> v2
            t_long = (yf - y0) / (y2 - y0)
            xa = x0 + t_long * (x2 - x0)

            # Short edge v0 -> v1 or v1 -> v2
            if yf < y1 and (y1 - y0) > 1e-5:
                t_short = (yf - y0) / (y1 - y0)
                xb = x0 + t_short * (x1 - x0)
            elif (y2 - y1) > 1e-5:
                t_short = (yf - y1) / (y2 - y1)
                xb = x1 + t_short * (x2 - x1)
            else:
                xb = x1

            if xa > xb:
                xa, xb = xb, xa
            ix0 = max(0, min(width, math.ceil(xa)))
            ix1 = max(0, min(width, math.floor(xb) + 1))
            if ix1 > ix0:
                span = ix1 - ix0
                start = y * row_stride + ix0 * bpp
                buf[start : start + span * bpp] = color * span

    @classmethod
    def _draw_text(
        cls,
        buf: bytearray,
        width: int,
        height: int,
        bpp: int,
        x: int,
        y: int,
        text: str,
        color: bytes,
        scale: int = 1,
    ) -> None:
        """Draws an ASCII string using the embedded 5x7 bitmap font."""
        cursor_x = x
        for ch in text.upper():
            glyph = _FONT_5X7.get(ch, _FONT_5X7[" "])
            for row_idx, row_bits in enumerate(glyph):
                if row_bits == 0:
                    continue
                for col_idx in range(5):
                    if (row_bits >> (4 - col_idx)) & 1:
                        px = cursor_x + col_idx * scale
                        py = y + row_idx * scale
                        cls._fill_rect(
                            buf,
                            width,
                            height,
                            bpp,
                            px,
                            py,
                            px + scale,
                            py + scale,
                            color,
                        )
            cursor_x += 6 * scale

    def render_frame(
        self,
        display_id: int = 0,
        req_width: int = 0,
        req_height: int = 0,
        max_dimension: int = 0,
        output_format: str = "PNG",  # "PNG", "RGB888", "RGBA8888"
    ) -> RenderedFrame:
        """Renders a synthetic frame for `display_id` with rotating triangle, moving block, and HUD."""
        with self.state._lock:
            disp = self.state.displays.get(display_id, self.state.displays[0])
            native_w, native_h = self.state.get_effective_display_dims(display_id)
            rot_deg = (int(round(disp.rotation_deg / 90.0)) * 90) % 360
            brightness = max(10, disp.brightness_percent) / 100.0
            power_mode = disp.power_mode
            seq = self.state.frame_seq
            angle_deg = self.state.animation_angle_deg
            anim_step = self.state.animation_step
            posture = self.state.posture
            is_foldable = self.state.preset.is_foldable
            fold_axis = self.state.preset.fold_axis
            folded = is_foldable and posture == 1 and display_id == 0
            folded_x = self.state.preset.folded_x_offset if folded else 0
            folded_y = self.state.preset.folded_y_offset if folded else 0
            folded_w = native_w if folded else 0
            folded_h = native_h if folded else 0
            display_mode = self.state.display_mode
            avd_name = self.state.preset.avd_name
            battery_pct = self.state.battery_charge_level
            touches = [
                (t.pointer_id, t.x_norm, t.y_norm, t.tool)
                for t in self.state.active_touches.values()
                if t.display_id == display_id
            ]
            mouse_x = self.state.mouse_x_norm
            mouse_y = self.state.mouse_y_norm
            mouse_buttons = self.state.mouse_buttons
            last_key = self.state.last_key_event
            ts_ns = self.state.monotonic_ns()

        # Apply scaling constraints (v1 fit_within or v2 max_dimension).
        # When rotated 90 or 270 degrees, if a caller supplies an unrotated
        # bounding box (e.g. portrait req_width x req_height for a now-landscape
        # display), swap the requested bounds to match the rotated orientation.
        if (
            req_width > 0
            and req_height > 0
            and rot_deg in (90, 270)
            and (
                (req_width < req_height and native_w > native_h)
                or (req_width > req_height and native_w < native_h)
            )
        ):
            req_width, req_height = req_height, req_width
        out_w, out_h = fit_within(native_w, native_h, req_width, req_height)
        effective_max_dim = (
            min(max_dimension, MAX_DISPLAY_DIMENSION)
            if max_dimension > 0
            else MAX_DISPLAY_DIMENSION
        )
        if max(out_w, out_h) > effective_max_dim:
            if out_w >= out_h:
                out_w, out_h = fit_within(out_w, out_h, effective_max_dim, out_h)
            else:
                out_w, out_h = fit_within(out_w, out_h, out_w, effective_max_dim)

        fmt_upper = output_format.upper()
        bpp = 3 if fmt_upper == "RGB888" else 4

        def _px(r: int, g: int, b: int) -> bytes:
            sr = max(0, min(255, int(r * brightness)))
            sg = max(0, min(255, int(g * brightness)))
            sb = max(0, min(255, int(b * brightness)))
            return bytes((sr, sg, sb)) if bpp == 3 else bytes((sr, sg, sb, 255))

        # Base canvas buffer
        if power_mode == 4:  # OFF
            bg = _px(8, 8, 12)
            buf = bytearray(bg * (out_w * out_h))
        else:
            bg = _px(26, 34, 48)
            buf = bytearray(bg * (out_w * out_h))

            # Moving block across the canvas (loops smoothly with anim_step)
            block_w = max(8, out_w // 8)
            block_h = max(8, out_h // 14)
            travel_x = max(1, out_w - block_w)
            phase = (anim_step * 17) % (2 * travel_x)
            bx = phase if phase < travel_x else (2 * travel_x - phase)
            by = int(out_h * 0.72)
            self._fill_rect(
                buf,
                out_w,
                out_h,
                bpp,
                bx,
                by,
                bx + block_w,
                by + block_h,
                _px(56, 189, 248),
            )

            # Rotating triangle in the center of the display
            cx = out_w * 0.5
            cy = out_h * 0.48
            radius = max(8.0, min(out_w, out_h) * 0.24)
            theta = math.radians(angle_deg + rot_deg)
            v0 = (
                cx + radius * math.cos(theta),
                cy + radius * math.sin(theta),
            )
            v1 = (
                cx + radius * math.cos(theta + 2.0 * math.pi / 3.0),
                cy + radius * math.sin(theta + 2.0 * math.pi / 3.0),
            )
            v2 = (
                cx + radius * math.cos(theta + 4.0 * math.pi / 3.0),
                cy + radius * math.sin(theta + 4.0 * math.pi / 3.0),
            )
            self._fill_triangle(buf, out_w, out_h, bpp, v0, v1, v2, _px(249, 115, 22))

            # Foldable hinge seam indicator when unfolded/half-opened
            if is_foldable and not folded:
                seam_color = _px(148, 163, 184)
                seam_vertical = (
                    (fold_axis == "vertical")
                    if rot_deg in (0, 180)
                    else (fold_axis != "vertical")
                )
                if seam_vertical:
                    mid_x = out_w // 2
                    self._fill_rect(
                        buf,
                        out_w,
                        out_h,
                        bpp,
                        mid_x - 1,
                        0,
                        mid_x + 1,
                        out_h,
                        seam_color,
                    )
                else:
                    mid_y = out_h // 2
                    self._fill_rect(
                        buf,
                        out_w,
                        out_h,
                        bpp,
                        0,
                        mid_y - 1,
                        out_w,
                        mid_y + 1,
                        seam_color,
                    )

            # Top HUD status bar (frame counter always rendered at the top of the oriented frame)
            if out_w >= 16 and out_h >= 16:
                bar_h = min(out_h // 4, max(18, out_h // 10))
                self._fill_rect(
                    buf, out_w, out_h, bpp, 0, 0, out_w, bar_h, _px(15, 23, 42)
                )
                scale = max(1, min(out_w // 180, bar_h // 10))
                hud_text = (
                    f"#{seq} ROTATION: {rot_deg} DEGREES {avd_name[:12]} {battery_pct}%"
                )
                if last_key:
                    hud_text += f" K:{last_key[:6]}"
                self._draw_text(
                    buf,
                    out_w,
                    out_h,
                    bpp,
                    4,
                    max(1, (bar_h - 7 * scale) // 2),
                    hud_text,
                    _px(226, 232, 240),
                    scale=scale,
                )

                # Rotation label rendered directly on the display canvas
                rot_text = f"ROTATION: {rot_deg} DEGREES"
                if len(rot_text) * 6 > out_w - 4:
                    rot_text = f"ROTATION: {rot_deg} DEG"
                rot_scale = max(
                    1,
                    min(
                        (out_w - 8) // max(1, len(rot_text) * 6),
                        min(out_w, out_h) // 160,
                    ),
                )
                text_w = len(rot_text) * 6 * rot_scale - rot_scale
                text_h = 7 * rot_scale
                rot_x = max(2, (out_w - text_w) // 2)
                rot_y = max(
                    bar_h + 2,
                    min(out_h - text_h - 2, int(out_h * 0.84)),
                )
                pad_x = max(2, 4 * rot_scale)
                pad_y = max(1, 2 * rot_scale)
                self._fill_rect(
                    buf,
                    out_w,
                    out_h,
                    bpp,
                    rot_x - pad_x,
                    rot_y - pad_y,
                    rot_x + text_w + pad_x,
                    rot_y + text_h + pad_y,
                    _px(15, 23, 42),
                )
                self._draw_text(
                    buf,
                    out_w,
                    out_h,
                    bpp,
                    rot_x,
                    rot_y,
                    rot_text,
                    _px(250, 204, 21),
                    scale=rot_scale,
                )

            # Active touch circles & mouse pointer crosshair
            for ptr_id, tx_norm, ty_norm, tool in touches:
                tx = int(tx_norm * (out_w - 1))
                ty = int(ty_norm * (out_h - 1))
                r = max(3, min(out_w, out_h) // 25)
                t_color = _px(34, 197, 94) if tool == "touch" else _px(168, 85, 247)
                self._fill_rect(
                    buf,
                    out_w,
                    out_h,
                    bpp,
                    tx - r,
                    ty - r,
                    tx + r + 1,
                    ty + r + 1,
                    t_color,
                )

            if mouse_buttons > 0:
                mx = int(mouse_x * (out_w - 1))
                my = int(mouse_y * (out_h - 1))
                r = max(2, min(out_w, out_h) // 35)
                self._fill_rect(
                    buf,
                    out_w,
                    out_h,
                    bpp,
                    mx - r,
                    my - r,
                    mx + r + 1,
                    my + r + 1,
                    _px(239, 68, 68),
                )

        # Keep a pure green pixel at top-left (0, 0) across all rotations (0, 90, 180, 270)
        if out_w > 0 and out_h > 0:
            buf[0:bpp] = bytes((0, 255, 0)) if bpp == 3 else bytes((0, 255, 0, 255))

        raw_bytes = bytes(buf)
        if fmt_upper == "PNG":
            encoded = encode_png_rgba(raw_bytes, out_w, out_h, bpp=4)
            row_stride = 0
        else:
            encoded = raw_bytes
            row_stride = out_w * bpp

        rot_skin = (rot_deg // 90) % 4
        return RenderedFrame(
            data=encoded,
            width=out_w,
            height=out_h,
            row_stride_bytes=row_stride,
            bpp=bpp,
            seq=seq,
            timestamp_ns=ts_ns,
            rotation_deg=rot_deg,
            rotation_skin=rot_skin,
            folded=folded,
            folded_x_offset=folded_x,
            folded_y_offset=folded_y,
            folded_width=folded_w,
            folded_height=folded_h,
            display_mode=display_mode,
        )
