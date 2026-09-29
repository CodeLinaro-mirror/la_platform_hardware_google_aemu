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
  RebootDeviceRequest,
  SystemState,
} from '@android/emulator-services/system_service';
import { Empty } from '@android/emulator-services/google/protobuf/empty';
import { FakeTransport } from './fake_transport';

const SERVICE = 'android.emulation.v2.SystemService';

describe('SystemClient', () => {
  let transport: FakeTransport;
  let client: SystemClient;

  beforeEach(() => {
    transport = new FakeTransport();
    client = new SystemClient(transport.asTransport());
  });

  function encodeState(state: Partial<SystemState>): Uint8Array {
    return SystemState.encode(SystemState.fromPartial(state)).finish();
  }

  it('calls GetSystemState and decodes the lifecycle and connection state', async () => {
    transport.respondWith(
      encodeState({
        lifecycleState: SystemState_Lifecycle.BOOT_COMPLETED,
        connectionState: SystemState_Connection.CONNECTED,
        lockState: SystemState_Lock.UNLOCKED,
        uptimeMs: 120000,
        bootCompletedDurationMs: 15400,
        heartbeatCounter: 99,
      })
    );

    const result = await client.GetSystemState();

    expect(transport.calls).toHaveLength(1);
    expect(transport.lastCall.service).toBe(SERVICE);
    expect(transport.lastCall.method).toBe('GetSystemState');
    expect(result.lifecycleState).toBe(SystemState_Lifecycle.BOOT_COMPLETED);
    expect(result.connectionState).toBe(SystemState_Connection.CONNECTED);
    expect(result.bootCompletedDurationMs).toBe(15400);
  });

  it('calls RebootDevice with an empty request', async () => {
    transport.respondWith(Empty.encode({}).finish());

    await client.RebootDevice();

    expect(transport.calls).toHaveLength(1);
    expect(transport.lastCall.service).toBe(SERVICE);
    expect(transport.lastCall.method).toBe('RebootDevice');
    expect(RebootDeviceRequest.decode(transport.lastCall.data)).toBeDefined();
  });

  it('decodes each state pushed by StreamSystemState', async () => {
    const onState = vi.fn();

    const unsubscribe = client.subscribeSystemState(onState);
    transport.stream().push(
      encodeState({
        lifecycleState: SystemState_Lifecycle.BOOT_COMPLETED,
        connectionState: SystemState_Connection.CONNECTED,
        lockState: SystemState_Lock.UNLOCKED,
        uptimeMs: 5000,
        bootCompletedDurationMs: 4200,
        heartbeatCounter: 1,
      })
    );
    await settle();

    expect(transport.streamCalls[0].service).toBe(SERVICE);
    expect(transport.streamCalls[0].method).toBe('StreamSystemState');
    expect(onState).toHaveBeenCalledWith(
      expect.objectContaining({ lifecycleState: SystemState_Lifecycle.BOOT_COMPLETED })
    );

    unsubscribe();
  });

  it('aborts the underlying stream on unsubscribe', async () => {
    const unsubscribe = client.subscribeSystemState(() => {});
    await settle();

    expect(transport.streamCalls[0].signal?.aborted).toBe(false);

    unsubscribe();

    expect(transport.streamCalls[0].signal?.aborted).toBe(true);
  });

  it('yields decoded states from the iterator form', async () => {
    const controller = new AbortController();
    const states: SystemState[] = [];

    const pump = (async () => {
      for await (const state of client.StreamSystemState({}, controller.signal)) {
        states.push(state);
        controller.abort();
      }
    })();

    transport.stream().push(encodeState({ heartbeatCounter: 7 }));
    await pump;

    expect(transport.streamCalls[0].method).toBe('StreamSystemState');
    expect(states).toHaveLength(1);
    expect(states[0].heartbeatCounter).toBe(7);
  });
});

/** Lets the subscription's background task drain the queued messages. */
async function settle(): Promise<void> {
  for (let i = 0; i < 5; i++) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}