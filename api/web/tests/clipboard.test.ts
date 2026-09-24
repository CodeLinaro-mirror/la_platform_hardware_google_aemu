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
import { ClipboardClient } from '../src/core/clipboard';
import {
  ClipboardState,
  UpdateClipboardStateRequest,
} from '@android/emulator-services/system_service';
import { FakeTransport } from './fake_transport';

const SERVICE = 'android.emulation.v2.SystemService';

describe('ClipboardClient', () => {
  let transport: FakeTransport;
  let client: ClipboardClient;

  beforeEach(() => {
    transport = new FakeTransport();
    client = new ClipboardClient(transport.asTransport());
  });

  function encodeText(text: string): Uint8Array {
    return ClipboardState.encode({ text, mimeType: 'text/plain' }).finish();
  }

  it('calls GetClipboardState and decodes the clipboard text', async () => {
    transport.respondWith(
      ClipboardState.encode({ text: 'Hello Android Device', mimeType: 'text/plain' }).finish()
    );

    const result = await client.GetClipboardState();

    expect(transport.calls).toHaveLength(1);
    expect(transport.lastCall.service).toBe(SERVICE);
    expect(transport.lastCall.method).toBe('GetClipboardState');
    expect(result.text).toBe('Hello Android Device');
    expect(result.mimeType).toBe('text/plain');
  });

  it('reports an empty clipboard as an empty string', async () => {
    transport.respondWith(ClipboardState.encode({ text: '', mimeType: '' }).finish());

    const result = await client.GetClipboardState();

    expect(result.text).toBe('');
  });

  it('sends the text payload to UpdateClipboardState', async () => {
    transport.respondWith(encodeText('New Copied Text'));

    const result = await client.UpdateClipboardState({
      text: 'New Copied Text',
      mimeType: 'text/plain',
    });

    expect(transport.lastCall.service).toBe(SERVICE);
    expect(transport.lastCall.method).toBe('UpdateClipboardState');

    const request = UpdateClipboardStateRequest.decode(transport.lastCall.data);
    expect(request.text).toBe('New Copied Text');
    expect(result.text).toBe('New Copied Text');
  });

  it('decodes each state pushed by StreamClipboardState', async () => {
    const onState = vi.fn();

    const unsubscribe = client.subscribeClipboardState(onState);
    transport.stream().push(encodeText('Live Clipboard Stream'));
    await settle();

    expect(transport.streamCalls[0].service).toBe(SERVICE);
    expect(transport.streamCalls[0].method).toBe('StreamClipboardState');
    expect(onState).toHaveBeenCalledWith(
      expect.objectContaining({ text: 'Live Clipboard Stream' })
    );

    unsubscribe();
  });

  it('aborts the underlying stream on unsubscribe', async () => {
    const unsubscribe = client.subscribeClipboardState(() => {});
    await settle();

    expect(transport.streamCalls[0].signal?.aborted).toBe(false);

    unsubscribe();

    expect(transport.streamCalls[0].signal?.aborted).toBe(true);
  });

  it('yields decoded states from the iterator form', async () => {
    const controller = new AbortController();
    const states: ClipboardState[] = [];

    const pump = (async () => {
      for await (const state of client.StreamClipboardState({}, controller.signal)) {
        states.push(state);
        controller.abort();
      }
    })();

    transport.stream().push(encodeText('Iterated'));
    await pump;

    expect(transport.streamCalls[0].method).toBe('StreamClipboardState');
    expect(states).toHaveLength(1);
    expect(states[0].text).toBe('Iterated');
  });
});

/** Lets the subscription's background task drain the queued messages. */
async function settle(): Promise<void> {
  for (let i = 0; i < 5; i++) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}