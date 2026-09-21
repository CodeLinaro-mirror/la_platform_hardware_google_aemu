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
import { SensorsClient, SensorsState, TransitionMode } from '../src/core/sensors';
import { DeepPartial, UpdateSensorsStateRequest } from '@android/emulator-services/sensors_service';
import { FakeTransport } from './fake_transport';

const SERVICE = 'android.emulation.v2.SensorsService';

describe('SensorsClient', () => {
  let transport: FakeTransport;
  let client: SensorsClient;

  beforeEach(() => {
    transport = new FakeTransport();
    client = new SensorsClient(transport.asTransport());
  });

  function encodeState(state: DeepPartial<SensorsState>): Uint8Array {
    return SensorsState.encode(SensorsState.fromPartial(state)).finish();
  }

  it('decodes the state returned by GetSensorsState', async () => {
    transport.respondWith(
      encodeState({
        pose: { rotationDegrees: { x: 0, y: 0, z: 90 } },
        environment: { lightLux: 320 },
        imu: { accelerometer: { x: 0, y: 9.81, z: 0 } },
      })
    );

    const result = await client.GetSensorsState();

    expect(transport.calls).toHaveLength(1);
    expect(transport.lastCall.service).toBe(SERVICE);
    expect(transport.lastCall.method).toBe('GetSensorsState');
    expect(result.pose?.rotationDegrees?.z).toBe(90);
    expect(result.environment?.lightLux).toBe(320);
    expect(result.imu?.accelerometer?.y).toBeCloseTo(9.81);
  });

  it('sends pose and transition mode on the wire', async () => {
    transport.respondWith(encodeState({}));

    await client.UpdateSensorsState({
      pose: {
        rotationDegrees: { x: 0, y: 0, z: 90 },
        ambientMotion: 0.25,
      },
      transition: TransitionMode.TRANSITION_MODE_SMOOTH,
    });

    expect(transport.lastCall.method).toBe('UpdateSensorsState');
    const request = UpdateSensorsStateRequest.decode(transport.lastCall.data);
    expect(request.pose?.rotationDegrees?.z).toBe(90);
    expect(request.pose?.ambientMotion).toBeCloseTo(0.25);
    expect(request.transition).toBe(TransitionMode.TRANSITION_MODE_SMOOTH);
    expect(request.environment).toBeUndefined();
  });

  it('sends environment and rawOverrides fields on the wire', async () => {
    transport.respondWith(encodeState({}));

    await client.UpdateSensorsState({
      environment: {
        ambientTemperatureCelsius: 21.5,
        proximityCentimeters: 3,
        lightLux: 500,
        pressureHpa: 1013.25,
        relativeHumidityPercent: 40,
        heartRateBpm: 72,
      },
      rawOverrides: {
        accelerometer: { x: 0, y: 0, z: 0 },
        gyroscope: { x: 1, y: 0, z: 0 },
        magnetometer: { x: 0, y: 0, z: 1 },
      },
    });

    const request = UpdateSensorsStateRequest.decode(transport.lastCall.data);
    expect(request.environment?.ambientTemperatureCelsius).toBeCloseTo(21.5);
    expect(request.environment?.proximityCentimeters).toBe(3);
    expect(request.environment?.lightLux).toBe(500);
    expect(request.environment?.pressureHpa).toBeCloseTo(1013.25);
    expect(request.environment?.relativeHumidityPercent).toBe(40);
    expect(request.environment?.heartRateBpm).toBe(72);
    expect(request.rawOverrides?.accelerometer).toEqual({ x: 0, y: 0, z: 0 });
    expect(request.rawOverrides?.gyroscope?.x).toBe(1);
    expect(request.rawOverrides?.magnetometer?.z).toBe(1);
  });

  it('preserves zero-valued readings via proto3 optional wire presence', async () => {
    transport.respondWith(encodeState({}));

    await client.UpdateSensorsState({
      rawOverrides: { accelerometer: { x: 0, y: 0, z: 0 } },
      environment: { lightLux: 0 },
    });

    const request = UpdateSensorsStateRequest.decode(transport.lastCall.data);
    expect(request.rawOverrides?.accelerometer).toEqual({ x: 0, y: 0, z: 0 });
    expect(request.environment?.lightLux).toBe(0);
    expect(request.environment?.heartRateBpm).toBeUndefined();
  });

  it('supports clearRawOverrides flag', async () => {
    transport.respondWith(encodeState({}));

    await client.UpdateSensorsState({ clearRawOverrides: true });

    const request = UpdateSensorsStateRequest.decode(transport.lastCall.data);
    expect(request.clearRawOverrides).toBe(true);
  });

  it('decodes the state returned by UpdateSensorsState', async () => {
    transport.respondWith(encodeState({ environment: { lightLux: 12 } }));

    const result = await client.UpdateSensorsState({ environment: { lightLux: 12 } });

    expect(result.environment?.lightLux).toBe(12);
  });

  it('decodes each state pushed by StreamSensorsState', async () => {
    const onState = vi.fn();

    const unsubscribe = client.subscribeSensorsState(onState);
    transport.stream().push(encodeState({ environment: { lightLux: 77 } }));
    await settle();

    expect(transport.streamCalls[0].service).toBe(SERVICE);
    expect(transport.streamCalls[0].method).toBe('StreamSensorsState');
    expect(onState).toHaveBeenCalledWith(
      expect.objectContaining({ environment: expect.objectContaining({ lightLux: 77 }) })
    );

    unsubscribe();
  });

  it('aborts the underlying stream on unsubscribe', async () => {
    const unsubscribe = client.subscribeSensorsState(() => {});
    await settle();

    expect(transport.streamCalls[0].signal?.aborted).toBe(false);

    unsubscribe();

    expect(transport.streamCalls[0].signal?.aborted).toBe(true);
  });

  it('yields states from the iterator form', async () => {
    const stream = transport.stream();
    const controller = new AbortController();
    const states: SensorsState[] = [];

    const consumed = (async () => {
      for await (const state of client.StreamSensorsState({}, controller.signal)) {
        states.push(state);
        controller.abort();
      }
    })();

    stream.push(encodeState({ environment: { lightLux: 5 } }));
    await consumed;

    expect(transport.streamCalls[0].method).toBe('StreamSensorsState');
    expect(states).toHaveLength(1);
    expect(states[0].environment?.lightLux).toBe(5);
  });
});

/** Lets the subscription's background task drain the queued messages. */
async function settle(): Promise<void> {
  for (let i = 0; i < 5; i++) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}
