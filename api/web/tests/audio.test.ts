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

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { AudioClient, SampleFormat } from '../src/core/audio';
import { AudioChunk } from '@android/emulator-services/environment/audio_service';

describe('AudioClient (TDD)', () => {
  let mockTransport: any;

  beforeEach(() => {
    mockTransport = {
      request: vi.fn(),
      serverStream: vi.fn(),
    };
  });

  it('streamSpeakerAudio iterates over decoded AudioChunks', async () => {
    const client = new AudioClient(mockTransport);

    const chunk1: AudioChunk = {
      pcmData: new Uint8Array([0x01, 0x02, 0x03, 0x04]),
      sampleRateHz: 48000,
      channelCount: 2,
      format: SampleFormat.SAMPLE_FORMAT_PCM16_BIT,
      timestampNanos: 1000,
    };

    const encodedChunk = AudioChunk.encode(chunk1).finish();
    mockTransport.serverStream.mockReturnValue((async function* () {
      yield encodedChunk;
    })());

    const chunks: AudioChunk[] = [];
    for await (const chunk of client.streamSpeakerAudio({ sampleRateHz: 48000 })) {
      chunks.push(chunk);
    }

    expect(mockTransport.serverStream).toHaveBeenCalledTimes(1);
    const [service, method] = mockTransport.serverStream.mock.calls[0];
    expect(service).toBe('android.emulation.v2.environment.AudioService');
    expect(method).toBe('StreamSpeakerAudio');
    expect(chunks).toHaveLength(1);
    expect(chunks[0].sampleRateHz).toBe(48000);
    expect(chunks[0].pcmData).toEqual(new Uint8Array([0x01, 0x02, 0x03, 0x04]));
  });

  it('sendMicrophoneChunk encodes AudioChunk for microphone input', async () => {
    const client = new AudioClient(mockTransport);
    mockTransport.request.mockResolvedValue(new Uint8Array([]));

    const pcm = new Uint8Array([0x10, 0x20]);
    await client.sendMicrophoneChunk({ pcmData: pcm, sampleRateHz: 16000 });

    expect(mockTransport.request).toHaveBeenCalledTimes(1);
    const [service, method, data] = mockTransport.request.mock.calls[0];
    expect(service).toBe('android.emulation.v2.environment.AudioService');
    expect(method).toBe('StreamMicrophoneAudio');

    const decoded = AudioChunk.decode(data);
    expect(decoded.sampleRateHz).toBe(16000);
    expect(decoded.pcmData).toEqual(pcm);
  });
});