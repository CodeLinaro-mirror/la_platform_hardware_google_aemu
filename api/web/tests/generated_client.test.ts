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

import { describe, it, expect, vi } from 'vitest';

import {
  AudioServiceClientImpl,
} from '@android/emulator-services/audio_service';
import {
  HypervisorServiceClientImpl,
  VmState,
  VmState_State,
} from '@android/emulator-services/hypervisor_service';

import { DeviceError } from '../src/core/auth';
import { GrpcWebTransport } from '../src/core/transport';

describe('generated client compatibility', () => {
  it('constructs a generated client from the real transport', () => {
    const transport = new GrpcWebTransport({ endpoint: 'http://localhost:8554' });

    // A compile error here is the point of the test.
    const hypervisor = new HypervisorServiceClientImpl(transport);

    expect(hypervisor).toBeDefined();
  });

  it('routes a unary call to the service and method named in the proto', async () => {
    const request = vi.fn().mockResolvedValue(
      VmState.encode({
        state: VmState_State.PAUSED,
        allocatedCpuCoreCount: 4,
        allocatedRamBytes: 2048,
      }).finish()
    );
    const hypervisor = new HypervisorServiceClientImpl({
      request,
      clientStreamingRequest: vi.fn(),
      serverStreamingRequest: vi.fn(),
      bidirectionalStreamingRequest: vi.fn(),
    });

    const state = await hypervisor.PauseVm({});

    const [service, method] = request.mock.calls[0];
    expect(service).toBe('android.emulation.v2.HypervisorService');
    expect(method).toBe('PauseVm');
    expect(state.state).toBe(VmState_State.PAUSED);
    expect(state.allocatedCpuCoreCount).toBe(4);
  });

  it('rejects client-streaming RPCs with UNIMPLEMENTED', async () => {
    const transport = new GrpcWebTransport({ endpoint: 'http://localhost:8554' });
    const audio = new AudioServiceClientImpl(transport);

    const attempt = audio.StreamMicrophoneAudio(
      (async function* () {
        yield { pcmData: new Uint8Array(0), sampleRateHz: 16000, channelCount: 1, format: 0, timestampNanos: 0 };
      })()
    );

    await expect(attempt).rejects.toThrow(DeviceError);
    await expect(attempt).rejects.toThrow(/not supported over gRPC-Web/);
  });
});
