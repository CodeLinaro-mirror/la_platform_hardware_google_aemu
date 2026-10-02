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

"""Implementation of `android.emulation.v2.RtcService` (`webrtc_service.proto`)."""

import logging
import queue
import threading
import uuid
from collections.abc import Iterator

import common_pb2
import grpc
import webrtc_service_pb2 as pb
import webrtc_service_pb2_grpc as pb_grpc

from clownfish.state import EmulatorState

_logger = logging.getLogger(__name__)

_SUPPORTED_TRACKS = {
    common_pb2.TRACK_TYPE_SCREEN_PRIMARY,
    common_pb2.TRACK_TYPE_SCREEN_SECONDARY,
    common_pb2.TRACK_TYPE_AUDIO_SPEAKER,
    common_pb2.TRACK_TYPE_AUDIO_MICROPHONE,
}

_SYNTHETIC_SDP_OFFER = (
    "v=0\r\n"
    "o=clownfish 123456 2 IN IP4 127.0.0.1\r\n"
    "s=Clownfish Synthetic WebRTC Session\r\n"
    "t=0 0\r\n"
    "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
    "a=mid:0\r\n"
    "a=sendonly\r\n"
    "a=rtpmap:96 VP8/90000\r\n"
)

_SYNTHETIC_SDP_ANSWER = (
    "v=0\r\n"
    "o=clownfish 123456 2 IN IP4 127.0.0.1\r\n"
    "s=Clownfish Synthetic WebRTC Answer\r\n"
    "t=0 0\r\n"
    "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
    "a=mid:0\r\n"
    "a=sendonly\r\n"
    "a=rtpmap:96 VP8/90000\r\n"
)


class RtcServiceServicer(pb_grpc.RtcServiceServicer):
    """Stateful implementation of `android.emulation.v2.RtcService`.

    Supports two operating modes:
    - Real WebRTC mode (when `videobridge_address` is configured): proxies
      signaling RPCs (`RequestRtcStream`, `UpdateRtcStream`, `CloseRtcStream`,
      `SendJsepMessage`, `ReceiveJsepMessageStream`) to a running C++
      `videobridge` instance backed by `@webrtc` `Switchboard`.
    - Synthetic loopback mode (when `videobridge_address` is `None`): provides
      deterministic in-memory JSEP session and SDP loopback for unit tests.
    """

    def __init__(
        self,
        state: EmulatorState,
        videobridge_address: str | None = None,
    ) -> None:
        self.state = state
        self._vb_lock = threading.Lock()
        self._videobridge_address: str | None = None
        self._vb_channel: grpc.Channel | None = None
        self._vb_stub: pb_grpc.RtcServiceStub | None = None
        if videobridge_address:
            self.set_videobridge_address(videobridge_address)

    @property
    def videobridge_address(self) -> str | None:
        with self._vb_lock:
            return self._videobridge_address

    def set_videobridge_address(self, address: str | None) -> None:
        """Configures or clears the upstream C++ `videobridge` gRPC target address."""
        with self._vb_lock:
            if self._vb_channel is not None:
                self._vb_channel.close()
                self._vb_channel = None
                self._vb_stub = None
            self._videobridge_address = address
            if address:
                self._vb_channel = grpc.insecure_channel(address)
                self._vb_stub = pb_grpc.RtcServiceStub(self._vb_channel)
                _logger.info(
                    "RtcService proxying WebRTC signaling to videobridge at %s",
                    address,
                )

    def _get_vb_stub(self) -> pb_grpc.RtcServiceStub | None:
        with self._vb_lock:
            return self._vb_stub

    def _build_track_bindings(
        self, request: pb.RtcStreamRequest
    ) -> list[pb.RtcTrackBinding]:
        bindings: list[pb.RtcTrackBinding] = []
        mid_idx = 0
        for trk in request.requested_tracks:
            if trk == common_pb2.TRACK_TYPE_SCREEN_PRIMARY:
                bindings.append(
                    pb.RtcTrackBinding(mid=str(mid_idx), track_type=trk, display_id=0)
                )
                mid_idx += 1
            elif trk == common_pb2.TRACK_TYPE_SCREEN_SECONDARY:
                sec_ids = list(request.secondary_display_ids) or [1]
                for sid in sec_ids:
                    bindings.append(
                        pb.RtcTrackBinding(
                            mid=str(mid_idx), track_type=trk, display_id=sid
                        )
                    )
                    mid_idx += 1
            else:
                bindings.append(pb.RtcTrackBinding(mid=str(mid_idx), track_type=trk))
                mid_idx += 1
        return bindings

    def _resolve_ice_servers(self, request: pb.RtcStreamRequest) -> list[pb.IceServer]:
        if (
            request.HasField("ice_server_config")
            and request.ice_server_config.ice_servers
        ):
            return list(request.ice_server_config.ice_servers)
        return [pb.IceServer(urls=["stun:stun.l.google.com:19302"])]

    def RequestRtcStream(
        self, request: pb.RtcStreamRequest, context: grpc.ServicerContext
    ) -> pb.RtcStreamResponse:
        if not request.requested_tracks:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "requested_tracks must not be empty.",
            )
        for trk in request.requested_tracks:
            if trk not in _SUPPORTED_TRACKS:
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    f"Unsupported TrackType: {trk}",
                )

        bindings = self._build_track_bindings(request)
        ice_servers = self._resolve_ice_servers(request)
        vb_stub = self._get_vb_stub()

        placeholder_id = f"pending-{uuid.uuid4().hex}"
        with self.state._lock:
            for sec_id in request.secondary_display_ids:
                if sec_id <= 0 or sec_id not in self.state.displays:
                    context.abort(
                        grpc.StatusCode.INVALID_ARGUMENT,
                        f"Invalid secondary display ID: {sec_id}",
                    )
            if len(self.state.rtc_sessions) >= 4:
                context.abort(
                    grpc.StatusCode.RESOURCE_EXHAUSTED,
                    "Maximum concurrent WebRTC session limit (4) reached.",
                )
            if vb_stub is None:
                session_id = f"rtc-{uuid.uuid4().hex[:12]}"
                q: queue.Queue[pb.JsepMessage] = queue.Queue()
                handle = pb.RtcSession(session_id=session_id)
                q.put(
                    pb.JsepMessage(
                        handle=handle,
                        sdp=pb.SessionDescription(
                            type=pb.SessionDescription.OFFER,
                            sdp=_SYNTHETIC_SDP_OFFER,
                        ),
                    )
                )
                q.put(
                    pb.JsepMessage(
                        handle=handle,
                        ice_candidate=pb.IceCandidate(
                            candidate="", sdp_mid="0", sdp_m_line_index=0
                        ),
                    )
                )
                self.state.rtc_sessions[session_id] = {
                    "tracks": list(request.requested_tracks),
                    "bindings": bindings,
                    "queue": q,
                    "received_messages": [],
                    "proxied": False,
                }
                return pb.RtcStreamResponse(
                    handle=handle,
                    ice_servers=ice_servers,
                    track_bindings=bindings,
                )
            self.state.rtc_sessions[placeholder_id] = {"proxied": True}

        try:
            vb_resp = vb_stub.RequestRtcStream(request)
        except grpc.RpcError as err:
            with self.state._lock:
                self.state.rtc_sessions.pop(placeholder_id, None)
            context.abort(err.code(), err.details() or str(err))

        session_id = vb_resp.handle.session_id
        with self.state._lock:
            self.state.rtc_sessions.pop(placeholder_id, None)
            self.state.rtc_sessions[session_id] = {
                "tracks": list(request.requested_tracks),
                "bindings": bindings,
                "queue": queue.Queue(),
                "received_messages": [],
                "proxied": True,
            }
        return pb.RtcStreamResponse(
            handle=vb_resp.handle,
            ice_servers=list(vb_resp.ice_servers) or ice_servers,
            track_bindings=list(vb_resp.track_bindings) or bindings,
        )

    def UpdateRtcStream(
        self, request: pb.RtcStreamUpdateRequest, context: grpc.ServicerContext
    ) -> pb.UpdateRtcStreamResponse:
        vb_stub = self._get_vb_stub()
        if vb_stub is not None:
            try:
                return vb_stub.UpdateRtcStream(request)
            except grpc.RpcError as err:
                context.abort(err.code(), err.details() or str(err))

        sid = request.handle.session_id
        with self.state._lock:
            sess = self.state.rtc_sessions.get(sid)
            if sess is None:
                context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    f"WebRTC session '{sid}' does not exist.",
                )
            sess["queue"].put(
                pb.JsepMessage(
                    handle=request.handle,
                    sdp=pb.SessionDescription(
                        type=pb.SessionDescription.OFFER,
                        sdp=_SYNTHETIC_SDP_OFFER,
                    ),
                )
            )
        return pb.UpdateRtcStreamResponse()

    def CloseRtcStream(
        self, request: pb.CloseRtcStreamRequest, context: grpc.ServicerContext
    ) -> pb.CloseRtcStreamResponse:
        sid = request.handle.session_id
        vb_stub = self._get_vb_stub()
        if vb_stub is not None:
            with self.state._lock:
                self.state.rtc_sessions.pop(sid, None)
            try:
                return vb_stub.CloseRtcStream(request)
            except grpc.RpcError as err:
                context.abort(err.code(), err.details() or str(err))

        with self.state._lock:
            if sid not in self.state.rtc_sessions:
                context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    f"WebRTC session '{sid}' does not exist.",
                )
            del self.state.rtc_sessions[sid]
        return pb.CloseRtcStreamResponse()

    def SendJsepMessage(
        self, request: pb.JsepMessage, context: grpc.ServicerContext
    ) -> pb.SendJsepMessageResponse:
        sid = request.handle.session_id
        if not request.WhichOneof("payload"):
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "JsepMessage payload (sdp or ice_candidate) must be set.",
            )
        if request.HasField("sdp") and not request.sdp.sdp.strip():
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "SessionDescription.sdp must not be empty.",
            )

        vb_stub = self._get_vb_stub()
        if vb_stub is not None:
            with self.state._lock:
                sess = self.state.rtc_sessions.get(sid)
                if sess is not None:
                    sess["received_messages"].append(request)
            try:
                return vb_stub.SendJsepMessage(request)
            except grpc.RpcError as err:
                context.abort(err.code(), err.details() or str(err))

        with self.state._lock:
            sess = self.state.rtc_sessions.get(sid)
            if sess is None:
                context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    f"WebRTC session '{sid}' does not exist.",
                )
            sess["received_messages"].append(request)
            if (
                request.HasField("sdp")
                and request.sdp.type == pb.SessionDescription.OFFER
            ):
                sess["queue"].put(
                    pb.JsepMessage(
                        handle=request.handle,
                        sdp=pb.SessionDescription(
                            type=pb.SessionDescription.ANSWER,
                            sdp=_SYNTHETIC_SDP_ANSWER,
                        ),
                    )
                )
        return pb.SendJsepMessageResponse()

    def ReceiveJsepMessageStream(
        self, request: pb.RtcSession, context: grpc.ServicerContext
    ) -> Iterator[pb.JsepMessage]:
        vb_stub = self._get_vb_stub()
        if vb_stub is not None:
            try:
                upstream = vb_stub.ReceiveJsepMessageStream(request)
                context.add_callback(lambda: upstream.cancel())
                for msg in upstream:
                    if not context.is_active():
                        break
                    yield msg
            except grpc.RpcError as err:
                if err.code() != grpc.StatusCode.CANCELLED and context.is_active():
                    context.abort(err.code(), err.details() or str(err))
            return

        sid = request.session_id
        with self.state._lock:
            sess = self.state.rtc_sessions.get(sid)
            if sess is None:
                context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    f"WebRTC session '{sid}' does not exist.",
                )
            q: queue.Queue[pb.JsepMessage] = sess["queue"]

        while context.is_active():
            try:
                msg = q.get(timeout=0.05)
                yield msg
            except queue.Empty:
                with self.state._lock:
                    if sid not in self.state.rtc_sessions:
                        break
                continue
