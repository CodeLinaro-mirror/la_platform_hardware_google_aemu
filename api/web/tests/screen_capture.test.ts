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
import { ScreenCaptureClient, ImageFormat, Screenshot } from '../src/core/screen_capture';
import {
  GetScreenshotRequest,
  StreamScreenshotsRequest,
} from '@android/emulator-services/display_service';
import { FakeTransport } from './fake_transport';

const SERVICE = 'android.emulation.v2.DisplayService';

describe('ScreenCaptureClient', () => {
  let transport: FakeTransport;
  let client: ScreenCaptureClient;

  beforeEach(() => {
    transport = new FakeTransport();
    client = new ScreenCaptureClient(transport.asTransport());
  });

  function encodeFrame(frame: Partial<Screenshot>): Uint8Array {
    return Screenshot.encode(Screenshot.fromPartial(frame)).finish();
  }

  it('requests a screenshot and decodes the frame', async () => {
    transport.respondWith(
      encodeFrame({
        imageData: new Uint8Array([0x89, 0x50, 0x4e, 0x47]),
        width: 1080,
        height: 2400,
        rowStrideBytes: 4320,
        frameSequenceNumber: 42,
        presentationTimeNanos: 123456789,
      })
    );

    const result = await client.GetScreenshot({
      displayId: 0,
      format: ImageFormat.IMAGE_FORMAT_PNG,
    });

    expect(transport.calls).toHaveLength(1);
    expect(transport.lastCall.service).toBe(SERVICE);
    expect(transport.lastCall.method).toBe('GetScreenshot');

    const request = GetScreenshotRequest.decode(transport.lastCall.data);
    expect(request.displayId).toBe(0);
    expect(request.format).toBe(ImageFormat.IMAGE_FORMAT_PNG);
    expect(result.width).toBe(1080);
    expect(result.frameSequenceNumber).toBe(42);
  });

  // The client no longer substitutes PNG on display 0 for an unspecified
  // request, so the format and display the caller picks are what get encoded.
  it('sends the requested format and display verbatim', async () => {
    transport.respondWith(encodeFrame({}));

    await client.GetScreenshot({ displayId: 2, format: ImageFormat.IMAGE_FORMAT_JPEG });

    const request = GetScreenshotRequest.decode(transport.lastCall.data);
    expect(request.displayId).toBe(2);
    expect(request.format).toBe(ImageFormat.IMAGE_FORMAT_JPEG);
  });

  it('streams continuous frames with the requested fps throttle', async () => {
    const stream = transport.stream();
    const controller = new AbortController();
    const frames: Screenshot[] = [];

    const consumed = (async () => {
      for await (const frame of client.StreamScreenshots(
        { displayId: 0, maxFramesPerSecond: 15 },
        controller.signal
      )) {
        frames.push(frame);
        controller.abort();
      }
    })();

    stream.push(
      encodeFrame({
        imageData: new Uint8Array([0xff, 0xd8]),
        width: 720,
        height: 1280,
        rowStrideBytes: 2880,
        frameSequenceNumber: 1,
        presentationTimeNanos: 1000,
      })
    );
    await consumed;

    expect(transport.streamCalls).toHaveLength(1);
    expect(transport.streamCalls[0].service).toBe(SERVICE);
    expect(transport.streamCalls[0].method).toBe('StreamScreenshots');

    const request = StreamScreenshotsRequest.decode(transport.streamCalls[0].data);
    expect(request.maxFramesPerSecond).toBe(15);
    expect(frames).toHaveLength(1);
    expect(frames[0].width).toBe(720);
  });

  // There is no client-side 10fps/PNG default any more: a subscription sends
  // exactly the request the caller handed it.
  it('sends the subscription request the caller supplied', () => {
    const unsubscribe = client.subscribeScreenshots(() => {}, undefined, {
      displayId: 1,
      format: ImageFormat.IMAGE_FORMAT_JPEG,
      maxFramesPerSecond: 5,
    });

    const request = StreamScreenshotsRequest.decode(transport.streamCalls[0].data);
    expect(request.displayId).toBe(1);
    expect(request.format).toBe(ImageFormat.IMAGE_FORMAT_JPEG);
    expect(request.maxFramesPerSecond).toBe(5);

    unsubscribe();
  });

  it('decodes each frame pushed to a subscription', async () => {
    const onScreenshot = vi.fn();

    const unsubscribe = client.subscribeScreenshots(onScreenshot, undefined, {
      maxFramesPerSecond: 30,
    });
    transport.stream().push(encodeFrame({ width: 540, frameSequenceNumber: 7 }));
    await settle();

    expect(transport.streamCalls[0].method).toBe('StreamScreenshots');
    expect(onScreenshot).toHaveBeenCalledWith(
      expect.objectContaining({ width: 540, frameSequenceNumber: 7 })
    );

    unsubscribe();
  });

  it('aborts the underlying stream on unsubscribe', async () => {
    const unsubscribe = client.subscribeScreenshots(() => {});
    await settle();

    expect(transport.streamCalls[0].signal?.aborted).toBe(false);

    unsubscribe();

    expect(transport.streamCalls[0].signal?.aborted).toBe(true);
  });
});

/** Lets the subscription's background task drain the queued messages. */
async function settle(): Promise<void> {
  for (let i = 0; i < 5; i++) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}