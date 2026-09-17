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
import { CameraClient, ImageFormat } from '../src/core/camera';
import {
  CameraFrame,
  ResetCameraRequest,
} from '@android/emulator-services/environment/camera_service';

describe('CameraClient (TDD)', () => {
  let mockTransport: any;

  beforeEach(() => {
    mockTransport = {
      request: vi.fn(),
    };
  });

  it('injectFrame encodes frame payload and camera id', async () => {
    const client = new CameraClient(mockTransport);
    mockTransport.request.mockResolvedValue(new Uint8Array([]));

    const testPng = new Uint8Array([0x89, 0x50, 0x4e, 0x47]);
    await client.injectFrame({
      cameraId: '1',
      frameData: testPng,
      format: ImageFormat.IMAGE_FORMAT_PNG,
    });

    expect(mockTransport.request).toHaveBeenCalledTimes(1);
    const [service, method, data] = mockTransport.request.mock.calls[0];
    expect(service).toBe('android.emulation.v2.environment.CameraService');
    expect(method).toBe('InjectFrame');

    const decoded = CameraFrame.decode(data);
    expect(decoded.cameraId).toBe('1');
    expect(decoded.format).toBe(ImageFormat.IMAGE_FORMAT_PNG);
    expect(decoded.frameData).toEqual(testPng);
  });

  it('resetCamera encodes ResetCameraRequest', async () => {
    const client = new CameraClient(mockTransport);
    mockTransport.request.mockResolvedValue(new Uint8Array([]));

    await client.resetCamera('0');
    expect(mockTransport.request).toHaveBeenCalledTimes(1);
    const [service, method, data] = mockTransport.request.mock.calls[0];
    expect(service).toBe('android.emulation.v2.environment.CameraService');
    expect(method).toBe('ResetCamera');

    const decoded = ResetCameraRequest.decode(data);
    expect(decoded.cameraId).toBe('0');
  });
});