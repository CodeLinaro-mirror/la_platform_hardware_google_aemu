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
import { DisplayClient, Rotation } from '../src/core/display';
import {
  ListDisplaysResponse,
  DisplayState,
  CreateDisplayRequest,
  DeleteDisplayRequest,
} from '@android/emulator-services/environment/display_service';

describe('DisplayClient (TDD)', () => {
  let mockTransport: any;

  beforeEach(() => {
    mockTransport = {
      request: vi.fn(),
    };
  });

  it('listDisplays decodes display configuration array', async () => {
    const client = new DisplayClient(mockTransport);

    const mockDisplays: DisplayState[] = [
      {
        displayId: 0,
        primary: true,
        width: 1080,
        height: 2400,
        densityDpi: 420,
        rotation: Rotation.ROTATION_DEG0,
        brightnessPercent: 100,
        autoBrightnessEnabled: true,
        flags: [],
      },
    ];

    mockTransport.request.mockResolvedValue(
      ListDisplaysResponse.encode({
        displays: mockDisplays,
      }).finish()
    );

    const result = await client.listDisplays();
    expect(mockTransport.request).toHaveBeenCalledTimes(1);
    const [service, method] = mockTransport.request.mock.calls[0];
    expect(service).toBe('android.emulation.v2.environment.DisplayService');
    expect(method).toBe('ListDisplays');
    expect(result).toHaveLength(1);
    expect(result[0].width).toBe(1080);
    expect(result[0].primary).toBe(true);
  });

  it('createDisplay encodes secondary display creation request', async () => {
    const client = new DisplayClient(mockTransport);

    const createdDisplay: DisplayState = {
      displayId: 1,
      primary: false,
      width: 720,
      height: 1280,
      densityDpi: 320,
      rotation: Rotation.ROTATION_DEG0,
      brightnessPercent: 100,
      autoBrightnessEnabled: false,
      flags: [],
    };
    mockTransport.request.mockResolvedValue(DisplayState.encode(createdDisplay).finish());

    const result = await client.createDisplay(720, 1280, 320);
    expect(mockTransport.request).toHaveBeenCalledTimes(1);
    const [service, method, data] = mockTransport.request.mock.calls[0];
    expect(service).toBe('android.emulation.v2.environment.DisplayService');
    expect(method).toBe('CreateDisplay');

    const decoded = CreateDisplayRequest.decode(data);
    expect(decoded.displayState?.width).toBe(720);
    expect(decoded.displayState?.height).toBe(1280);
    expect(result.displayId).toBe(1);
  });

  it('deleteDisplay sends DeleteDisplayRequest with displayId', async () => {
    const client = new DisplayClient(mockTransport);
    mockTransport.request.mockResolvedValue(new Uint8Array([]));

    await client.deleteDisplay(1);
    expect(mockTransport.request).toHaveBeenCalledTimes(1);
    const [service, method, data] = mockTransport.request.mock.calls[0];
    expect(service).toBe('android.emulation.v2.environment.DisplayService');
    expect(method).toBe('DeleteDisplay');

    const decoded = DeleteDisplayRequest.decode(data);
    expect(decoded.displayId).toBe(1);
  });

  it('streamDisplays subscribes to live updates via transport.stream', () => {
    mockTransport.stream = vi.fn();
    const client = new DisplayClient(mockTransport);

    const onDisplays = vi.fn();
    client.streamDisplays(onDisplays);

    expect(mockTransport.stream).toHaveBeenCalledTimes(1);
    const [service, method, , onMsg] = mockTransport.stream.mock.calls[0];
    expect(service).toBe('android.emulation.v2.environment.DisplayService');
    expect(method).toBe('StreamDisplayState');

    const sampleDisplay: DisplayState = {
      displayId: 0,
      primary: true,
      width: 1080,
      height: 2400,
      densityDpi: 440,
      rotation: Rotation.ROTATION_DEG0,
      brightnessPercent: 100,
      autoBrightnessEnabled: false,
      flags: [],
    };
    onMsg(ListDisplaysResponse.encode({ displays: [sampleDisplay] }).finish());
    expect(onDisplays).toHaveBeenCalledWith([expect.objectContaining({ width: 1080 })]);
  });
});