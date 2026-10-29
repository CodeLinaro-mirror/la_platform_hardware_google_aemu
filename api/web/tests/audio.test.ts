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
import {
  AudioChunk,
  AudioState,
  MicrophoneInput_Kind,
  StreamSpeakerAudioRequest,
  UpdateAudioStateRequest,
} from '@android/emulator-services/audio_service';
import { FakeTransport } from './fake_transport';

const SERVICE = 'android.emulation.v2.AudioService';

describe('AudioClient', () => {
  let transport: FakeTransport;
  let client: AudioClient;

  beforeEach(() => {
    transport = new FakeTransport();
    client = new AudioClient(transport.asTransport());
  });

  function encodeChunk(chunk: Partial<AudioChunk>): Uint8Array {
    return AudioChunk.encode(AudioChunk.fromPartial(chunk)).finish();
  }

  it('iterates over decoded AudioChunks', async () => {
    transport.stream().push(
      encodeChunk({
        pcmData: new Uint8Array([0x01, 0x02, 0x03, 0x04]),
        sampleRateHz: 48000,
        channelCount: 2,
        format: SampleFormat.SAMPLE_FORMAT_PCM16_BIT,
        timestampNanos: 1000,
      })
    );
    transport.stream().end();

    const chunks: AudioChunk[] = [];
    for await (const chunk of client.StreamSpeakerAudio({ sampleRateHz: 48000 })) {
      chunks.push(chunk);
    }

    expect(transport.streamCalls).toHaveLength(1);
    expect(transport.streamCalls[0].service).toBe(SERVICE);
    expect(transport.streamCalls[0].method).toBe('StreamSpeakerAudio');
    expect(chunks).toHaveLength(1);
    expect(chunks[0].sampleRateHz).toBe(48000);
    expect(chunks[0].pcmData).toEqual(new Uint8Array([0x01, 0x02, 0x03, 0x04]));
  });

  it('sends the requested sample rate and format', async () => {
    transport.stream().end();

    await drain(
      client.StreamSpeakerAudio({
        sampleRateHz: 8000,
        format: SampleFormat.SAMPLE_FORMAT_F32_BIT,
      })
    );

    const request = StreamSpeakerAudioRequest.decode(transport.streamCalls[0].data);
    expect(request.sampleRateHz).toBe(8000);
    expect(request.format).toBe(SampleFormat.SAMPLE_FORMAT_F32_BIT);
  });

  // Both fields are `optional` in the proto, so leaving them out has to reach
  // the server as absent rather than as a rate the client picked: that is what
  // asks for the device's native format and avoids a resample.
  it('leaves the sample rate and format off the wire when they are not asked for', async () => {
    transport.stream().end();

    await drain(client.StreamSpeakerAudio());

    const request = StreamSpeakerAudioRequest.decode(transport.streamCalls[0].data);
    expect(request.sampleRateHz).toBeUndefined();
    expect(request.format).toBeUndefined();
  });

  it('sends only the field the caller supplied', async () => {
    transport.stream().end();

    await drain(client.StreamSpeakerAudio({ sampleRateHz: 16000 }));

    const request = StreamSpeakerAudioRequest.decode(transport.streamCalls[0].data);
    expect(request.sampleRateHz).toBe(16000);
    expect(request.format).toBeUndefined();
  });

  it('decodes each chunk pushed to a subscriber', async () => {
    const onChunk = vi.fn();

    const unsubscribe = client.subscribeSpeakerAudio(onChunk);
    transport.stream().push(encodeChunk({ sampleRateHz: 22050, channelCount: 1 }));
    await settle();

    expect(transport.streamCalls[0].service).toBe(SERVICE);
    expect(transport.streamCalls[0].method).toBe('StreamSpeakerAudio');
    expect(onChunk).toHaveBeenCalledWith(expect.objectContaining({ sampleRateHz: 22050 }));

    unsubscribe();
  });

  it('aborts the underlying stream on unsubscribe', async () => {
    const unsubscribe = client.subscribeSpeakerAudio(() => {});
    await settle();

    unsubscribe();

    expect(transport.streamCalls[0].signal?.aborted).toBe(true);
  });

  it('updates and observes AudioState over unary and streaming RPCs', async () => {
    transport.respondWith(
      AudioState.encode(
        AudioState.fromPartial({
          microphoneMuted: true,
          hostMicrophoneEnabled: false,
          microphoneInputs: [
            {
              sourceId: 'host',
              kind: MicrophoneInput_Kind.HOST,
              muted: true,
              gain: 1.0,
            },
          ],
        })
      ).finish()
    );

    const updated = await client.UpdateAudioState({
      microphoneMuted: true,
      hostMicrophoneEnabled: false,
    });

    expect(transport.calls).toHaveLength(1);
    expect(transport.calls[0].service).toBe(SERVICE);
    expect(transport.calls[0].method).toBe('UpdateAudioState');
    const sent = UpdateAudioStateRequest.decode(transport.calls[0].data);
    expect(sent.microphoneMuted).toBe(true);
    expect(sent.hostMicrophoneEnabled).toBe(false);
    expect(updated.microphoneMuted).toBe(true);
    expect(updated.hostMicrophoneEnabled).toBe(false);
    expect(updated.microphoneInputs).toHaveLength(1);
    expect(updated.microphoneInputs[0].sourceId).toBe('host');
  });
});

/** Lets the subscription's background task drain the queued messages. */
async function settle(): Promise<void> {
  for (let i = 0; i < 5; i++) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}

/** Consumes a stream to completion and returns everything it yielded. */
async function drain<T>(stream: AsyncIterable<T>): Promise<T[]> {
  const items: T[] = [];
  for await (const item of stream) {
    items.push(item);
  }
  return items;
}