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
import { DeviceView, computeNormalizedCoordinates } from '../src/react/DeviceView';

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

  it('DeviceView renders unmuted video element and container', () => {
    const html = renderToString(
      <DeviceProvider client={client}>
        <DeviceView className="custom-device-class" />
      </DeviceProvider>
    );

    expect(html).toContain('<video');
    expect(html).not.toContain('muted=""');
    expect(html).toContain('custom-device-class');
  });

  it('computeNormalizedCoordinates accounts for pillarboxed (left/right) black bars', () => {
    // 1000x1000 element displaying a 500x1000 (1:2 aspect ratio) portrait phone stream
    // Active video area is width=500 centered at x=[250..750], y=[0..1000]
    const rect = { left: 0, top: 0, width: 1000, height: 1000 };

    // Center of active video -> (0.5, 0.5)
    expect(computeNormalizedCoordinates(500, 500, rect, 500, 1000)).toEqual({
      x: 0.5,
      y: 0.5,
    });

    // Left edge of active video (x=250) -> (0.0, 0.25)
    expect(computeNormalizedCoordinates(250, 250, rect, 500, 1000)).toEqual({
      x: 0,
      y: 0.25,
    });

    // Click inside left black pillarbox bar (x=100) clamps to x=0
    expect(computeNormalizedCoordinates(100, 500, rect, 500, 1000)).toEqual({
      x: 0,
      y: 0.5,
    });
  });

  it('computeNormalizedCoordinates accounts for letterboxed (top/bottom) black bars', () => {
    // 1000x1000 element displaying a 1000x500 (2:1 aspect ratio) landscape video stream
    // Active video area is height=500 centered at x=[0..1000], y=[250..750]
    const rect = { left: 0, top: 0, width: 1000, height: 1000 };

    // Top edge of active video (y=250) -> (0.5, 0.0)
    expect(computeNormalizedCoordinates(500, 250, rect, 1000, 500)).toEqual({
      x: 0.5,
      y: 0,
    });

    // Bottom edge of active video (y=750) -> (0.5, 1.0)
    expect(computeNormalizedCoordinates(500, 750, rect, 1000, 500)).toEqual({
      x: 0.5,
      y: 1,
    });
  });
});
