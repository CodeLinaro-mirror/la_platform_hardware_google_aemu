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
import { BiometricsClient } from '../src/core/biometrics';
import { InjectFingerprintRequest } from '@android/emulator-services/system/biometrics_service';

describe('BiometricsClient (TDD)', () => {
  let mockTransport: any;

  beforeEach(() => {
    mockTransport = {
      request: vi.fn(),
    };
  });

  it('sendFingerprint sends InjectFingerprintRequest with fingerId and touching flag', async () => {
    const client = new BiometricsClient(mockTransport);
    mockTransport.request.mockResolvedValue(new Uint8Array([]));

    await client.sendFingerprint(1, true);

    expect(mockTransport.request).toHaveBeenCalledTimes(1);
    const [service, method, data] = mockTransport.request.mock.calls[0];
    expect(service).toBe('android.emulation.v2.system.BiometricsService');
    expect(method).toBe('InjectFingerprint');

    const decoded = InjectFingerprintRequest.decode(data);
    expect(decoded.fingerId).toBe(1);
    expect(decoded.touching).toBe(true);
  });
});