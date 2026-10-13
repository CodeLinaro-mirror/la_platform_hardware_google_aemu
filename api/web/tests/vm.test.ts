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
import { VmClient, VmState, VmState_State } from '../src/core/vm';
import { StopVmRequest } from '@android/emulator-services/hypervisor_service';
import { FakeTransport } from './fake_transport';

const SERVICE = 'android.emulation.v2.HypervisorService';

describe('VmClient', () => {
  let transport: FakeTransport;
  let client: VmClient;

  beforeEach(() => {
    transport = new FakeTransport();
    client = new VmClient(transport.asTransport());
  });

  function encodeState(state: VmState_State): Uint8Array {
    return VmState.encode({
      state,
      allocatedCpuCoreCount: 4,
      allocatedRamBytes: 2048,
    }).finish();
  }

  function respondWith(state: VmState_State) {
    transport.respondWith(encodeState(state));
  }

  it('decodes the VmState returned by GetVmState', async () => {
    respondWith(VmState_State.RUNNING);

    const state = await client.GetVmState();

    expect(transport.lastCall.service).toBe(SERVICE);
    expect(transport.lastCall.method).toBe('GetVmState');
    expect(state.state).toBe(VmState_State.RUNNING);
    expect(state.allocatedCpuCoreCount).toBe(4);
  });

  // Each transition is a distinct RPC: there is no SetVmState method on the
  // service, so a target_state field cannot be used to select between them.
  const transitions: Array<[string, () => Promise<VmState>, VmState_State]> = [
    ['PauseVm', () => client.PauseVm(), VmState_State.PAUSED],
    ['ResumeVm', () => client.ResumeVm(), VmState_State.RUNNING],
    ['ResetVm', () => client.ResetVm(), VmState_State.RESETTING],
    ['HardResetVm', () => client.HardResetVm(), VmState_State.RESETTING],
    ['StopVm', () => client.StopVm(), VmState_State.SHUTTING_DOWN],
  ];

  for (const [rpc, invoke, resulting] of transitions) {
    it(`calls ${rpc} and decodes the resulting state`, async () => {
      respondWith(resulting);

      const state = await invoke();

      expect(transport.calls).toHaveLength(1);
      expect(transport.lastCall.service).toBe(SERVICE);
      expect(transport.lastCall.method).toBe(rpc);
      expect(state.state).toBe(resulting);
    });
  }

  it('defaults saveQuickbootSnapshot to false on StopVm unless requested', async () => {
    respondWith(VmState_State.SHUTTING_DOWN);

    await client.StopVm();

    expect(StopVmRequest.decode(transport.lastCall.data).saveQuickbootSnapshot).toBe(false);
  });

  it('forwards the quickboot snapshot request to StopVm', async () => {
    respondWith(VmState_State.SAVING_SNAPSHOT);

    await client.StopVm({ saveQuickbootSnapshot: true });

    const request = StopVmRequest.decode(transport.lastCall.data);
    expect(request.saveQuickbootSnapshot).toBe(true);
  });

  it('decodes each VmState pushed by StreamVmState', async () => {
    const states: VmState[] = [];

    const unsubscribe = client.subscribeVmState((state) => states.push(state));
    transport.stream().push(encodeState(VmState_State.PAUSED));
    await settle();

    expect(transport.streamCalls[0].service).toBe(SERVICE);
    expect(transport.streamCalls[0].method).toBe('StreamVmState');
    expect(states).toHaveLength(1);
    expect(states[0].state).toBe(VmState_State.PAUSED);

    unsubscribe();
  });

  // The subscription must survive an idle stream: a consumer that unsubscribes
  // while no message is in flight has to abort, because the iterator cannot be
  // woken by return() while it is parked awaiting the next message.
  it('aborts the underlying stream on unsubscribe', async () => {
    const unsubscribe = client.subscribeVmState(() => {});
    await settle();

    expect(transport.streamCalls[0].signal?.aborted).toBe(false);

    unsubscribe();

    expect(transport.streamCalls[0].signal?.aborted).toBe(true);
  });

  it('stops delivering messages after unsubscribe', async () => {
    const states: VmState[] = [];

    const unsubscribe = client.subscribeVmState((state) => states.push(state));
    await settle();
    unsubscribe();
    transport.stream().push(encodeState(VmState_State.RUNNING));
    await settle();

    expect(states).toHaveLength(0);
  });
});

/** Lets the subscription's background task drain the queued messages. */
async function settle(): Promise<void> {
  for (let i = 0; i < 5; i++) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}
