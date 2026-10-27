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

"""Implementation of `android.emulation.v2.AudioService`."""

from collections.abc import Iterator
import math
import queue

import audio_service_pb2 as pb
import audio_service_pb2_grpc as pb_grpc
import common_pb2
import grpc

from clownfish.state import EmulatorState

_SUPPORTED_RATES = {16000, 44100, 48000}
_SUPPORTED_CHANNELS = {1, 2}
_SUPPORTED_FORMATS = {
    common_pb2.SAMPLE_FORMAT_PCM16_BIT,
    common_pb2.SAMPLE_FORMAT_F32_BIT,
}
_MIN_INPUT_GAIN = 0.0
_MAX_INPUT_GAIN = 4.0


class AudioServiceServicer(pb_grpc.AudioServiceServicer):
    """Stateful implementation of `android.emulation.v2.AudioService`."""

    def __init__(self, state: EmulatorState) -> None:
        self.state = state

    def _build_audio_state(self) -> pb.AudioState:
        with self.state._lock:
            inputs = [
                pb.MicrophoneInput(
                    source_id=src_id,
                    kind=info["kind"],
                    muted=info["muted"],
                    gain=info["gain"],
                )
                for src_id, info in self.state.mic_inputs.items()
            ]
            return pb.AudioState(
                microphone_muted=self.state.mic_is_muted,
                host_microphone_enabled=self.state.host_mic_enabled,
                microphone_in_use_by_guest=self.state.mic_in_use_by_guest,
                microphone_inputs=inputs,
                timestamp_nanos=self.state.monotonic_ns(),
            )

    def GetAudioState(
        self, request: pb.GetAudioStateRequest, context: grpc.ServicerContext
    ) -> pb.AudioState:
        return self._build_audio_state()

    def StreamAudioState(
        self, request: pb.StreamAudioStateRequest, context: grpc.ServicerContext
    ) -> Iterator[pb.AudioState]:
        with self.state.subscribe("audio") as q:
            yield self._build_audio_state()
            while context.is_active():
                try:
                    q.get(timeout=0.1)
                except queue.Empty:
                    continue
                if not context.is_active():
                    break
                yield self._build_audio_state()

    def UpdateAudioState(
        self, request: pb.UpdateAudioStateRequest, context: grpc.ServicerContext
    ) -> pb.AudioState:
        for upd in request.microphone_inputs:
            if not upd.source_id:
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    "source_id in microphone_inputs must be non-empty.",
                )
            if upd.HasField("gain") and (
                not math.isfinite(upd.gain)
                or upd.gain < _MIN_INPUT_GAIN
                or upd.gain > _MAX_INPUT_GAIN
            ):
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    f"gain {upd.gain} must be within [{_MIN_INPUT_GAIN}, {_MAX_INPUT_GAIN}].",
                )

        with self.state._lock:
            for upd in request.microphone_inputs:
                if upd.source_id not in self.state.mic_inputs:
                    context.abort(
                        grpc.StatusCode.NOT_FOUND,
                        f"Unknown microphone input source_id '{upd.source_id}'.",
                    )

            if request.HasField("microphone_muted"):
                self.state.mic_is_muted = request.microphone_muted
            if request.HasField("host_microphone_enabled"):
                self.state.host_mic_enabled = request.host_microphone_enabled
                if "host" in self.state.mic_inputs:
                    self.state.mic_inputs["host"]["muted"] = (
                        not request.host_microphone_enabled
                    )

            for upd in request.microphone_inputs:
                entry = self.state.mic_inputs[upd.source_id]
                if upd.HasField("muted"):
                    entry["muted"] = upd.muted
                    if upd.source_id == "host":
                        self.state.host_mic_enabled = not upd.muted
                if upd.HasField("gain"):
                    entry["gain"] = upd.gain

            queues = self.state._snapshot_subscribers_locked("audio")

        self.state._publish_unlocked(queues, True)
        return self._build_audio_state()

    @staticmethod
    def _validate_chunk(chunk: pb.AudioChunk, context: grpc.ServicerContext) -> int:
        if chunk.source_id == "host":
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "source_id 'host' is reserved for the host physical microphone.",
            )
        if (
            chunk.sample_rate_hz not in _SUPPORTED_RATES
            or chunk.channel_count not in _SUPPORTED_CHANNELS
            or chunk.format not in _SUPPORTED_FORMATS
        ):
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "Unsupported audio sample rate, channel count, or sample format.",
            )
        bps = 2 if chunk.format == common_pb2.SAMPLE_FORMAT_PCM16_BIT else 4
        bytes_per_sec = max(1, chunk.sample_rate_hz * chunk.channel_count * bps)
        return int((len(chunk.pcm_data) * 1000) / bytes_per_sec)

    def InjectAudioClip(
        self, request: pb.AudioChunk, context: grpc.ServicerContext
    ) -> pb.InjectAudioClipResponse:
        duration_ms = self._validate_chunk(request, context)
        with self.state._lock:
            self.state.injected_audio_bytes += len(request.pcm_data)
        return pb.InjectAudioClipResponse(duration_ms=duration_ms)

    def StreamMicrophoneAudio(
        self, request_iterator: Iterator[pb.AudioChunk], context: grpc.ServicerContext
    ) -> pb.StreamMicrophoneAudioResponse:
        total = 0
        for chunk in request_iterator:
            self._validate_chunk(chunk, context)
            total += len(chunk.pcm_data)
        with self.state._lock:
            self.state.injected_audio_bytes += total
        return pb.StreamMicrophoneAudioResponse()

    def StreamSpeakerAudio(
        self, request: pb.StreamSpeakerAudioRequest, context: grpc.ServicerContext
    ) -> Iterator[pb.AudioChunk]:
        rate = request.sample_rate_hz if request.HasField("sample_rate_hz") else 48000
        ch = request.channel_count if request.HasField("channel_count") else 2
        fmt = (
            request.format
            if request.HasField("format")
            and request.format != common_pb2.SAMPLE_FORMAT_UNSPECIFIED
            else common_pb2.SAMPLE_FORMAT_PCM16_BIT
        )
        if (
            rate not in _SUPPORTED_RATES
            or ch not in _SUPPORTED_CHANNELS
            or fmt not in _SUPPORTED_FORMATS
        ):
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "Requested speaker sample rate, channel count, or format is unsupported.",
            )
        bps = 2 if fmt == common_pb2.SAMPLE_FORMAT_PCM16_BIT else 4
        num_samples = rate // 20  # 50ms frame
        yield pb.AudioChunk(
            pcm_data=b"\x00" * (num_samples * ch * bps),
            sample_rate_hz=rate,
            channel_count=ch,
            format=fmt,
            timestamp_nanos=self.state.monotonic_ns(),
        )
