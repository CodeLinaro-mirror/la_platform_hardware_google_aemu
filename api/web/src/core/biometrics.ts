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
import {
  DeepPartial,
  InjectFingerprintRequest,
  InjectFingerprintResponse,
  SystemService,
  SystemServiceClientImpl,
} from '@android/emulator-services/system_service';

export { InjectFingerprintRequest, InjectFingerprintResponse };

/**
 * Headless API client for biometric simulation RPCs on SystemService.
 *
 * Methods named in `PascalCase` are the RPCs of `SystemService`, spelled
 * exactly as `system_service.proto` declares them.
 */
export class BiometricsClient {
  private readonly rpc: SystemService;

  constructor(transport: GrpcWebTransport) {
    this.rpc = new SystemServiceClientImpl(transport);
  }

  /**
   * `fingerId` selects an enrolled finger; an unenrolled id simulates a
   * rejected scan. When `touching` is omitted, performs a synchronous atomic
   * touch-and-release scan cycle (~100ms hold).
   */
  public InjectFingerprint(
    request: DeepPartial<InjectFingerprintRequest>
  ): Promise<InjectFingerprintResponse> {
    return this.rpc.InjectFingerprint(InjectFingerprintRequest.fromPartial(request));
  }
}