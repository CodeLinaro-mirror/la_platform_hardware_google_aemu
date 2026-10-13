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

"""Implementation of `android.emulation.v2.CameraService`."""

import queue
from collections.abc import Iterator

import camera_service_pb2 as pb
import camera_service_pb2_grpc as pb_grpc
import grpc

from clownfish.state import EmulatorState


class CameraServiceServicer(pb_grpc.CameraServiceServicer):
    """Stateful implementation of `android.emulation.v2.CameraService`."""

    def __init__(self, state: EmulatorState) -> None:
        self.state = state

    def _build_camera_state(self) -> pb.CameraState:
        with self.state._lock:
            sensors = [
                pb.CameraState.SensorStatus(
                    camera_id=cid,
                    in_use_by_guest=info["in_use"],
                    active_source=info["active_source"],
                )
                for cid, info in sorted(self.state.cameras.items())
            ]
        return pb.CameraState(sensors=sensors)

    def GetCameraState(
        self, request: pb.GetCameraStateRequest, context: grpc.ServicerContext
    ) -> pb.CameraState:
        return self._build_camera_state()

    def StreamCameraState(
        self, request: pb.StreamCameraStateRequest, context: grpc.ServicerContext
    ) -> Iterator[pb.CameraState]:
        with self.state.subscribe("camera") as q:
            yield self._build_camera_state()
            while context.is_active():
                try:
                    q.get(timeout=0.1)
                except queue.Empty:
                    continue
                if not context.is_active():
                    break
                yield self._build_camera_state()

    def InjectFrame(
        self, request: pb.CameraFrame, context: grpc.ServicerContext
    ) -> pb.InjectFrameResponse:
        cam_id = request.camera_id or "0"
        if not request.frame_data:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "CameraFrame.frame_data must not be empty.",
            )
        with self.state._lock:
            cam = self.state.cameras.get(cam_id)
            if cam is None:
                context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    f"Camera '{cam_id}' does not exist.",
                )
            cam["active_source"] = pb.CameraState.INJECTED_STILL
            cam["last_frame"] = request.frame_data
            queues = self.state._snapshot_subscribers_locked("camera")

        self.state._publish_unlocked(
            queues, {"type": "camera_injected", "camera_id": cam_id}
        )
        return pb.InjectFrameResponse()

    def StreamCameraFrames(
        self, request_iterator: Iterator[pb.CameraFrame], context: grpc.ServicerContext
    ) -> pb.StreamCameraFramesResponse:
        accepted = 0
        for frame in request_iterator:
            cam_id = frame.camera_id or "0"
            if not frame.frame_data:
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    "CameraFrame.frame_data must not be empty.",
                )
            with self.state._lock:
                cam = self.state.cameras.get(cam_id)
                if cam is None:
                    context.abort(
                        grpc.StatusCode.NOT_FOUND,
                        f"Camera '{cam_id}' does not exist.",
                    )
                cam["active_source"] = pb.CameraState.INJECTED_STREAM
                cam["last_frame"] = frame.frame_data
                accepted += 1
                queues = self.state._snapshot_subscribers_locked("camera")
            self.state._publish_unlocked(
                queues, {"type": "camera_stream", "camera_id": cam_id}
            )
        return pb.StreamCameraFramesResponse(
            accepted_frame_count=accepted, dropped_frame_count=0
        )

    def ResetCamera(
        self, request: pb.ResetCameraRequest, context: grpc.ServicerContext
    ) -> pb.ResetCameraResponse:
        if not request.reset_all and not request.HasField("camera_id"):
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "Specify either camera_id or reset_all = true.",
            )
        with self.state._lock:
            if request.reset_all:
                for cam in self.state.cameras.values():
                    cam["active_source"] = pb.CameraState.DEFAULT_SCENE
                    cam["last_frame"] = b""
            else:
                cid = request.camera_id
                cam = self.state.cameras.get(cid)
                if cam is None:
                    context.abort(
                        grpc.StatusCode.NOT_FOUND,
                        f"Camera '{cid}' does not exist.",
                    )
                cam["active_source"] = pb.CameraState.DEFAULT_SCENE
                cam["last_frame"] = b""
            queues = self.state._snapshot_subscribers_locked("camera")

        self.state._publish_unlocked(queues, {"type": "camera_reset"})
        return pb.ResetCameraResponse()
