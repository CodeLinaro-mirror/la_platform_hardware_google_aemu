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
import { subscribe, Unsubscribe } from './streaming';
import {
  DeepPartial,
  GetVmStateRequest,
  HardResetVmRequest,
  HypervisorService,
  HypervisorServiceClientImpl,
  PauseVmRequest,
  ResetVmRequest,
  ResumeVmRequest,
  StopVmRequest,
  StreamVmStateRequest,
  VmState,
  VmState_State,
} from '@android/emulator-services/hypervisor_service';

export { VmState, VmState_State, StopVmRequest };

/**
 * Headless API client for VM lifecycle RPCs on HypervisorService.
 *
 * Methods named in `PascalCase` are the RPCs of `HypervisorService`, spelled
 * exactly as `hypervisor_service.proto` declares them. `camelCase` methods are
 * conveniences this package adds and have no RPC of their own.
 */
export class VmClient {
  private readonly rpc: HypervisorService;

  constructor(transport: GrpcWebTransport) {
    this.rpc = new HypervisorServiceClientImpl(transport);
  }

  public GetVmState(request: DeepPartial<GetVmStateRequest> = {}): Promise<VmState> {
    return this.rpc.GetVmState(GetVmStateRequest.fromPartial(request));
  }

  public StreamVmState(
    request: DeepPartial<StreamVmStateRequest> = {},
    signal?: AbortSignal
  ): AsyncIterable<VmState> {
    return this.rpc.StreamVmState(StreamVmStateRequest.fromPartial(request), signal);
  }

  /**
   * Suspends the hypervisor. The guest stops executing but retains its state.
   */
  public PauseVm(request: DeepPartial<PauseVmRequest> = {}): Promise<VmState> {
    return this.rpc.PauseVm(PauseVmRequest.fromPartial(request));
  }

  /** Resumes a paused hypervisor. */
  public ResumeVm(request: DeepPartial<ResumeVmRequest> = {}): Promise<VmState> {
    return this.rpc.ResumeVm(ResumeVmRequest.fromPartial(request));
  }

  /** Soft/warm reboot (ACPI restart). Virtual hardware and RAM are preserved. */
  public ResetVm(request: DeepPartial<ResetVmRequest> = {}): Promise<VmState> {
    return this.rpc.ResetVm(ResetVmRequest.fromPartial(request));
  }

  /**
   * Cold power-cycle: RAM is cleared and the virtual hardware is reset. Only
   * available on emulated devices.
   */
  public HardResetVm(request: DeepPartial<HardResetVmRequest> = {}): Promise<VmState> {
    return this.rpc.HardResetVm(HardResetVmRequest.fromPartial(request));
  }

  /** Gracefully terminates the emulator process. */
  public StopVm(request: DeepPartial<StopVmRequest> = {}): Promise<VmState> {
    return this.rpc.StopVm(StopVmRequest.fromPartial(request));
  }

  /** Callback form of {@link VmClient.StreamVmState}. */
  public subscribeVmState(
    onState: (state: VmState) => void,
    onError?: (err: unknown) => void,
    request: DeepPartial<StreamVmStateRequest> = {}
  ): Unsubscribe {
    return subscribe((signal) => this.StreamVmState(request, signal), onState, onError);
  }
}
