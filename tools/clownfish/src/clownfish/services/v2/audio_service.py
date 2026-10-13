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


class AudioServiceServicer(pb_grpc.AudioServiceServicer):
    """Stateful implementation of `android.emulation.v2.AudioService`."""

    def __init__(self, state: EmulatorState) -> None:
        self.state = state

    @staticmethod
    def _validate_chunk(chunk: pb.AudioChunk, context: grpc.ServicerContext) -> int:
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
