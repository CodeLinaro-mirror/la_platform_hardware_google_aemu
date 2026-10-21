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
  SensorsClient,
  SensorsState,
  TransitionMode,
  TRANSITION_MODE_IMMEDIATE,
} from '../src/core/sensors';
import { SensorsChannel } from '../src/core/sensors_channel';
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

class MockRTCDataChannel {
  public label: string;
  public readyState: RTCDataChannelState = 'connecting';
  public bufferedAmount = 0;
  public binaryType = 'arraybuffer';
  public onopen: ((ev: any) => any) | null = null;
  public onbufferedamountlow: ((ev: any) => any) | null = null;
  public onclose: ((ev: any) => any) | null = null;
  public onerror: ((ev: any) => any) | null = null;
  public onmessage: ((ev: any) => any) | null = null;

  public sentData: Uint8Array[] = [];

  constructor(label = 'sensors_v2') {
    this.label = label;
  }

  public send(data: string | ArrayBuffer | ArrayBufferView | Blob): void {
    if (this.readyState !== 'open') {
      throw new Error(`DataChannel is not in 'open' state (current: ${this.readyState})`);
    }
    if (data instanceof Uint8Array) {
      this.sentData.push(new Uint8Array(data));
    } else if (data instanceof ArrayBuffer) {
      this.sentData.push(new Uint8Array(data));
    }
  }

  public simulateOpen() {
    this.readyState = 'open';
    if (this.onopen) {
      this.onopen({ type: 'open' });
    }
  }

  public simulateBufferedAmountLow() {
    if (this.onbufferedamountlow) {
      this.onbufferedamountlow({ type: 'bufferedamountlow' });
    }
  }
}

describe('SensorsChannel', () => {
  let mockChannel: MockRTCDataChannel;
  let sensorsChannel: SensorsChannel;

  beforeEach(() => {
    mockChannel = new MockRTCDataChannel('sensors_v2');
    sensorsChannel = new SensorsChannel();
  });

  it('attaches to RTCDataChannel and sets binaryType to arraybuffer', () => {
    expect(sensorsChannel.isReady).toBe(false);
    sensorsChannel.attachDataChannel(mockChannel as unknown as RTCDataChannel);
    expect(mockChannel.binaryType).toBe('arraybuffer');
    expect(sensorsChannel.isReady).toBe(false);

    mockChannel.simulateOpen();
    expect(sensorsChannel.isReady).toBe(true);
  });

  it('cleans up event listeners on previous channel when re-attaching', () => {
    const channel1 = new MockRTCDataChannel('sensors_v2');
    const channel2 = new MockRTCDataChannel('sensors_v2');

    sensorsChannel.attachDataChannel(channel1 as unknown as RTCDataChannel);
    expect(channel1.onopen).not.toBeNull();
    expect(channel1.onbufferedamountlow).not.toBeNull();

    // Re-attach new channel (e.g. on ICE restart or reconnection)
    sensorsChannel.attachDataChannel(channel2 as unknown as RTCDataChannel);
    expect(channel1.onopen).toBeNull();
    expect(channel1.onbufferedamountlow).toBeNull();
    expect(channel2.onopen).not.toBeNull();
    expect(channel2.onbufferedamountlow).not.toBeNull();
  });

  it('detach removes event listeners and clears channel', () => {
    mockChannel.simulateOpen();
    sensorsChannel.attachDataChannel(mockChannel as unknown as RTCDataChannel);
    expect(sensorsChannel.isReady).toBe(true);

    sensorsChannel.detach();
    expect(mockChannel.onopen).toBeNull();
    expect(mockChannel.onbufferedamountlow).toBeNull();
    expect(sensorsChannel.isReady).toBe(false);
  });

  it('sends UpdateSensorsStateRequest over open channel', () => {
    mockChannel.simulateOpen();
    sensorsChannel.attachDataChannel(mockChannel as unknown as RTCDataChannel);

    const sent = sensorsChannel.sendUpdate({
      pose: { rotationDegrees: { x: 10, y: 20, z: 30 } },
      transition: TransitionMode.TRANSITION_MODE_STEP,
    });

    expect(sent).toBe(true);
    expect(mockChannel.sentData).toHaveLength(1);
    const decoded = sensorsChannel.decodeUpdate(mockChannel.sentData[0]);
    expect(decoded.pose?.rotationDegrees?.x).toBe(10);
    expect(decoded.pose?.rotationDegrees?.y).toBe(20);
    expect(decoded.pose?.rotationDegrees?.z).toBe(30);
    expect(decoded.transition).toBe(TransitionMode.TRANSITION_MODE_STEP);
  });

  it('queues updates when connecting and flushes on open', () => {
    sensorsChannel.attachDataChannel(mockChannel as unknown as RTCDataChannel);

    const sent = sensorsChannel.sendUpdate({
      environment: { lightLux: 450 },
    });

    expect(sent).toBe(false);
    expect(mockChannel.sentData).toHaveLength(0);

    mockChannel.simulateOpen();
    expect(mockChannel.sentData).toHaveLength(1);
    const decoded = sensorsChannel.decodeUpdate(mockChannel.sentData[0]);
    expect(decoded.environment?.lightLux).toBe(450);
  });

  it('returns false and queues when congested, then flushes on bufferedamountlow', () => {
    mockChannel.simulateOpen();
    mockChannel.bufferedAmount = 70 * 1024; // > 64KB maxBufferedAmount
    sensorsChannel.attachDataChannel(mockChannel as unknown as RTCDataChannel);

    const sent = sensorsChannel.sendUpdate({
      environment: { pressureHpa: 1013 },
    });

    expect(sent).toBe(false);
    expect(mockChannel.sentData).toHaveLength(0);

    mockChannel.bufferedAmount = 0;
    mockChannel.simulateBufferedAmountLow();
    expect(mockChannel.sentData).toHaveLength(1);
    const decoded = sensorsChannel.decodeUpdate(mockChannel.sentData[0]);
    expect(decoded.environment?.pressureHpa).toBe(1013);
  });
});

describe('SensorsClient high-frequency streaming & DoS protection', () => {
  let transport: FakeTransport;
  let mockChannel: MockRTCDataChannel;
  let sensorsChannel: SensorsChannel;
  let clientWithChannel: SensorsClient;
  let clientWithoutChannel: SensorsClient;

  beforeEach(() => {
    transport = new FakeTransport();
    mockChannel = new MockRTCDataChannel('sensors_v2');
    sensorsChannel = new SensorsChannel();
    clientWithChannel = new SensorsClient(transport.asTransport(), sensorsChannel);
    clientWithoutChannel = new SensorsClient(transport.asTransport());
  });

  it('sendSensorUpdate sends over open DataChannel without invoking unary gRPC', () => {
    mockChannel.simulateOpen();
    sensorsChannel.attachDataChannel(mockChannel as unknown as RTCDataChannel);

    const sent = clientWithChannel.sendSensorUpdate({
      pose: { rotationDegrees: { x: 1, y: 2, z: 3 } },
      transition: TransitionMode.TRANSITION_MODE_STEP,
    });

    expect(sent).toBe(true);
    expect(mockChannel.sentData).toHaveLength(1);
    expect(transport.calls).toHaveLength(0); // NO unary gRPC request sent!
  });

  it('sendSensorUpdate drops update and NEVER calls unary gRPC when channel is not configured', () => {
    const sent = clientWithoutChannel.sendSensorUpdate({
      pose: { rotationDegrees: { x: 1, y: 2, z: 3 } },
    });

    expect(sent).toBe(false);
    expect(transport.calls).toHaveLength(0); // Protects against DoS / congestion collapse
  });

  it('sendSensorUpdate drops update and NEVER calls unary gRPC when channel is congested', () => {
    mockChannel.simulateOpen();
    mockChannel.bufferedAmount = 70 * 1024;
    sensorsChannel.attachDataChannel(mockChannel as unknown as RTCDataChannel);

    const sent = clientWithChannel.sendSensorUpdate({
      pose: { rotationDegrees: { x: 1, y: 2, z: 3 } },
    });

    expect(sent).toBe(false);
    expect(transport.calls).toHaveLength(0); // Protects against DoS / congestion collapse
  });

  it('startBrowserSensorStream streams orientation with TRANSITION_MODE_IMMEDIATE / STEP', () => {
    mockChannel.simulateOpen();
    sensorsChannel.attachDataChannel(mockChannel as unknown as RTCDataChannel);

    const listeners: Record<string, ((ev: any) => void)[]> = {};
    const mockWindow = {
      addEventListener(type: string, listener: (ev: any) => void) {
        listeners[type] = listeners[type] || [];
        listeners[type].push(listener);
      },
      removeEventListener(type: string, listener: (ev: any) => void) {
        if (listeners[type]) {
          listeners[type] = listeners[type].filter((l) => l !== listener);
        }
      },
    };

    const prevWindow = (globalThis as any).window;
    try {
      (globalThis as any).window = mockWindow;

      expect(clientWithChannel.isBrowserStreaming).toBe(false);
      clientWithChannel.startBrowserSensorStream();
      expect(clientWithChannel.isBrowserStreaming).toBe(true);
      expect(listeners['deviceorientation']).toHaveLength(1);

      // Simulate 60Hz orientation event
      listeners['deviceorientation'][0]({
        beta: 12.5,  // pitch
        alpha: 45.0, // yaw
        gamma: -3.2, // roll
      });

      expect(mockChannel.sentData).toHaveLength(1);
      const decoded = sensorsChannel.decodeUpdate(mockChannel.sentData[0]);
      expect(decoded.pose?.rotationDegrees?.x).toBeCloseTo(12.5);
      expect(decoded.pose?.rotationDegrees?.y).toBeCloseTo(45.0);
      expect(decoded.pose?.rotationDegrees?.z).toBeCloseTo(-3.2);
      expect(decoded.transition).toBe(TransitionMode.TRANSITION_MODE_STEP);
      expect(decoded.transition).toBe(TRANSITION_MODE_IMMEDIATE);
      expect(transport.calls).toHaveLength(0); // No gRPC flood

      // Stop stream
      clientWithChannel.stopBrowserSensorStream();
      expect(clientWithChannel.isBrowserStreaming).toBe(false);
      expect(listeners['deviceorientation']).toHaveLength(0);
    } finally {
      if (prevWindow === undefined) {
        delete (globalThis as any).window;
      } else {
        (globalThis as any).window = prevWindow;
      }
    }
  });

  it('startBrowserSensorStream drops events without flooding gRPC when channel unavailable', () => {
    const listeners: Record<string, ((ev: any) => void)[]> = {};
    const mockWindow = {
      addEventListener(type: string, listener: (ev: any) => void) {
        listeners[type] = listeners[type] || [];
        listeners[type].push(listener);
      },
      removeEventListener(type: string, listener: (ev: any) => void) {
        if (listeners[type]) {
          listeners[type] = listeners[type].filter((l) => l !== listener);
        }
      },
    };

    const prevWindow = (globalThis as any).window;
    try {
      (globalThis as any).window = mockWindow;

      clientWithoutChannel.startBrowserSensorStream();
      expect(clientWithoutChannel.isBrowserStreaming).toBe(true);

      // Simulate 60 events (1 second at 60Hz)
      for (let i = 0; i < 60; i++) {
        listeners['deviceorientation'][0]({ beta: i, alpha: i * 2, gamma: 0 });
      }

      // Must be 0 gRPC calls instead of 60 un-awaited unary calls!
      expect(transport.calls).toHaveLength(0);

      clientWithoutChannel.stopBrowserSensorStream();
      expect(clientWithoutChannel.isBrowserStreaming).toBe(false);
    } finally {
      if (prevWindow === undefined) {
        delete (globalThis as any).window;
      } else {
        (globalThis as any).window = prevWindow;
      }
    }
  });
});

/** Lets the subscription's background task drain the queued messages. */
async function settle(): Promise<void> {
  for (let i = 0; i < 5; i++) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}
