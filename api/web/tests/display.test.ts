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
import { DisplayClient, Rotation } from '../src/core/display';
import {
  ListDisplaysResponse,
  Display,
  CreateDisplayRequest,
  DeleteDisplayRequest,
  UpdateDisplayRequest,
} from '@android/emulator-services/display_service';
import { FakeTransport } from './fake_transport';

const SERVICE = 'android.emulation.v2.DisplayService';

describe('DisplayClient', () => {
  let transport: FakeTransport;
  let client: DisplayClient;

  beforeEach(() => {
    transport = new FakeTransport();
    client = new DisplayClient(transport.asTransport());
  });

  function display(overrides: Partial<Display>): Display {
    return Display.fromPartial({
      displayId: 0,
      primary: true,
      width: 1080,
      height: 2400,
      densityDpi: 420,
      rotation: Rotation.ROTATION_DEG0,
      brightnessPercent: 100,
      autoBrightnessEnabled: true,
      ...overrides,
    });
  }

  function encodeList(displays: Display[]): Uint8Array {
    return ListDisplaysResponse.encode({ displays }).finish();
  }

  it('decodes the display list returned by ListDisplays', async () => {
    transport.respondWith(encodeList([display({ width: 1080, primary: true })]));

    const result = await client.ListDisplays();

    expect(transport.calls).toHaveLength(1);
    expect(transport.lastCall.service).toBe(SERVICE);
    expect(transport.lastCall.method).toBe('ListDisplays');
    expect(result.displays).toHaveLength(1);
    expect(result.displays[0].width).toBe(1080);
    expect(result.displays[0].primary).toBe(true);
  });

  it('sends exactly the display geometry the caller supplies to CreateDisplay', async () => {
    transport.respondWith(
      Display.encode(
        display({ displayId: 1, primary: false, width: 720, height: 1280, densityDpi: 320 })
      ).finish()
    );

    const result = await client.CreateDisplay({
      width: 720,
      height: 1280,
      densityDpi: 320,
    });

    expect(transport.calls).toHaveLength(1);
    expect(transport.lastCall.service).toBe(SERVICE);
    expect(transport.lastCall.method).toBe('CreateDisplay');

    const request = CreateDisplayRequest.decode(transport.lastCall.data);
    expect(request.width).toBe(720);
    expect(request.height).toBe(1280);
    expect(request.densityDpi).toBe(320);
    expect(result.displayId).toBe(1);
  });

  it('sends DeleteDisplayRequest with the display id', async () => {
    transport.respondWith(new Uint8Array());

    await client.DeleteDisplay({ displayId: 1 });

    expect(transport.calls).toHaveLength(1);
    expect(transport.lastCall.service).toBe(SERVICE);
    expect(transport.lastCall.method).toBe('DeleteDisplay');
    expect(DeleteDisplayRequest.decode(transport.lastCall.data).displayId).toBe(1);
  });

  it('decodes the display returned by GetDisplay', async () => {
    transport.respondWith(Display.encode(display({ displayId: 2 })).finish());

    const result = await client.GetDisplay({ displayId: 2 });

    expect(transport.lastCall.method).toBe('GetDisplay');
    expect(result.displayId).toBe(2);
  });

  it('sends only the fields the update supplies', async () => {
    transport.respondWith(Display.encode(display({ densityDpi: 560 })).finish());

    const result = await client.UpdateDisplay({ displayId: 0, densityDpi: 560 });

    expect(transport.lastCall.method).toBe('UpdateDisplay');
    const request = UpdateDisplayRequest.decode(transport.lastCall.data);
    expect(request.displayId).toBe(0);
    expect(request.densityDpi).toBe(560);
    expect(request.width).toBeUndefined();
    expect(request.height).toBeUndefined();
    expect(result.densityDpi).toBe(560);
  });

  it('sends every supplied field of a wider update', async () => {
    transport.respondWith(Display.encode(display({})).finish());

    await client.UpdateDisplay({
      displayId: 1,
      width: 800,
      height: 600,
      userRotation: Rotation.ROTATION_DEG90,
      brightnessPercent: 50,
      autoBrightnessEnabled: false,
    });

    const request = UpdateDisplayRequest.decode(transport.lastCall.data);
    expect(request.displayId).toBe(1);
    expect(request.width).toBe(800);
    expect(request.height).toBe(600);
    expect(request.userRotation).toBe(Rotation.ROTATION_DEG90);
    expect(request.brightnessPercent).toBe(50);
    expect(request.autoBrightnessEnabled).toBe(false);
  });

  it('leaves absent fields undefined on the wire', async () => {
    transport.respondWith(Display.encode(display({})).finish());

    await client.UpdateDisplay({ displayId: 0, width: 640, height: undefined });

    const request = UpdateDisplayRequest.decode(transport.lastCall.data);
    expect(request.displayId).toBe(0);
    expect(request.width).toBe(640);
    expect(request.height).toBeUndefined();
  });

  it('delivers each display list pushed by StreamDisplays', async () => {
    const onDisplays = vi.fn();

    const unsubscribe = client.subscribeDisplayState(onDisplays);
    transport.stream().push(encodeList([display({ width: 1080, densityDpi: 440 })]));
    await settle();

    expect(transport.streamCalls[0].service).toBe(SERVICE);
    expect(transport.streamCalls[0].method).toBe('StreamDisplays');
    expect(onDisplays).toHaveBeenCalledWith(
      expect.objectContaining({ displays: [expect.objectContaining({ width: 1080 })] })
    );

    unsubscribe();
  });

  it('aborts the underlying stream on unsubscribe', async () => {
    const unsubscribe = client.subscribeDisplayState(() => {});
    await settle();

    expect(transport.streamCalls[0].signal?.aborted).toBe(false);

    unsubscribe();

    expect(transport.streamCalls[0].signal?.aborted).toBe(true);
  });

  it('yields display lists from the iterator form', async () => {
    const stream = transport.stream();
    const controller = new AbortController();
    const batches: ListDisplaysResponse[] = [];

    const consumed = (async () => {
      for await (const response of client.StreamDisplays({}, controller.signal)) {
        batches.push(response);
        controller.abort();
      }
    })();

    stream.push(encodeList([display({ width: 1440 })]));
    await consumed;

    expect(transport.streamCalls[0].method).toBe('StreamDisplays');
    expect(batches).toHaveLength(1);
    expect(batches[0].displays[0].width).toBe(1440);
  });
});

/** Lets the subscription's background task drain the queued messages. */
async function settle(): Promise<void> {
  for (let i = 0; i < 5; i++) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}