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
import { ClipboardState, UpdateClipboardStateRequest } from '@android/emulator-services/system/clipboard_service';

describe('ClipboardClient (TDD)', () => {
  let mockTransport: any;

  beforeEach(() => {
    mockTransport = {
      request: vi.fn(),
    };
  });

  it('getClipboard fetches raw clipboard buffer', async () => {
    const client = new ClipboardClient(mockTransport);

    const testPayload = new TextEncoder().encode('Hello Android Device');
    mockTransport.request.mockResolvedValue(
      ClipboardState.encode({
        data: testPayload,
      }).finish()
    );

    const result = await client.getClipboard();
    expect(mockTransport.request).toHaveBeenCalledTimes(1);
    const [service, method] = mockTransport.request.mock.calls[0];
    expect(service).toBe('android.emulation.v2.system.ClipboardService');
    expect(method).toBe('GetClipboardState');
    expect(result.data).toEqual(testPayload);
  });

  it('getClipboardText decodes UTF-8 text string', async () => {
    const client = new ClipboardClient(mockTransport);

    mockTransport.request.mockResolvedValue(
      ClipboardState.encode({
        data: new TextEncoder().encode('Test Clipboard String'),
      }).finish()
    );

    const text = await client.getClipboardText();
    expect(text).toBe('Test Clipboard String');
  });

  it('setClipboardText encodes string and sends UpdateClipboardState request', async () => {
    const client = new ClipboardClient(mockTransport);

    mockTransport.request.mockResolvedValue(
      ClipboardState.encode({
        data: new TextEncoder().encode('New Copied Text'),
      }).finish()
    );

    const result = await client.setClipboardText('New Copied Text');
    expect(mockTransport.request).toHaveBeenCalledTimes(1);
    const [service, method, data] = mockTransport.request.mock.calls[0];
    expect(service).toBe('android.emulation.v2.system.ClipboardService');
    expect(method).toBe('UpdateClipboardState');

    const decoded = UpdateClipboardStateRequest.decode(data);
    expect(new TextDecoder().decode(decoded.clipboardState?.data)).toBe('New Copied Text');
    expect(new TextDecoder().decode(result.data)).toBe('New Copied Text');
  });

  it('streamClipboard subscribes to live updates via transport.stream', () => {
    mockTransport.stream = vi.fn();
    const client = new ClipboardClient(mockTransport);

    const onState = vi.fn();
    client.streamClipboard(onState);

    expect(mockTransport.stream).toHaveBeenCalledTimes(1);
    const [service, method, , onMsg] = mockTransport.stream.mock.calls[0];
    expect(service).toBe('android.emulation.v2.system.ClipboardService');
    expect(method).toBe('StreamClipboardState');

    const sampleBytes = new TextEncoder().encode('Live Clipboard Stream');
    onMsg(ClipboardState.encode({ data: sampleBytes }).finish());
    expect(onState).toHaveBeenCalledWith(expect.objectContaining({ data: sampleBytes }));
  });
});