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
import {
  BatteryState,
  UpdateBatteryStateRequest,
} from '@android/emulator-services/battery_service';
import { FakeTransport } from './fake_transport';

const SERVICE = 'android.emulation.v2.BatteryService';

describe('BatteryClient', () => {
  let transport: FakeTransport;
  let client: BatteryClient;

  beforeEach(() => {
    transport = new FakeTransport();
    client = new BatteryClient(transport.asTransport());
  });

  function encodeState(state: Partial<BatteryState>): Uint8Array {
    return BatteryState.encode(BatteryState.fromPartial(state)).finish();
  }

  it('calls GetBatteryState and decodes the reply', async () => {
    transport.respondWith(
      encodeState({ levelPercent: 85, state: BatteryState_State.DISCHARGING })
    );

    const result = await client.GetBatteryState();

    expect(transport.calls).toHaveLength(1);
    expect(transport.lastCall.service).toBe(SERVICE);
    expect(transport.lastCall.method).toBe('GetBatteryState');
    expect(result.levelPercent).toBe(85);
    expect(result.state).toBe(BatteryState_State.DISCHARGING);
  });

  it('sends only the updated level field', async () => {
    transport.respondWith(encodeState({ levelPercent: 42 }));

    const result = await client.UpdateBatteryState({ levelPercent: 42 });

    expect(transport.lastCall.method).toBe('UpdateBatteryState');
    const request = UpdateBatteryStateRequest.decode(transport.lastCall.data);
    expect(request.levelPercent).toBe(42);
    expect(request.state).toBeUndefined();
    expect(request.charger).toBeUndefined();
    expect(request.health).toBeUndefined();
    expect(result.levelPercent).toBe(42);
  });

  it('sends only the fields a charging-state update touches', async () => {
    transport.respondWith(encodeState({ state: BatteryState_State.FULL }));

    const result = await client.UpdateBatteryState({
      state: BatteryState_State.FULL,
      charger: ChargerSource.CHARGER_SOURCE_WIRELESS,
    });

    const request = UpdateBatteryStateRequest.decode(transport.lastCall.data);
    expect(request.state).toBe(BatteryState_State.FULL);
    expect(request.charger).toBe(ChargerSource.CHARGER_SOURCE_WIRELESS);
    expect(request.levelPercent).toBeUndefined();
    expect(request.health).toBeUndefined();
    expect(result.state).toBe(BatteryState_State.FULL);
  });

  it('preserves explicit zero values via proto3 optional wire presence', async () => {
    transport.respondWith(encodeState({}));

    await client.UpdateBatteryState({ levelPercent: 0 });

    const request = UpdateBatteryStateRequest.decode(transport.lastCall.data);
    expect(request.levelPercent).toBe(0);
    expect(request.state).toBeUndefined();
    expect(request.charger).toBeUndefined();
    expect(request.health).toBeUndefined();
  });

  it('leaves unsupplied fields undefined on the wire', async () => {
    transport.respondWith(encodeState({}));

    await client.UpdateBatteryState({ health: undefined, levelPercent: 10 });

    const request = UpdateBatteryStateRequest.decode(transport.lastCall.data);
    expect(request.levelPercent).toBe(10);
    expect(request.health).toBeUndefined();
  });

  it('decodes each state pushed by StreamBatteryState', async () => {
    const onState = vi.fn();

    const unsubscribe = client.subscribeBatteryState(onState);
    transport.stream().push(encodeState({ levelPercent: 77 }));
    await settle();

    expect(transport.streamCalls[0].service).toBe(SERVICE);
    expect(transport.streamCalls[0].method).toBe('StreamBatteryState');
    expect(onState).toHaveBeenCalledWith(expect.objectContaining({ levelPercent: 77 }));

    unsubscribe();
  });

  it('aborts the underlying stream on unsubscribe', async () => {
    const unsubscribe = client.subscribeBatteryState(() => {});
    await settle();

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