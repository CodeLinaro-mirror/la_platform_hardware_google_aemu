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
import { subscribe, Unsubscribe } from './streaming';
import {
  AudioChunk,
  AudioService,
  AudioServiceClientImpl,
  DeepPartial,
  InjectAudioClipResponse,
  StreamSpeakerAudioRequest,
} from '@android/emulator-services/audio_service';
import { SampleFormat } from '@android/emulator-services/common';

export { AudioChunk, InjectAudioClipResponse, SampleFormat };

/**
 * Headless API client for AudioService.
 *
 * Methods named in `PascalCase` are the RPCs of `AudioService`, spelled exactly
 * as `audio_service.proto` declares them. `camelCase` methods are conveniences
 * this package adds and have no RPC of their own.
 */
export class AudioClient {
  private readonly rpc: AudioService;

  constructor(transport: GrpcWebTransport) {
    this.rpc = new AudioServiceClientImpl(transport);
  }

  public InjectAudioClip(clip: DeepPartial<AudioChunk>): Promise<InjectAudioClipResponse> {
    return this.rpc.InjectAudioClip(AudioChunk.fromPartial(clip));
  }

  /**
   * `sampleRateHz` and `format` are optional in the proto: leaving them out
   * asks for the device's native rate and format, which avoids a resample.
   */
  public StreamSpeakerAudio(
    request: DeepPartial<StreamSpeakerAudioRequest> = {},
    signal?: AbortSignal
  ): AsyncIterable<AudioChunk> {
    return this.rpc.StreamSpeakerAudio(StreamSpeakerAudioRequest.fromPartial(request), signal);
  }

  /** Callback form of {@link AudioClient.StreamSpeakerAudio}. */
  public subscribeSpeakerAudio(
    onChunk: (chunk: AudioChunk) => void,
    onError?: (err: unknown) => void,
    request: DeepPartial<StreamSpeakerAudioRequest> = {}
  ): Unsubscribe {
    return subscribe((signal) => this.StreamSpeakerAudio(request, signal), onChunk, onError);
  }
}