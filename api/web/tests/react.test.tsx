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
import React from 'react';
import { renderToString } from 'react-dom/server';
import { DeviceClient } from '../src/core/client';
import { DeviceProvider, useDeviceClient } from '../src/react/context';
import { useDevice, useSensors, useVm } from '../src/react/hooks';
import { DeviceView } from '../src/react/DeviceView';

describe('React Integration (TDD)', () => {
  let client: DeviceClient;

  beforeEach(() => {
    client = DeviceClient.create({
      endpoint: 'http://localhost:8085',
      fetch: vi.fn(),
    });
  });

  it('DeviceProvider exposes client to context consumers', () => {
    let capturedClient: DeviceClient | null = null;

    function TestChild() {
      capturedClient = useDeviceClient();
      return <div>Test</div>;
    }

    const html = renderToString(
      <DeviceProvider client={client}>
        <TestChild />
      </DeviceProvider>
    );

    expect(html).toContain('Test');
    expect(capturedClient).toBe(client);
  });

  it('useDeviceClient throws when rendered outside of DeviceProvider', () => {
    function OrphanChild() {
      useDeviceClient();
      return null;
    }

    expect(() => renderToString(<OrphanChild />)).toThrow(
      'useDeviceClient must be used within a <DeviceProvider>'
    );
  });

  it('DeviceView renders video canvas and container element', () => {
    const html = renderToString(
      <DeviceProvider client={client}>
        <DeviceView className="custom-device-class" />
      </DeviceProvider>
    );

    expect(html).toContain('<video');
    expect(html).toContain('custom-device-class');
  });
});
