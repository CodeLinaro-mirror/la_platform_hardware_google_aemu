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
import { DeviceClient } from '../src/core/client';
import { SensorsClient } from '../src/core/sensors';
import { CapabilitiesClient } from '../src/core/capabilities';

describe('DeviceClient & Subsystem Clients (TDD)', () => {
  let mockTransport: any;

  beforeEach(() => {
    mockTransport = {
      request: vi.fn(),
    };
  });

  it('SensorsClient.updateSensorsState serializes sensor updates with field mask', async () => {
    const sensors = new SensorsClient(mockTransport);

    // Mock successful return of updated state
    mockTransport.request.mockResolvedValue(new Uint8Array([]));

    await sensors.updateSensorsState({
      acceleration: { x: 0, y: 9.81, z: 0 },
    });

    expect(mockTransport.request).toHaveBeenCalledTimes(1);
    const [service, method, data] = mockTransport.request.mock.calls[0];
    expect(service).toBe('android.emulation.v2.environment.SensorsService');
    expect(method).toBe('UpdateSensorsState');
    expect(data.byteLength).toBeGreaterThan(0);
  });

  it('CapabilitiesClient.getCapabilities fetches hardware and display properties', async () => {
    const capabilities = new CapabilitiesClient(mockTransport);

    mockTransport.request.mockResolvedValue(new Uint8Array([]));

    await capabilities.getCapabilities();

    expect(mockTransport.request).toHaveBeenCalledTimes(1);
    const [service, method] = mockTransport.request.mock.calls[0];
    expect(service).toBe('android.emulation.v2.discovery.CapabilitiesService');
    expect(method).toBe('GetDeviceCapabilities');
  });

  it('DeviceClient creates unified client root with all subsystem clients wired', () => {
    const client = DeviceClient.create({
      endpoint: 'http://localhost:8554',
      authProvider: 'jwt-test-token',
    });

    expect(client).toBeDefined();
    expect(client.transport).toBeDefined();
    expect(client.rtc).toBeDefined();
    expect(client.input).toBeDefined();
    expect(client.sensors).toBeDefined();
    expect(client.capabilities).toBeDefined();
    expect(client.location).toBeDefined();
    expect(client.vm).toBeDefined();
  });
});
