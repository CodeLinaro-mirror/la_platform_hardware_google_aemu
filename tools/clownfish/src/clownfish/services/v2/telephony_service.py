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

"""Implementation of `android.emulation.v2.TelephonyService`."""

import queue
from collections.abc import Iterator

import grpc
import telephony_service_pb2 as pb
import telephony_service_pb2_grpc as pb_grpc

from clownfish.state import EmulatorState


class TelephonyServiceServicer(pb_grpc.TelephonyServiceServicer):
    """Stateful implementation of `android.emulation.v2.TelephonyService`."""

    def __init__(self, state: EmulatorState) -> None:
        self.state = state

    def _ensure_telephony_supported(self, context: grpc.ServicerContext) -> None:
        with self.state._lock:
            if not self.state.preset.has_telephony:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "Device has no cellular modem subsystem.",
                )

    def _build_telephony_state(self) -> pb.TelephonyState:
        with self.state._lock:
            calls = [
                pb.CallState(
                    call_id=c.call_id,
                    caller_phone_number=c.caller_phone_number,
                    status=c.status,
                )
                for _, c in sorted(self.state.active_calls.items())
            ]
            return pb.TelephonyState(
                radio_powered=self.state.radio_powered,
                active_technology=self.state.active_technology_v2,
                signal_strength=self.state.signal_strength_v2,
                registration_state=self.state.registration_state_v2,
                active_calls=calls,
            )

    def GetTelephonyState(
        self, request: pb.GetTelephonyStateRequest, context: grpc.ServicerContext
    ) -> pb.TelephonyState:
        self._ensure_telephony_supported(context)
        return self._build_telephony_state()

    def StreamTelephonyState(
        self, request: pb.StreamTelephonyStateRequest, context: grpc.ServicerContext
    ) -> Iterator[pb.TelephonyState]:
        self._ensure_telephony_supported(context)
        with self.state.subscribe("telephony") as q:
            yield self._build_telephony_state()
            while context.is_active():
                try:
                    q.get(timeout=0.1)
                except queue.Empty:
                    continue
                if not context.is_active():
                    break
                yield self._build_telephony_state()

    def UpdateTelephonyState(
        self, request: pb.UpdateTelephonyStateRequest, context: grpc.ServicerContext
    ) -> pb.TelephonyState:
        self._ensure_telephony_supported(context)
        with self.state._lock:
            if request.HasField("radio_powered"):
                self.state.radio_powered = request.radio_powered
            if (
                request.HasField("active_technology")
                and request.active_technology
                != pb.TelephonyState.TECHNOLOGY_UNSPECIFIED
            ):
                self.state.active_technology_v2 = request.active_technology
            if (
                request.HasField("signal_strength")
                and request.signal_strength != pb.TelephonyState.STRENGTH_UNSPECIFIED
            ):
                self.state.signal_strength_v2 = request.signal_strength
            if (
                request.HasField("registration_state")
                and request.registration_state
                != pb.TelephonyState.REGISTRATION_UNSPECIFIED
            ):
                self.state.registration_state_v2 = request.registration_state
            queues = self.state._snapshot_subscribers_locked("telephony")

        self.state._publish_unlocked(queues, {"type": "telephony_updated"})
        return self._build_telephony_state()

    def SendSms(
        self, request: pb.SendSmsRequest, context: grpc.ServicerContext
    ) -> pb.SendSmsResponse:
        self._ensure_telephony_supported(context)
        if not request.sender_phone_number or not request.sender_phone_number.strip():
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "sender_phone_number must not be empty.",
            )
        if len(request.message_body) > 1024:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "message_body exceeds 1024 characters.",
            )
        with self.state._lock:
            self.state.sms_messages.append(
                (request.sender_phone_number, request.message_body)
            )
        return pb.SendSmsResponse()

    def TransitionCall(
        self, request: pb.TransitionCallRequest, context: grpc.ServicerContext
    ) -> pb.TransitionCallResponse:
        self._ensure_telephony_supported(context)
        if (
            not request.caller_phone_number
            or not request.caller_phone_number.strip()
            or request.action == pb.TransitionCallRequest.ACTION_UNSPECIFIED
        ):
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "caller_phone_number and an explicit action are required.",
            )
        res = self.state.transition_call_v2(request.caller_phone_number, request.action)
        if res is None:
            context.abort(
                grpc.StatusCode.FAILED_PRECONDITION,
                f"Invalid call transition action {request.action} for '{request.caller_phone_number}'.",
            )
        return pb.TransitionCallResponse(
            call=pb.CallState(
                call_id=res.call_id,
                caller_phone_number=res.caller_phone_number,
                status=res.status,
            )
        )
