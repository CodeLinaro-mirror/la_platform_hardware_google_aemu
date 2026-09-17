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
import { ScreenCaptureClient, ImageFormat } from '../src/core/screen_capture';
import {
  Screenshot,
  GetScreenshotRequest,
  StreamScreenshotsRequest,
} from '@android/emulator-services/media/screen_capture_service';

describe('ScreenCaptureClient (TDD)', () => {
  let mockTransport: any;

  beforeEach(() => {
    mockTransport = {
      request: vi.fn(),
      serverStream: vi.fn(),
    };
  });

  it('getScreenshot requests screenshot and decodes frame bytes', async () => {
    const client = new ScreenCaptureClient(mockTransport);

    const mockFrame: Screenshot = {
      imageData: new Uint8Array([0x89, 0x50, 0x4e, 0x47]),
      width: 1080,
      height: 2400,
      rowStrideBytes: 4320,
      frameSequenceNumber: 42,
      presentationTimeNanos: 123456789,
    };
    mockTransport.request.mockResolvedValue(Screenshot.encode(mockFrame).finish());

    const result = await client.getScreenshot({ displayId: 0, format: ImageFormat.IMAGE_FORMAT_PNG });
    expect(mockTransport.request).toHaveBeenCalledTimes(1);
    const [service, method, data] = mockTransport.request.mock.calls[0];
    expect(service).toBe('android.emulation.v2.media.ScreenCaptureService');
    expect(method).toBe('GetScreenshot');

    const decodedReq = GetScreenshotRequest.decode(data);
    expect(decodedReq.displayId).toBe(0);
    expect(decodedReq.format).toBe(ImageFormat.IMAGE_FORMAT_PNG);
    expect(result.width).toBe(1080);
    expect(result.frameSequenceNumber).toBe(42);
  });

  it('streamScreenshots streams continuous frames with fps throttle', async () => {
    const client = new ScreenCaptureClient(mockTransport);

    const mockFrame: Screenshot = {
      imageData: new Uint8Array([0xff, 0xd8]),
      width: 720,
      height: 1280,
      rowStrideBytes: 2880,
      frameSequenceNumber: 1,
      presentationTimeNanos: 1000,
    };
    const encoded = Screenshot.encode(mockFrame).finish();

    mockTransport.serverStream.mockReturnValue((async function* () {
      yield encoded;
    })());

    const frames: Screenshot[] = [];
    for await (const frame of client.streamScreenshots({ displayId: 0, maxFramesPerSecond: 15 })) {
      frames.push(frame);
    }

    expect(mockTransport.serverStream).toHaveBeenCalledTimes(1);
    const [service, method, data] = mockTransport.serverStream.mock.calls[0];
    expect(service).toBe('android.emulation.v2.media.ScreenCaptureService');
    expect(method).toBe('StreamScreenshots');

    const decodedReq = StreamScreenshotsRequest.decode(data);
    expect(decodedReq.maxFramesPerSecond).toBe(15);
    expect(frames).toHaveLength(1);
    expect(frames[0].width).toBe(720);
  });
});