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

import { GrpcWebTransport } from './transport';
import { InjectFingerprintRequest } from '@android/emulator-services/system/biometrics_service';

export interface FingerprintInjectionOptions {
  fingerId: number;
  touching?: boolean;
}

/**
 * Headless API client for BiometricsService (Fingerprint simulation).
 */
export class BiometricsClient {
  private readonly transport: GrpcWebTransport;

  constructor(transport: GrpcWebTransport) {
    this.transport = transport;
  }

  public async sendFingerprint(
    fingerId: number = 1,
    touching: boolean = true
  ): Promise<Uint8Array> {
    const req: InjectFingerprintRequest = {
      fingerId,
      touching,
    };
    const reqBytes = InjectFingerprintRequest.encode(req).finish();
    return this.transport.request(
      'android.emulation.v2.system.BiometricsService',
      'InjectFingerprint',
      reqBytes
    );
  }
}