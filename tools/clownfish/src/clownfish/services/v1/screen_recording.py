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

"""Implementation of `android.emulation.control.incubating.ScreenRecording`."""

import os
import queue
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import grpc
from aemu.proto import screen_recording_service_pb2 as pb
from aemu.proto import screen_recording_service_pb2_grpc as pb_grpc
from google.protobuf import empty_pb2

from clownfish.renderer import MAX_DISPLAY_DIMENSION
from clownfish.state import EmulatorState, RecordingEntry

# Minimal valid EBML / WebM header bytes so file inspection tools see a valid container.
_MINIMAL_WEBM_HEADER = (
    b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\xf7\x81\x01\x42\xf2\x81\x04"
    b"\x42\xf3\x81\x08\x42\x82\x84webm\x42\x87\x81\x02\x42\x85\x81\x02"
)


class ScreenRecordingServicer(pb_grpc.ScreenRecordingServicer):
    """Stateful implementation of `ScreenRecording` matching `screen_recording_impl.cc`."""

    def __init__(self, state: EmulatorState) -> None:
        self.state = state

    @staticmethod
    def _resolve_allowed_recording_path(file_name: str) -> str | None:
        if (
            not file_name
            or not file_name.endswith(".webm")
            or ".." in Path(file_name).parts
            or os.path.islink(file_name)
        ):
            return None
        allowed_dir = os.path.realpath(tempfile.gettempdir())
        candidate = (
            file_name
            if os.path.isabs(file_name)
            else os.path.join(allowed_dir, file_name)
        )
        real_path = os.path.realpath(candidate)
        try:
            if (
                os.path.commonpath([real_path, allowed_dir]) != allowed_dir
                or real_path == allowed_dir
            ):
                return None
        except ValueError:
            return None
        return real_path

    @staticmethod
    def _to_proto(rec: RecordingEntry) -> pb.RecordingInfo:
        return pb.RecordingInfo(
            file_name=rec.file_name,
            width=rec.width,
            height=rec.height,
            fps=rec.fps,
            bit_rate=rec.bit_rate,
            time_limit=rec.time_limit,
            display=rec.display_id,
            state=rec.state,
        )

    def StartRecording(
        self, request: pb.RecordingInfo, context: grpc.ServicerContext
    ) -> pb.RecordingInfo:
        if not request.file_name or not request.file_name.endswith(".webm"):
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "Recording file_name must end with '.webm'.",
            )

        safe_path = self._resolve_allowed_recording_path(request.file_name)
        if safe_path is None:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "Recording file_name must reside within the allowed temporary directory.",
            )

        fps = request.fps if request.fps != 0 else 24
        if fps < 1 or fps > 60:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "Recording fps must be within [1, 60].",
            )

        bit_rate = request.bit_rate if request.bit_rate != 0 else 4_000_000
        if bit_rate < 100_000 or bit_rate > 25_000_000:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "Recording bit_rate must be within [100000, 25000000].",
            )

        time_limit = request.time_limit if request.time_limit != 0 else 180
        if time_limit < 1 or time_limit > 180:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "Recording time_limit must be within [1, 180].",
            )

        if (
            request.width < 0
            or request.width > MAX_DISPLAY_DIMENSION
            or request.height < 0
            or request.height > MAX_DISPLAY_DIMENSION
        ):
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"Recording width and height must not exceed {MAX_DISPLAY_DIMENSION}.",
            )

        disp_w, disp_h = self.state.get_effective_display_dims(request.display)
        width = request.width if request.width > 0 else disp_w
        height = request.height if request.height > 0 else disp_h

        with self.state._lock:
            if self.state.active_recording is not None:
                context.abort(
                    grpc.StatusCode.ALREADY_EXISTS,
                    f"Recording already in progress: {self.state.active_recording.file_name}",
                )

            try:
                os.makedirs(os.path.dirname(safe_path), exist_ok=True)
                with open(safe_path, "wb") as f:
                    f.write(_MINIMAL_WEBM_HEADER)
            except OSError as err:
                context.abort(
                    grpc.StatusCode.INTERNAL, f"Failed to create {safe_path}: {err}"
                )

            rec = RecordingEntry(
                file_name=safe_path,
                width=width,
                height=height,
                fps=fps,
                bit_rate=bit_rate,
                time_limit=time_limit,
                display_id=request.display,
                state=pb.RecordingInfo.RECORDER_STATE_RECORDING,
                start_time_s=int(time.time()),
            )
            self.state.active_recording = rec
            self.state.recordings.append(rec)
            proto_msg = self._to_proto(rec)
            queues = self.state._snapshot_subscribers_locked("recording")

        self.state._publish_unlocked(queues, proto_msg)
        return proto_msg

    def StopRecording(
        self, request: pb.RecordingInfo, context: grpc.ServicerContext
    ) -> pb.RecordingInfo:
        with self.state._lock:
            active = self.state.active_recording
            if active is None:
                context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    "No active screen recording to stop.",
                )
            if request.file_name:
                req_safe = self._resolve_allowed_recording_path(request.file_name)
                req_abs = os.path.abspath(request.file_name)
                if (
                    req_safe != active.file_name
                    and req_abs != active.file_name
                    and request.file_name != active.file_name
                ):
                    context.abort(
                        grpc.StatusCode.NOT_FOUND,
                        f"Active recording does not match '{request.file_name}'.",
                    )
            active.state = pb.RecordingInfo.RECORDER_STATE_STOPPED
            self.state.active_recording = None
            proto_msg = self._to_proto(active)
            queues = self.state._snapshot_subscribers_locked("recording")

        self.state._publish_unlocked(queues, proto_msg)
        return proto_msg

    def ListRecordings(
        self, request: empty_pb2.Empty, context: grpc.ServicerContext
    ) -> pb.RecordingInfoList:
        with self.state._lock:
            recs = [self._to_proto(r) for r in self.state.recordings]
        return pb.RecordingInfoList(recordings=recs)

    def ReceiveRecordingEvents(
        self, request: empty_pb2.Empty, context: grpc.ServicerContext
    ) -> Iterator[pb.RecordingInfo]:
        with self.state.subscribe("recording") as q:
            with self.state._lock:
                existing = [self._to_proto(r) for r in self.state.recordings]
            yield from existing

            while context.is_active():
                try:
                    ev = q.get(timeout=0.1)
                except queue.Empty:
                    continue
                if not context.is_active():
                    break
                yield ev
