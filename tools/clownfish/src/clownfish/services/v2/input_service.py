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

"""Implementation of `android.emulation.v2.InputService`."""

import queue
from collections.abc import Iterator

import grpc
import input_service_pb2 as pb
import input_service_pb2_grpc as pb_grpc

from clownfish.state import EmulatorState


class InputServiceServicer(pb_grpc.InputServiceServicer):
    """Stateful implementation of `android.emulation.v2.InputService`."""

    def __init__(self, state: EmulatorState) -> None:
        self.state = state

    def _apply_input_event(
        self, ev: pb.InputEvent, context: grpc.ServicerContext
    ) -> None:
        which = ev.WhichOneof("event")
        if not which:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "InputEvent must specify one of pointer, key, axis, or text.",
            )

        if which == "pointer":
            pe = ev.pointer
            disp_id = pe.display_id
            with self.state._lock:
                if disp_id not in self.state.displays:
                    context.abort(
                        grpc.StatusCode.INVALID_ARGUMENT,
                        f"Target display_id {disp_id} does not exist.",
                    )

            for pt in pe.pointers:
                if pt.tool_type == pb.TOOL_TYPE_MOUSE:
                    self.state.record_mouse(
                        x_norm=pt.x,
                        y_norm=pt.y,
                        buttons=pt.button_mask,
                        display_id=disp_id,
                    )
                else:
                    pressure = (
                        0.0
                        if pe.action
                        in (
                            pb.POINTER_ACTION_UP,
                            pb.POINTER_ACTION_CANCEL,
                            pb.POINTER_ACTION_HOVER_ENTER,
                            pb.POINTER_ACTION_HOVER_MOVE,
                            pb.POINTER_ACTION_HOVER_EXIT,
                        )
                        else (pt.pressure if pt.HasField("pressure") else 1.0)
                    )
                    tool_name = (
                        "stylus"
                        if pt.tool_type in (pb.TOOL_TYPE_STYLUS, pb.TOOL_TYPE_ERASER)
                        else "touch"
                    )
                    self.state.record_touch(
                        pointer_id=pt.pointer_id,
                        x_norm=pt.x,
                        y_norm=pt.y,
                        pressure=pressure,
                        display_id=disp_id,
                        tool=tool_name,
                    )
        elif which == "key":
            ke = ev.key
            key_label = ke.dom_code or (
                f"code:{ke.android_keycode}"
                if ke.android_keycode
                else f"evdev:{ke.evdev_code}"
            )
            self.state.record_key(
                key_label=key_label,
                keycode=ke.android_keycode,
                action=ke.action,
            )
        elif which == "axis":
            with self.state._lock:
                if (
                    ev.axis.axis == pb.AXIS_TYPE_ROTARY_ENCODER
                    and not self.state.preset.has_rotary_input
                ):
                    context.abort(
                        grpc.StatusCode.FAILED_PRECONDITION,
                        "Device does not support rotary encoder input.",
                    )
            self.state.record_rotary(round(ev.axis.value))
        elif which == "text":
            self.state.record_key(key_label=ev.text.text)

    def SendInputEvents(
        self, request: pb.SendInputEventsRequest, context: grpc.ServicerContext
    ) -> pb.SendInputEventsResponse:
        if len(request.events) >= 2:
            for i in range(1, len(request.events)):
                if (
                    request.events[i].HasField("timestamp_nanos")
                    and request.events[i - 1].HasField("timestamp_nanos")
                    and request.events[i].timestamp_nanos
                    < request.events[i - 1].timestamp_nanos
                ):
                    context.abort(
                        grpc.StatusCode.INVALID_ARGUMENT,
                        "InputEvent.timestamp_nanos must be non-decreasing.",
                    )
            span_ns = (
                request.events[-1].timestamp_nanos - request.events[0].timestamp_nanos
            )
            if span_ns > 30_000_000_000:
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    "Batch gesture duration exceeds max_batch_duration_ms (30000ms).",
                )
        first_ts = self.state.monotonic_ns()
        for ev in request.events:
            self._apply_input_event(ev, context)
        last_ts = self.state.monotonic_ns()
        return pb.SendInputEventsResponse(
            first_event_dispatched_at_nanos=first_ts,
            last_event_dispatched_at_nanos=last_ts,
            events_dispatched=len(request.events),
        )

    def StreamInputEvents(
        self, request_iterator: Iterator[pb.InputEvent], context: grpc.ServicerContext
    ) -> pb.StreamInputEventsResponse:
        count = 0
        for ev in request_iterator:
            self._apply_input_event(ev, context)
            count += 1
        return pb.StreamInputEventsResponse(events_dispatched=count)

    def StreamInputActivity(
        self, request: pb.StreamInputActivityRequest, context: grpc.ServicerContext
    ) -> Iterator[pb.InputActivity]:
        del request
        with self.state.subscribe("input_activity") as q:
            while context.is_active():
                try:
                    rec = q.get(timeout=0.1)
                except queue.Empty:
                    continue
                if not context.is_active():
                    break
                disp_id = rec.get("display_id", 0)
                ts_ns = rec.get("timestamp_ns", self.state.monotonic_ns())
                kind = rec.get("kind")
                if kind == "touch":
                    ev = pb.InputEvent(
                        timestamp_nanos=ts_ns,
                        pointer=pb.PointerEvent(
                            display_id=disp_id,
                            action=(
                                pb.POINTER_ACTION_DOWN
                                if rec["pressure"] > 0
                                else pb.POINTER_ACTION_UP
                            ),
                            pointers=[
                                pb.PointerEvent.Pointer(
                                    pointer_id=rec["pointer_id"],
                                    tool_type=pb.TOOL_TYPE_FINGER,
                                    x=rec["x_norm"],
                                    y=rec["y_norm"],
                                    pressure=rec["pressure"],
                                )
                            ],
                        ),
                    )
                elif kind == "mouse":
                    ev = pb.InputEvent(
                        timestamp_nanos=ts_ns,
                        pointer=pb.PointerEvent(
                            display_id=disp_id,
                            action=pb.POINTER_ACTION_MOVE,
                            pointers=[
                                pb.PointerEvent.Pointer(
                                    tool_type=pb.TOOL_TYPE_MOUSE,
                                    x=rec["x_norm"],
                                    y=rec["y_norm"],
                                    button_mask=rec["buttons"],
                                )
                            ],
                        ),
                    )
                else:
                    ev = pb.InputEvent(
                        timestamp_nanos=ts_ns,
                        key=pb.KeyEvent(
                            action=pb.KEY_ACTION_PRESS,
                            dom_code=rec.get("key", ""),
                        ),
                    )
                yield pb.InputActivity(
                    event=ev,
                    dispatched_at_nanos=ts_ns,
                    synthesized=False,
                )
