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

import { describe, it, expect, beforeEach } from 'vitest';
import { CameraClient, ImageFormat } from '../src/core/camera';
import {
  CameraFrame,
  ResetCameraRequest,
} from '@android/emulator-services/camera_service';
import { Empty } from '@android/emulator-services/google/protobuf/empty';
import { FakeTransport } from './fake_transport';

const SERVICE = 'android.emulation.v2.CameraService';

describe('CameraClient', () => {
  let transport: FakeTransport;
  let client: CameraClient;

  beforeEach(() => {
    transport = new FakeTransport();
    client = new CameraClient(transport.asTransport());
    transport.respondWith(Empty.encode({}).finish());
  });

  it('sends the frame payload and the camera id to InjectFrame', async () => {
    const testPng = new Uint8Array([0x89, 0x50, 0x4e, 0x47]);

    await client.InjectFrame({
      cameraId: '1',
      frameData: testPng,
      format: ImageFormat.IMAGE_FORMAT_PNG,
    });

    expect(transport.calls).toHaveLength(1);
    expect(transport.lastCall.service).toBe(SERVICE);
    expect(transport.lastCall.method).toBe('InjectFrame');

    const frame = CameraFrame.decode(transport.lastCall.data);
    expect(frame.cameraId).toBe('1');
    expect(frame.format).toBe(ImageFormat.IMAGE_FORMAT_PNG);
    expect(frame.frameData).toEqual(testPng);
  });

  // The client no longer picks a camera or a format for the caller, so a frame
  // that names neither arrives carrying only the proto3 defaults and the server
  // decides what they mean.
  it('does not fill in a camera id or a format the caller left out', async () => {
    await client.InjectFrame({ frameData: new Uint8Array([1, 2, 3]) });

    const frame = CameraFrame.decode(transport.lastCall.data);
    expect(frame.cameraId).toBe('');
    expect(frame.format).toBe(ImageFormat.IMAGE_FORMAT_UNSPECIFIED);
    expect(frame.frameData).toEqual(new Uint8Array([1, 2, 3]));
  });

  it('forwards the raw frame geometry when it is supplied', async () => {
    await client.InjectFrame({
      frameData: new Uint8Array([0, 0, 0, 0]),
      format: ImageFormat.IMAGE_FORMAT_RAW_RGBA8888,
      width: 2,
      height: 1,
      rowStrideBytes: 8,
    });

    const frame = CameraFrame.decode(transport.lastCall.data);
    expect(frame.width).toBe(2);
    expect(frame.height).toBe(1);
    expect(frame.rowStrideBytes).toBe(8);
  });

  it('sends the camera id to ResetCamera', async () => {
    await client.ResetCamera({ cameraId: '0' });

    expect(transport.calls).toHaveLength(1);
    expect(transport.lastCall.service).toBe(SERVICE);
    expect(transport.lastCall.method).toBe('ResetCamera');
    expect(ResetCameraRequest.decode(transport.lastCall.data).cameraId).toBe('0');
  });
});