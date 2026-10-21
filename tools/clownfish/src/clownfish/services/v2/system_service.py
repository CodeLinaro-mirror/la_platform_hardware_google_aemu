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

"""Implementation of `android.emulation.v2.SystemService`."""

import queue
from collections.abc import Iterator

import grpc
import system_service_pb2 as pb
import system_service_pb2_grpc as pb_grpc

from clownfish.state import EmulatorState


class SystemServiceServicer(pb_grpc.SystemServiceServicer):
    """Stateful implementation of `android.emulation.v2.SystemService`."""

    def __init__(self, state: EmulatorState) -> None:
        self.state = state

    def _build_system_state(self) -> pb.SystemState:
        with self.state._lock:
            lifecycle = (
                pb.SystemState.BOOT_COMPLETED
                if self.state.booted
                else pb.SystemState.BOOTING
            )
            return pb.SystemState(
                lifecycle_state=lifecycle,
                connection_state=pb.SystemState.CONNECTED,
                lock_state=pb.SystemState.UNLOCKED,
                uptime_ms=self.state.uptime_ms(),
                boot_completed_duration_ms=self.state.boot_completed_time_ms,
                heartbeat_counter=self.state.frame_seq,
            )

    def GetSystemState(
        self, request: pb.GetSystemStateRequest, context: grpc.ServicerContext
    ) -> pb.SystemState:
        del request, context
        return self._build_system_state()

    def StreamSystemState(
        self, request: pb.StreamSystemStateRequest, context: grpc.ServicerContext
    ) -> Iterator[pb.SystemState]:
        del request
        with self.state.subscribe("system") as q:
            yield self._build_system_state()
            while context.is_active():
                try:
                    q.get(timeout=0.1)
                except queue.Empty:
                    continue
                if not context.is_active():
                    break
                yield self._build_system_state()

    def RebootDevice(
        self, request: pb.RebootDeviceRequest, context: grpc.ServicerContext
    ) -> pb.RebootDeviceResponse:
        del request, context
        self.state.reset_vm_v2(hard=False)
        return pb.RebootDeviceResponse()

    def GetClipboardState(
        self, request: pb.GetClipboardStateRequest, context: grpc.ServicerContext
    ) -> pb.ClipboardState:
        del request, context
        with self.state._lock:
            text = self.state.clipboard_text
        return pb.ClipboardState(
            text=text,
            mime_type="text/plain" if text else "",
        )

    def StreamClipboardState(
        self, request: pb.StreamClipboardStateRequest, context: grpc.ServicerContext
    ) -> Iterator[pb.ClipboardState]:
        del request
        with self.state.subscribe("clipboard") as q:
            yield self.GetClipboardState(pb.GetClipboardStateRequest(), context)
            while context.is_active():
                try:
                    item = q.get(timeout=0.1)
                except queue.Empty:
                    continue
                if not context.is_active():
                    break
                txt = item["text"]
                yield pb.ClipboardState(
                    text=txt,
                    mime_type="text/plain" if txt else "",
                )

    def UpdateClipboardState(
        self, request: pb.UpdateClipboardStateRequest, context: grpc.ServicerContext
    ) -> pb.ClipboardState:
        peer = context.peer() if hasattr(context, "peer") else ""
        self.state.set_clipboard(request.text, source_peer=peer)
        return pb.ClipboardState(
            text=request.text,
            mime_type=request.mime_type or ("text/plain" if request.text else ""),
        )

    def InjectFingerprint(
        self, request: pb.InjectFingerprintRequest, context: grpc.ServicerContext
    ) -> pb.InjectFingerprintResponse:
        del context
        touching = request.touching if request.HasField("touching") else False
        with self.state._lock:
            self.state.last_fingerprint = (touching, request.finger_id)
        return pb.InjectFingerprintResponse()
