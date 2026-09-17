// Copyright (C) 2026 The Android Open Source Project
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

import { GrpcWebTransport } from './transport';
import {
  AudioChunk,
  StreamSpeakerAudioRequest,
} from '@android/emulator-services/environment/audio_service';
import { SampleFormat } from '@android/emulator-services/common/common';

export { AudioChunk, SampleFormat };

export interface SpeakerAudioOptions {
  sampleRateHz?: number;
  format?: SampleFormat;
}

/**
 * Headless API client for AudioService (Speaker stream & Microphone audio injection).
 */
export class AudioClient {
  private readonly transport: GrpcWebTransport;

  constructor(transport: GrpcWebTransport) {
    this.transport = transport;
  }

  public streamSpeakerAudio(options?: SpeakerAudioOptions): AsyncIterable<AudioChunk> {
    const req: StreamSpeakerAudioRequest = {
      sampleRateHz: options?.sampleRateHz ?? 44100,
      format: options?.format ?? SampleFormat.SAMPLE_FORMAT_PCM16_BIT,
    };
    const reqBytes = StreamSpeakerAudioRequest.encode(req).finish();
    const byteStream = this.transport.serverStream(
      'android.emulation.v2.environment.AudioService',
      'StreamSpeakerAudio',
      reqBytes
    );

    return {
      async *[Symbol.asyncIterator]() {
        for await (const chunkBytes of byteStream) {
          yield AudioChunk.decode(chunkBytes);
        }
      },
    };
  }

  public subscribeSpeakerAudio(
    onChunk: (chunk: AudioChunk) => void,
    onError?: (err: any) => void,
    options?: SpeakerAudioOptions
  ): () => void {
    const req: StreamSpeakerAudioRequest = {
      sampleRateHz: options?.sampleRateHz ?? 44100,
      format: options?.format ?? SampleFormat.SAMPLE_FORMAT_PCM16_BIT,
    };
    const reqBytes = StreamSpeakerAudioRequest.encode(req).finish();
    return this.transport.stream(
      'android.emulation.v2.environment.AudioService',
      'StreamSpeakerAudio',
      reqBytes,
      (bytes) => onChunk(AudioChunk.decode(bytes)),
      onError
    );
  }

  public async sendMicrophoneChunk(chunk: {
    pcmData: Uint8Array;
    sampleRateHz?: number;
    channelCount?: number;
    format?: SampleFormat;
  }): Promise<Uint8Array> {
    const audioChunk: AudioChunk = {
      pcmData: chunk.pcmData,
      sampleRateHz: chunk.sampleRateHz ?? 16000,
      channelCount: chunk.channelCount ?? 1,
      format: chunk.format ?? SampleFormat.SAMPLE_FORMAT_PCM16_BIT,
      timestampNanos: 0,
    };
    const reqBytes = AudioChunk.encode(audioChunk).finish();
    return this.transport.request(
      'android.emulation.v2.environment.AudioService',
      'StreamMicrophoneAudio',
      reqBytes
    );
  }
}