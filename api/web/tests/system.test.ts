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
import {
  SystemClient,
  SystemState_Lifecycle,
  SystemState_Connection,
  SystemState_Lock,
} from '../src/core/system';
import {
  SystemState,
  RebootDeviceRequest,
} from '@android/emulator-services/system/system_service';

describe('SystemClient (TDD)', () => {
  let mockTransport: any;

  beforeEach(() => {
    mockTransport = {
      request: vi.fn(),
      serverStream: vi.fn(),
    };
  });

  it('getSystemState decodes OS lifecycle and connection state', async () => {
    const client = new SystemClient(mockTransport);

    const mockState: SystemState = {
      lifecycleState: SystemState_Lifecycle.BOOT_COMPLETED,
      connectionState: SystemState_Connection.CONNECTED,
      lockState: SystemState_Lock.UNLOCKED,
      uptimeMs: 120000,
      bootCompletedDurationMs: 15400,
      heartbeatCounter: 99,
    };
    mockTransport.request.mockResolvedValue(SystemState.encode(mockState).finish());

    const result = await client.getSystemState();
    expect(mockTransport.request).toHaveBeenCalledTimes(1);
    const [service, method] = mockTransport.request.mock.calls[0];
    expect(service).toBe('android.emulation.v2.system.SystemService');
    expect(method).toBe('GetSystemState');
    expect(result.lifecycleState).toBe(SystemState_Lifecycle.BOOT_COMPLETED);
    expect(result.bootCompletedDurationMs).toBe(15400);
  });

  it('rebootDevice sends RebootDeviceRequest', async () => {
    const client = new SystemClient(mockTransport);
    mockTransport.request.mockResolvedValue(new Uint8Array([]));

    await client.rebootDevice();
    expect(mockTransport.request).toHaveBeenCalledTimes(1);
    const [service, method, data] = mockTransport.request.mock.calls[0];
    expect(service).toBe('android.emulation.v2.system.SystemService');
    expect(method).toBe('RebootDevice');

    const decoded = RebootDeviceRequest.decode(data);
    expect(decoded).toBeDefined();
  });

  it('subscribeSystemState subscribes to live updates via transport.stream', () => {
    mockTransport.stream = vi.fn();
    const client = new SystemClient(mockTransport);

    const onState = vi.fn();
    client.subscribeSystemState(onState);

    expect(mockTransport.stream).toHaveBeenCalledTimes(1);
    const [service, method, , onMsg] = mockTransport.stream.mock.calls[0];
    expect(service).toBe('android.emulation.v2.system.SystemService');
    expect(method).toBe('StreamSystemState');

    const sampleState: SystemState = {
      lifecycleState: SystemState_Lifecycle.BOOT_COMPLETED,
      connectionState: SystemState_Connection.CONNECTED,
      lockState: SystemState_Lock.UNLOCKED,
      uptimeMs: 5000,
      bootCompletedDurationMs: 4200,
      heartbeatCounter: 1,
    };
    onMsg(SystemState.encode(sampleState).finish());
    expect(onState).toHaveBeenCalledWith(expect.objectContaining({
      lifecycleState: SystemState_Lifecycle.BOOT_COMPLETED,
    }));
  });
});