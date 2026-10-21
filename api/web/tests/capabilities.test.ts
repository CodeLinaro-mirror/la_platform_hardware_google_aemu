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
import { CapabilitiesClient, AccessMode } from '../src/core/capabilities';
import {
  DeepPartial,
  DeviceCapabilities,
  VmOperation,
} from '@android/emulator-services/capabilities_service';
import { FakeTransport } from './fake_transport';

const SERVICE = 'android.emulation.v2.CapabilitiesService';

describe('CapabilitiesClient', () => {
  let transport: FakeTransport;
  let client: CapabilitiesClient;

  beforeEach(() => {
    transport = new FakeTransport();
    client = new CapabilitiesClient(transport.asTransport());
  });

  function encodeCapabilities(caps: DeepPartial<DeviceCapabilities>): Uint8Array {
    return DeviceCapabilities.encode(DeviceCapabilities.fromPartial(caps)).finish();
  }

  it('decodes the snapshot returned by GetDeviceCapabilities', async () => {
    transport.respondWith(
      encodeCapabilities({
        metadata: { displayName: 'Pixel 9' },
        display: { maxSupportedDisplayCount: 4 },
      })
    );

    const caps = await client.GetDeviceCapabilities();

    expect(transport.calls).toHaveLength(1);
    expect(transport.lastCall.service).toBe(SERVICE);
    expect(transport.lastCall.method).toBe('GetDeviceCapabilities');
    expect(caps.metadata?.displayName).toBe('Pixel 9');
    expect(caps.display?.maxSupportedDisplayCount).toBe(4);
  });

  // The RPC is the un-memoized half of the pair: a caller that spells it out is
  // asking to touch the wire, every time.
  it('queries the device on every GetDeviceCapabilities call', async () => {
    transport.respondWith(encodeCapabilities({ metadata: { displayName: 'Pixel 9' } }));

    await client.GetDeviceCapabilities();
    await client.GetDeviceCapabilities();

    expect(transport.calls).toHaveLength(2);
  });

  // The snapshot is immutable for the lifetime of a connection, so repeated
  // cached reads must not cost a round trip.
  it('serves later cached reads from memory', async () => {
    transport.respondWith(encodeCapabilities({ metadata: { displayName: 'Pixel 9' } }));

    const first = await client.getCachedDeviceCapabilities();
    const second = await client.getCachedDeviceCapabilities();

    expect(transport.calls).toHaveLength(1);
    expect(second).toBe(first);
    expect(second.metadata?.displayName).toBe('Pixel 9');
  });

  it('refetches after the cache is invalidated', async () => {
    transport.respondWith(encodeCapabilities({ metadata: { displayName: 'Pixel 9' } }));
    await client.getCachedDeviceCapabilities();

    transport.respondWith(encodeCapabilities({ metadata: { displayName: 'Pixel Fold' } }));
    client.invalidateCache();
    const caps = await client.getCachedDeviceCapabilities();

    expect(transport.calls).toHaveLength(2);
    expect(caps.metadata?.displayName).toBe('Pixel Fold');
  });

  // An omitted capability block means the subsystem is unsupported, which has
  // to read as undefined rather than as an empty block: `posture` below is the
  // difference between "does not fold" and "folds, limits unknown".
  it('reads each section off one cached snapshot', async () => {
    transport.respondWith(
      encodeCapabilities({
        sensors: { accessMode: AccessMode.ACCESS_MODE_READ_WRITE },
        vm: { supportedOperations: [VmOperation.VM_OPERATION_PAUSE_RESUME] },
      })
    );

    const caps = await client.getCachedDeviceCapabilities();

    expect(caps.sensors).toEqual(
      expect.objectContaining({ accessMode: AccessMode.ACCESS_MODE_READ_WRITE })
    );
    expect(caps.vm).toEqual(
      expect.objectContaining({
        supportedOperations: [VmOperation.VM_OPERATION_PAUSE_RESUME],
      })
    );
    expect(caps.sensors?.posture).toBeUndefined();
    expect(transport.calls).toHaveLength(1);
  });
});
