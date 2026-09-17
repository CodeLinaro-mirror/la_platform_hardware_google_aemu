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
import { InputChannel, PointerAction, KeyAction, ToolType } from '../src/core/input_channel';

// Mock RTCDataChannel
class MockRTCDataChannel {
  public label: string;
  public readyState: RTCDataChannelState = 'connecting';
  public bufferedAmount = 0;
  public binaryType = 'arraybuffer';
  public onopen: ((ev: Event) => any) | null = null;
  public onclose: ((ev: Event) => any) | null = null;
  public onerror: ((ev: Event) => any) | null = null;
  public onmessage: ((ev: MessageEvent) => any) | null = null;

  public sentData: Uint8Array[] = [];

  constructor(label = 'input') {
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
      this.onopen(new Event('open'));
    }
  }
}

describe('InputChannel (TDD)', () => {
  let mockChannel: MockRTCDataChannel;
  let inputChannel: InputChannel;

  beforeEach(() => {
    mockChannel = new MockRTCDataChannel('input');
    inputChannel = new InputChannel();
  });

  it('attaches to RTCDataChannel and sets binaryType to arraybuffer', () => {
    inputChannel.attach(mockChannel as any);
    expect(mockChannel.binaryType).toBe('arraybuffer');
    expect(inputChannel.isReady).toBe(false);

    mockChannel.simulateOpen();
    expect(inputChannel.isReady).toBe(true);
  });

  it('serializes and sends pointer down/move/up events when channel is open', () => {
    inputChannel.attach(mockChannel as any);
    mockChannel.simulateOpen();

    inputChannel.sendPointer({
      displayId: 0,
      slot: 0,
      action: PointerAction.DOWN,
      x: 0.5,
      y: 0.25,
      pressure: 1.0,
      toolType: ToolType.FINGER,
    });

    expect(mockChannel.sentData.length).toBe(1);
    const sentBytes = mockChannel.sentData[0];
    expect(sentBytes.byteLength).toBeGreaterThan(0);

    // Verify deserialization matches sent parameters
    const decoded = inputChannel.decodeEvent(sentBytes);
    expect(decoded.pointer).toBeDefined();
    expect(decoded.pointer?.displayId).toBe(0);
    expect(decoded.pointer?.pointers?.[0]?.pointerId).toBe(0);
    expect(decoded.pointer?.action).toBe(PointerAction.DOWN);
    expect(decoded.pointer?.pointers?.[0]?.x).toBeCloseTo(0.5);
    expect(decoded.pointer?.pointers?.[0]?.y).toBeCloseTo(0.25);
    expect(decoded.pointer?.pointers?.[0]?.pressure).toBeCloseTo(1.0);
  });

  it('serializes and sends key events', () => {
    inputChannel.attach(mockChannel as any);
    mockChannel.simulateOpen();

    inputChannel.sendKey({
      domCode: 'Enter',
      action: KeyAction.DOWN,
    });

    expect(mockChannel.sentData.length).toBe(1);
    const decoded = inputChannel.decodeEvent(mockChannel.sentData[0]);
    expect(decoded.key).toBeDefined();
    expect(decoded.key?.domCode).toBe('Enter');
    expect(decoded.key?.action).toBe(KeyAction.DOWN);
  });

  it('serializes and sends scroll wheel events', () => {
    inputChannel.attach(mockChannel as any);
    mockChannel.simulateOpen();

    inputChannel.sendScroll({
      displayId: 0,
      deltaX: 0,
      deltaY: -120,
    });

    expect(mockChannel.sentData.length).toBe(1);
    const decoded = inputChannel.decodeEvent(mockChannel.sentData[0]);
    expect(decoded.pointer).toBeDefined();
    expect(decoded.pointer?.action).toBe(PointerAction.SCROLL);
    expect(decoded.pointer?.scrollY).toBe(-120);
  });

  it('queues events when channel is connecting and flushes upon open', () => {
    inputChannel.attach(mockChannel as any);
    expect(inputChannel.isReady).toBe(false);

    // Send while connecting
    inputChannel.sendPointer({
      displayId: 0,
      slot: 0,
      action: PointerAction.DOWN,
      x: 0.1,
      y: 0.2,
    });
    inputChannel.sendPointer({
      displayId: 0,
      slot: 0,
      action: PointerAction.UP,
      x: 0.1,
      y: 0.2,
    });

    expect(mockChannel.sentData.length).toBe(0);

    // Channel opens
    mockChannel.simulateOpen();

    // Queued events should be flushed in order
    expect(mockChannel.sentData.length).toBe(2);
    const ev1 = inputChannel.decodeEvent(mockChannel.sentData[0]);
    const ev2 = inputChannel.decodeEvent(mockChannel.sentData[1]);
    expect(ev1.pointer?.action).toBe(PointerAction.DOWN);
    expect(ev2.pointer?.action).toBe(PointerAction.UP);
  });
});
