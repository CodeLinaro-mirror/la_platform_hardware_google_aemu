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
import { LocationClient } from '../src/core/location';
import {
  LocationState,
  UpdateLocationStateRequest,
} from '@android/emulator-services/location_service';
import { FakeTransport } from './fake_transport';

const SERVICE = 'android.emulation.v2.LocationService';

describe('LocationClient', () => {
  let transport: FakeTransport;
  let client: LocationClient;

  beforeEach(() => {
    transport = new FakeTransport();
    client = new LocationClient(transport.asTransport());
  });

  function encodeState(state: Partial<LocationState>): Uint8Array {
    return LocationState.encode(LocationState.fromPartial(state)).finish();
  }

  it('calls GetLocationState and decodes the reply', async () => {
    transport.respondWith(encodeState({ latitude: 37.422, longitude: -122.084 }));

    const result = await client.GetLocationState();

    expect(transport.calls).toHaveLength(1);
    expect(transport.lastCall.service).toBe(SERVICE);
    expect(transport.lastCall.method).toBe('GetLocationState');
    expect(result.latitude).toBeCloseTo(37.422);
    expect(result.longitude).toBeCloseTo(-122.084);
  });

  it('sends only latitude and longitude for a bare fix', async () => {
    transport.respondWith(encodeState({ latitude: 1, longitude: 2 }));

    const result = await client.UpdateLocationState({ latitude: 1, longitude: 2 });

    expect(transport.lastCall.service).toBe(SERVICE);
    expect(transport.lastCall.method).toBe('UpdateLocationState');
    const request = UpdateLocationStateRequest.decode(transport.lastCall.data);
    expect(request.latitude).toBe(1);
    expect(request.longitude).toBe(2);
    expect(request.altitudeMeters).toBeUndefined();
    expect(request.satelliteCount).toBeUndefined();
    expect(result.latitude).toBe(1);
  });

  it('sends every supplied optional field on the wire', async () => {
    transport.respondWith(encodeState({}));

    await client.UpdateLocationState({
      latitude: 1,
      longitude: 2,
      altitudeMeters: 3,
      speedMetersPerSecond: 4,
      bearingDegrees: 5,
      accuracyMeters: 6,
      satelliteCount: 7,
    });

    const request = UpdateLocationStateRequest.decode(transport.lastCall.data);
    expect(request.latitude).toBe(1);
    expect(request.longitude).toBe(2);
    expect(request.altitudeMeters).toBe(3);
    expect(request.speedMetersPerSecond).toBe(4);
    expect(request.bearingDegrees).toBe(5);
    expect(request.accuracyMeters).toBe(6);
    expect(request.satelliteCount).toBe(7);
  });

  it('leaves absent fields undefined on the wire', async () => {
    transport.respondWith(encodeState({}));

    await client.UpdateLocationState({ latitude: 1, longitude: 2, altitudeMeters: undefined });

    const request = UpdateLocationStateRequest.decode(transport.lastCall.data);
    expect(request.latitude).toBe(1);
    expect(request.longitude).toBe(2);
    expect(request.altitudeMeters).toBeUndefined();
  });

  it('decodes each state pushed by StreamLocationState', async () => {
    const onState = vi.fn();

    const unsubscribe = client.subscribeLocationState(onState);
    transport.stream().push(encodeState({ latitude: 51.5 }));
    await settle();

    expect(transport.streamCalls[0].service).toBe(SERVICE);
    expect(transport.streamCalls[0].method).toBe('StreamLocationState');
    expect(onState).toHaveBeenCalledWith(expect.objectContaining({ latitude: 51.5 }));

    unsubscribe();
  });

  it('decodes each state yielded by the iterator form', async () => {
    transport.stream().push(encodeState({ longitude: -0.12 }));
    transport.stream().end();

    const states: LocationState[] = [];
    for await (const state of client.StreamLocationState()) {
      states.push(state);
    }

    expect(transport.streamCalls[0].method).toBe('StreamLocationState');
    expect(states).toHaveLength(1);
    expect(states[0].longitude).toBeCloseTo(-0.12);
  });

  it('aborts the underlying stream on unsubscribe', async () => {
    const unsubscribe = client.subscribeLocationState(() => {});
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
