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
import { BiometricsClient } from '../src/core/biometrics';
import { InjectFingerprintRequest } from '@android/emulator-services/system_service';
import { Empty } from '@android/emulator-services/google/protobuf/empty';
import { FakeTransport } from './fake_transport';

const SERVICE = 'android.emulation.v2.SystemService';

describe('BiometricsClient', () => {
  let transport: FakeTransport;
  let client: BiometricsClient;

  beforeEach(() => {
    transport = new FakeTransport();
    client = new BiometricsClient(transport.asTransport());
    transport.respondWith(Empty.encode({}).finish());
  });

  it('sends InjectFingerprint with the finger id and the touching flag', async () => {
    await client.InjectFingerprint({ fingerId: 1, touching: true });

    expect(transport.calls).toHaveLength(1);
    expect(transport.lastCall.service).toBe(SERVICE);
    expect(transport.lastCall.method).toBe('InjectFingerprint');

    const request = InjectFingerprintRequest.decode(transport.lastCall.data);
    expect(request.fingerId).toBe(1);
    expect(request.touching).toBe(true);
  });

  // Nothing is filled in on the client any more: an empty request reaches the
  // server as an empty request, leaving the finger id at the proto3 default and
  // `touching` — which is `optional` — absent, for the server to interpret.
  it('sends nothing the caller did not ask for', async () => {
    await client.InjectFingerprint({});

    const request = InjectFingerprintRequest.decode(transport.lastCall.data);
    expect(request.fingerId).toBe(0);
    expect(request.touching).toBeUndefined();
  });

  it('forwards a finger lift', async () => {
    await client.InjectFingerprint({ fingerId: 2, touching: false });

    const request = InjectFingerprintRequest.decode(transport.lastCall.data);
    expect(request.fingerId).toBe(2);
    expect(request.touching).toBe(false);
  });
});