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

"""Implementation of `android.emulation.v2.DisplayService`."""

import queue
import time
from collections.abc import Iterator

import common_pb2
import display_service_pb2 as pb
import display_service_pb2_grpc as pb_grpc
import grpc

from clownfish.renderer import (
    MAX_DISPLAY_DIMENSION,
    MAX_DISPLAY_DPI,
    SyntheticFrameRenderer,
)
from clownfish.state import DisplayRecord, EmulatorState


class DisplayServiceServicer(pb_grpc.DisplayServiceServicer):
    """Stateful implementation of `android.emulation.v2.DisplayService`."""

    def __init__(self, state: EmulatorState, renderer: SyntheticFrameRenderer) -> None:
        self.state = state
        self.renderer = renderer

    @staticmethod
    def _deg_to_rotation_enum(deg: int) -> int:
        return {
            0: common_pb2.ROTATION_DEG0,
            90: common_pb2.ROTATION_DEG90,
            180: common_pb2.ROTATION_DEG180,
            270: common_pb2.ROTATION_DEG270,
        }.get(deg % 360, common_pb2.ROTATION_DEG0)

    @staticmethod
    def _rotation_enum_to_deg(rot: int) -> int:
        return {
            common_pb2.ROTATION_DEG0: 0,
            common_pb2.ROTATION_DEG90: 90,
            common_pb2.ROTATION_DEG180: 180,
            common_pb2.ROTATION_DEG270: 270,
        }.get(rot, 0)

    def _to_display_proto(self, rec: DisplayRecord) -> pb.Display:
        eff_w, eff_h = self.state.get_effective_display_dims(rec.display_id)
        rot_enum = self._deg_to_rotation_enum(rec.rotation_deg)
        return pb.Display(
            display_id=rec.display_id,
            primary=(rec.display_id == 0),
            width=eff_w,
            height=eff_h,
            density_dpi=rec.dpi,
            rotation=rot_enum,
            brightness_percent=float(rec.brightness_percent),
            auto_brightness_enabled=False,
            auto_rotation_enabled=not rec.rotation_locked,
            user_rotation=rot_enum,
        )

    def _build_list_displays_response(
        self, filter_id: int | None = None
    ) -> pb.ListDisplaysResponse:
        with self.state._lock:
            items = [
                self._to_display_proto(rec)
                for disp_id, rec in sorted(self.state.displays.items())
                if filter_id is None or disp_id == filter_id
            ]
        return pb.ListDisplaysResponse(displays=items)

    def ListDisplays(
        self, request: pb.ListDisplaysRequest, context: grpc.ServicerContext
    ) -> pb.ListDisplaysResponse:
        del request, context
        return self._build_list_displays_response()

    def GetDisplay(
        self, request: pb.GetDisplayRequest, context: grpc.ServicerContext
    ) -> pb.Display:
        with self.state._lock:
            rec = self.state.displays.get(request.display_id)
            if rec is None:
                context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    f"Display {request.display_id} does not exist.",
                )
            return self._to_display_proto(rec)

    def StreamDisplays(
        self, request: pb.StreamDisplaysRequest, context: grpc.ServicerContext
    ) -> Iterator[pb.ListDisplaysResponse]:
        filter_id = request.display_id if request.HasField("display_id") else None
        with self.state.subscribe("displays") as q:
            yield self._build_list_displays_response(filter_id=filter_id)
            while context.is_active():
                try:
                    q.get(timeout=0.1)
                except queue.Empty:
                    continue
                if not context.is_active():
                    break
                yield self._build_list_displays_response(filter_id=filter_id)

    def UpdateDisplay(
        self, request: pb.UpdateDisplayRequest, context: grpc.ServicerContext
    ) -> pb.Display:
        w = request.width if request.HasField("width") else None
        h = request.height if request.HasField("height") else None
        dpi = request.density_dpi if request.HasField("density_dpi") else None
        if (
            (w is not None and (w <= 0 or w > MAX_DISPLAY_DIMENSION))
            or (h is not None and (h <= 0 or h > MAX_DISPLAY_DIMENSION))
            or (dpi is not None and (dpi <= 0 or dpi > MAX_DISPLAY_DPI))
        ):
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"Display width and height must be in [1, {MAX_DISPLAY_DIMENSION}] "
                f"and density_dpi in [1, {MAX_DISPLAY_DPI}].",
            )
        bright = (
            round(request.brightness_percent)
            if request.HasField("brightness_percent")
            else None
        )
        if bright is not None and (bright < 0 or bright > 100):
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "brightness_percent must be within [0, 100].",
            )
        rot_deg = None
        if (
            request.HasField("user_rotation")
            and request.user_rotation != common_pb2.ROTATION_UNSPECIFIED
        ):
            rot_deg = self._rotation_enum_to_deg(request.user_rotation)
        rot_locked = (
            not request.auto_rotation_enabled
            if request.HasField("auto_rotation_enabled")
            else None
        )

        updated = self.state.update_display_v2(
            display_id=request.display_id,
            width=w,
            height=h,
            dpi=dpi,
            rotation_deg=rot_deg,
            rotation_locked=rot_locked,
            brightness_percent=bright,
        )
        if updated is None:
            context.abort(
                grpc.StatusCode.NOT_FOUND,
                f"Display {request.display_id} does not exist.",
            )
        return self._to_display_proto(updated)

    def CreateDisplay(
        self, request: pb.CreateDisplayRequest, context: grpc.ServicerContext
    ) -> pb.Display:
        if (
            request.width <= 0
            or request.width > MAX_DISPLAY_DIMENSION
            or request.height <= 0
            or request.height > MAX_DISPLAY_DIMENSION
            or request.density_dpi <= 0
            or request.density_dpi > MAX_DISPLAY_DPI
        ):
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"width and height must be in [1, {MAX_DISPLAY_DIMENSION}] "
                f"and density_dpi in [1, {MAX_DISPLAY_DPI}].",
            )
        disp_id = request.display_id if request.display_id > 0 else None
        rec = self.state.create_display_v2(
            width=request.width,
            height=request.height,
            dpi=request.density_dpi,
            display_id=disp_id,
        )
        if rec is None:
            max_count = self.state.preset.user_configurable_displays + 1
            context.abort(
                grpc.StatusCode.RESOURCE_EXHAUSTED,
                f"Maximum supported display count ({max_count}) reached.",
            )
        return self._to_display_proto(rec)

    def DeleteDisplay(
        self, request: pb.DeleteDisplayRequest, context: grpc.ServicerContext
    ) -> pb.DeleteDisplayResponse:
        if request.display_id == 0:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "Cannot delete primary display 0.",
            )
        if not self.state.delete_display_v2(request.display_id):
            context.abort(
                grpc.StatusCode.NOT_FOUND,
                f"Display {request.display_id} does not exist.",
            )
        return pb.DeleteDisplayResponse()

    def _render_v2_screenshot(
        self,
        display_id: int,
        fmt: int,
        context: grpc.ServicerContext,
    ) -> pb.Screenshot:
        with self.state._lock:
            disp = self.state.displays.get(display_id)
            if disp is None:
                context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    f"Display {display_id} does not exist.",
                )
            dpi = disp.dpi

        if fmt in (common_pb2.IMAGE_FORMAT_UNSPECIFIED, common_pb2.IMAGE_FORMAT_PNG):
            fmt_str = "PNG"
        elif fmt == common_pb2.IMAGE_FORMAT_RAW_RGBA8888:
            fmt_str = "RGBA8888"
        elif fmt == common_pb2.IMAGE_FORMAT_JPEG:
            fmt_str = "PNG"
        else:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"Unsupported ImageFormat: {fmt}",
            )

        frame = self.renderer.render_frame(
            display_id=display_id,
            output_format=fmt_str,
        )
        return pb.Screenshot(
            image_data=frame.data,
            width=frame.width,
            height=frame.height,
            row_stride_bytes=frame.row_stride_bytes,
            frame_sequence_number=frame.seq,
            rotation=self._deg_to_rotation_enum(frame.rotation_deg),
            presentation_time_nanos=frame.timestamp_ns,
            density_scale=float(dpi) / 160.0,
        )

    def GetScreenshot(
        self, request: pb.GetScreenshotRequest, context: grpc.ServicerContext
    ) -> pb.Screenshot:
        return self._render_v2_screenshot(
            display_id=request.display_id,
            fmt=request.format,
            context=context,
        )

    def StreamScreenshots(
        self, request: pb.StreamScreenshotsRequest, context: grpc.ServicerContext
    ) -> Iterator[pb.Screenshot]:
        if (
            request.HasField("max_frames_per_second")
            and request.max_frames_per_second <= 0.0
        ):
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "max_frames_per_second must be > 0.0 when set.",
            )
        min_interval = (
            (1.0 / request.max_frames_per_second)
            if request.HasField("max_frames_per_second")
            and request.max_frames_per_second > 0
            else 0.0
        )
        with self.state.subscribe("frame") as q:
            yield self._render_v2_screenshot(
                display_id=request.display_id,
                fmt=request.format,
                context=context,
            )
            last_sent = time.monotonic()

            while context.is_active():
                try:
                    q.get(timeout=0.1)
                except queue.Empty:
                    continue
                if not context.is_active():
                    break
                now = time.monotonic()
                if min_interval > 0 and (now - last_sent) < min_interval:
                    continue
                last_sent = now
                yield self._render_v2_screenshot(
                    display_id=request.display_id,
                    fmt=request.format,
                    context=context,
                )
