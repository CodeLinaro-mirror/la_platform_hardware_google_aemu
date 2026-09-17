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
import { BatteryClient, BatteryState_State, ChargerSource } from '../src/core/battery';
import { BatteryState, UpdateBatteryStateRequest } from '@android/emulator-services/environment/battery_service';

describe('BatteryClient (TDD)', () => {
  let mockTransport: any;

  beforeEach(() => {
    mockTransport = {
      request: vi.fn(),
    };
  });

  it('calls GetBatteryState RPC correctly', async () => {
    const batteryClient = new BatteryClient(mockTransport);

    const mockResponse: BatteryState = {
      levelPercent: 85,
      state: BatteryState_State.DISCHARGING,
      charger: ChargerSource.CHARGER_SOURCE_NONE,
      health: 1, // GOOD
    };
    mockTransport.request.mockResolvedValue(BatteryState.encode(mockResponse).finish());

    const result = await batteryClient.getBatteryState();
    expect(mockTransport.request).toHaveBeenCalledTimes(1);
    const [service, method] = mockTransport.request.mock.calls[0];
    expect(service).toBe('android.emulation.v2.environment.BatteryService');
    expect(method).toBe('GetBatteryState');
    expect(result.levelPercent).toBe(85);
    expect(result.state).toBe(BatteryState_State.DISCHARGING);
  });

  it('updateBatteryState sets level and field mask', async () => {
    const batteryClient = new BatteryClient(mockTransport);

    const mockResponse: BatteryState = {
      levelPercent: 42,
      state: BatteryState_State.CHARGING,
      charger: ChargerSource.CHARGER_SOURCE_AC,
      health: 1,
    };
    mockTransport.request.mockResolvedValue(BatteryState.encode(mockResponse).finish());

    const result = await batteryClient.setBatteryLevel(42);
    expect(mockTransport.request).toHaveBeenCalledTimes(1);
    const [service, method, data] = mockTransport.request.mock.calls[0];
    expect(service).toBe('android.emulation.v2.environment.BatteryService');
    expect(method).toBe('UpdateBatteryState');

    const decodedReq = UpdateBatteryStateRequest.decode(data);
    expect(decodedReq.batteryState?.levelPercent).toBe(42);
    expect(decodedReq.updateMask).toContain('level_percent');
    expect(result.levelPercent).toBe(42);
  });

  it('setChargingState updates charging state and charger source', async () => {
    const batteryClient = new BatteryClient(mockTransport);

    mockTransport.request.mockResolvedValue(
      BatteryState.encode({
        levelPercent: 100,
        state: BatteryState_State.FULL,
        charger: ChargerSource.CHARGER_SOURCE_WIRELESS,
        health: 1,
      }).finish()
    );

    const result = await batteryClient.setChargingState(
      BatteryState_State.FULL,
      ChargerSource.CHARGER_SOURCE_WIRELESS
    );
    expect(mockTransport.request).toHaveBeenCalledTimes(1);
    const [, , data] = mockTransport.request.mock.calls[0];
    const decodedReq = UpdateBatteryStateRequest.decode(data);
    expect(decodedReq.updateMask).toContain('state');
    expect(result.state).toBe(BatteryState_State.FULL);
  });

  it('streamBatteryState subscribes to live updates via transport.stream', () => {
    mockTransport.stream = vi.fn();
    const batteryClient = new BatteryClient(mockTransport);

    const onState = vi.fn();
    batteryClient.streamBatteryState(onState);

    expect(mockTransport.stream).toHaveBeenCalledTimes(1);
    const [service, method, , onMsg] = mockTransport.stream.mock.calls[0];
    expect(service).toBe('android.emulation.v2.environment.BatteryService');
    expect(method).toBe('StreamBatteryState');

    const sampleState: BatteryState = {
      levelPercent: 77,
      state: BatteryState_State.CHARGING,
      charger: ChargerSource.CHARGER_SOURCE_AC,
      health: 1,
    };
    onMsg(BatteryState.encode(sampleState).finish());
    expect(onState).toHaveBeenCalledWith(expect.objectContaining({ levelPercent: 77 }));
  });
});